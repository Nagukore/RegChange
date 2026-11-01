/** Persistent chrome: sidebar navigation, live-stream indicator, status rail. */
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity,
  BookOpenText,
  CircleDot,
  FlaskConical,
  Landmark,
  LayoutDashboard,
  Settings as SettingsIcon,
  ShieldCheck,
  Target,
  type LucideIcon,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { api } from "../lib/api";
import { useEventStream } from "../lib/useEventStream";
import { StreamIndicator } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  hint: string;
}

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, hint: "Executive overview" },
  { to: "/regulations", label: "Regulatory Explorer", icon: BookOpenText, hint: "Sources, documents, versions" },
  { to: "/impact", label: "Impact Analysis", icon: Target, hint: "Requirement to CBS rule" },
  { to: "/tests", label: "Test Management", icon: ShieldCheck, hint: "Generated scenarios and approval" },
  { to: "/executions", label: "Execution Monitor", icon: Activity, hint: "Live runs and evidence" },
  { to: "/simulator", label: "Banking Simulator", icon: Landmark, hint: "Simulated core banking" },
  { to: "/settings", label: "Settings", icon: SettingsIcon, hint: "Configuration and health" },
];

export default function AppShell() {
  const { connected } = useEventStream();
  const { data: capabilities } = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
    staleTime: 60_000,
  });

  const tinyfishLive = capabilities?.integrations?.tinyfish_search?.configured ?? false;
  const geminiLive = capabilities?.integrations?.gemini?.configured ?? false;

  return (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-screen w-[248px] shrink-0 flex-col border-r border-white/[.06] bg-navy-900/60 backdrop-blur lg:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-accent/15 text-accent-soft ring-1 ring-accent/25">
            <ShieldCheck size={18} />
          </span>
          <div className="leading-tight">
            <p className="text-[13px] font-semibold tracking-tight text-slate-100">
              RegChange Sentinel
            </p>
            <p className="text-[10.5px] text-slate-500">Compliance test automation</p>
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 px-3 py-2">
          {NAV.map(({ to, label, icon: Icon, hint }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              title={hint}
              className={({ isActive }) =>
                clsx(
                  "group flex items-center gap-3 rounded-lg px-3 py-2.5 text-[13px] font-medium transition-colors",
                  isActive
                    ? "bg-accent/12 text-slate-100 ring-1 ring-inset ring-accent/25"
                    : "text-slate-400 hover:bg-white/[.04] hover:text-slate-200",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon
                    size={16}
                    className={clsx(
                      "shrink-0",
                      isActive ? "text-accent-soft" : "text-slate-500 group-hover:text-slate-300",
                    )}
                  />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Integration status is always visible: the operator should never have
            to guess whether a result came from a live service. */}
        <div className="space-y-2 border-t border-white/[.06] px-4 py-4">
          <p className="text-[10px] font-semibold uppercase tracking-[.1em] text-slate-600">
            Integrations
          </p>
          <IntegrationPill label="TinyFish" live={tinyfishLive} />
          <IntegrationPill label="Gemini" live={geminiLive} />
          <IntegrationPill
            label={capabilities?.database.dialect === "postgresql" ? "Supabase PG" : "SQLite (dev)"}
            live={capabilities?.database.configured ?? false}
            neutralWhenOff
          />
          <div className="pt-1">
            <StreamIndicator connected={connected} />
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile / narrow navigation */}
        <div className="flex items-center gap-2 overflow-x-auto border-b border-white/[.06] bg-navy-900/70 px-4 py-2.5 lg:hidden">
          <span className="mr-1 grid h-7 w-7 shrink-0 place-items-center rounded-md bg-accent/15 text-accent-soft">
            <ShieldCheck size={14} />
          </span>
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                clsx(
                  "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12px] font-medium",
                  isActive ? "bg-accent/15 text-slate-100" : "text-slate-400",
                )
              }
            >
              <Icon size={13} />
              {label}
            </NavLink>
          ))}
        </div>

        <main className="min-w-0 flex-1 px-5 py-6 sm:px-7 lg:px-9">
          <div className="mx-auto max-w-[1500px]">
            <Outlet />
          </div>
        </main>

        <footer className="border-t border-white/[.06] px-5 py-3 text-[11px] text-slate-600 sm:px-7 lg:px-9">
          <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-4 gap-y-1">
            <span className="inline-flex items-center gap-1.5">
              <FlaskConical size={12} />
              Banking operations are simulated. Nothing here is a compliance determination.
            </span>
            <span className="text-slate-700">·</span>
            <span>
              Regulatory authority: {capabilities?.regulatory_authority ?? "—"} · Institution type:{" "}
              {capabilities?.institution_type ?? "—"}
            </span>
          </div>
        </footer>
      </div>
    </div>
  );
}

function IntegrationPill({
  label,
  live,
  neutralWhenOff = false,
}: {
  label: string;
  live: boolean;
  neutralWhenOff?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2 text-[11.5px]">
      <span className="text-slate-400">{label}</span>
      <span
        className={clsx(
          "inline-flex items-center gap-1 font-medium",
          live ? "text-emerald-400" : neutralWhenOff ? "text-slate-500" : "text-amber-400",
        )}
      >
        <CircleDot size={11} />
        {live ? "configured" : neutralWhenOff ? "local" : "not set"}
      </span>
    </div>
  );
}
