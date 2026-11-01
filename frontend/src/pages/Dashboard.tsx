/** Screen 1 -- Executive Dashboard. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BookOpenText,
  CheckCircle2,
  FileWarning,
  Gauge,
  LayoutDashboard,
  Layers,
  PlayCircle,
  Search,
  ShieldAlert,
  ShieldCheck,
  Target,
  XCircle,
} from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  AssertionBadge,
  Badge,
  Card,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  SimulationNotice,
  Spinner,
  StatCard,
} from "../components/ui";
import { api } from "../lib/api";
import type { AssertionBasis } from "../lib/types";
import { useEventStream } from "../lib/useEventStream";

/* Chart palette: one hue per semantic meaning, consistent across all screens. */
const PRIORITY_COLORS: Record<string, string> = {
  CRITICAL: "#f43f5e",
  HIGH: "#f59e0b",
  MEDIUM: "#38bdf8",
  LOW: "#64748b",
  UNSPECIFIED: "#475569",
};

const MODULE_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#a78bfa", "#38bdf8", "#f43f5e"];

const CHART_AXIS = {
  stroke: "#475569",
  fontSize: 11,
  tickLine: false,
  axisLine: false,
} as const;

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-white/10 bg-navy-850/95 px-3 py-2 shadow-card backdrop-blur">
      {label && <p className="mb-1 text-[11px] font-semibold text-slate-300">{label}</p>}
      {payload.map((entry: any) => (
        <p key={entry.name} className="flex items-center gap-2 text-[11.5px] text-slate-400">
          <span
            className="h-2 w-2 rounded-full"
            style={{ background: entry.color ?? entry.fill }}
          />
          {entry.name}: <span className="mono text-slate-200">{entry.value}</span>
        </p>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const queryClient = useQueryClient();
  const { events, connected } = useEventStream();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["dashboard"],
    queryFn: api.dashboard,
  });

  const discovery = useMutation({
    mutationFn: () => api.startDiscovery({ max_results_per_query: 10 }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["discovery"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const counts = data?.counts;

  const passRate = useMemo(() => {
    if (!counts) return null;
    const decided = counts.executions_passed + counts.executions_failed;
    if (!decided) return null;
    return Math.round((counts.executions_passed / decided) * 100);
  }, [counts]);

  const recentPipelineEvents = useMemo(
    () => events.filter((event) => event.type !== "agent_activity").slice(0, 8),
    [events],
  );

  return (
    <>
      <PageHeader
        icon={LayoutDashboard}
        title="Executive Dashboard"
        subtitle="Regulatory change, its mapped impact on core banking rules, and the state of compliance test execution."
        actions={
          <>
            <Link to="/regulations" className="btn-ghost">
              <BookOpenText size={15} />
              Regulatory Explorer
            </Link>
            <button
              type="button"
              className="btn-primary"
              onClick={() => discovery.mutate()}
              disabled={discovery.isPending}
            >
              {discovery.isPending ? <Spinner /> : <Search size={15} />}
              Discover Regulatory Updates
            </button>
          </>
        }
      />

      {discovery.isError && (
        <div className="mb-5">
          <ErrorState error={discovery.error} onRetry={() => discovery.reset()} />
        </div>
      )}
      {discovery.isSuccess && (
        <div className="mb-5 rounded-lg border border-accent/25 bg-accent/[.07] px-4 py-3 text-[13px] text-slate-300">
          <p className="flex items-center gap-2 font-medium text-accent-soft">
            <Search size={14} /> Discovery run started
          </p>
          <p className="mt-1 text-slate-400">
            TinyFish Search is querying official sources. Progress streams in below and on the{" "}
            <Link to="/executions" className="text-accent-soft underline-offset-2 hover:underline">
              Execution Monitor
            </Link>
            .
          </p>
        </div>
      )}

      {error && (
        <div className="mb-5">
          <ErrorState error={error} onRetry={() => refetch()} />
        </div>
      )}

      {isLoading && !data ? (
        <Card>
          <LoadingBlock label="Loading dashboard" rows={5} />
        </Card>
      ) : (
        data && (
          <div className="space-y-5">
            {/* --- headline counters --- */}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
              <StatCard
                label="Documents"
                value={counts!.regulatory_documents}
                hint={`${counts!.current_documents} current version(s)`}
                icon={BookOpenText}
              />
              <StatCard
                label="Changes"
                value={counts!.regulatory_changes}
                hint={`${counts!.explicit_requirements} explicit`}
                tone="info"
                icon={Layers}
              />
              <StatCard
                label="CBS modules hit"
                value={counts!.affected_cbs_modules}
                hint={`${counts!.business_rules} rules in catalogue`}
                icon={Target}
              />
              <StatCard
                label="Impact reviews"
                value={counts!.impacts_pending_review}
                hint="awaiting a human decision"
                tone={counts!.impacts_pending_review ? "warn" : "default"}
                icon={ShieldAlert}
              />
              <StatCard
                label="Test cases"
                value={counts!.test_cases}
                hint={`${counts!.tests_approved} approved`}
                icon={ShieldCheck}
              />
              <StatCard
                label="Passed"
                value={counts!.executions_passed}
                hint={passRate !== null ? `${passRate}% pass rate` : "no runs yet"}
                tone="pass"
                icon={CheckCircle2}
              />
              <StatCard
                label="Failed"
                value={counts!.executions_failed}
                hint="assertion mismatch"
                tone={counts!.executions_failed ? "fail" : "default"}
                icon={XCircle}
              />
              <StatCard
                label="Blocked"
                value={counts!.executions_blocked}
                hint="awaiting approval"
                tone={counts!.executions_blocked ? "blocked" : "default"}
                icon={ShieldAlert}
              />
            </div>

            {/* Honest reporting of documents we could not retrieve. */}
            {counts!.documents_inaccessible > 0 && (
              <div className="rounded-lg border border-amber-500/25 bg-amber-500/[.06] px-4 py-3 text-[13px] text-amber-200/90">
                <p className="flex items-center gap-2 font-medium">
                  <FileWarning size={14} />
                  {counts!.documents_inaccessible} document(s) could not be retrieved
                </p>
                <p className="mt-1 text-slate-400">
                  The limitation is recorded against each document rather than filled with
                  generated content. Open the{" "}
                  <Link
                    to="/regulations?status=FETCH_FAILED"
                    className="text-amber-200 underline-offset-2 hover:underline"
                  >
                    Regulatory Explorer
                  </Link>{" "}
                  to review them.
                </p>
              </div>
            )}

            <div className="grid gap-5 xl:grid-cols-3">
              {/* --- regulatory timeline --- */}
              <Card title="Regulatory update timeline" className="xl:col-span-2">
                {data.timeline.length ? (
                  <ResponsiveContainer width="100%" height={248}>
                    <AreaChart data={data.timeline} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                      <defs>
                        <linearGradient id="docGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.45} />
                          <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.02} />
                        </linearGradient>
                        <linearGradient id="changeGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#10b981" stopOpacity={0.4} />
                          <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="period" {...CHART_AXIS} />
                      <YAxis allowDecimals={false} {...CHART_AXIS} />
                      <Tooltip content={<ChartTooltip />} />
                      <Legend
                        wrapperStyle={{ fontSize: 11, color: "#94a3b8", paddingTop: 6 }}
                        iconType="circle"
                        iconSize={7}
                      />
                      <Area
                        type="monotone"
                        dataKey="documents"
                        name="Documents published"
                        stroke="#3b82f6"
                        strokeWidth={2}
                        fill="url(#docGradient)"
                      />
                      <Area
                        type="monotone"
                        dataKey="changes"
                        name="Requirements effective"
                        stroke="#10b981"
                        strokeWidth={2}
                        fill="url(#changeGradient)"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState
                    icon={BookOpenText}
                    title="No dated regulatory activity yet"
                    body="Run a discovery pass to populate the timeline. Documents appear here once a publication or effective date is known."
                  />
                )}
              </Card>

              {/* --- risk distribution --- */}
              <Card title="Impact priority distribution">
                {data.risk_distribution.length ? (
                  <>
                    <ResponsiveContainer width="100%" height={200}>
                      <PieChart>
                        <Pie
                          data={data.risk_distribution}
                          dataKey="count"
                          nameKey="label"
                          innerRadius={52}
                          outerRadius={80}
                          paddingAngle={2}
                          stroke="none"
                        >
                          {data.risk_distribution.map((bucket) => (
                            <Cell
                              key={bucket.label}
                              fill={PRIORITY_COLORS[bucket.label] ?? "#475569"}
                            />
                          ))}
                        </Pie>
                        <Tooltip content={<ChartTooltip />} />
                      </PieChart>
                    </ResponsiveContainer>
                    <ul className="mt-2 space-y-1.5">
                      {data.risk_distribution.map((bucket) => (
                        <li
                          key={bucket.label}
                          className="flex items-center justify-between text-[12px]"
                        >
                          <span className="flex items-center gap-2 text-slate-400">
                            <span
                              className="h-2 w-2 rounded-full"
                              style={{ background: PRIORITY_COLORS[bucket.label] ?? "#475569" }}
                            />
                            {bucket.label}
                          </span>
                          <span className="mono text-slate-300">{bucket.count}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <EmptyState
                    icon={Gauge}
                    title="No impact mappings yet"
                    body="Analyse a document, then map its requirements onto CBS business rules to populate this chart."
                  />
                )}
              </Card>
            </div>

            <div className="grid gap-5 xl:grid-cols-3">
              {/* --- affected modules --- */}
              <Card title="Affected CBS modules" className="xl:col-span-2">
                {data.module_distribution.length ? (
                  <ResponsiveContainer width="100%" height={230}>
                    <BarChart
                      data={data.module_distribution}
                      margin={{ top: 4, right: 8, left: -18, bottom: 0 }}
                    >
                      <XAxis
                        dataKey="label"
                        {...CHART_AXIS}
                        tickFormatter={(value: string) => value.replace(/_/g, " ").slice(0, 16)}
                      />
                      <YAxis allowDecimals={false} {...CHART_AXIS} />
                      <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(255,255,255,.03)" }} />
                      <Bar dataKey="count" name="Impact mappings" radius={[5, 5, 0, 0]}>
                        {data.module_distribution.map((bucket, index) => (
                          <Cell key={bucket.label} fill={MODULE_COLORS[index % MODULE_COLORS.length]} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState
                    icon={Target}
                    title="No module impact recorded"
                    body="Impact mappings link a regulatory requirement to a CBS rule and its banking module."
                  />
                )}
              </Card>

              {/* --- provenance mix: the credibility panel --- */}
              <Card title="Requirement provenance">
                {data.assertion_basis_distribution.length ? (
                  <ul className="space-y-3">
                    {data.assertion_basis_distribution.map((bucket) => (
                      <li key={bucket.label} className="space-y-1.5">
                        <div className="flex items-center justify-between gap-2">
                          <AssertionBadge basis={bucket.label as AssertionBasis} />
                          <span className="mono text-slate-300">{bucket.count}</span>
                        </div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-white/[.06]">
                          <div
                            className="h-full rounded-full bg-accent/70"
                            style={{
                              width: `${
                                (bucket.count /
                                  Math.max(
                                    ...data.assertion_basis_distribution.map((b) => b.count),
                                  )) *
                                100
                              }%`,
                            }}
                          />
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState
                    icon={ShieldCheck}
                    title="Nothing analysed yet"
                    body="Every extracted requirement is labelled by provenance: an explicit regulatory requirement, an AI interpretation, a proposed control, or an unverified assumption."
                  />
                )}
              </Card>
            </div>

            <div className="grid gap-5 xl:grid-cols-2">
              {/* --- live pipeline feed --- */}
              <Card
                title="Live pipeline events"
                actions={
                  <Badge tone={connected ? "pass" : "muted"}>
                    {connected ? "streaming" : "reconnecting"}
                  </Badge>
                }
              >
                {recentPipelineEvents.length ? (
                  <ol className="space-y-2.5">
                    {recentPipelineEvents.map((event) => (
                      <li key={event.id} className="flex items-start gap-3">
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent/80" />
                        <div className="min-w-0">
                          <p className="text-[12.5px] font-medium text-slate-300">
                            {event.type.replace(/_/g, " ")}
                          </p>
                          <p className="mono truncate text-slate-500">
                            {summarisePayload(event.payload)}
                          </p>
                        </div>
                        <time className="ml-auto shrink-0 text-[11px] text-slate-600">
                          {new Date(event.created_at).toLocaleTimeString()}
                        </time>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <EmptyState
                    icon={Activity}
                    title="No pipeline activity in this session"
                    body="Start a discovery run and events will stream here in real time."
                  />
                )}
              </Card>

              {/* --- agent activity log --- */}
              <Card
                title="Recent agent activity"
                actions={
                  <Link to="/executions" className="text-[11.5px] text-accent-soft hover:underline">
                    View monitor
                  </Link>
                }
              >
                {data.recent_activity.length ? (
                  <ul className="space-y-2">
                    {data.recent_activity.slice(0, 9).map((entry) => (
                      <li key={entry.id} className="flex items-start gap-3 text-[12.5px]">
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
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-slate-300">{entry.operation}</p>
                          <p className="text-[11px] text-slate-500">
                            {entry.agent_name.replace(/_/g, " ")}
                            {entry.duration_ms !== null && ` · ${entry.duration_ms} ms`}
                            {entry.external_service && ` · ${entry.external_service}`}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState
                    icon={Activity}
                    title="No agent activity recorded"
                    body="Every agent operation is logged here with its duration and external service, forming the audit trail."
                  />
                )}
              </Card>
            </div>

            {/* --- latest execution run --- */}
            {data.latest_execution_run && (
              <Card
                title="Latest execution run"
                actions={
                  <Link
                    to={`/executions/${data.latest_execution_run.id}`}
                    className="btn-ghost text-[12px]"
                  >
                    <PlayCircle size={14} />
                    Open run
                  </Link>
                }
              >
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                  <Metric label="Status" value={data.latest_execution_run.status} />
                  <Metric label="Driver" value={data.latest_execution_run.driver} />
                  <Metric label="Total" value={data.latest_execution_run.total} />
                  <Metric label="Passed" value={data.latest_execution_run.passed} tone="pass" />
                  <Metric label="Failed" value={data.latest_execution_run.failed} tone="fail" />
                  <Metric
                    label="Blocked"
                    value={data.latest_execution_run.blocked}
                    tone="blocked"
                  />
                </div>
              </Card>
            )}

            <SimulationNotice />
          </div>
        )
      )}
    </>
  );
}

function Metric({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string | number;
  tone?: "default" | "pass" | "fail" | "blocked";
}) {
  const tones: Record<string, string> = {
    default: "text-slate-200",
    pass: "text-emerald-300",
    fail: "text-rose-300",
    blocked: "text-violet-300",
  };
  return (
    <div>
      <p className="text-[10.5px] font-semibold uppercase tracking-[.09em] text-slate-500">
        {label}
      </p>
      <p className={`mt-1 text-[15px] font-semibold ${tones[tone]}`}>{value}</p>
    </div>
  );
}

/** Condense an event payload into one readable line. */
function summarisePayload(payload: Record<string, unknown>): string {
  const interesting = [
    "title",
    "requirement_code",
    "test_code",
    "rule_code",
    "status",
    "found",
    "new",
    "change_count",
    "mapping_count",
    "test_count",
    "passed",
    "failed",
    "error",
  ];
  const parts = interesting
    .filter((key) => payload[key] !== undefined && payload[key] !== null)
    .map((key) => `${key}=${String(payload[key]).slice(0, 48)}`);
  return parts.length ? parts.join(" · ") : "—";
}
