/** Types mirroring the backend Pydantic schemas (app/schemas/pipeline.py). */

export type ReviewStatus = "PENDING_REVIEW" | "APPROVED" | "REJECTED" | "NEEDS_CHANGES";
export type Priority = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type ExecutionStatusValue =
  | "QUEUED"
  | "RUNNING"
  | "PASSED"
  | "FAILED"
  | "BLOCKED"
  | "ERROR"
  | "SKIPPED";

/**
 * Provenance of a statement. The UI must render these distinctly: an AI
 * interpretation is never shown as if the regulator said it.
 */
export type AssertionBasis =
  | "EXPLICIT_REQUIREMENT"
  | "AI_INTERPRETATION"
  | "PROPOSED_CONTROL"
  | "UNVERIFIED_ASSUMPTION";

export type DataProvenance = "LIVE_TINYFISH" | "LIVE_GEMINI" | "DETERMINISTIC" | "DEMO_FIXTURE";

export type ExecutionDriver = "PYTEST_API" | "PLAYWRIGHT_LOCAL" | "TINYFISH_BROWSER";

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Job {
  job_id: string;
  kind: string;
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
  detail: string | null;
  result: Record<string, unknown> | null;
  correlation_id: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface DiscoveryRun {
  id: string;
  status: string;
  regulatory_authority: string;
  institution_type: string;
  queries: string[] | null;
  search_domains: string[] | null;
  documents_found: number;
  documents_new: number;
  documents_duplicate: number;
  provenance: DataProvenance;
  tinyfish_request_ids: string[] | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface RegulatoryDocument {
  id: string;
  title: string;
  circular_number: string | null;
  document_type: string;
  source_url: string;
  publication_date: string | null;
  effective_date: string | null;
  banking_domains: string[] | null;
  applicable_institution_types: string[] | null;
  version: number;
  is_current: boolean;
  status: string;
  provenance: DataProvenance;
  access_limitation: string | null;
  char_count: number | null;
  content_hash: string | null;
  fetched_at: string | null;
  created_at: string;
}

export interface RegulatoryClause {
  id: string;
  clause_reference: string;
  heading: string | null;
  text: string;
  ordinal: number;
  clause_hash: string;
  provenance: DataProvenance;
}

export interface RegulatoryDocumentDetail extends RegulatoryDocument {
  raw_text: string | null;
  applicability_text: string | null;
  content_format: string;
  search_snippet: string | null;
  supersedes_id: string | null;
  clauses: RegulatoryClause[];
}

export interface DocumentVersion {
  id: string;
  version: number;
  is_current: boolean;
  content_hash: string | null;
  char_count: number | null;
  publication_date: string | null;
  effective_date: string | null;
  fetched_at: string | null;
  created_at: string;
}

export interface ClauseDiffEntry {
  change: "ADDED" | "REMOVED" | "MODIFIED" | "UNCHANGED";
  clause_reference: string;
  heading: string | null;
  previous_text: string | null;
  current_text: string | null;
  similarity: number | null;
}

export interface DocumentComparison {
  previous: DocumentVersion;
  current: DocumentVersion;
  added: number;
  removed: number;
  modified: number;
  unchanged: number;
  entries: ClauseDiffEntry[];
}

export interface RegulatoryChange {
  id: string;
  document_id: string;
  clause_id: string | null;
  compared_to_document_id: string | null;
  requirement_code: string;
  title: string;
  requirement_text: string;
  change_type: string;
  assertion_basis: AssertionBasis;
  source_quote: string | null;
  source_reference: string | null;
  previous_text: string | null;
  applicable_entities: string[] | null;
  primary_banking_module: string;
  effective_date: string | null;
  priority: Priority;
  confidence: number | null;
  review_status: ReviewStatus;
  review_notes: string | null;
  reviewed_at: string | null;
  model_name: string | null;
  provenance: DataProvenance;
  created_at: string;
}

export interface BusinessRule {
  id: string;
  rule_code: string;
  name: string;
  description: string;
  banking_module: string;
  api_endpoint: string | null;
  ui_route: string | null;
  affected_fields: string[] | null;
  is_active: boolean;
}

export interface ImpactMapping {
  id: string;
  change_id: string;
  business_rule_id: string;
  impact_category: string;
  banking_module: string;
  priority: Priority;
  rationale: string;
  assertion_basis: AssertionBasis;
  affected_fields: string[] | null;
  affected_workflows: string[] | null;
  regression_risk: Priority;
  confidence: number | null;
  retrieval_score: number | null;
  review_status: ReviewStatus;
  review_notes: string | null;
  reviewed_at: string | null;
  model_name: string | null;
  provenance: DataProvenance;
  created_at: string;
  business_rule: BusinessRule | null;
  change: RegulatoryChange | null;
}

export interface TestStep {
  step: number;
  action: string;
  expected?: string | null;
}

export interface TestCase {
  id: string;
  test_code: string;
  impact_mapping_id: string | null;
  change_id: string | null;
  business_rule_id: string | null;
  title: string;
  objective: string | null;
  banking_module: string;
  category: "POSITIVE" | "NEGATIVE" | "BOUNDARY" | "REGRESSION" | "INTEGRATION";
  priority: Priority;
  automation_type: "API" | "BROWSER" | "MANUAL";
  preconditions: string[] | null;
  test_data: Record<string, unknown> | null;
  steps: TestStep[] | null;
  expected_result: string;
  execution_spec: Record<string, unknown> | null;
  is_state_changing: boolean;
  source_clause_reference: string | null;
  source_quote: string | null;
  requirement_code: string | null;
  approval_status: ReviewStatus;
  model_name: string | null;
  provenance: DataProvenance;
  created_at: string;
}

export interface Evidence {
  id: string;
  evidence_type: "SCREENSHOT" | "LOG" | "API_RESPONSE" | "HTML_SNAPSHOT" | "DIFF";
  label: string;
  file_path: string | null;
  content_type: string | null;
  size_bytes: number | null;
  payload: Record<string, unknown> | null;
  text_payload: string | null;
  sha256: string | null;
  captured_at: string | null;
}

export interface AssertionResult {
  name: string;
  passed: boolean;
  expected: unknown;
  actual: unknown;
  detail?: string;
}

export interface TestExecution {
  id: string;
  run_id: string;
  test_case_id: string;
  status: ExecutionStatusValue;
  driver: ExecutionDriver;
  expected_result: string | null;
  actual_result: string | null;
  assertions: AssertionResult[] | null;
  failure_reason: string | null;
  duration_ms: number | null;
  approved_by_email: string | null;
  started_at: string | null;
  finished_at: string | null;
  test_case: TestCase | null;
  evidence: Evidence[];
}

export interface ExecutionRun {
  id: string;
  status: ExecutionStatusValue;
  driver: ExecutionDriver;
  target_base_url: string | null;
  total: number;
  passed: number;
  failed: number;
  blocked: number;
  errored: number;
  tinyfish_session_id: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface ExecutionRunDetail extends ExecutionRun {
  executions: TestExecution[];
}

export interface TraceabilityNode {
  id: string | null;
  label: string;
  detail: string | null;
  status: string | null;
  extra: Record<string, unknown> | null;
}

export interface Traceability {
  test_case: TestCase;
  document: RegulatoryDocument | null;
  clause: RegulatoryClause | null;
  change: RegulatoryChange | null;
  impact_mapping: ImpactMapping | null;
  business_rule: BusinessRule | null;
  executions: TestExecution[];
  chain: TraceabilityNode[];
}

export interface AgentActivity {
  id: string;
  agent_name: string;
  operation: string;
  status: "STARTED" | "SUCCEEDED" | "FAILED" | "SKIPPED";
  message: string | null;
  duration_ms: number | null;
  external_service: string | null;
  external_request_id: string | null;
  correlation_id: string | null;
  entity_type: string | null;
  entity_id: string | null;
  created_at: string;
}

export interface DashboardSummary {
  counts: {
    regulatory_documents: number;
    current_documents: number;
    documents_inaccessible: number;
    regulatory_changes: number;
    changes_pending_review: number;
    explicit_requirements: number;
    affected_cbs_modules: number;
    impact_mappings: number;
    impacts_pending_review: number;
    business_rules: number;
    test_cases: number;
    tests_pending_approval: number;
    tests_approved: number;
    executions_total: number;
    executions_passed: number;
    executions_failed: number;
    executions_blocked: number;
  };
  timeline: { period: string; documents: number; changes: number }[];
  risk_distribution: { label: string; count: number }[];
  module_distribution: { label: string; count: number }[];
  impact_category_distribution: { label: string; count: number }[];
  test_category_distribution: { label: string; count: number }[];
  assertion_basis_distribution: { label: string; count: number }[];
  recent_activity: AgentActivity[];
  latest_execution_run: ExecutionRun | null;
  capabilities_configured: Record<string, boolean>;
}

export interface IntegrationStatus {
  configured: boolean;
  endpoint?: string;
  model?: string;
  required_for: string;
}

export interface Capabilities {
  regulatory_authority: string;
  institution_type: string;
  institution_types_available: string[];
  regulatory_domains: string[];
  database: {
    dialect: string;
    configured: boolean;
    business_rules: number;
    regulatory_documents: number;
  };
  integrations: Record<string, IntegrationStatus>;
  execution_drivers: Record<string, { available: boolean; detail: string }>;
  safety: { require_approval_for_execution: boolean; simulated_cbs_only: boolean };
}

export interface ExternalHealth {
  tinyfish: { configured: boolean; reachable: boolean; detail: string; request_id?: string };
  gemini: { configured: boolean; reachable: boolean; detail: string };
}

/* --- Core banking simulator --- */
export interface CbsCustomer {
  id: string;
  ucic: string;
  first_name: string;
  last_name: string;
  date_of_birth: string | null;
  pan: string | null;
  aadhaar_last4: string | null;
  mobile: string | null;
  email: string | null;
  city: string | null;
  state: string | null;
  pincode: string | null;
  country: string;
  customer_type: string;
  customer_segment: string;
  risk_category: "LOW" | "MEDIUM" | "HIGH";
  is_pep: boolean;
  kyc_status: string;
  kyc_verified_at: string | null;
  next_kyc_due_date: string | null;
  status: string;
  is_test_data: boolean;
  created_at: string;
}

export interface CbsKycRecord {
  id: string;
  version: number;
  kyc_type: string;
  status: string;
  verified_document_count: number;
  documents: Record<string, unknown>[];
  verification_mode: string | null;
  remarks: string | null;
  verified_at: string | null;
  due_date: string | null;
  created_at: string | null;
}

export interface CbsKycView {
  customer_id: string;
  ucic: string;
  kyc_status: string;
  risk_category: string;
  kyc_verified_at: string | null;
  next_kyc_due_date: string | null;
  kyc_due_now: boolean;
  periodicity_years: number;
  requires_edd: boolean;
  records: CbsKycRecord[];
}

export interface CbsAccount {
  id: string;
  account_number: string;
  customer_id: string;
  account_type: string;
  product_code: string;
  currency: string;
  balance: string;
  status: string;
  restrictions: string[] | null;
  daily_debit_limit: string;
  per_txn_limit: string;
  minimum_balance: string;
  opened_on: string | null;
  branch_code: string;
  created_at: string;
}

export interface CbsTransaction {
  id: string;
  reference_number: string;
  account_id: string;
  txn_type: string;
  channel: string;
  amount: string;
  currency: string;
  fee: string;
  balance_after: string | null;
  narration: string | null;
  status: string;
  rejection_code: string | null;
  rejection_reason: string | null;
  aml_flagged: boolean;
  aml_reason: string | null;
  value_date: string | null;
  created_at: string;
}

export interface CbsMakerCheckerTask {
  id: string;
  operation: string;
  entity_type: string;
  entity_id: string | null;
  payload: Record<string, unknown> | null;
  status: string;
  maker_id: string;
  checker_id: string | null;
  maker_remarks: string | null;
  checker_remarks: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface CbsAuditEvent {
  id: string;
  entity_type: string;
  entity_id: string | null;
  action: string;
  actor: string;
  rule_code: string | null;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  message: string | null;
  created_at: string;
}

export interface CbsReference {
  accepted_ovd_types: string[];
  kyc_periodicity_years: Record<string, number>;
  products: Record<string, Record<string, string>>;
  restriction_codes: string[];
  notice: string;
}

/** SSE payload shape emitted by app/core/events.py. */
export interface PipelineEvent {
  id: string;
  type: string;
  correlation_id: string | null;
  created_at: string;
  payload: Record<string, unknown>;
}
