/**
 * Typed API client.
 *
 * Requests go to a same-origin `/api` path, proxied to the backend in dev (see
 * vite.config.ts), so there is no CORS preflight and the SSE stream is treated
 * as first-party.
 */
import type {
  AgentActivity,
  Capabilities,
  CbsAccount,
  CbsAuditEvent,
  CbsCustomer,
  CbsKycView,
  CbsMakerCheckerTask,
  CbsReference,
  CbsTransaction,
  DashboardSummary,
  DiscoveryRun,
  DocumentComparison,
  DocumentVersion,
  ExecutionRun,
  ExecutionRunDetail,
  ExternalHealth,
  ImpactMapping,
  Job,
  Paginated,
  RegulatoryChange,
  RegulatoryDocument,
  RegulatoryDocumentDetail,
  TestCase,
  Traceability,
} from "./types";

const BASE = "/api";

/** An API error carrying the backend's structured detail, including remediation. */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  remediation?: string;

  constructor(status: number, message: string, detail: unknown, remediation?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.remediation = remediation;
  }
}

function buildQuery(params?: Record<string, unknown>): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = await response.text().catch(() => null);
    }
    // FastAPI nests our agent errors under `detail`; surface the useful parts.
    const detailObject =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : body;
    const message =
      (detailObject && typeof detailObject === "object" && "message" in detailObject
        ? String((detailObject as { message: unknown }).message)
        : undefined) ??
      (typeof detailObject === "string" ? detailObject : undefined) ??
      (body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : undefined) ??
      `Request failed with HTTP ${response.status}`;
    const remediation =
      detailObject && typeof detailObject === "object" && "remediation" in detailObject
        ? (String((detailObject as { remediation: unknown }).remediation) || undefined)
        : body && typeof body === "object" && "remediation" in body
          ? String((body as { remediation: unknown }).remediation)
          : undefined;

    throw new ApiError(response.status, message, detailObject, remediation);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const get = <T>(path: string, params?: Record<string, unknown>) =>
  request<T>(`${path}${buildQuery(params)}`);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const patch = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  /* --- system --- */
  health: () => get<{ status: string; database: { ok: boolean; dialect: string } }>("/health"),
  capabilities: () => get<Capabilities>("/system/capabilities"),
  externalHealth: () => get<ExternalHealth>("/system/health/external"),

  /* --- dashboard --- */
  dashboard: () => get<DashboardSummary>("/dashboard/summary"),
  activity: (params?: { agent_name?: string; status?: string; limit?: number }) =>
    get<AgentActivity[]>("/activity", params),

  /* --- discovery (Agent 1) --- */
  startDiscovery: (body: {
    queries?: string[];
    institution_type?: string;
    recency_minutes?: number;
    max_results_per_query?: number;
  }) => post<Job>("/discovery/start", body),
  discoveryRuns: (limit = 20) => get<DiscoveryRun[]>("/discovery", { limit }),
  discoveryStatus: (id: string) =>
    get<{ run: DiscoveryRun; documents: RegulatoryDocument[] }>(`/discovery/${id}/status`),
  job: (id: string) => get<Job>(`/jobs/${id}`),
  jobs: (limit = 25) => get<Job[]>("/jobs", { limit }),

  /* --- regulations (Agent 2) --- */
  regulations: (params?: {
    q?: string;
    status?: string;
    document_type?: string;
    banking_domain?: string;
    current_only?: boolean;
    limit?: number;
    offset?: number;
  }) => get<Paginated<RegulatoryDocument>>("/regulations", params),
  regulation: (id: string) => get<RegulatoryDocumentDetail>(`/regulations/${id}`),
  regulationVersions: (id: string) => get<DocumentVersion[]>(`/regulations/${id}/versions`),
  compareRegulation: (id: string, against?: string) =>
    get<DocumentComparison>(`/regulations/${id}/compare`, { against }),
  extractDocuments: (document_ids: string[], force_refetch = false) =>
    post<Job>("/regulations/extract", { document_ids, force_refetch }),

  /* --- analysis (Agent 3) --- */
  analyseDocument: (
    id: string,
    body?: { compare_to_document_id?: string; use_pro_model?: boolean },
  ) => post<Job>(`/analysis/${id}`, body ?? {}),
  changes: (params?: {
    document_id?: string;
    banking_module?: string;
    assertion_basis?: string;
    change_type?: string;
    priority?: string;
    review_status?: string;
    limit?: number;
    offset?: number;
  }) => get<Paginated<RegulatoryChange>>("/changes", params),
  change: (id: string) => get<RegulatoryChange>(`/changes/${id}`),
  reviewChange: (id: string, decision: string, notes?: string) =>
    patch<RegulatoryChange>(`/changes/${id}/review`, { decision, notes }),

  /* --- impact (Agent 4) --- */
  analyseImpact: (change_ids: string[], candidate_limit = 8) =>
    post<Job>("/impact/analyze", { change_ids, candidate_limit }),
  impacts: (params?: {
    change_id?: string;
    rule_code?: string;
    impact_category?: string;
    banking_module?: string;
    priority?: string;
    review_status?: string;
    limit?: number;
    offset?: number;
  }) => get<Paginated<ImpactMapping>>("/impact", params),
  reviewImpact: (id: string, decision: string, notes?: string) =>
    patch<ImpactMapping>(`/impact/${id}/review`, { decision, notes }),
  businessRules: () => get<import("./types").BusinessRule[]>("/business-rules"),

  /* --- test cases (Agent 5) --- */
  generateTests: (impact_mapping_ids: string[], tests_per_category = 1) =>
    post<Job>("/test-cases/generate", { impact_mapping_ids, tests_per_category }),
  testCases: (params?: {
    impact_mapping_id?: string;
    change_id?: string;
    requirement_code?: string;
    banking_module?: string;
    category?: string;
    approval_status?: string;
    priority?: string;
    limit?: number;
    offset?: number;
  }) => get<Paginated<TestCase>>("/test-cases", params),
  testCase: (id: string) => get<TestCase>(`/test-cases/${id}`),
  approveTestCase: (id: string, decision: string, notes?: string) =>
    patch<{ id: string; decision: string }>(`/test-cases/${id}/approve`, { decision, notes }),
  traceability: (id: string) => get<Traceability>(`/test-cases/${id}/traceability`),

  /* --- executions (Agent 6) --- */
  startExecution: (test_case_ids: string[], driver = "PYTEST_API") =>
    post<Job>("/executions/start", { test_case_ids, driver }),
  executionRuns: (limit = 25) => get<Paginated<ExecutionRun>>("/executions", { limit }),
  executionRun: (id: string) => get<ExecutionRunDetail>(`/executions/${id}`),
  evidenceFileUrl: (evidenceId: string) => `${BASE}/evidence/${evidenceId}/file`,

  /* --- core banking simulator --- */
  cbs: {
    reference: () => get<CbsReference>("/cbs/reference"),
    customers: (params?: {
      q?: string;
      kyc_status?: string;
      status?: string;
      risk_category?: string;
      limit?: number;
      offset?: number;
    }) => get<Paginated<CbsCustomer>>("/cbs/customers", params),
    customer: (id: string) => get<CbsCustomer>(`/cbs/customers/${id}`),
    createCustomer: (body: Record<string, unknown>) => post<CbsCustomer>("/cbs/customers", body),
    kyc: (customerId: string) => get<CbsKycView>(`/cbs/customers/${customerId}/kyc`),
    verifyKyc: (customerId: string, body: Record<string, unknown>) =>
      post<unknown>(`/cbs/customers/${customerId}/kyc/verify`, body),
    updateKyc: (customerId: string, body: Record<string, unknown>) =>
      post<unknown>(`/cbs/customers/${customerId}/kyc/update`, body),
    classifyRisk: (customerId: string) =>
      post<{
        risk_category: string;
        reasons: string[];
        requires_edd: boolean;
        periodicity_years: number;
        next_kyc_due_date: string | null;
      }>(`/cbs/customers/${customerId}/risk-classification`),
    accounts: (params?: { customer_id?: string; status?: string; limit?: number }) =>
      get<Paginated<CbsAccount>>("/cbs/accounts", params),
    openAccount: (body: Record<string, unknown>) => post<CbsAccount>("/cbs/accounts", body),
    accountStatus: (id: string) =>
      get<{
        account_number: string;
        balance: string;
        debit_allowed: boolean;
        credit_allowed: boolean;
        restrictions: string[];
        blocking_restrictions: string[];
      }>(`/cbs/accounts/${id}/status`),
    applyRestrictions: (id: string, body: { add?: string[]; remove?: string[]; reason?: string }) =>
      post<CbsAccount>(`/cbs/accounts/${id}/restrictions`, body),
    activateAccount: (id: string) => post<CbsAccount>(`/cbs/accounts/${id}/activate`),
    transactions: (params?: {
      account_id?: string;
      status?: string;
      aml_flagged?: boolean;
      limit?: number;
    }) => get<Paginated<CbsTransaction>>("/cbs/transactions", params),
    createTransaction: (body: Record<string, unknown>) =>
      post<CbsTransaction>("/cbs/transactions", body),
    makerChecker: (status = "PENDING") =>
      get<Paginated<CbsMakerCheckerTask>>("/cbs/maker-checker", { status }),
    decide: (taskId: string, body: { decision: string; checker_id: string; remarks?: string }) =>
      post<CbsMakerCheckerTask>(`/cbs/maker-checker/${taskId}/decide`, body),
    audit: (params?: { entity_id?: string; rule_code?: string; limit?: number }) =>
      get<Paginated<CbsAuditEvent>>("/cbs/audit", params),
  },
};

export const SSE_URL = `${BASE}/events/stream`;
