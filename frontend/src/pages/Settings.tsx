/** Screen 7 -- Settings: configuration, integration status and system health. */
import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity,
  CheckCircle2,
  Database,
  Globe,
  KeyRound,
  Landmark,
  Settings as SettingsIcon,
  ShieldCheck,
  Stethoscope,
  XCircle,
} from "lucide-react";

import {
  Badge,
  Card,
  ErrorState,
  KeyValue,
  LoadingBlock,
  PageHeader,
  SectionLabel,
  Spinner,
} from "../components/ui";
import { api } from "../lib/api";

export default function Settings() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
  });

  const probe = useMutation({ mutationFn: api.externalHealth });

  return (
    <>
      <PageHeader
        icon={SettingsIcon}
        title="Settings & System Health"
        subtitle="What this deployment is configured to do, and what each missing credential would unlock. Nothing is hidden behind a fallback."
        actions={
          <button
            type="button"
            className="btn-primary"
            onClick={() => probe.mutate()}
            disabled={probe.isPending}
          >
            {probe.isPending ? <Spinner /> : <Stethoscope size={15} />}
            Probe external services
          </button>
        }
      />

      {error && (
        <div className="mb-5">
          <ErrorState error={error} onRetry={() => refetch()} />
        </div>
      )}

      {isLoading && !data ? (
        <Card>
          <LoadingBlock label="Loading configuration" />
        </Card>
      ) : (
        data && (
          <div className="space-y-5">
            {probe.isError && <ErrorState error={probe.error} />}
            {probe.data && (
              <Card title="Live connectivity probe">
                <div className="grid gap-3 sm:grid-cols-2">
                  <ProbeResult name="TinyFish (Search)" result={probe.data.tinyfish} />
                  <ProbeResult name="Google Gemini" result={probe.data.gemini} />
                </div>
                <p className="mt-3 text-[11.5px] text-slate-500">
                  The TinyFish probe runs one free-tier Search call against the configured
                  regulator domain; the Gemini probe reads model metadata. Neither consumes
                  metered quota.
                </p>
              </Card>
            )}

            <div className="grid gap-5 lg:grid-cols-2">
              <Card title="Regulatory scope" actions={<Globe size={15} className="text-slate-600" />}>
                <dl className="grid grid-cols-2 gap-4">
                  <KeyValue label="Authority">{data.regulatory_authority}</KeyValue>
                  <KeyValue label="Institution type">
                    <Badge tone="accent">{data.institution_type.replace(/_/g, " ")}</Badge>
                  </KeyValue>
                  <KeyValue label="Search domains">
                    <span className="mono">{data.regulatory_domains.join(", ")}</span>
                  </KeyValue>
                  <KeyValue label="Types supported">
                    {data.institution_types_available.length}
                  </KeyValue>
                </dl>
                <div className="mt-4 rounded-lg border border-white/[.07] bg-navy-950/50 p-3">
                  <SectionLabel>Why institution type matters</SectionLabel>
                  <p className="text-[12px] leading-relaxed text-slate-400">
                    The RBI KYC framework is entity-specific. The 2025 Directions are titled
                    “Commercial Banks”, so this project does not assume they bind co-operative
                    banks, NBFCs or payments banks. A document's applicability is recorded from
                    its own wording, and the configured type is never used to widen it.
                  </p>
                  <p className="mt-2 text-[11.5px] text-slate-500">
                    Change with <span className="mono">INSTITUTION_TYPE</span> in{" "}
                    <span className="mono">backend/.env</span>. Available:{" "}
                    <span className="mono">
                      {data.institution_types_available.join(", ")}
                    </span>
                  </p>
                </div>
              </Card>

              <Card title="Database" actions={<Database size={15} className="text-slate-600" />}>
                <dl className="grid grid-cols-2 gap-4">
                  <KeyValue label="Dialect">
                    <Badge tone={data.database.dialect === "postgresql" ? "pass" : "muted"}>
                      {data.database.dialect}
                    </Badge>
                  </KeyValue>
                  <KeyValue label="DATABASE_URL set">
                    {data.database.configured ? "yes" : "no (local SQLite)"}
                  </KeyValue>
                  <KeyValue label="Business rules">{data.database.business_rules}</KeyValue>
                  <KeyValue label="Documents stored">
                    {data.database.regulatory_documents}
                  </KeyValue>
                </dl>
                {data.database.dialect !== "postgresql" && (
                  <p className="mt-4 rounded-lg border border-white/[.07] bg-navy-950/50 p-3 text-[12px] leading-relaxed text-slate-400">
                    Running on a local SQLite file. The models use portable type decorators, so
                    the same Alembic migrations apply unchanged to Supabase PostgreSQL — set{" "}
                    <span className="mono">DATABASE_URL</span> to the Supabase connection URI and
                    run <span className="mono">alembic upgrade head</span>.
                  </p>
                )}
              </Card>
            </div>

            <Card
              title="Integrations"
              actions={<KeyRound size={15} className="text-slate-600" />}
              bodyClassName="p-0"
            >
              <div className="table-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Service</th>
                      <th>Status</th>
                      <th>Endpoint / model</th>
                      <th>Required for</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.integrations).map(([key, integration]) => (
                      <tr key={key}>
                        <td className="font-medium text-slate-200">
                          {key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                        </td>
                        <td>
                          <Badge tone={integration.configured ? "pass" : "warn"}>
                            {integration.configured ? "configured" : "not configured"}
                          </Badge>
                        </td>
                        <td>
                          <span className="mono text-slate-400">
                            {integration.endpoint ?? integration.model ?? "—"}
                          </span>
                        </td>
                        <td className="text-slate-400">{integration.required_for}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="border-t border-white/[.06] px-5 py-4">
                <SectionLabel>Configuring credentials</SectionLabel>
                <p className="text-[12px] leading-relaxed text-slate-400">
                  Secrets live only in backend environment variables. Copy{" "}
                  <span className="mono">backend/.env.example</span> to{" "}
                  <span className="mono">backend/.env</span> (gitignored) and restart the API.
                </p>
                <ul className="mt-2 space-y-1 text-[12px] text-slate-400">
                  <li>
                    <span className="mono text-slate-300">TINYFISH_API_KEY</span> —{" "}
                    <a
                      href="https://agent.tinyfish.ai/api-keys"
                      target="_blank"
                      rel="noreferrer"
                      className="text-accent-soft hover:underline"
                    >
                      agent.tinyfish.ai/api-keys
                    </a>{" "}
                    (Search free to 12,000/day, Fetch to 1,000 URLs/day)
                  </li>
                  <li>
                    <span className="mono text-slate-300">GEMINI_API_KEY</span> —{" "}
                    <a
                      href="https://aistudio.google.com/apikey"
                      target="_blank"
                      rel="noreferrer"
                      className="text-accent-soft hover:underline"
                    >
                      aistudio.google.com/apikey
                    </a>
                  </li>
                  <li>
                    <span className="mono text-slate-300">DATABASE_URL</span> — Supabase project
                    settings, Database, Connection string (URI)
                  </li>
                </ul>
              </div>
            </Card>

            <Card
              title="Test execution drivers"
              actions={<Activity size={15} className="text-slate-600" />}
            >
              <div className="space-y-3">
                {Object.entries(data.execution_drivers).map(([name, driver]) => (
                  <div
                    key={name}
                    className={clsx(
                      "rounded-lg border px-4 py-3",
                      driver.available
                        ? "border-emerald-500/20 bg-emerald-500/[.05]"
                        : "border-white/[.07] bg-navy-950/40",
                    )}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="mono text-[12.5px] font-medium text-slate-200">{name}</p>
                      <Badge tone={driver.available ? "pass" : "muted"}>
                        {driver.available ? "available" : "unavailable"}
                      </Badge>
                    </div>
                    <p className="mt-1.5 text-[12px] leading-relaxed text-slate-400">
                      {driver.detail}
                    </p>
                  </div>
                ))}
              </div>
            </Card>

            <Card
              title="Safety controls"
              actions={<ShieldCheck size={15} className="text-slate-600" />}
            >
              <ul className="space-y-2.5 text-[12.5px] text-slate-300">
                <SafetyRow
                  on={data.safety.require_approval_for_execution}
                  label="Human approval required before state-changing test execution"
                  detail="A test that writes to the simulated CBS runs only when an APPROVED approval record exists. Otherwise the execution is recorded as BLOCKED."
                />
                <SafetyRow
                  on={data.safety.simulated_cbs_only}
                  label="Simulated core banking only"
                  detail="There is no code path from this application to a real banking system."
                />
                <SafetyRow
                  on
                  label="Regulatory content treated as untrusted data"
                  detail="Source text is delimited and never interpreted as instructions to the model."
                />
                <SafetyRow
                  on
                  label="Source quotes verified before being labelled explicit"
                  detail="A quote that cannot be located in the source document is downgraded to UNVERIFIED_ASSUMPTION."
                />
                <SafetyRow
                  on
                  label="CBS rule identifiers validated against the catalogue"
                  detail="An impact mapping naming a rule code that does not exist in the database is discarded."
                />
                <SafetyRow
                  on
                  label="Credentials redacted from logs and events"
                  detail="API keys and browser CDP URLs never appear in log output, activity records or the SSE stream."
                />
              </ul>
            </Card>
          </div>
        )
      )}
    </>
  );
}

function SafetyRow({ on, label, detail }: { on: boolean; label: string; detail: string }) {
  return (
    <li className="flex items-start gap-3">
      {on ? (
        <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-emerald-400" />
      ) : (
        <XCircle size={15} className="mt-0.5 shrink-0 text-rose-400" />
      )}
      <div>
        <p className="font-medium text-slate-200">{label}</p>
        <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">{detail}</p>
      </div>
    </li>
  );
}

function ProbeResult({
  name,
  result,
}: {
  name: string;
  result: { configured: boolean; reachable: boolean; detail: string };
}) {
  const tone = !result.configured ? "warn" : result.reachable ? "pass" : "fail";
  return (
    <div className="rounded-lg border border-white/[.07] bg-navy-950/40 px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-[13px] font-medium text-slate-200">
          <Landmark size={14} className="text-slate-500" />
          {name}
        </p>
        <Badge tone={tone}>
          {!result.configured ? "not configured" : result.reachable ? "reachable" : "unreachable"}
        </Badge>
      </div>
      <p className="mt-1.5 break-words text-[12px] leading-relaxed text-slate-400">
        {result.detail}
      </p>
    </div>
  );
}
