/** Screen 2 -- Regulatory Explorer: sources, documents and discovery runs. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpenText,
  Brain,
  Download,
  ExternalLink,
  FileWarning,
  RefreshCw,
  Search,
} from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  ProvenanceTag,
  Spinner,
} from "../components/ui";
import { api } from "../lib/api";
import type { RegulatoryDocument } from "../lib/types";

const STATUS_TONE: Record<string, "pass" | "info" | "warn" | "fail" | "muted"> = {
  ANALYSED: "pass",
  EXTRACTED: "info",
  FETCHING: "info",
  DISCOVERED: "muted",
  FETCH_FAILED: "fail",
  INACCESSIBLE: "warn",
};

export default function RegulatoryExplorer() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const statusFilter = searchParams.get("status") ?? "";
  const domainFilter = searchParams.get("banking_domain") ?? "";
  const currentOnly = searchParams.get("current_only") !== "false";

  const documents = useQuery({
    queryKey: ["regulations", { query, statusFilter, domainFilter, currentOnly }],
    queryFn: () =>
      api.regulations({
        q: query || undefined,
        status: statusFilter || undefined,
        banking_domain: domainFilter || undefined,
        current_only: currentOnly,
        limit: 100,
      }),
  });

  const runs = useQuery({ queryKey: ["discovery"], queryFn: () => api.discoveryRuns(5) });

  const discovery = useMutation({
    mutationFn: () => api.startDiscovery({ max_results_per_query: 10 }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["discovery"] });
    },
  });

  const extract = useMutation({
    mutationFn: (ids: string[]) => api.extractDocuments(ids),
    onSuccess: () => {
      setSelected(new Set());
      queryClient.invalidateQueries({ queryKey: ["regulations"] });
    },
  });

  const toggle = (id: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    value ? next.set(key, value) : next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const items = documents.data?.items ?? [];
  const extractable = items.filter((d) => selected.has(d.id));

  return (
    <>
      <PageHeader
        icon={BookOpenText}
        title="Regulatory Explorer"
        subtitle="Documents discovered from official regulator feeds and date-bounded TinyFish Search, then retrieved and clause-segmented via TinyFish Fetch."
        actions={
          <>
            {selected.size > 0 && (
              <button
                type="button"
                className="btn-ghost"
                onClick={() => extract.mutate(Array.from(selected))}
                disabled={extract.isPending}
              >
                {extract.isPending ? <Spinner /> : <Download size={15} />}
                Extract {selected.size} selected
              </button>
            )}
            <button
              type="button"
              className="btn-primary"
              onClick={() => discovery.mutate()}
              disabled={discovery.isPending}
            >
              {discovery.isPending ? <Spinner /> : <Search size={15} />}
              Discover updates
            </button>
          </>
        }
      />

      {(discovery.isError || extract.isError) && (
        <div className="mb-5 space-y-3">
          {discovery.isError && <ErrorState error={discovery.error} />}
          {extract.isError && <ErrorState error={extract.error} />}
        </div>
      )}

      <div className="space-y-5">
        {/* --- discovery runs --- */}
        <Card
          title="Discovery runs"
          actions={
            <button
              type="button"
              className="text-slate-500 hover:text-slate-300"
              onClick={() => runs.refetch()}
              title="Refresh"
            >
              <RefreshCw size={14} />
            </button>
          }
          bodyClassName="p-0"
        >
          {runs.isLoading ? (
            <div className="p-5">
              <LoadingBlock label="Loading runs" rows={2} />
            </div>
          ) : runs.data?.length ? (
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Status</th>
                    <th>Checked</th>
                    <th className="text-right">Found</th>
                    <th className="text-right">New</th>
                    <th className="text-right">Duplicate</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.data.map((run) => (
                    <tr key={run.id}>
                      <td className="whitespace-nowrap">
                        {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
                      </td>
                      <td>
                        <Badge
                          tone={
                            run.status === "COMPLETED"
                              ? "pass"
                              : run.status === "FAILED"
                                ? "fail"
                                : run.status === "PARTIAL"
                                  ? "warn"
                                  : "info"
                          }
                        >
                          {run.status}
                        </Badge>
                        {run.error_message && (
                          <p className="mt-1 max-w-sm text-[11px] text-rose-300/80">
                            {run.error_message}
                          </p>
                        )}
                      </td>
                      <td className="max-w-xs">
                        <span className="text-[12px] text-slate-400">
                          {(run.queries ?? []).length} sources
                        </span>
                      </td>
                      <td className="text-right mono">{run.documents_found}</td>
                      <td className="text-right mono text-emerald-300">{run.documents_new}</td>
                      <td className="text-right mono text-slate-500">
                        {run.documents_duplicate}
                      </td>
                      <td>
                        <ProvenanceTag provenance={run.provenance} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={Search}
              title="No discovery runs yet"
              body="A run issues domain-scoped searches against official regulator sites and records every document it finds."
            />
          )}
        </Card>

        {/* --- documents --- */}
        <Card
          title={`Regulatory documents${documents.data ? ` (${documents.data.total})` : ""}`}
          bodyClassName="p-0"
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="input w-48"
                placeholder="Search title or circular no."
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <select
                className="input w-40"
                value={statusFilter}
                onChange={(event) => setFilter("status", event.target.value)}
              >
                <option value="">All statuses</option>
                <option value="DISCOVERED">Discovered</option>
                <option value="EXTRACTED">Extracted</option>
                <option value="ANALYSED">Analysed</option>
                <option value="FETCH_FAILED">Fetch failed</option>
              </select>
              <select
                className="input w-40"
                value={domainFilter}
                onChange={(event) => setFilter("banking_domain", event.target.value)}
              >
                <option value="">All domains</option>
                <option value="KYC_AML">KYC &amp; AML</option>
                <option value="CUSTOMER_ONBOARDING">Customer onboarding</option>
                <option value="ACCOUNT_MANAGEMENT">Account management</option>
                <option value="TRANSACTIONS">Transactions</option>
              </select>
              <label className="flex items-center gap-1.5 text-[11.5px] text-slate-400">
                <input
                  type="checkbox"
                  checked={currentOnly}
                  onChange={(event) =>
                    setFilter("current_only", event.target.checked ? "" : "false")
                  }
                  className="accent-accent"
                />
                Current versions only
              </label>
            </div>
          }
        >
          {documents.error ? (
            <div className="p-5">
              <ErrorState error={documents.error} onRetry={() => documents.refetch()} />
            </div>
          ) : documents.isLoading ? (
            <div className="p-5">
              <LoadingBlock label="Loading documents" rows={4} />
            </div>
          ) : items.length ? (
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th className="w-9">
                      <input
                        type="checkbox"
                        className="accent-accent"
                        checked={selected.size > 0 && selected.size === items.length}
                        onChange={(event) =>
                          setSelected(
                            event.target.checked ? new Set(items.map((d) => d.id)) : new Set(),
                          )
                        }
                        aria-label="Select all documents"
                      />
                    </th>
                    <th>Document</th>
                    <th>Type</th>
                    <th>Published</th>
                    <th>Effective</th>
                    <th>Applies to</th>
                    <th>Ver.</th>
                    <th>Status</th>
                    <th>Provenance</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((document) => (
                    <DocumentRow
                      key={document.id}
                      document={document}
                      checked={selected.has(document.id)}
                      onToggle={() => toggle(document.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              icon={BookOpenText}
              title="No documents stored yet"
              body={
                <>
                  Click <strong>Discover updates</strong> to search official regulator sites. Each
                  result is stored with its source URL, then retrieved and segmented into
                  citable clauses.
                </>
              }
            />
          )}
        </Card>

        {extractable.length > 0 && (
          <Card title="Next step">
            <p className="text-[13px] text-slate-400">
              {extractable.length} document(s) selected. Extraction retrieves the official text
              via TinyFish Fetch and segments it into clauses using the regulator's own
              numbering. After that, run analysis from the document page to produce
              source-anchored requirements.
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                className="btn-primary"
                onClick={() => extract.mutate(Array.from(selected))}
                disabled={extract.isPending}
              >
                {extract.isPending ? <Spinner /> : <Download size={15} />}
                Extract selected
              </button>
              <button type="button" className="btn-ghost" onClick={() => setSelected(new Set())}>
                Clear selection
              </button>
            </div>
          </Card>
        )}
      </div>
    </>
  );
}

function DocumentRow({
  document,
  checked,
  onToggle,
}: {
  document: RegulatoryDocument;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <tr>
      <td>
        <input
          type="checkbox"
          className="accent-accent"
          checked={checked}
          onChange={onToggle}
          aria-label={`Select ${document.title}`}
        />
      </td>
      <td className="max-w-md">
        <Link
          to={`/regulations/${document.id}`}
          className="font-medium text-slate-200 hover:text-accent-soft"
        >
          {document.title}
        </Link>
        {document.circular_number && (
          <p className="mono mt-0.5 text-slate-500">{document.circular_number}</p>
        )}
        {document.access_limitation && (
          <p className="mt-1 flex items-start gap-1.5 text-[11px] leading-snug text-amber-300/90">
            <FileWarning size={12} className="mt-0.5 shrink-0" />
            {document.access_limitation}
          </p>
        )}
      </td>
      <td className="whitespace-nowrap text-[12px]">
        {document.document_type.replace(/_/g, " ")}
      </td>
      <td className="whitespace-nowrap mono">{document.publication_date ?? "—"}</td>
      <td className="whitespace-nowrap mono">{document.effective_date ?? "—"}</td>
      <td className="max-w-[160px]">
        {document.applicable_institution_types?.length ? (
          <div className="flex flex-wrap gap-1">
            {document.applicable_institution_types.map((entity) => (
              <Badge key={entity} tone="accent">
                {entity.replace(/_/g, " ")}
              </Badge>
            ))}
          </div>
        ) : (
          <span className="text-[11px] italic text-slate-500">not stated</span>
        )}
      </td>
      <td className="mono">
        v{document.version}
        {!document.is_current && (
          <span className="ml-1 text-[10px] text-slate-500">(superseded)</span>
        )}
      </td>
      <td>
        <Badge tone={STATUS_TONE[document.status] ?? "muted"}>
          {document.status.replace(/_/g, " ")}
        </Badge>
      </td>
      <td>
        <ProvenanceTag provenance={document.provenance} />
      </td>
      <td className="whitespace-nowrap">
        <div className="flex items-center gap-1.5">
          <Link
            to={`/regulations/${document.id}`}
            className="rounded p-1.5 text-slate-500 hover:bg-white/[.06] hover:text-accent-soft"
            title="Open document"
          >
            <Brain size={14} />
          </Link>
          <a
            href={document.source_url}
            target="_blank"
            rel="noreferrer"
            className="rounded p-1.5 text-slate-500 hover:bg-white/[.06] hover:text-slate-300"
            title="Open the official source"
          >
            <ExternalLink size={14} />
          </a>
        </div>
      </td>
    </tr>
  );
}
