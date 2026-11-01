/** Screen 5 -- Execution Monitor: live agent activity, results and evidence. */
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity,
  CheckCircle2,
  Clock,
  Code2,
  FileText,
  Image as ImageIcon,
  ShieldAlert,
  Terminal,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  ExecutionBadge,
  KeyValue,
  LiveDot,
  LoadingBlock,
  PageHeader,
  SectionLabel,
  Spinner,
} from "../components/ui";
import { api } from "../lib/api";
import type { Evidence, TestExecution } from "../lib/types";
import { useEventStream } from "../lib/useEventStream";

export default function ExecutionMonitor() {
  const { runId } = useParams<{ runId?: string }>();
  const navigate = useNavigate();
  const { events, connected } = useEventStream();
  const [activeRun, setActiveRun] = useState<string | undefined>(runId);

  const runs = useQuery({
    queryKey: ["executionRuns"],
    queryFn: () => api.executionRuns(25),
    // A run in progress emits SSE progress events, but poll as a fallback so
    // the list still advances if the stream drops.
    refetchInterval: (query) =>
      query.state.data?.items.some((run) => run.status === "RUNNING") ? 4000 : false,
  });

  const activity = useQuery({
    queryKey: ["activity"],
    queryFn: () => api.activity({ limit: 40 }),
  });

  // Default to the newest run so the screen is never empty on arrival.
  useEffect(() => {
    if (!activeRun && runs.data?.items.length) {
      setActiveRun(runs.data.items[0].id);
    }
  }, [activeRun, runs.data]);

  const detail = useQuery({
    queryKey: ["executionRun", activeRun],
    queryFn: () => api.executionRun(activeRun!),
    enabled: Boolean(activeRun),
    refetchInterval: (query) => (query.state.data?.status === "RUNNING" ? 2500 : false),
  });

  const progressEvents = events.filter((event) =>
    ["execution_started", "execution_progress", "execution_completed"].includes(event.type),
  );

  return (
    <>
      <PageHeader
        icon={Activity}
        title="Execution Monitor"
        subtitle="Live agent activity, test results against the simulated core banking system, and the evidence captured for each run."
        actions={<LiveDot active={connected} label={connected ? "Streaming" : "Reconnecting"} />}
      />

      <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        {/* --- run list --- */}
        <div className="space-y-5">
          <Card title="Execution runs" bodyClassName="p-0">
            {runs.isLoading ? (
              <div className="p-5">
                <LoadingBlock label="Loading runs" rows={3} />
              </div>
            ) : runs.data?.items.length ? (
              <ul className="divide-y divide-white/[.05]">
                {runs.data.items.map((run) => (
                  <li key={run.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setActiveRun(run.id);
                        navigate(`/executions/${run.id}`, { replace: true });
                      }}
                      className={clsx(
                        "w-full px-4 py-3 text-left transition-colors",
                        activeRun === run.id ? "bg-accent/[.09]" : "hover:bg-white/[.03]",
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <ExecutionBadge status={run.status} />
                        <span className="text-[10.5px] text-slate-500">
                          {run.started_at
                            ? new Date(run.started_at).toLocaleTimeString()
                            : "—"}
                        </span>
                      </div>
                      <p className="mono mt-1.5 text-[11px] text-slate-500">{run.driver}</p>
                      <div className="mt-1.5 flex flex-wrap gap-x-3 text-[11px]">
                        <span className="text-emerald-400">{run.passed} passed</span>
                        <span className="text-rose-400">{run.failed} failed</span>
                        {run.blocked > 0 && (
                          <span className="text-violet-400">{run.blocked} blocked</span>
                        )}
                        {run.errored > 0 && (
                          <span className="text-amber-400">{run.errored} errored</span>
                        )}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                icon={Clock}
                title="No runs yet"
                body="Approve a test case and execute it from the Test Management screen."
              />
            )}
          </Card>

          {/* --- live feed --- */}
          <Card title="Live execution events">
            {progressEvents.length ? (
              <ol className="space-y-2">
                {progressEvents.slice(0, 12).map((event) => (
                  <li key={event.id} className="flex items-start gap-2.5 text-[12px]">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent/80" />
                    <div className="min-w-0">
                      <p className="text-slate-300">
                        {String(event.payload.test_code ?? event.type.replace(/_/g, " "))}
                      </p>
                      <p className="mono truncate text-[11px] text-slate-500">
                        {String(event.payload.status ?? "")}
                        {event.payload.index
                          ? ` · ${String(event.payload.index)}/${String(event.payload.total)}`
                          : ""}
                        {event.payload.duration_ms
                          ? ` · ${String(event.payload.duration_ms)} ms`
                          : ""}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-[12.5px] text-slate-500">
                No execution events in this session yet. Start a run and progress appears here as
                each test completes.
              </p>
            )}
          </Card>

          {/* --- agent activity --- */}
          <Card title="Agent activity log" bodyClassName="p-0">
            {activity.isLoading ? (
              <div className="p-5">
                <LoadingBlock label="Loading activity" rows={3} />
              </div>
            ) : activity.data?.length ? (
              <ul className="max-h-[420px] divide-y divide-white/[.05] overflow-y-auto">
                {activity.data.map((entry) => (
                  <li key={entry.id} className="px-4 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <Badge
                        tone={
                          entry.status === "SUCCEEDED"
                            ? "pass"
                            : entry.status === "FAILED"
                              ? "fail"
                              : entry.status === "SKIPPED"
                                ? "warn"
                                : "info"
                        }
                      >
                        {entry.status}
                      </Badge>
                      <span className="text-[10.5px] text-slate-600">
                        {new Date(entry.created_at).toLocaleTimeString()}
                      </span>
                    </div>
                    <p className="mt-1 break-words text-[12px] text-slate-300">
                      {entry.operation}
                    </p>
                    <p className="text-[10.5px] text-slate-500">
                      {entry.agent_name.replace(/_/g, " ")}
                      {entry.duration_ms !== null && ` · ${entry.duration_ms} ms`}
                      {entry.external_service && ` · ${entry.external_service}`}
                    </p>
                    {entry.message && entry.status === "FAILED" && (
                      <p className="mt-1 text-[11px] leading-snug text-rose-300/80">
                        {entry.message}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon={Terminal} title="No agent activity yet" />
            )}
          </Card>
        </div>

        {/* --- run detail --- */}
        <div className="space-y-5">
          {!activeRun ? (
            <Card>
              <EmptyState
                icon={Activity}
                title="Select a run"
                body="Choose an execution run to see per-test results, assertion comparisons and captured evidence."
              />
            </Card>
          ) : detail.isLoading ? (
            <Card>
              <LoadingBlock label="Loading run detail" rows={5} />
            </Card>
          ) : detail.error ? (
            <ErrorState error={detail.error} onRetry={() => detail.refetch()} />
          ) : (
            detail.data && (
              <>
                <Card
                  title="Run summary"
                  actions={
                    detail.data.status === "RUNNING" ? (
                      <span className="flex items-center gap-2 text-[11.5px] text-accent-soft">
                        <Spinner /> running
                      </span>
                    ) : (
                      <ExecutionBadge status={detail.data.status} />
                    )
                  }
                >
                  <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                    <KeyValue label="Driver">
                      <span className="mono">{detail.data.driver}</span>
                    </KeyValue>
                    <KeyValue label="Target">
                      <span className="mono text-[11px] break-all">
                        {detail.data.target_base_url ?? "—"}
                      </span>
                    </KeyValue>
                    <KeyValue label="Started">
                      {detail.data.started_at
                        ? new Date(detail.data.started_at).toLocaleString()
                        : "—"}
                    </KeyValue>
                    <KeyValue label="Finished">
                      {detail.data.finished_at
                        ? new Date(detail.data.finished_at).toLocaleString()
                        : "—"}
                    </KeyValue>
                  </dl>
                  <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
                    <Tile label="Total" value={detail.data.total} />
                    <Tile label="Passed" value={detail.data.passed} tone="pass" />
                    <Tile label="Failed" value={detail.data.failed} tone="fail" />
                    <Tile label="Blocked" value={detail.data.blocked} tone="blocked" />
                    <Tile label="Errored" value={detail.data.errored} tone="warn" />
                  </div>
                  {detail.data.tinyfish_session_id && (
                    <p className="mono mt-3 text-[11px] text-slate-500">
                      TinyFish browser session: {detail.data.tinyfish_session_id}
                    </p>
                  )}
                </Card>

                <Card
                  title={`Test results (${detail.data.executions.length})`}
                  bodyClassName="p-0"
                >
                  {detail.data.executions.length ? (
                    <ul className="divide-y divide-white/[.05]">
                      {detail.data.executions.map((execution) => (
                        <ExecutionRow key={execution.id} execution={execution} />
                      ))}
                    </ul>
                  ) : (
                    <EmptyState icon={Clock} title="No test results in this run" />
                  )}
                </Card>
              </>
            )
          )}
        </div>
      </div>
    </>
  );
}

function Tile({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: number;
  tone?: "default" | "pass" | "fail" | "blocked" | "warn";
}) {
  const tones: Record<string, string> = {
    default: "text-slate-100",
    pass: "text-emerald-300",
    fail: "text-rose-300",
    blocked: "text-violet-300",
    warn: "text-amber-300",
  };
  return (
    <div className="rounded-lg border border-white/[.07] bg-navy-950/40 px-3 py-2.5">
      <p className="text-[10.5px] font-semibold uppercase tracking-[.09em] text-slate-500">
        {label}
      </p>
      <p className={clsx("mt-1 text-lg font-semibold", tones[tone])}>{value}</p>
    </div>
  );
}

function ExecutionRow({ execution }: { execution: TestExecution }) {
  const [open, setOpen] = useState(execution.status === "FAILED");
  const test = execution.test_case;

  return (
    <li className="px-5 py-4">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-start gap-3 text-left"
        aria-expanded={open}
      >
        <span className="mt-0.5 shrink-0">
          {execution.status === "PASSED" ? (
            <CheckCircle2 size={16} className="text-emerald-400" />
          ) : execution.status === "BLOCKED" ? (
            <ShieldAlert size={16} className="text-violet-400" />
          ) : (
            <XCircle size={16} className="text-rose-400" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="mono text-[12px] text-slate-300">
              {test?.test_code ?? execution.test_case_id.slice(0, 8)}
            </span>
            <ExecutionBadge status={execution.status} />
            {test && <Badge>{test.category}</Badge>}
            <span className="mono ml-auto text-[11px] text-slate-500">
              {execution.duration_ms !== null ? `${execution.duration_ms} ms` : ""}
            </span>
          </div>
          {test && <p className="mt-1 text-[12.5px] text-slate-300">{test.title}</p>}
          {execution.failure_reason && (
            <p className="mt-1 text-[11.5px] leading-snug text-rose-300/90">
              {execution.failure_reason}
            </p>
          )}
        </div>
      </button>

      {open && (
        <div className="mt-3 space-y-4 rounded-lg border border-white/[.07] bg-navy-950/40 p-4">
          {/* Expected versus actual is the heart of the evidence. */}
          <div className="grid gap-3 lg:grid-cols-2">
            <div>
              <SectionLabel>Expected</SectionLabel>
              <p className="rounded border border-emerald-500/20 bg-emerald-500/[.05] px-3 py-2 font-mono text-[11.5px] leading-relaxed text-slate-300">
                {execution.expected_result ?? "—"}
              </p>
            </div>
            <div>
              <SectionLabel>Actual</SectionLabel>
              <p
                className={clsx(
                  "break-words rounded border px-3 py-2 font-mono text-[11.5px] leading-relaxed text-slate-300",
                  execution.status === "PASSED"
                    ? "border-emerald-500/20 bg-emerald-500/[.05]"
                    : "border-rose-500/20 bg-rose-500/[.05]",
                )}
              >
                {execution.actual_result ?? "—"}
              </p>
            </div>
          </div>

          {execution.assertions?.length ? (
            <div>
              <SectionLabel>Assertions</SectionLabel>
              <div className="table-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Assertion</th>
                      <th>Expected</th>
                      <th>Actual</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {execution.assertions.map((assertion, index) => (
                      <tr key={index}>
                        <td className="mono text-[11.5px]">{assertion.name}</td>
                        <td className="mono text-[11.5px]">{String(assertion.expected)}</td>
                        <td className="mono text-[11.5px]">{String(assertion.actual)}</td>
                        <td>
                          <Badge tone={assertion.passed ? "pass" : "fail"}>
                            {assertion.passed ? "pass" : "fail"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {execution.approved_by_email && (
            <p className="text-[11.5px] text-slate-500">
              Execution authorised by{" "}
              <span className="text-slate-400">{execution.approved_by_email}</span>
            </p>
          )}

          {execution.evidence.length > 0 && (
            <div>
              <SectionLabel>Evidence ({execution.evidence.length})</SectionLabel>
              <div className="space-y-2.5">
                {execution.evidence.map((item) => (
                  <EvidenceItem key={item.id} evidence={item} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function EvidenceItem({ evidence }: { evidence: Evidence }) {
  const [show, setShow] = useState(false);
  const isImage = evidence.evidence_type === "SCREENSHOT";
  const hasFile = Boolean(evidence.file_path);

  const Icon =
    evidence.evidence_type === "SCREENSHOT"
      ? ImageIcon
      : evidence.evidence_type === "API_RESPONSE"
        ? Code2
        : evidence.evidence_type === "HTML_SNAPSHOT"
          ? FileText
          : Terminal;

  return (
    <div className="rounded-lg border border-white/[.07] bg-navy-950/60">
      <button
        type="button"
        onClick={() => setShow(!show)}
        className="flex w-full items-center gap-2.5 px-3 py-2 text-left"
      >
        <Icon size={14} className="shrink-0 text-slate-500" />
        <span className="min-w-0 flex-1 truncate text-[12px] text-slate-300">
          {evidence.label}
        </span>
        <Badge>{evidence.evidence_type.replace(/_/g, " ")}</Badge>
        {evidence.size_bytes !== null && (
          <span className="mono shrink-0 text-[10.5px] text-slate-600">
            {formatBytes(evidence.size_bytes)}
          </span>
        )}
      </button>

      {show && (
        <div className="space-y-2 border-t border-white/[.06] px-3 py-3">
          {isImage && hasFile && (
            <img
              src={api.evidenceFileUrl(evidence.id)}
              alt={evidence.label}
              className="w-full rounded border border-white/10"
              loading="lazy"
            />
          )}
          {!isImage && hasFile && (
            <a
              href={api.evidenceFileUrl(evidence.id)}
              target="_blank"
              rel="noreferrer"
              className="btn-ghost text-[11.5px]"
            >
              Open artefact
            </a>
          )}
          {evidence.payload && (
            <pre className="max-h-72 overflow-auto rounded border border-white/[.07] bg-navy-950/80 p-3 font-mono text-[10.5px] leading-relaxed text-slate-400">
              {JSON.stringify(evidence.payload, null, 2)}
            </pre>
          )}
          {evidence.text_payload && (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded border border-white/[.07] bg-navy-950/80 p-3 font-mono text-[10.5px] leading-relaxed text-slate-400">
              {evidence.text_payload}
            </pre>
          )}
          {evidence.sha256 && (
            <p className="mono text-[10.5px] text-slate-600">
              sha256: {evidence.sha256}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
