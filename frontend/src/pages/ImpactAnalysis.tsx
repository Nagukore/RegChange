/** Screen 3 -- Impact Analysis: requirement to CBS rule, with human approval. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  FlaskConical,
  Target,
  ThumbsDown,
  X,
} from "lucide-react";
import { useState } from "react";

import {
  AssertionBadge,
  Badge,
  Card,
  ConfidenceMeter,
  EmptyState,
  ErrorState,
  KeyValue,
  LoadingBlock,
  PageHeader,
  PriorityBadge,
  ProvenanceTag,
  ReviewBadge,
  SectionLabel,
  SourceQuote,
  Spinner,
} from "../components/ui";
import { api } from "../lib/api";
import type { ImpactMapping } from "../lib/types";

export default function ImpactAnalysis() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("PENDING_REVIEW");
  const [moduleFilter, setModuleFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const impacts = useQuery({
    queryKey: ["impacts", { statusFilter, moduleFilter, priorityFilter }],
    queryFn: () =>
      api.impacts({
        review_status: statusFilter || undefined,
        banking_module: moduleFilter || undefined,
        priority: priorityFilter || undefined,
        limit: 100,
      }),
  });

  const pendingChanges = useQuery({
    queryKey: ["changes", { unmapped: true }],
    queryFn: () => api.changes({ limit: 100 }),
  });

  const review = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: string }) =>
      api.reviewImpact(id, decision),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["impacts"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const mapAll = useMutation({
    mutationFn: (changeIds: string[]) => api.analyseImpact(changeIds),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["impacts"] }),
  });

  const generate = useMutation({
    mutationFn: (mappingIds: string[]) => api.generateTests(mappingIds),
    onSuccess: () => {
      setSelected(new Set());
      queryClient.invalidateQueries({ queryKey: ["testCases"] });
    },
  });

  const items = impacts.data?.items ?? [];
  const approvedSelected = items.filter(
    (mapping) => selected.has(mapping.id) && mapping.review_status === "APPROVED",
  );
  const unmappedChanges = (pendingChanges.data?.items ?? []).length;

  const toggle = (id: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <>
      <PageHeader
        icon={Target}
        title="Impact Analysis"
        subtitle="Each regulatory requirement mapped onto the core banking business rules it affects. Every mapping needs a human decision before tests can be generated from it."
        actions={
          <>
            {unmappedChanges > 0 && (
              <button
                type="button"
                className="btn-ghost"
                onClick={() =>
                  mapAll.mutate((pendingChanges.data?.items ?? []).map((change) => change.id))
                }
                disabled={mapAll.isPending}
              >
                {mapAll.isPending ? <Spinner /> : <Target size={15} />}
                Map {unmappedChanges} requirement(s)
              </button>
            )}
            {approvedSelected.length > 0 && (
              <button
                type="button"
                className="btn-primary"
                onClick={() => generate.mutate(approvedSelected.map((m) => m.id))}
                disabled={generate.isPending}
              >
                {generate.isPending ? <Spinner /> : <FlaskConical size={15} />}
                Generate tests ({approvedSelected.length})
              </button>
            )}
          </>
        }
      />

      {(review.isError || mapAll.isError || generate.isError) && (
        <div className="mb-5 space-y-3">
          {review.isError && <ErrorState error={review.error} />}
          {mapAll.isError && <ErrorState error={mapAll.error} />}
          {generate.isError && <ErrorState error={generate.error} />}
        </div>
      )}

      {/* The reviewer is the control point, so say so plainly. */}
      <div className="mb-5 rounded-lg border border-accent/20 bg-accent/[.05] px-4 py-3 text-[12.5px] leading-relaxed text-slate-400">
        <p className="flex items-center gap-2 font-medium text-accent-soft">
          <AlertTriangle size={14} />
          These mappings are proposals, not compliance determinations
        </p>
        <p className="mt-1">
          Candidate rules are retrieved from the CBS catalogue, a model reasons over that
          shortlist, and any rule code that does not exist in the catalogue is discarded before
          you see it. What remains still needs your judgement — approving a mapping is what
          authorises test generation against that rule.
        </p>
      </div>

      <Card
        title={`Impact mappings${impacts.data ? ` (${impacts.data.total})` : ""}`}
        bodyClassName="p-0"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="input w-40"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              <option value="">All statuses</option>
              <option value="PENDING_REVIEW">Pending review</option>
              <option value="APPROVED">Approved</option>
              <option value="REJECTED">Rejected</option>
              <option value="NEEDS_CHANGES">Needs changes</option>
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
            <select
              className="input w-32"
              value={priorityFilter}
              onChange={(event) => setPriorityFilter(event.target.value)}
            >
              <option value="">Any priority</option>
              <option value="CRITICAL">Critical</option>
              <option value="HIGH">High</option>
              <option value="MEDIUM">Medium</option>
              <option value="LOW">Low</option>
            </select>
          </div>
        }
      >
        {impacts.error ? (
          <div className="p-5">
            <ErrorState error={impacts.error} onRetry={() => impacts.refetch()} />
          </div>
        ) : impacts.isLoading ? (
          <div className="p-5">
            <LoadingBlock label="Loading impact mappings" rows={4} />
          </div>
        ) : items.length ? (
          <ul className="divide-y divide-white/[.05]">
            {items.map((mapping) => (
              <ImpactRow
                key={mapping.id}
                mapping={mapping}
                expanded={expanded === mapping.id}
                onExpand={() => setExpanded(expanded === mapping.id ? null : mapping.id)}
                checked={selected.has(mapping.id)}
                onToggle={() => toggle(mapping.id)}
                onReview={(decision) => review.mutate({ id: mapping.id, decision })}
                reviewing={review.isPending}
              />
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={Target}
            title={
              statusFilter
                ? `No mappings with status ${statusFilter.replace(/_/g, " ").toLowerCase()}`
                : "No impact mappings yet"
            }
            body={
              unmappedChanges > 0 ? (
                <>
                  There are {unmappedChanges} analysed requirement(s) awaiting mapping. Use{" "}
                  <strong>Map requirement(s)</strong> above to run the impact engine.
                </>
              ) : (
                "Analyse a regulatory document first, then map its requirements onto CBS business rules."
              )
            }
          />
        )}
      </Card>
    </>
  );
}

function ImpactRow({
  mapping,
  expanded,
  onExpand,
  checked,
  onToggle,
  onReview,
  reviewing,
}: {
  mapping: ImpactMapping;
  expanded: boolean;
  onExpand: () => void;
  checked: boolean;
  onToggle: () => void;
  onReview: (decision: string) => void;
  reviewing: boolean;
}) {
  const change = mapping.change;
  const rule = mapping.business_rule;

  return (
    <li className="px-5 py-4">
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 accent-accent"
          checked={checked}
          onChange={onToggle}
          aria-label={`Select mapping for ${rule?.rule_code ?? "rule"}`}
        />
        <button
          type="button"
          onClick={onExpand}
          className="mt-0.5 shrink-0 text-slate-500 hover:text-slate-300"
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse details" : "Expand details"}
        >
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {change && <Badge tone="accent">{change.requirement_code}</Badge>}
            <span className="text-slate-600">→</span>
            {rule && (
              <Badge tone="info">
                {rule.rule_code}: {rule.name}
              </Badge>
            )}
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <Badge>{mapping.impact_category.replace(/_/g, " ")}</Badge>
              <PriorityBadge priority={mapping.priority} />
              <ReviewBadge status={mapping.review_status} />
            </div>
          </div>

          <p className="mt-1.5 text-[13px] font-medium text-slate-200">
            {change?.title ?? "(requirement unavailable)"}
          </p>
          <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">{mapping.rationale}</p>

          <div className="mt-2.5 flex flex-wrap items-center gap-x-5 gap-y-2">
            <AssertionBadge basis={mapping.assertion_basis} />
            <span className="flex items-center gap-2 text-[11.5px] text-slate-500">
              Model confidence <ConfidenceMeter value={mapping.confidence} />
            </span>
            <span className="flex items-center gap-2 text-[11.5px] text-slate-500">
              Retrieval match <ConfidenceMeter value={mapping.retrieval_score} />
            </span>
            <span className="text-[11.5px] text-slate-500">
              Regression risk:{" "}
              <span className="font-medium text-slate-400">{mapping.regression_risk}</span>
            </span>
            <ProvenanceTag provenance={mapping.provenance} />
          </div>

          {mapping.review_status === "PENDING_REVIEW" && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className="btn-success text-[12px]"
                onClick={() => onReview("APPROVE")}
                disabled={reviewing}
              >
                <Check size={14} />
                Approve mapping
              </button>
              <button
                type="button"
                className="btn-ghost text-[12px]"
                onClick={() => onReview("NEEDS_CHANGES")}
                disabled={reviewing}
              >
                <ThumbsDown size={14} />
                Needs changes
              </button>
              <button
                type="button"
                className="btn-danger text-[12px]"
                onClick={() => onReview("REJECT")}
                disabled={reviewing}
              >
                <X size={14} />
                Reject
              </button>
            </div>
          )}

          {expanded && (
            <div className="mt-4 space-y-4 rounded-lg border border-white/[.07] bg-navy-950/40 p-4">
              <div>
                <SectionLabel>Regulatory requirement</SectionLabel>
                <p className="text-[12.5px] leading-relaxed text-slate-400">
                  {change?.requirement_text ?? "—"}
                </p>
              </div>

              <div>
                <SectionLabel>Verbatim source</SectionLabel>
                <SourceQuote
                  quote={change?.source_quote}
                  reference={change?.source_reference}
                />
              </div>

              <div>
                <SectionLabel>Affected CBS business rule</SectionLabel>
                {rule ? (
                  <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <KeyValue label="Rule code">
                      <span className="mono">{rule.rule_code}</span>
                    </KeyValue>
                    <KeyValue label="Module">
                      {rule.banking_module.replace(/_/g, " ")}
                    </KeyValue>
                    <KeyValue label="API endpoint">
                      <span className="mono text-[11px]">{rule.api_endpoint ?? "—"}</span>
                    </KeyValue>
                    <KeyValue label="UI route">
                      <span className="mono text-[11px]">{rule.ui_route ?? "—"}</span>
                    </KeyValue>
                    <div className="col-span-2 md:col-span-4">
                      <KeyValue label="Rule behaviour">{rule.description}</KeyValue>
                    </div>
                  </dl>
                ) : (
                  <p className="text-[12px] italic text-slate-500">Rule unavailable.</p>
                )}
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <SectionLabel>Affected fields</SectionLabel>
                  <div className="flex flex-wrap gap-1.5">
                    {(mapping.affected_fields ?? []).length ? (
                      mapping.affected_fields!.map((field) => (
                        <Badge key={field}>
                          <span className="mono">{field}</span>
                        </Badge>
                      ))
                    ) : (
                      <span className="text-[12px] italic text-slate-500">none recorded</span>
                    )}
                  </div>
                </div>
                <div>
                  <SectionLabel>Affected workflows</SectionLabel>
                  <div className="flex flex-wrap gap-1.5">
                    {(mapping.affected_workflows ?? []).length ? (
                      mapping.affected_workflows!.map((workflow) => (
                        <Badge key={workflow}>{workflow}</Badge>
                      ))
                    ) : (
                      <span className="text-[12px] italic text-slate-500">none recorded</span>
                    )}
                  </div>
                </div>
              </div>

              {mapping.review_notes && (
                <div>
                  <SectionLabel>Reviewer notes</SectionLabel>
                  <p className="text-[12.5px] text-slate-400">{mapping.review_notes}</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
