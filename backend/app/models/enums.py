"""Status enumerations shared by models and API schemas.

Stored as strings (native_enum=False) so that adding a value does not require a
Postgres type migration -- important for an evolving regulatory pipeline.
"""
from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    REVIEWER = "REVIEWER"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"


class DiscoveryStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class DocumentStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    FETCHING = "FETCHING"
    EXTRACTED = "EXTRACTED"
    ANALYSED = "ANALYSED"
    FETCH_FAILED = "FETCH_FAILED"
    INACCESSIBLE = "INACCESSIBLE"


class BankingModule(StrEnum):
    CUSTOMER_ONBOARDING = "CUSTOMER_ONBOARDING"
    KYC_AML = "KYC_AML"
    ACCOUNT_MANAGEMENT = "ACCOUNT_MANAGEMENT"
    TRANSACTIONS = "TRANSACTIONS"
    CROSS_CUTTING = "CROSS_CUTTING"


class ChangeType(StrEnum):
    NEW_REQUIREMENT = "NEW_REQUIREMENT"
    AMENDED_REQUIREMENT = "AMENDED_REQUIREMENT"
    REMOVED_REQUIREMENT = "REMOVED_REQUIREMENT"
    CLARIFICATION = "CLARIFICATION"
    DEADLINE_CHANGE = "DEADLINE_CHANGE"


class AssertionBasis(StrEnum):
    """Provenance of a statement -- the project must never blur these.

    EXPLICIT_REQUIREMENT : quoted verbatim from the regulatory source.
    AI_INTERPRETATION    : model-derived reading of the source text.
    PROPOSED_CONTROL     : a suggested banking control, not a regulatory demand.
    UNVERIFIED_ASSUMPTION: stated without a locatable source reference.
    """

    EXPLICIT_REQUIREMENT = "EXPLICIT_REQUIREMENT"
    AI_INTERPRETATION = "AI_INTERPRETATION"
    PROPOSED_CONTROL = "PROPOSED_CONTROL"
    UNVERIFIED_ASSUMPTION = "UNVERIFIED_ASSUMPTION"


class ImpactCategory(StrEnum):
    BUSINESS_LOGIC = "BUSINESS_LOGIC"
    DATA_VALIDATION = "DATA_VALIDATION"
    CUSTOMER_ONBOARDING = "CUSTOMER_ONBOARDING"
    KYC_COMPLIANCE = "KYC_COMPLIANCE"
    ACCOUNT_MANAGEMENT = "ACCOUNT_MANAGEMENT"
    TRANSACTION_PROCESSING = "TRANSACTION_PROCESSING"
    UI_WORKFLOW = "UI_WORKFLOW"
    REGRESSION_RISK = "REGRESSION_RISK"


class Priority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ReviewStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_CHANGES = "NEEDS_CHANGES"


class TestCategory(StrEnum):
    # Not a pytest test class; see note in app/models/testing.py.
    __test__ = False

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    BOUNDARY = "BOUNDARY"
    REGRESSION = "REGRESSION"
    INTEGRATION = "INTEGRATION"


class AutomationType(StrEnum):
    API = "API"
    BROWSER = "BROWSER"
    MANUAL = "MANUAL"


class ExecutionStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class EvidenceType(StrEnum):
    SCREENSHOT = "SCREENSHOT"
    LOG = "LOG"
    API_RESPONSE = "API_RESPONSE"
    HTML_SNAPSHOT = "HTML_SNAPSHOT"
    DIFF = "DIFF"


class ExecutionDriver(StrEnum):
    PYTEST_API = "PYTEST_API"
    PLAYWRIGHT_LOCAL = "PLAYWRIGHT_LOCAL"
    TINYFISH_BROWSER = "TINYFISH_BROWSER"


class AgentName(StrEnum):
    DISCOVERY = "REGULATORY_DISCOVERY"
    EXTRACTION = "REGULATORY_EXTRACTION"
    ANALYSIS = "REGULATORY_ANALYSIS"
    IMPACT = "BANKING_IMPACT_ANALYSIS"
    TEST_GENERATION = "TEST_GENERATION"
    TEST_EXECUTION = "TEST_EXECUTION"


class ActivityStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class DataProvenance(StrEnum):
    """How a stored record came to exist -- surfaced in the UI verbatim."""

    LIVE_TINYFISH = "LIVE_TINYFISH"
    LIVE_GEMINI = "LIVE_GEMINI"
    DETERMINISTIC = "DETERMINISTIC"
    DEMO_FIXTURE = "DEMO_FIXTURE"
