/** Screen 4 -- Test Management: generated scenarios, approval and traceability. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Check,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Play,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";
import { useState } from "react";

import {
  AssertionBadge,
  Badge,
  Card,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  PriorityBadge,
  ProvenanceTag,
  ReviewBadge,
  SectionLabel,
  SimulationNotice,
  SourceQuote,
  Spinner,
} from "../components/ui";
import { api } from "../lib/api";
import type { TestCase, TraceabilityNode } from "../lib/types";

const CATEGORY_TONE: Record<string, "pass" | "fail" | "warn" | "info" | "blocked"> = {
  POSITIVE: "pass",
  NEGATIVE: "fail",
  BOUNDARY: "warn",
  REGRESSION: "blocked",
  INTEGRATION: "info",
};

export default function TestManagement() {
  const queryClient = useQueryClient();
  const [categoryFilter, setCategoryFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [moduleFilter, setModuleFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [traceFor, setTraceFor] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [driver, setDriver] = useState("PYTEST_API");

  const tests = useQuery({
    queryKey: ["testCases", { categoryFilter, statusFilter, moduleFilter }],
    queryFn: () =>
      api.testCases({
        category: categoryFilter || undefined,
        approval_status: statusFilter || undefined,
        banking_module: moduleFilter || undefined,
        limit: 200,
      }),
  });

  const approve = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: string }) =>
      api.approveTestCase(id, decision),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["testCases"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const execute = useMutation({
    mutationFn: (ids: string[]) => api.startExecution(ids, driver),
    onSuccess: () => {
      setSelected(new Set());
      queryClient.invalidateQueries({ queryKey: ["executionRuns"] });
    },
  });

  const items = tests.data?.items ?? [];
  const selectedCases = items.filter((test) => selected.has(test.id));
  // A state-changing test needs an approval record; a read-only one does not.
  const blockedSelection = selectedCases.filter(
    (test) => test.is_state_changing && test.approval_status !== "APPROVED",
  );

  const toggle = (id: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const counts = {
    total: items.length,
    approved: items.filter((t) => t.approval_status === "APPROVED").length,
    pending: items.filter((t) => t.approval_status === "PENDING_REVIEW").length,
  };

  return (
    <>
      <PageHeader
        icon={ShieldCheck}
        title="Test Management"
        subtitle="Scenarios generated from approved impact mappings, validated against the CBS API contract. Approval here is what authorises execution."
        actions={
          <>
            <select
              className="input w-44"
              value={driver}
              onChange={(event) => setDriver(event.target.value)}
              title="Execution driver"
            >
              <option value="PYTEST_API">API driver (in-process)</option>
              <option value="PLAYWRIGHT_LOCAL">Playwright (local browser)</option>
              <option value="TINYFISH_BROWSER">TinyFish cloud browser</option>
            </select>
            <button
              type="button"
              className="btn-primary"
              onClick={() => execute.mutate(Array.from(selected))}
              disabled={execute.isPending || selected.size === 0}
            >
              {execute.isPending ? <Spinner /> : <Play size={15} />}
              Execute {selected.size || ""} test{selected.size === 1 ? "" : "s"}
            </button>
          </>
        }
      />

      {(approve.isError || execute.isError) && (
        <div className="mb-5 space-y-3">
          {approve.isError && <ErrorState error={approve.error} />}
          {execute.isError && <ErrorState error={execute.error} />}
        </div>
      )}

      {blockedSelection.length > 0 && (
        <div className="mb-5 rounded-lg border border-violet-500/30 bg-violet-500/[.07] px-4 py-3 text-[12.5px] leading-relaxed text-slate-300">
          <p className="flex items-center gap-2 font-medium text-violet-200">
            <ShieldAlert size={14} />
            {blockedSelection.length} selected test(s) will be blocked, not executed
          </p>
          <p className="mt-1 text-slate-400">
            They modify data in the simulated core banking system and have no approval record
            yet. Approve them first, or run them anyway to see the block recorded as evidence —
            either way nothing is executed without authorisation.
          </p>
        </div>
      )}

      <div className="mb-5 grid grid-cols-3 gap-3">
        <Card bodyClassName="p-4">
          <p className="text-[11px] font-semibold uppercase tracking-[.09em] text-slate-500">
            Test cases
          </p>
          <p className="mt-1.5 text-2xl font-semibold text-slate-100">{counts.total}</p>
        </Card>
        <Card bodyClassName="p-4">
          <p className="text-[11px] font-semibold uppercase tracking-[.09em] text-slate-500">
            Approved
          </p>
          <p className="mt-1.5 text-2xl font-semibold text-emerald-300">{counts.approved}</p>
        </Card>
        <Card bodyClassName="p-4">
          <p className="text-[11px] font-semibold uppercase tracking-[.09em] text-slate-500">
            Awaiting approval
          </p>
          <p className="mt-1.5 text-2xl font-semibold text-amber-300">{counts.pending}</p>
        </Card>
      </div>

      <Card
        title={`Generated test cases${tests.data ? ` (${tests.data.total})` : ""}`}
        bodyClassName="p-0"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="input w-36"
              value={categoryFilter}
              onChange={(event) => setCategoryFilter(event.target.value)}
            >
              <option value="">All categories</option>
              <option value="POSITIVE">Positive</option>
              <option value="NEGATIVE">Negative</option>
              <option value="BOUNDARY">Boundary</option>
              <option value="REGRESSION">Regression</option>
              <option value="INTEGRATION">Integration</option>
            </select>
            <select
              className="input w-40"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              <option value="">All statuses</option>
              <option value="PENDING_REVIEW">Pending approval</option>
              <option value="APPROVED">Approved</option>
              <option value="REJECTED">Rejected</option>
            </select>
            <select
              className="input w-44"
              value={moduleFilter}
              onChange={(event) => setModuleFilter(event.target.value)}
            >
              <option value="">All modules</option>
              <option value="CUSTOMER_ONBOARDING">Customer onboarding</option>
              <option value="KYC_AML">KYC &amp; AML</option>
              <option value="ACCOUNT_MANAGEMENT">Account management</option>
              <option value="TRANSACTIONS">Transactions</option>
              <option value="CROSS_CUTTING">Cross-cutting</option>
            </select>
            {items.length > 0 && (
              <button
                type="button"
                className="btn-ghost text-[11.5px]"
                onClick={() =>
                  setSelected(
                    selected.size === items.length ? new Set() : new Set(items.map((t) => t.id)),
                  )
                }
              >
                {selected.size === items.length ? "Clear" : "Select all"}
              </button>
            )}
          </div>
        }
      >
        {tests.error ? (
          <div className="p-5">
            <ErrorState error={tests.error} onRetry={() => tests.refetch()} />
          </div>
        ) : tests.isLoading ? (
          <div className="p-5">
            <LoadingBlock label="Loading test cases" rows={4} />
          </div>
        ) : items.length ? (
          <ul className="divide-y divide-white/[.05]">
            {items.map((test) => (
              <TestRow
                key={test.id}
                test={test}
                checked={selected.has(test.id)}
                onToggle={() => toggle(test.id)}
                expanded={expanded === test.id}
                onExpand={() => setExpanded(expanded === test.id ? null : test.id)}
                traceOpen={traceFor === test.id}
                onTrace={() => setTraceFor(traceFor === test.id ? null : test.id)}
                onApprove={(decision) => approve.mutate({ id: test.id, decision })}
                busy={approve.isPending}
              />
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={ShieldCheck}
            title="No test cases generated yet"
            body="Approve an impact mapping on the Impact Analysis screen, then generate tests from it. Each generated test is checked against the CBS API contract before it is stored."
          />
        )}
      </Card>

      <div className="mt-5">
        <SimulationNotice />
      </div>
    </>
  );
}

function TestRow({
  test,
  checked,
  onToggle,
  expanded,
  onExpand,
  traceOpen,
  onTrace,
  onApprove,
  busy,
}: {
  test: TestCase;
  checked: boolean;
  onToggle: () => void;
  expanded: boolean;
  onExpand: () => void;
  traceOpen: boolean;
  onTrace: () => void;
  onApprove: (decision: string) => void;
  busy: boolean;
}) {
  const spec = test.execution_spec ?? {};

  return (
    <li className="px-5 py-4">
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 accent-accent"
          checked={checked}
          onChange={onToggle}
          aria-label={`Select ${test.test_code}`}
        />
        <button
          type="button"
          onClick={onExpand}
          className="mt-0.5 shrink-0 text-slate-500 hover:text-slate-300"
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="accent">
              <span className="mono">{test.test_code}</span>
            </Badge>
            <Badge tone={CATEGORY_TONE[test.category] ?? "muted"}>{test.category}</Badge>
            <PriorityBadge priority={test.priority} />
            <Badge>{test.automation_type}</Badge>
            {test.is_state_changing && (
              <Badge tone="blocked">
                <ShieldAlert size={11} />
                state-changing
              </Badge>
            )}
            <div className="ml-auto flex items-center gap-2">
              <ReviewBadge status={test.approval_status} />
            </div>
          </div>

          <p className="mt-1.5 text-[13px] font-medium text-slate-200">{test.title}</p>
          {test.objective && (
            <p className="mt-0.5 text-[12.5px] text-slate-400">{test.objective}</p>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11.5px] text-slate-500">
            {test.requirement_code && (
              <span>
                Requirement:{" "}
                <span className="mono text-slate-400">{test.requirement_code}</span>
              </span>
            )}
            {test.source_clause_reference && (
              <span>
                Clause:{" "}
                <span className="mono text-slate-400">{test.source_clause_reference}</span>
              </span>
            )}
            {Boolean(spec.method && spec.path) && (
              <span className="mono text-slate-400">
                {String(spec.method)} {String(spec.path)} → {String(spec.expect_status)}
              </span>
            )}
            <ProvenanceTag provenance={test.provenance} />
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            {test.approval_status !== "APPROVED" && (
              <button
                type="button"
                className="btn-success text-[12px]"
                onClick={() => onApprove("APPROVE")}
                disabled={busy}
              >
                <Check size={14} />
                Approve for execution
              </button>
            )}
            {test.approval_status === "PENDING_REVIEW" && (
              <button
                type="button"
                className="btn-danger text-[12px]"
                onClick={() => onApprove("REJECT")}
                disabled={busy}
              >
                <X size={14} />
                Reject
              </button>
            )}
            <button type="button" className="btn-ghost text-[12px]" onClick={onTrace}>
              <GitBranch size={14} />
              {traceOpen ? "Hide traceability" : "Trace to clause"}
            </button>
          </div>

          {expanded && (
            <div className="mt-4 space-y-4 rounded-lg border border-white/[.07] bg-navy-950/40 p-4">
              <div>
                <SectionLabel>Originating regulatory text</SectionLabel>
                <SourceQuote
                  quote={test.source_quote}
                  reference={test.source_clause_reference}
                />
              </div>

              {(test.preconditions ?? []).length > 0 && (
                <div>
                  <SectionLabel>Preconditions</SectionLabel>
                  <ul className="list-inside list-disc space-y-0.5 text-[12.5px] text-slate-400">
                    {test.preconditions!.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              {(test.steps ?? []).length > 0 && (
                <div>
                  <SectionLabel>Execution steps</SectionLabel>
                  <ol className="space-y-1.5">
                    {test.steps!.map((step) => (
                      <li key={step.step} className="flex gap-3 text-[12.5px]">
                        <span className="mono shrink-0 text-slate-600">{step.step}.</span>
                        <div>
                          <p className="text-slate-300">{step.action}</p>
                          {step.expected && (
                            <p className="text-[11.5px] text-slate-500">
                              Expected: {step.expected}
                            </p>
                          )}
                        </div>
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <SectionLabel>Test data</SectionLabel>
                  <pre className="overflow-auto rounded border border-white/[.07] bg-navy-950/70 p-3 font-mono text-[11px] leading-relaxed text-slate-400">
                    {JSON.stringify(test.test_data ?? {}, null, 2)}
                  </pre>
                </div>
                <div>
                  <SectionLabel>Assertion contract</SectionLabel>
                  <pre className="overflow-auto rounded border border-white/[.07] bg-navy-950/70 p-3 font-mono text-[11px] leading-relaxed text-slate-400">
                    {JSON.stringify(spec, null, 2)}
                  </pre>
                </div>
              </div>

              <div>
                <SectionLabel>Expected result</SectionLabel>
                <p className="text-[12.5px] text-slate-300">{test.expected_result}</p>
              </div>
            </div>
          )}

          {traceOpen && <TraceabilityPanel testCaseId={test.id} />}
        </div>
      </div>
    </li>
  );
}

/** The provenance chain for one test, rendered as a vertical trail. */
export function TraceabilityPanel({ testCaseId }: { testCaseId: string }) {
  const trace = useQuery({
    queryKey: ["traceability", testCaseId],
    queryFn: () => api.traceability(testCaseId),
  });

  if (trace.isLoading) {
    return (
      <div className="mt-4 rounded-lg border border-white/[.07] bg-navy-950/40 p-4">
        <LoadingBlock label="Building traceability chain" rows={3} />
      </div>
    );
  }
  if (trace.error) {
    return (
      <div className="mt-4">
        <ErrorState error={trace.error} onRetry={() => trace.refetch()} />
      </div>
    );
  }
  if (!trace.data) return null;

  return (
    <div className="mt-4 rounded-lg border border-accent/20 bg-accent/[.04] p-4">
      <SectionLabel>Regulatory traceability</SectionLabel>
      <p className="mb-3 text-[11.5px] text-slate-500">
        Every test traces back to the clause that produced it. Each hop records its own
        provenance and review state.
      </p>
      <ol className="space-y-0">
        {trace.data.chain.map((node, index) => (
          <TraceNode
            key={`${node.label}-${index}`}
            node={node}
            last={index === trace.data!.chain.length - 1}
          />
        ))}
      </ol>
    </div>
  );
}

const STAGE_LABEL: Record<string, string> = {
  DOCUMENT: "Source document",
  CLAUSE: "Clause",
  REQUIREMENT: "Requirement",
  IMPACT: "Impact mapping",
  BUSINESS_RULE: "CBS business rule",
  TEST_CASE: "Test case",
  EXECUTION: "Execution",
};

function TraceNode({ node, last }: { node: TraceabilityNode; last: boolean }) {
  const stage = String(node.extra?.stage ?? "");
  const sourceUrl = node.extra?.source_url as string | undefined;
  const quote = node.extra?.source_quote as string | undefined;

  return (
    <li className="relative pb-4 pl-7 last:pb-0">
      {!last && <span className="absolute left-[7px] top-4 h-full w-px bg-white/10" />}
      <span
        className={clsx(
          "absolute left-0 top-1.5 h-3.5 w-3.5 rounded-full border-2",
          stage === "EXECUTION"
            ? node.status === "PASSED"
              ? "border-emerald-400 bg-emerald-400/30"
              : node.status === "BLOCKED"
                ? "border-violet-400 bg-violet-400/30"
                : "border-rose-400 bg-rose-400/30"
            : "border-accent/60 bg-accent/25",
        )}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="muted">{STAGE_LABEL[stage] ?? stage}</Badge>
        {node.status && (
          <Badge
            tone={
              node.status === "APPROVED" || node.status === "PASSED"
                ? "pass"
                : node.status === "UNLINKED" || node.status === "PENDING_REVIEW"
                  ? "warn"
                  : node.status === "BLOCKED"
                    ? "blocked"
                    : node.status === "FAILED" || node.status === "REJECTED"
                      ? "fail"
                      : "muted"
            }
          >
            {node.status.replace(/_/g, " ")}
          </Badge>
        )}
        {Boolean(node.extra?.assertion_basis) && (
          <AssertionBadge basis={node.extra!.assertion_basis as never} />
        )}
      </div>
      <p className="mt-1 text-[12.5px] font-medium text-slate-200">{node.label}</p>
      {node.detail && (
        <p className="mt-0.5 text-[12px] leading-relaxed text-slate-400">{node.detail}</p>
      )}
      {quote && (
        <blockquote className="quote mt-1.5">“{quote}”</blockquote>
      )}
      {sourceUrl && (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noreferrer"
          className="mono mt-1 inline-block text-[11px] text-accent-soft hover:underline"
        >
          {sourceUrl}
        </a>
      )}
      {stage === "EXECUTION" && node.extra?.evidence_count !== undefined && (
        <p className="mt-1 text-[11px] text-slate-500">
          {String(node.extra.evidence_count)} evidence artefact(s)
          {node.extra.approved_by ? ` · authorised by ${String(node.extra.approved_by)}` : ""}
          {node.extra.duration_ms ? ` · ${String(node.extra.duration_ms)} ms` : ""}
        </p>
      )}
    </li>
  );
}
