/**
 * Shared UI primitives.
 *
 * The provenance components here carry real weight: a compliance tool must never
 * let an AI interpretation read like a regulator's own words, so
 * `AssertionBadge` and `ProvenanceTag` render those distinctions explicitly
 * everywhere they appear.
 */
import clsx from "clsx";
import {
  AlertTriangle,
  BadgeCheck,
  BrainCircuit,
  CircleHelp,
  FlaskConical,
  Info,
  Loader2,
  Lightbulb,
  Radio,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import type { AssertionBasis, DataProvenance, ExecutionStatusValue, Priority, ReviewStatus } from "../lib/types";

/* ------------------------------------------------------------------ layout */
export function PageHeader({
  title,
  subtitle,
  actions,
  icon: Icon,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  icon?: LucideIcon;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="flex items-start gap-3">
        {Icon && (
          <span className="mt-0.5 grid h-10 w-10 place-items-center rounded-xl border border-white/10 bg-accent/10 text-accent-soft">
            <Icon size={19} />
          </span>
        )}
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-100">{title}</h1>
          {subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-400">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Card({
  title,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={clsx("card animate-fade-in", className)}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2 className="card-title">{title}</h2>}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={clsx("p-5", bodyClassName)}>{children}</div>
    </section>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone = "default",
  icon: Icon,
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: "default" | "pass" | "fail" | "warn" | "info" | "blocked";
  icon?: LucideIcon;
}) {
  const toneRing: Record<string, string> = {
    default: "text-slate-100",
    pass: "text-emerald-300",
    fail: "text-rose-300",
    warn: "text-amber-300",
    info: "text-sky-300",
    blocked: "text-violet-300",
  };
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[.09em] text-slate-500">
          {label}
        </p>
        {Icon && <Icon size={15} className="shrink-0 text-slate-600" />}
      </div>
      <p className={clsx("mt-2 text-2xl font-semibold tracking-tight", toneRing[tone])}>{value}</p>
      {hint && <p className="mt-1 text-[11px] leading-snug text-slate-500">{hint}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ states */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 size={16} className={clsx("animate-spin", className)} />;
}

export function LoadingBlock({ label = "Loading", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite">
      <p className="mb-3 flex items-center gap-2 text-sm text-slate-500">
        <Spinner /> {label}…
      </p>
      <div className="space-y-2">
        {Array.from({ length: rows }).map((_, index) => (
          <div key={index} className="skeleton h-9 w-full" />
        ))}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
  icon: Icon = Info,
}: {
  title: string;
  body?: ReactNode;
  action?: ReactNode;
  icon?: LucideIcon;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-full border border-white/10 bg-white/[.03] text-slate-500">
        <Icon size={19} />
      </span>
      <p className="text-sm font-medium text-slate-300">{title}</p>
      {body && <div className="max-w-md text-[13px] leading-relaxed text-slate-500">{body}</div>}
      {action}
    </div>
  );
}

/**
 * Error panel that surfaces the backend's remediation text.
 *
 * When a credential is missing the API returns 503 with an actionable
 * `remediation`, and showing it beats a generic failure message.
 */
export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const message = error instanceof Error ? error.message : String(error);
  const remediation =
    error && typeof error === "object" && "remediation" in error
      ? (error as { remediation?: string }).remediation
      : undefined;
  const status =
    error && typeof error === "object" && "status" in error
      ? (error as { status?: number }).status
      : undefined;
  const isConfigIssue = status === 503;

  return (
    <div
      className={clsx(
        "rounded-lg border px-4 py-3",
        isConfigIssue
          ? "border-amber-500/30 bg-amber-500/[.07]"
          : "border-rose-500/30 bg-rose-500/[.07]",
      )}
      role="alert"
    >
      <p
        className={clsx(
          "flex items-start gap-2 text-sm font-medium",
          isConfigIssue ? "text-amber-200" : "text-rose-200",
        )}
      >
        {isConfigIssue ? (
          <ShieldAlert size={16} className="mt-0.5 shrink-0" />
        ) : (
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
        )}
        <span>{isConfigIssue ? "Not configured" : "Something went wrong"}</span>
      </p>
      <p className="mt-1.5 text-[13px] leading-relaxed text-slate-300">{message}</p>
      {remediation && (
        <p className="mt-2 rounded border border-white/10 bg-navy-950/60 px-3 py-2 text-[12px] leading-relaxed text-slate-400">
          <span className="font-semibold text-slate-300">How to fix: </span>
          {remediation}
        </p>
      )}
      {onRetry && (
        <button type="button" className="btn-ghost mt-3" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ badges */
export function Badge({
  children,
  tone = "muted",
  className,
}: {
  children: ReactNode;
  tone?: "muted" | "pass" | "fail" | "warn" | "info" | "blocked" | "accent";
  className?: string;
}) {
  const tones: Record<string, string> = {
    muted: "border-white/10 bg-white/[.04] text-slate-400",
    pass: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    fail: "border-rose-500/30 bg-rose-500/10 text-rose-300",
    warn: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    info: "border-sky-500/30 bg-sky-500/10 text-sky-300",
    blocked: "border-violet-500/30 bg-violet-500/10 text-violet-300",
    accent: "border-accent/35 bg-accent/10 text-accent-soft",
  };
  return <span className={clsx("badge", tones[tone], className)}>{children}</span>;
}

const PRIORITY_TONE: Record<Priority, "fail" | "warn" | "info" | "muted"> = {
  CRITICAL: "fail",
  HIGH: "warn",
  MEDIUM: "info",
  LOW: "muted",
};

export function PriorityBadge({ priority }: { priority: Priority }) {
  return <Badge tone={PRIORITY_TONE[priority] ?? "muted"}>{priority}</Badge>;
}

const REVIEW_TONE: Record<ReviewStatus, "pass" | "fail" | "warn" | "muted"> = {
  APPROVED: "pass",
  REJECTED: "fail",
  NEEDS_CHANGES: "warn",
  PENDING_REVIEW: "muted",
};

export function ReviewBadge({ status }: { status: ReviewStatus }) {
  return <Badge tone={REVIEW_TONE[status] ?? "muted"}>{status.replace(/_/g, " ")}</Badge>;
}

const EXEC_TONE: Record<ExecutionStatusValue, "pass" | "fail" | "warn" | "info" | "blocked" | "muted"> =
  {
    PASSED: "pass",
    FAILED: "fail",
    ERROR: "fail",
    BLOCKED: "blocked",
    RUNNING: "info",
    QUEUED: "muted",
    SKIPPED: "muted",
  };

export function ExecutionBadge({ status }: { status: ExecutionStatusValue }) {
  return <Badge tone={EXEC_TONE[status] ?? "muted"}>{status}</Badge>;
}

/**
 * The provenance of a statement: whether the regulator said it, or we inferred
 * it. This distinction is a hard requirement of the project, so it is always
 * labelled rather than implied by styling alone.
 */
const ASSERTION_META: Record<
  AssertionBasis,
  { label: string; tone: "pass" | "info" | "warn" | "fail"; icon: LucideIcon; help: string }
> = {
  EXPLICIT_REQUIREMENT: {
    label: "Explicit requirement",
    tone: "pass",
    icon: BadgeCheck,
    help: "Stated by the regulator in the source document, with a verified verbatim quote.",
  },
  AI_INTERPRETATION: {
    label: "AI interpretation",
    tone: "info",
    icon: BrainCircuit,
    help: "A model-derived reading of the source. Not a regulatory obligation on its own.",
  },
  PROPOSED_CONTROL: {
    label: "Proposed control",
    tone: "warn",
    icon: Lightbulb,
    help: "A suggested banking control. The regulation does not demand this specific measure.",
  },
  UNVERIFIED_ASSUMPTION: {
    label: "Unverified assumption",
    tone: "fail",
    icon: CircleHelp,
    help: "No locatable support in the source text. Treat with caution and verify manually.",
  },
};

export function AssertionBadge({
  basis,
  showHelp = false,
}: {
  basis: AssertionBasis;
  showHelp?: boolean;
}) {
  const meta = ASSERTION_META[basis] ?? ASSERTION_META.UNVERIFIED_ASSUMPTION;
  const Icon = meta.icon;
  return (
    <span className="inline-flex flex-col gap-1">
      <Badge tone={meta.tone} className="w-fit">
        <Icon size={12} />
        {meta.label}
      </Badge>
      {showHelp && <span className="text-[11px] leading-snug text-slate-500">{meta.help}</span>}
    </span>
  );
}

/** Where a stored record came from. A demo fixture is always labelled as one. */
const PROVENANCE_META: Record<DataProvenance, { label: string; tone: "accent" | "info" | "muted" | "warn" }> = {
  LIVE_TINYFISH: { label: "Live · TinyFish", tone: "accent" },
  LIVE_GEMINI: { label: "Live · Gemini", tone: "info" },
  DETERMINISTIC: { label: "Deterministic", tone: "muted" },
  DEMO_FIXTURE: { label: "Demo fixture", tone: "warn" },
};

export function ProvenanceTag({ provenance }: { provenance: DataProvenance }) {
  const meta = PROVENANCE_META[provenance] ?? PROVENANCE_META.DETERMINISTIC;
  return (
    <Badge tone={meta.tone}>
      {provenance === "DEMO_FIXTURE" && <FlaskConical size={12} />}
      {meta.label}
    </Badge>
  );
}

/** Verbatim regulatory text. Styled so a quote is never read as our own prose. */
export function SourceQuote({
  quote,
  reference,
  className,
}: {
  quote: string | null | undefined;
  reference?: string | null;
  className?: string;
}) {
  if (!quote) {
    return (
      <p className={clsx("text-[12px] italic text-slate-500", className)}>
        No verbatim quote was recorded for this item.
      </p>
    );
  }
  return (
    <figure className={className}>
      <blockquote className="quote">“{quote}”</blockquote>
      {reference && (
        <figcaption className="mt-1 text-[11px] text-slate-500">
          Source reference: <span className="mono text-slate-400">{reference}</span>
        </figcaption>
      )}
    </figure>
  );
}

export function LiveDot({ active, label }: { active: boolean; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-slate-400">
      <span className="relative grid h-2.5 w-2.5 place-items-center">
        <span
          className={clsx(
            "h-2 w-2 rounded-full",
            active ? "bg-emerald-400" : "bg-slate-600",
          )}
        />
        {active && (
          <span className="absolute h-2 w-2 animate-pulse-ring rounded-full bg-emerald-400/70" />
        )}
      </span>
      {label ?? (active ? "Live" : "Offline")}
    </span>
  );
}

export function ConfidenceMeter({ value, label }: { value: number | null; label?: string }) {
  if (value === null || value === undefined) {
    return <span className="text-[11px] text-slate-500">no score</span>;
  }
  const percent = Math.round(Math.min(Math.max(value, 0), 1) * 100);
  const tone = percent >= 75 ? "bg-emerald-400" : percent >= 50 ? "bg-amber-400" : "bg-rose-400";
  return (
    <span className="inline-flex items-center gap-2" title={`${label ?? "Confidence"}: ${percent}%`}>
      <span className="h-1.5 w-14 overflow-hidden rounded-full bg-white/10">
        <span className={clsx("block h-full rounded-full", tone)} style={{ width: `${percent}%` }} />
      </span>
      <span className="mono text-slate-400">{percent}%</span>
    </span>
  );
}

/** Banner reminding the operator that the banking environment is simulated. */
export function SimulationNotice({ className }: { className?: string }) {
  return (
    <p
      className={clsx(
        "flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/[.06] px-3 py-2 text-[12px] leading-relaxed text-amber-200/90",
        className,
      )}
    >
      <FlaskConical size={14} className="mt-0.5 shrink-0" />
      <span>
        <strong className="font-semibold">Simulated environment.</strong> Every customer, account
        and transaction here is synthetic test data. No real banking system is contacted and no
        result is a compliance determination.
      </span>
    </p>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[.09em] text-slate-500">
      {children}
    </h3>
  );
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="text-[13px] text-slate-300">{children ?? "—"}</dd>
    </div>
  );
}

export function StreamIndicator({ connected }: { connected: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md border border-white/10 bg-white/[.03] px-2 py-1 text-[11px] text-slate-400"
      title={
        connected
          ? "Connected to the backend event stream"
          : "Event stream disconnected; it will reconnect automatically"
      }
    >
      <Radio size={12} className={connected ? "text-emerald-400" : "text-slate-600"} />
      {connected ? "Live events" : "Reconnecting"}
    </span>
  );
}
