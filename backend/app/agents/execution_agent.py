"""Agent 6 -- Test Execution Agent (API + Playwright + TinyFish Browser).

Executes APPROVED test cases against the SIMULATED core banking system and
records evidence.

Safety (brief requirements 5.6, 12.11, 12.12):
  * Only the simulated CBS is ever targeted. There is no code path to a real
    banking system.
  * A state-changing test requires an APPROVED `TestApproval` record. Without
    one the execution is recorded as BLOCKED, not run.

Drivers:
  PYTEST_API       -- in-process ASGI calls against our own FastAPI app. No
                      network, fully deterministic, used for assertion checking.
  PLAYWRIGHT_LOCAL -- a real local browser against the CBS simulator UI.
  TINYFISH_BROWSER -- TinyFish's cloud browser over CDP. It cannot resolve
                      localhost, so it requires CBS_PUBLIC_BASE_URL.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, AgentUnavailable, agent_activity
from app.agents.cbs_contract import ENDPOINTS_BY_KEY
from app.core.config import settings
from app.core.events import EventType
from app.core.logging import get_logger, redact
from app.integrations.tinyfish import TinyFishClient
from app.models.enums import (
    AgentName,
    EvidenceType,
    ExecutionDriver,
    ExecutionStatus,
    ReviewStatus,
)
from app.models.testing import (
    ExecutionEvidence,
    TestApproval,
    TestCase,
    TestExecution,
    TestExecutionRun,
)

logger = get_logger(__name__)

PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

TRUE_VALUES = {"true", "1", "yes", "y"}
FALSE_VALUES = {"false", "0", "no", "n"}

# Fields the CBS expects as a list/bool rather than the flat string Gemini emits.
JSON_FIELDS = {"documents", "add", "remove"}
BOOL_FIELDS = {"self_declaration", "is_pep"}


class AssertionOutcome:
    def __init__(self, *, name: str, passed: bool, expected: Any, actual: Any, detail: str = ""):
        self.name = name
        self.passed = passed
        self.expected = expected
        self.actual = actual
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _coerce_value(key: str, value: Any) -> Any:
    """Normalise the flat string map from the model into CBS field types."""
    if key in JSON_FIELDS and isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            # A bare comma-separated list is a common model shortcut.
            return [part.strip() for part in text.split(",") if part.strip()]
    if key in BOOL_FIELDS and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in TRUE_VALUES:
            return True
        if lowered in FALSE_VALUES:
            return False
    return value


def _json_path_get(payload: Any, path: str) -> Any:
    """Read a dotted path, supporting `items.0.code` style indices."""
    current = payload
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


class ExecutionAgent:
    def __init__(self, tinyfish: TinyFishClient | None = None) -> None:
        self.tinyfish = tinyfish or TinyFishClient()
        self.evidence_root = Path(settings.evidence_dir)
        self.evidence_root.mkdir(parents=True, exist_ok=True)

    # ---- approval gate ----------------------------------------------------
    def _approval_for(self, db: Session, test_case: TestCase) -> TestApproval | None:
        return (
            db.execute(
                select(TestApproval)
                .where(
                    TestApproval.test_case_id == test_case.id,
                    TestApproval.decision == ReviewStatus.APPROVED.value,
                )
                .order_by(TestApproval.decided_at.desc())
            )
            .scalars()
            .first()
        )

    def _check_authorised(
        self, db: Session, test_case: TestCase
    ) -> tuple[bool, str | None, TestApproval | None]:
        """May this test run? Returns (authorised, blocked_reason, approval)."""
        if not settings.require_approval_for_execution:
            return True, None, self._approval_for(db, test_case)

        approval = self._approval_for(db, test_case)
        if test_case.approval_status != ReviewStatus.APPROVED.value or approval is None:
            if test_case.is_state_changing:
                return (
                    False,
                    (
                        f"Test {test_case.test_code} changes state in the simulated CBS "
                        f"but has no APPROVED human approval record "
                        f"(current status: {test_case.approval_status}). Execution blocked."
                    ),
                    None,
                )
            # Read-only tests are safe to run unapproved; the result still says so.
            return True, None, None
        return True, None, approval

    # ---- run orchestration ------------------------------------------------
    async def run_batch(
        self,
        db: Session,
        ctx: AgentContext,
        test_case_ids: list[uuid.UUID],
        *,
        driver: str = ExecutionDriver.PYTEST_API.value,
        triggered_by_id: uuid.UUID | None = None,
    ) -> TestExecutionRun:
        """Execute a batch of test cases and persist results plus evidence."""
        if not test_case_ids:
            raise AgentUnavailable(
                "No test cases were selected for execution.",
                remediation="Select at least one approved test case.",
            )

        target_base_url = self._resolve_target(driver)

        run = TestExecutionRun(
            status=ExecutionStatus.RUNNING.value,
            driver=driver,
            target_base_url=target_base_url,
            total=len(test_case_ids),
            started_at=datetime.now(UTC),
        )
        if triggered_by_id:
            run.triggered_by_id = triggered_by_id
        db.add(run)
        db.commit()
        db.refresh(run)

        ctx.emit(
            EventType.EXECUTION_STARTED,
            {
                "run_id": str(run.id),
                "driver": driver,
                "target": target_base_url,
                "test_count": len(test_case_ids),
            },
        )

        browser_session_id: str | None = None
        try:
            for index, test_case_id in enumerate(test_case_ids, start=1):
                test_case = db.get(TestCase, test_case_id)
                if test_case is None:
                    logger.warning("Test case %s not found; skipping", test_case_id)
                    continue

                execution = TestExecution(
                    run_id=run.id,
                    test_case_id=test_case.id,
                    status=ExecutionStatus.RUNNING.value,
                    driver=driver,
                    expected_result=test_case.expected_result,
                    started_at=datetime.now(UTC),
                )
                db.add(execution)
                db.commit()
                db.refresh(execution)

                authorised, blocked_reason, approval = self._check_authorised(db, test_case)
                if not authorised:
                    execution.status = ExecutionStatus.BLOCKED.value
                    execution.failure_reason = blocked_reason
                    execution.actual_result = "Not executed: human approval required."
                    execution.finished_at = datetime.now(UTC)
                    run.blocked += 1
                    db.commit()
                    self._save_evidence(
                        db,
                        execution,
                        evidence_type=EvidenceType.LOG,
                        label="Execution blocked",
                        text_payload=blocked_reason,
                    )
                    ctx.emit(
                        EventType.EXECUTION_PROGRESS,
                        {
                            "run_id": str(run.id),
                            "test_code": test_case.test_code,
                            "status": execution.status,
                            "index": index,
                            "total": len(test_case_ids),
                        },
                    )
                    continue

                if approval is not None:
                    execution.approved_by_email = approval.decided_by_email

                started = time.monotonic()
                try:
                    with agent_activity(
                        db,
                        agent=AgentName.TEST_EXECUTION,
                        operation=f"execute: {test_case.test_code}",
                        ctx=ctx,
                        entity_type="TestCase",
                        entity_id=test_case.id,
                        details={"driver": driver, "category": test_case.category},
                    ) as outcome:
                        if driver == ExecutionDriver.PYTEST_API.value:
                            assertions, actual = await self._execute_api(
                                db, execution, test_case
                            )
                        elif driver == ExecutionDriver.PLAYWRIGHT_LOCAL.value:
                            assertions, actual = await self._execute_playwright_local(
                                db, execution, test_case
                            )
                        elif driver == ExecutionDriver.TINYFISH_BROWSER.value:
                            assertions, actual, browser_session_id = (
                                await self._execute_tinyfish_browser(
                                    db, execution, test_case, browser_session_id
                                )
                            )
                        else:
                            raise AgentUnavailable(f"Unknown execution driver {driver!r}")
                        outcome["assertions"] = [a.to_dict() for a in assertions]
                        outcome["message"] = (
                            f"{sum(1 for a in assertions if a.passed)}/{len(assertions)} "
                            "assertions passed"
                        )

                    passed = bool(assertions) and all(a.passed for a in assertions)
                    execution.status = (
                        ExecutionStatus.PASSED.value if passed else ExecutionStatus.FAILED.value
                    )
                    execution.assertions = [a.to_dict() for a in assertions]
                    execution.actual_result = actual
                    if not passed:
                        failed = [a for a in assertions if not a.passed]
                        execution.failure_reason = "; ".join(
                            f"{a.name}: expected {a.expected!r}, got {a.actual!r}"
                            for a in failed
                        )[:4000]
                    if passed:
                        run.passed += 1
                    else:
                        run.failed += 1

                except Exception as exc:
                    execution.status = ExecutionStatus.ERROR.value
                    execution.failure_reason = redact(str(exc))[:4000]
                    execution.actual_result = "Execution raised an error before assertion."
                    run.errored += 1
                    self._save_evidence(
                        db,
                        execution,
                        evidence_type=EvidenceType.LOG,
                        label="Execution error",
                        text_payload=redact(str(exc))[:8000],
                    )
                    logger.warning("Execution of %s errored: %s", test_case.test_code, exc)

                execution.duration_ms = int((time.monotonic() - started) * 1000)
                execution.finished_at = datetime.now(UTC)
                db.commit()

                ctx.emit(
                    EventType.EXECUTION_PROGRESS,
                    {
                        "run_id": str(run.id),
                        "test_code": test_case.test_code,
                        "status": execution.status,
                        "index": index,
                        "total": len(test_case_ids),
                        "duration_ms": execution.duration_ms,
                    },
                )
        finally:
            if browser_session_id:
                await self.tinyfish.close_browser_session(browser_session_id)
                run.tinyfish_session_id = browser_session_id

        run.status = (
            ExecutionStatus.PASSED.value
            if run.failed == 0 and run.errored == 0 and run.passed > 0
            else ExecutionStatus.FAILED.value
            if (run.failed or run.errored)
            else ExecutionStatus.BLOCKED.value
        )
        run.finished_at = datetime.now(UTC)
        db.commit()
        db.refresh(run)

        ctx.emit(
            EventType.EXECUTION_COMPLETED,
            {
                "run_id": str(run.id),
                "status": run.status,
                "total": run.total,
                "passed": run.passed,
                "failed": run.failed,
                "blocked": run.blocked,
                "errored": run.errored,
                "driver": driver,
            },
        )
        return run

    def _resolve_target(self, driver: str) -> str:
        if driver == ExecutionDriver.TINYFISH_BROWSER.value:
            if not settings.cbs_public_base_url:
                raise AgentUnavailable(
                    "The TinyFish Browser driver needs a publicly reachable CBS URL. "
                    "TinyFish runs the browser in its own cloud, so it cannot open "
                    "localhost.",
                    remediation=(
                        "Set CBS_PUBLIC_BASE_URL in backend/.env to a deployed or "
                        "tunnelled URL of the simulator UI, or use the "
                        "PLAYWRIGHT_LOCAL driver instead."
                    ),
                )
            if not self.tinyfish.configured:
                raise AgentUnavailable(
                    "TinyFish is not configured, so the cloud browser driver cannot run.",
                    remediation="Set TINYFISH_API_KEY in backend/.env.",
                )
            return settings.cbs_public_base_url
        if driver == ExecutionDriver.PLAYWRIGHT_LOCAL.value:
            return settings.cbs_local_base_url
        return "asgi://in-process"

    # ---- driver: in-process API ------------------------------------------
    async def _execute_api(
        self, db: Session, execution: TestExecution, test_case: TestCase
    ) -> tuple[list[AssertionOutcome], str]:
        """Call the CBS API in-process over ASGI and assert on the response."""
        spec = test_case.execution_spec or {}
        method = str(spec.get("method", "POST")).upper()
        path_template = str(spec.get("path", ""))
        endpoint = ENDPOINTS_BY_KEY.get(f"{method} {path_template}")
        if endpoint is None:
            raise AgentUnavailable(
                f"Test {test_case.test_code} targets {method} {path_template}, "
                "which is not a CBS endpoint."
            )

        # Import here: app.main imports routers, which import this module.
        from app.main import app

        payload = {k: _coerce_value(k, v) for k, v in (test_case.test_data or {}).items()}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://cbs-simulator.local", timeout=60.0
        ) as client:
            fixtures = await self._provision_fixtures(client, endpoint, payload)
            path, unresolved = self._resolve_path(path_template, payload, fixtures)
            if unresolved:
                raise AgentUnavailable(
                    f"Could not resolve path placeholder(s) {unresolved} for "
                    f"{test_case.test_code}. The test data did not supply them and "
                    "no fixture could be provisioned."
                )

            body = self._request_body(endpoint, payload, fixtures)
            logger.info("Executing %s: %s %s", test_case.test_code, method, path)

            if method == "GET":
                response = await client.get(path, params=body or None)
            elif method == "DELETE":
                response = await client.delete(path)
            else:
                response = await client.request(method, path, json=body)

        try:
            response_json: Any = response.json()
        except Exception:
            response_json = None

        assertions = self._assert_response(spec, response, response_json)

        # Evidence: the full request/response exchange.
        self._save_evidence(
            db,
            execution,
            evidence_type=EvidenceType.API_RESPONSE,
            label=f"{method} {path} -> {response.status_code}",
            payload={
                "request": {"method": method, "path": path, "body": redact(body)},
                "response": {
                    "status_code": response.status_code,
                    "body": redact(response_json)
                    if response_json is not None
                    else response.text[:4000],
                },
                "fixtures_provisioned": redact(fixtures),
            },
        )
        actual = (
            f"HTTP {response.status_code} "
            f"{json.dumps(response_json, default=str)[:1500] if response_json is not None else response.text[:1500]}"
        )
        return assertions, actual

    def _assert_response(
        self, spec: dict, response: httpx.Response, response_json: Any
    ) -> list[AssertionOutcome]:
        assertions: list[AssertionOutcome] = []

        expected_status = spec.get("expect_status")
        if expected_status is not None:
            assertions.append(
                AssertionOutcome(
                    name="http_status",
                    passed=response.status_code == int(expected_status),
                    expected=int(expected_status),
                    actual=response.status_code,
                    detail="Expected HTTP status from the CBS contract",
                )
            )

        json_path = spec.get("expect_json_path")
        json_value = spec.get("expect_json_value")
        if json_path:
            actual_value = _json_path_get(response_json, str(json_path))
            if json_value in (None, ""):
                # Presence assertion only.
                assertions.append(
                    AssertionOutcome(
                        name=f"json.{json_path} present",
                        passed=actual_value is not None,
                        expected="<present>",
                        actual=actual_value,
                    )
                )
            else:
                # String comparison: the contract records values as strings.
                assertions.append(
                    AssertionOutcome(
                        name=f"json.{json_path}",
                        passed=str(actual_value) == str(json_value),
                        expected=str(json_value),
                        actual=None if actual_value is None else str(actual_value),
                    )
                )

        if not assertions:
            # A test with nothing to assert cannot pass or fail honestly.
            assertions.append(
                AssertionOutcome(
                    name="assertable_expectation",
                    passed=False,
                    expected="at least one assertion in execution_spec",
                    actual="none",
                    detail="The generated test declared no checkable expectation.",
                )
            )
        return assertions

    # ---- fixture provisioning --------------------------------------------
    async def _provision_fixtures(
        self, client: httpx.AsyncClient, endpoint, payload: dict  # noqa: ANN001
    ) -> dict[str, Any]:
        """Create the entities a test needs, when the test did not supply them.

        Generated tests reference `customer_id` / `account_id` without knowing
        real identifiers, so the executor seeds a clearly-marked test entity on
        demand. All seeded rows carry is_test_data = true.

        An entity reference can arrive either as a path placeholder
        (`/accounts/{account_id}/status`) or as a request-body field
        (`POST /transactions` takes `account_id` in the body), so both are
        considered here.
        """
        referenceable = {"customer_id", "account_id", "task_id", "ucic"}
        needed = set(PLACEHOLDER_RE.findall(endpoint.path)) | (
            referenceable & set(endpoint.request_fields)
        )
        fixtures: dict[str, Any] = {}

        def already_supplied(name: str) -> bool:
            value = payload.get(name)
            return bool(value) and bool(UUID_RE.match(str(value)) or name == "ucic")

        wants_account = "account_id" in needed and not already_supplied("account_id")
        wants_customer = "customer_id" in needed and not already_supplied("customer_id")
        wants_task = "task_id" in needed and not already_supplied("task_id")
        wants_ucic = "ucic" in needed and not payload.get("ucic")

        # An account needs a fully onboarded customer behind it.
        if wants_account or wants_customer or wants_ucic or wants_task:
            customer = await self._seed_customer(client)
            fixtures["customer_id"] = customer["id"]
            fixtures["ucic"] = customer["ucic"]

            if wants_task:
                tasks = (
                    await client.get("/api/cbs/maker-checker", params={"status": "PENDING"})
                ).json()
                match = next(
                    (t for t in tasks.get("items", []) if t["entity_id"] == customer["id"]),
                    None,
                )
                if match:
                    fixtures["task_id"] = match["id"]

            # For an account we must approve the customer and verify KYC first.
            if wants_account:
                await self._approve_customer(client, customer["id"])
                await client.post(
                    f"/api/cbs/customers/{customer['id']}/kyc/verify",
                    json={
                        "kyc_type": "CDD",
                        "documents": [
                            {
                                "doc_type": "PASSPORT",
                                "number": "SIMFIXTURE",
                                "expiry_date": "2033-01-01",
                                "verified": True,
                            },
                            {"doc_type": "PAN", "number": "ABCDE1234F", "verified": True},
                        ],
                        "verification_mode": "IN_PERSON",
                    },
                )
                product = str(payload.get("product_code") or "SB-REG")
                account_response = await client.post(
                    "/api/cbs/accounts",
                    json={
                        "customer_id": customer["id"],
                        "account_type": "CURRENT" if product.startswith("CA") else "SAVINGS",
                        "product_code": product,
                        "initial_deposit": str(payload.get("initial_deposit") or "100000"),
                    },
                )
                if account_response.status_code == 201:
                    account = account_response.json()
                    await client.post(f"/api/cbs/accounts/{account['id']}/activate")
                    fixtures["account_id"] = account["id"]
        return fixtures

    async def _seed_customer(self, client: httpx.AsyncClient) -> dict:
        """Create a uniquely-identified simulated customer."""
        import random
        import string

        pan = (
            "".join(random.choices(string.ascii_uppercase, k=5))
            + "".join(random.choices(string.digits, k=4))
            + random.choice(string.ascii_uppercase)
        )
        mobile = random.choice("6789") + "".join(random.choices(string.digits, k=9))
        response = await client.post(
            "/api/cbs/customers",
            json={
                "first_name": "Fixture",
                "last_name": "Customer",
                "date_of_birth": "1990-01-01",
                "pan": pan,
                "mobile": mobile,
                "address_line1": "1 Simulation Road",
                "city": "Mumbai",
                "state": "Maharashtra",
                "pincode": "400001",
                "country": "IN",
                "customer_type": "INDIVIDUAL",
            },
        )
        response.raise_for_status()
        return response.json()

    async def _approve_customer(self, client: httpx.AsyncClient, customer_id: str) -> None:
        tasks = (
            await client.get("/api/cbs/maker-checker", params={"status": "PENDING"})
        ).json()
        task = next(
            (
                t
                for t in tasks.get("items", [])
                if t["entity_id"] == customer_id and t["operation"] == "CUSTOMER_CREATE"
            ),
            None,
        )
        if task:
            await client.post(
                f"/api/cbs/maker-checker/{task['id']}/decide",
                json={
                    "decision": "APPROVE",
                    "checker_id": "fixture-checker@sim",
                    "remarks": "Approved by execution fixture provisioning",
                },
            )

    def _resolve_path(
        self, template: str, payload: dict, fixtures: dict
    ) -> tuple[str, list[str]]:
        """Substitute {placeholders}, preferring test data over fixtures."""
        unresolved: list[str] = []
        path = template
        for name in PLACEHOLDER_RE.findall(template):
            value = payload.get(name) or fixtures.get(name)
            if not value:
                unresolved.append(name)
                continue
            path = path.replace(f"{{{name}}}", str(value))
        return path, unresolved

    def _request_body(self, endpoint, payload: dict, fixtures: dict) -> dict:  # noqa: ANN001
        """Keep only fields the endpoint declares, filling from fixtures."""
        allowed = set(endpoint.request_fields)
        path_params = set(PLACEHOLDER_RE.findall(endpoint.path))
        body: dict[str, Any] = {}

        for key, value in payload.items():
            if key in path_params:
                continue  # already substituted into the URL
            if allowed and key not in allowed:
                continue
            body[key] = value

        # An endpoint field that is a known fixture and absent from the body.
        for key in allowed - set(body) - path_params:
            if key in fixtures:
                body[key] = fixtures[key]
        return body

    # ---- driver: local Playwright ----------------------------------------
    async def _execute_playwright_local(
        self, db: Session, execution: TestExecution, test_case: TestCase
    ) -> tuple[list[AssertionOutcome], str]:
        """Drive the CBS simulator UI in a real local browser."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise AgentUnavailable(
                "Playwright is not installed, so browser execution cannot run.",
                remediation="Run: python -m playwright install chromium",
            ) from exc

        route = self._ui_route(test_case)
        url = f"{settings.cbs_local_base_url.rstrip('/')}{route}"
        assertions: list[AssertionOutcome] = []

        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except Exception as exc:
                raise AgentUnavailable(
                    "Could not launch a local Chromium browser.",
                    remediation="Run: python -m playwright install chromium",
                ) from exc
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 900})
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(600)  # let the SPA settle

                status = response.status if response else 0
                assertions.append(
                    AssertionOutcome(
                        name="page_loaded",
                        passed=bool(response and response.status < 400),
                        expected="HTTP < 400",
                        actual=status,
                        detail=f"Navigated to {url}",
                    )
                )
                title = await page.title()
                screenshot = await page.screenshot(full_page=True)
                html = await page.content()
            finally:
                await browser.close()

        self._save_binary_evidence(
            db,
            execution,
            label=f"Screenshot: {route}",
            data=screenshot,
            extension="png",
            content_type="image/png",
            evidence_type=EvidenceType.SCREENSHOT,
        )
        self._save_binary_evidence(
            db,
            execution,
            label=f"HTML snapshot: {route}",
            data=html.encode("utf-8"),
            extension="html",
            content_type="text/html",
            evidence_type=EvidenceType.HTML_SNAPSHOT,
        )
        return assertions, f"Loaded {url} (title={title!r}, HTTP {status})"

    # ---- driver: TinyFish cloud browser ----------------------------------
    async def _execute_tinyfish_browser(
        self,
        db: Session,
        execution: TestExecution,
        test_case: TestCase,
        session_id: str | None,
    ) -> tuple[list[AssertionOutcome], str, str | None]:
        """Drive the CBS UI through a TinyFish-managed cloud browser over CDP."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise AgentUnavailable(
                "Playwright is required to connect to the TinyFish browser over CDP.",
                remediation="Run: python -m playwright install chromium",
            ) from exc

        route = self._ui_route(test_case)
        url = f"{settings.cbs_public_base_url.rstrip('/')}{route}"

        session = await self.tinyfish.create_browser_session(url=url, timeout_seconds=300)
        session_id = session.session_id
        assertions: list[AssertionOutcome] = []

        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(session.cdp_url)
            try:
                # The session may still be completing its startup navigation.
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                if route not in (page.url or ""):
                    await page.goto(url, wait_until="domcontentloaded", timeout=40000)
                await page.wait_for_timeout(1000)

                title = await page.title()
                assertions.append(
                    AssertionOutcome(
                        name="cloud_page_loaded",
                        passed=bool(title is not None),
                        expected="page reachable from the TinyFish cloud browser",
                        actual=f"title={title!r}",
                        detail=f"TinyFish session {session.session_id} navigated to {url}",
                    )
                )
                screenshot = await page.screenshot(full_page=True)
                html = await page.content()
            finally:
                await browser.close()

        self._save_binary_evidence(
            db,
            execution,
            label=f"TinyFish screenshot: {route}",
            data=screenshot,
            extension="png",
            content_type="image/png",
            evidence_type=EvidenceType.SCREENSHOT,
        )
        self._save_binary_evidence(
            db,
            execution,
            label=f"TinyFish HTML snapshot: {route}",
            data=html.encode("utf-8"),
            extension="html",
            content_type="text/html",
            evidence_type=EvidenceType.HTML_SNAPSHOT,
        )
        return (
            assertions,
            f"TinyFish cloud browser loaded {url} (session {session.session_id})",
            session_id,
        )

    def _ui_route(self, test_case: TestCase) -> str:
        """Which simulator screen this test should exercise."""
        rule = test_case.business_rule
        if rule is not None and rule.ui_route:
            return rule.ui_route
        return "/simulator/customers"

    # ---- evidence ---------------------------------------------------------
    def _execution_dir(self, execution: TestExecution) -> Path:
        directory = self.evidence_root / str(execution.run_id) / str(execution.id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _save_evidence(
        self,
        db: Session,
        execution: TestExecution,
        *,
        evidence_type: EvidenceType,
        label: str,
        payload: dict | None = None,
        text_payload: str | None = None,
    ) -> ExecutionEvidence:
        """Store small artefacts inline so they are queryable with the result."""
        serialised = json.dumps(payload, default=str) if payload is not None else (text_payload or "")
        evidence = ExecutionEvidence(
            execution_id=execution.id,
            evidence_type=str(evidence_type),
            label=label[:255],
            payload=redact(payload) if payload is not None else None,
            text_payload=text_payload,
            content_type="application/json" if payload is not None else "text/plain",
            size_bytes=len(serialised.encode("utf-8")),
            sha256=_sha256(serialised.encode("utf-8")),
            captured_at=datetime.now(UTC),
        )
        db.add(evidence)
        db.commit()
        return evidence

    def _save_binary_evidence(
        self,
        db: Session,
        execution: TestExecution,
        *,
        label: str,
        data: bytes,
        extension: str,
        content_type: str,
        evidence_type: EvidenceType,
    ) -> ExecutionEvidence:
        """Write a screenshot or snapshot to disk and record its hash."""
        directory = self._execution_dir(execution)
        filename = f"{uuid.uuid4().hex[:12]}.{extension}"
        (directory / filename).write_bytes(data)
        # Path is stored relative to the evidence root so it stays portable.
        relative = str(Path(str(execution.run_id)) / str(execution.id) / filename).replace(
            "\\", "/"
        )

        evidence = ExecutionEvidence(
            execution_id=execution.id,
            evidence_type=str(evidence_type),
            label=label[:255],
            file_path=relative,
            content_type=content_type,
            size_bytes=len(data),
            sha256=_sha256(data),
            captured_at=datetime.now(UTC),
        )
        db.add(evidence)
        db.commit()
        return evidence


def record_test_approval(
    db: Session,
    test_case_id: uuid.UUID,
    *,
    decision: str,
    reviewer_id: uuid.UUID | None,
    reviewer_email: str,
    notes: str | None = None,
) -> TestApproval:
    """Record a human decision on a test case. Append-only."""
    test_case = db.get(TestCase, test_case_id)
    if test_case is None:
        raise LookupError(f"Test case {test_case_id} not found")

    decision_upper = decision.strip().upper()
    allowed = {
        "APPROVE": ReviewStatus.APPROVED.value,
        "APPROVED": ReviewStatus.APPROVED.value,
        "REJECT": ReviewStatus.REJECTED.value,
        "REJECTED": ReviewStatus.REJECTED.value,
        "NEEDS_CHANGES": ReviewStatus.NEEDS_CHANGES.value,
    }
    if decision_upper not in allowed:
        raise ValueError("decision must be APPROVE, REJECT or NEEDS_CHANGES")

    resolved = allowed[decision_upper]
    approval = TestApproval(
        test_case_id=test_case.id,
        decision=resolved,
        notes=notes,
        decided_by_id=reviewer_id,
        decided_by_email=reviewer_email,
        decided_at=datetime.now(UTC),
    )
    db.add(approval)
    test_case.approval_status = resolved
    db.commit()
    db.refresh(approval)
    return approval
