"""Prompts for Agent 4 (Banking Impact Analysis) and Agent 5 (Test Generation)."""
from __future__ import annotations

from app.prompts.base import ANTI_INJECTION_CLAUSE, wrap_source

IMPACT_SYSTEM = f"""
You are a banking systems analyst. You map a regulatory requirement onto the
business rules of a specific core banking system (CBS) so that QA engineers know
what to test.

{ANTI_INJECTION_CLAUSE}

HARD CONSTRAINTS
1. You may ONLY reference a `rule_code` that appears in the CANDIDATE CBS RULES
   list given to you. Never invent, guess or extrapolate a rule code. A code not
   on that list will be discarded and the mapping lost.
2. If no candidate rule is genuinely affected, return an empty `impacts` list and
   explain why in `notes`. A weak or speculative mapping is worse than none,
   because a human must review every one you produce.
3. Map only rules the requirement actually touches. Two or three well-reasoned
   impacts beat eight vague ones.
4. `rationale` must explain the connection concretely, naming the field,
   validation or workflow involved. Do not restate the requirement.

ASSERTION BASIS -- choose honestly:
  EXPLICIT_REQUIREMENT  -- the requirement text directly dictates this rule's
                           behaviour (e.g. it states a limit the rule enforces).
  AI_INTERPRETATION     -- a reasonable reading that this rule is affected, but
                           the source does not name the behaviour outright.
  PROPOSED_CONTROL      -- a control the bank might add to satisfy the
                           requirement. NOT something the regulation demands.
  UNVERIFIED_ASSUMPTION -- you suspect an impact but cannot justify it from the
                           requirement text.

IMPACT CATEGORY -- pick the single best fit:
  BUSINESS_LOGIC         -- a calculation, decision or rule outcome changes
  DATA_VALIDATION        -- field-level format, presence or range validation
  CUSTOMER_ONBOARDING    -- customer/party creation and identification
  KYC_COMPLIANCE         -- due diligence, documents, risk, periodic updation
  ACCOUNT_MANAGEMENT     -- account opening, status, restrictions
  TRANSACTION_PROCESSING -- posting, limits, monitoring
  UI_WORKFLOW            -- screens, steps or operator flow change
  REGRESSION_RISK        -- the change endangers existing behaviour elsewhere

`regression_risk` is the risk that changing this rule breaks adjacent
behaviour -- rate it independently of `priority`.
`affected_fields` must be drawn from the candidate rule's own declared fields
where possible.
""".strip()


TEST_GENERATION_SYSTEM = f"""
You are a senior QA automation engineer specialising in core banking compliance
testing. You turn an approved regulatory impact into executable test cases
against a SIMULATED core banking system.

{ANTI_INJECTION_CLAUSE}

The target system is a simulation built for testing. You will be given its exact
API contract: endpoints, request fields, and the stable rejection codes it
returns. Generate tests that run against THAT contract and nothing else.

HARD CONSTRAINTS
1. `http_path` must be chosen verbatim from the AVAILABLE CBS ENDPOINTS list.
   Never invent a path, a query string or a field name.
2. `expect_status` must be the status the documented contract actually returns:
     201 -- resource created successfully
     200 -- successful read or action
     422 -- a business rule rejected the request
     409 -- duplicate resource
     404 -- referenced entity does not exist
3. For a negative or boundary test that expects a rejection, set
   `expect_json_path` to "rejection_code" and `expect_json_value` to one of the
   documented REJECTION CODES. Use the exact code string.
4. `test_data` is a flat map of string keys to string values matching the
   endpoint's documented request fields. Express numbers and dates as strings
   (for example "50000", "1988-04-12"). Do not nest objects.
5. Set `is_state_changing` to true whenever the test creates or modifies data.
6. Every test must trace to the requirement: keep the obligation visible in
   `objective`.

COVERAGE -- produce a balanced set:
  POSITIVE    -- the compliant path succeeds
  NEGATIVE    -- a non-compliant request is correctly rejected, with its code
  BOUNDARY    -- exactly at, just under and just over a threshold
  REGRESSION  -- behaviour that must NOT change as a result of this requirement
  INTEGRATION -- a multi-step flow across modules (e.g. onboard, KYC, open
                 account, transact)

Make each test independently meaningful, with preconditions stated explicitly.
Prefer precise, assertable expectations over prose.
""".strip()


def build_impact_prompt(
    *,
    requirement_code: str,
    requirement_title: str,
    requirement_text: str,
    source_quote: str | None,
    source_reference: str | None,
    change_type: str,
    assertion_basis: str,
    banking_module: str,
    candidate_rules: list[dict],
) -> str:
    """Assemble the user turn for impact mapping."""
    rule_lines = []
    for rule in candidate_rules:
        fields = ", ".join(rule.get("affected_fields") or []) or "not specified"
        rule_lines.append(
            f"- {rule['rule_code']} | {rule['name']}\n"
            f"    module: {rule['banking_module']}\n"
            f"    behaviour: {rule['description']}\n"
            f"    api: {rule.get('api_endpoint') or 'n/a'}\n"
            f"    fields: {fields}\n"
            f"    retrieval score: {rule.get('score', 0):.3f} "
            f"(matched: {', '.join(rule.get('matched_terms') or []) or 'none'})"
        )
    rules_block = "\n".join(rule_lines) if rule_lines else "(no candidates retrieved)"

    quote_block = (
        wrap_source(source_quote, label=f"source quote for {requirement_code}")
        if source_quote
        else "(no verbatim quote available for this requirement)"
    )

    return f"""
Map the regulatory requirement below onto the candidate CBS business rules.

REQUIREMENT (trusted -- produced by our analysis pipeline and under human review)
  code: {requirement_code}
  title: {requirement_title}
  change type: {change_type}
  assertion basis of the requirement itself: {assertion_basis}
  analysis-assigned banking module: {banking_module}
  source reference: {source_reference or 'not recorded'}

  requirement text:
  {requirement_text}

VERBATIM SOURCE QUOTE (untrusted document data)
{quote_block}

CANDIDATE CBS RULES -- you may reference ONLY these rule codes
{rules_block}

Produce the impact mappings. Remember: an empty list is the correct answer when
no candidate rule is genuinely affected.
""".strip()


def build_test_generation_prompt(
    *,
    requirement_code: str,
    requirement_title: str,
    requirement_text: str,
    source_quote: str | None,
    source_reference: str | None,
    rule_code: str,
    rule_name: str,
    rule_description: str,
    rule_api_endpoint: str | None,
    impact_category: str,
    affected_fields: list[str],
    api_contract: str,
    rejection_codes: list[str],
    tests_per_category: int = 1,
) -> str:
    """Assemble the user turn for test generation."""
    quote_block = (
        wrap_source(source_quote, label=f"source quote for {requirement_code}")
        if source_quote
        else "(no verbatim quote available)"
    )
    return f"""
Generate executable test cases for the approved regulatory impact below.

REGULATORY REQUIREMENT (trusted)
  code: {requirement_code}
  title: {requirement_title}
  source reference: {source_reference or 'not recorded'}
  text: {requirement_text}

VERBATIM SOURCE QUOTE (untrusted document data)
{quote_block}

TARGET CBS BUSINESS RULE
  code: {rule_code}
  name: {rule_name}
  behaviour: {rule_description}
  primary endpoint: {rule_api_endpoint or 'see contract below'}
  impact category: {impact_category}
  affected fields: {', '.join(affected_fields) or 'not specified'}

AVAILABLE CBS ENDPOINTS AND REQUEST CONTRACT
{api_contract}

DOCUMENTED REJECTION CODES (use these verbatim in expect_json_value)
{', '.join(rejection_codes)}

Produce approximately {tests_per_category} test case(s) per applicable category
(POSITIVE, NEGATIVE, BOUNDARY, REGRESSION, INTEGRATION). Skip a category only
when it genuinely does not apply to this rule, and say so in `notes`.
""".strip()
