/**
 * Regulatory document detail: metadata, clauses, extracted requirements,
 * version history and the old-versus-new clause comparison.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowLeft,
  Brain,
  ExternalLink,
  FileText,
  FileWarning,
  GitCompare,
  History,
  Layers,
  RefreshCw,
  Target,
} from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

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
import type { ClauseDiffEntry } from "../lib/types";

type Tab = "clauses" | "requirements" | "versions" | "text";

export default function RegulationDetail() {
  const { documentId } = useParams<{ documentId: string }>();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("clauses");
  const [compareAgainst, setCompareAgainst] = useState<string | undefined>(undefined);
  const [showComparison, setShowComparison] = useState(false);

  const document = useQuery({
    queryKey: ["regulation", documentId],
    queryFn: () => api.regulation(documentId!),
    enabled: Boolean(documentId),
  });

  const versions = useQuery({
    queryKey: ["regulationVersions", documentId],
    queryFn: () => api.regulationVersions(documentId!),
    enabled: Boolean(documentId),
  });

  const changes = useQuery({
    queryKey: ["changes", { document_id: documentId }],
    queryFn: () => api.changes({ document_id: documentId!, limit: 100 }),
    enabled: Boolean(documentId),
  });

  const comparison = useQuery({
    queryKey: ["comparison", documentId, compareAgainst],
    queryFn: () => api.compareRegulation(documentId!, compareAgainst),
    enabled: Boolean(documentId) && showComparison,
  });

  const extract = useMutation({
    mutationFn: (force: boolean) => api.extractDocuments([documentId!], force),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["regulation", documentId] }),
  });

  const analyse = useMutation({
    mutationFn: (comparisonTarget?: string) =>
      api.analyseDocument(documentId!, { compare_to_document_id: comparisonTarget }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["changes"] });
      queryClient.invalidateQueries({ queryKey: ["regulation", documentId] });
    },
  });

  const impact = useMutation({
    mutationFn: (changeIds: string[]) => api.analyseImpact(changeIds),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["impacts"] }),
  });

  if (document.isLoading) {
    return (
      <Card>
        <LoadingBlock label="Loading document" rows={6} />
      </Card>
    );
  }
  if (document.error || !document.data) {
    return <ErrorState error={document.error ?? "Document not found"} onRetry={() => document.refetch()} />;
  }

  const doc = document.data;
  const otherVersions = (versions.data ?? []).filter((version) => version.id !== doc.id);
  const requirements = changes.data?.items ?? [];
  const hasText = Boolean(doc.raw_text);

  return (
    <>
      <PageHeader
        icon={FileText}
        title={doc.title}
        subtitle={[
          doc.circular_number,
          doc.document_type.replace(/_/g, " "),
          `version ${doc.version}`,
          doc.is_current ? "current" : "superseded",
        ]
          .filter(Boolean)
          .join(" · ")}
        actions={
          <>
            <Link to="/regulations" className="btn-ghost">
              <ArrowLeft size={15} />
              Back
            </Link>
            <a href={doc.source_url} target="_blank" rel="noreferrer" className="btn-ghost">
              <ExternalLink size={15} />
              Official source
            </a>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => extract.mutate(true)}
              disabled={extract.isPending}
              title="Re-fetch the official document, bypassing any cache"
            >
              {extract.isPending ? <Spinner /> : <RefreshCw size={15} />}
              Re-extract
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => analyse.mutate(undefined)}
              disabled={analyse.isPending || !hasText}
              title={hasText ? "Extract requirements with Gemini" : "Extract the document first"}
            >
              {analyse.isPending ? <Spinner /> : <Brain size={15} />}
              Analyse requirements
            </button>
          </>
        }
      />

      {(extract.isError || analyse.isError || impact.isError) && (
        <div className="mb-5 space-y-3">
          {extract.isError && <ErrorState error={extract.error} />}
          {analyse.isError && <ErrorState error={analyse.error} />}
          {impact.isError && <ErrorState error={impact.error} />}
        </div>
      )}

      {doc.access_limitation && (
        <div className="mb-5 rounded-lg border border-amber-500/30 bg-amber-500/[.07] px-4 py-3">
          <p className="flex items-center gap-2 text-sm font-medium text-amber-200">
            <FileWarning size={15} />
            This document could not be retrieved
          </p>
          <p className="mt-1.5 text-[13px] leading-relaxed text-slate-300">
            {doc.access_limitation}
          </p>
          <p className="mt-2 text-[12px] text-slate-400">
            The limitation is recorded rather than substituted with generated text. Review the
            document manually at its official URL.
          </p>
        </div>
      )}

      <div className="space-y-5">
        {/* --- metadata --- */}
        <Card title="Document metadata">
          <dl className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <KeyValue label="Circular number">
              <span className="mono">{doc.circular_number ?? "not stated"}</span>
            </KeyValue>
            <KeyValue label="Published">
              <span className="mono">{doc.publication_date ?? "not stated"}</span>
            </KeyValue>
            <KeyValue label="Effective from">
              <span className="mono">{doc.effective_date ?? "not stated"}</span>
            </KeyValue>
            <KeyValue label="Status">
              <Badge tone={doc.status === "ANALYSED" ? "pass" : "info"}>
                {doc.status.replace(/_/g, " ")}
              </Badge>
            </KeyValue>
            <KeyValue label="Provenance">
              <ProvenanceTag provenance={doc.provenance} />
            </KeyValue>
            <KeyValue label="Extracted size">
              {doc.char_count ? `${doc.char_count.toLocaleString()} chars` : "—"}
            </KeyValue>
            <KeyValue label="Content hash">
              <span className="mono text-[11px]">
                {doc.content_hash ? `${doc.content_hash.slice(0, 16)}…` : "—"}
              </span>
            </KeyValue>
            <KeyValue label="Last fetched">
              {doc.fetched_at ? new Date(doc.fetched_at).toLocaleString() : "—"}
            </KeyValue>
          </dl>

          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <SectionLabel>Applicability (as stated by the document)</SectionLabel>
              {doc.applicability_text ? (
                <SourceQuote quote={doc.applicability_text} />
              ) : (
                <p className="text-[12px] italic text-slate-500">
                  The document does not contain an explicit applicability statement we could
                  locate. Entity scope has not been inferred.
                </p>
              )}
            </div>
            <div>
              <SectionLabel>Entity types named</SectionLabel>
              {doc.applicable_institution_types?.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {doc.applicable_institution_types.map((entity) => (
                    <Badge key={entity} tone="accent">
                      {entity.replace(/_/g, " ")}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="text-[12px] italic text-slate-500">
                  None named in the source text.
                </p>
              )}
              <SectionLabel>
                <span className="mt-4 block">Banking domains</span>
              </SectionLabel>
              <div className="flex flex-wrap gap-1.5">
                {(doc.banking_domains ?? []).map((domain) => (
                  <Badge key={domain}>{domain.replace(/_/g, " ")}</Badge>
                ))}
              </div>
            </div>
          </div>
        </Card>

        {/* --- tabs --- */}
        <div className="flex flex-wrap gap-1.5">
          {(
            [
              ["clauses", `Clauses (${doc.clauses.length})`, Layers],
              ["requirements", `Requirements (${requirements.length})`, Target],
              ["versions", `Versions (${versions.data?.length ?? 0})`, History],
              ["text", "Extracted text", FileText],
            ] as [Tab, string, typeof Layers][]
          ).map(([key, label, Icon]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={clsx(
                "inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-[12.5px] font-medium transition-colors",
                tab === key
                  ? "bg-accent/12 text-slate-100 ring-1 ring-inset ring-accent/25"
                  : "border border-white/10 bg-white/[.03] text-slate-400 hover:text-slate-200",
              )}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </div>

        {tab === "clauses" && (
          <Card title="Extracted clauses" bodyClassName="p-0">
            {doc.clauses.length ? (
              <ul className="divide-y divide-white/[.05]">
                {doc.clauses.map((clause) => (
                  <li key={clause.id} className="px-5 py-4">
                    <div className="mb-1.5 flex flex-wrap items-center gap-2">
                      <Badge tone="accent">{clause.clause_reference}</Badge>
                      {clause.heading && (
                        <span className="text-[12.5px] font-medium text-slate-300">
                          {clause.heading}
                        </span>
                      )}
                      <span className="mono ml-auto text-[10.5px] text-slate-600">
                        {clause.clause_hash.slice(0, 12)}
                      </span>
                    </div>
                    <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-slate-400">
                      {clause.text}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                icon={Layers}
                title="No clauses extracted"
                body={
                  hasText
                    ? "The document text is stored but could not be segmented into numbered clauses."
                    : "Extract the document first to retrieve and segment its official text."
                }
                action={
                  !hasText ? (
                    <button
                      type="button"
                      className="btn-primary mt-1"
                      onClick={() => extract.mutate(false)}
                      disabled={extract.isPending}
                    >
                      {extract.isPending ? <Spinner /> : <RefreshCw size={15} />}
                      Extract now
                    </button>
                  ) : undefined
                }
              />
            )}
          </Card>
        )}

        {tab === "requirements" && (
          <Card
            title="Extracted requirements"
            bodyClassName="p-0"
            actions={
              requirements.length > 0 && (
                <button
                  type="button"
                  className="btn-ghost text-[12px]"
                  onClick={() => impact.mutate(requirements.map((r) => r.id))}
                  disabled={impact.isPending}
                >
                  {impact.isPending ? <Spinner /> : <Target size={14} />}
                  Map all to CBS rules
                </button>
              )
            }
          >
            {changes.isLoading ? (
              <div className="p-5">
                <LoadingBlock label="Loading requirements" rows={3} />
              </div>
            ) : requirements.length ? (
              <ul className="divide-y divide-white/[.05]">
                {requirements.map((change) => (
                  <li key={change.id} className="px-5 py-4">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <Badge tone="accent">{change.requirement_code}</Badge>
                      <span className="text-[13px] font-medium text-slate-200">
                        {change.title}
                      </span>
                      <div className="ml-auto flex flex-wrap items-center gap-2">
                        <Badge>{change.change_type.replace(/_/g, " ")}</Badge>
                        <PriorityBadge priority={change.priority} />
                        <ReviewBadge status={change.review_status} />
                      </div>
                    </div>
                    <p className="mb-2.5 text-[12.5px] leading-relaxed text-slate-400">
                      {change.requirement_text}
                    </p>
                    <div className="mb-2.5">
                      <AssertionBadge basis={change.assertion_basis} showHelp />
                    </div>
                    <SourceQuote
                      quote={change.source_quote}
                      reference={change.source_reference}
                      className="mb-2.5"
                    />
                    {change.previous_text && (
                      <div className="mb-2.5">
                        <SectionLabel>Superseded wording</SectionLabel>
                        <p className="border-l-2 border-rose-500/40 bg-rose-500/[.05] px-3 py-2 font-mono text-[11.5px] leading-relaxed text-slate-400 line-through decoration-rose-400/40">
                          {change.previous_text}
                        </p>
                      </div>
                    )}
                    <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
                      <KeyValue label="Module">
                        {change.primary_banking_module.replace(/_/g, " ")}
                      </KeyValue>
                      <KeyValue label="Effective">
                        <span className="mono">{change.effective_date ?? "not stated"}</span>
                      </KeyValue>
                      <KeyValue label="Applies to">
                        {change.applicable_entities?.length
                          ? change.applicable_entities.join(", ")
                          : "not stated"}
                      </KeyValue>
                      <KeyValue label="Model confidence">
                        <ConfidenceMeter value={change.confidence} />
                      </KeyValue>
                    </dl>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                icon={Target}
                title="No requirements extracted yet"
                body="Run analysis to identify the obligations this document places on the bank. Every requirement is anchored to a verbatim source quote."
                action={
                  <button
                    type="button"
                    className="btn-primary mt-1"
                    onClick={() => analyse.mutate(undefined)}
                    disabled={analyse.isPending || !hasText}
                  >
                    {analyse.isPending ? <Spinner /> : <Brain size={15} />}
                    Analyse requirements
                  </button>
                }
              />
            )}
          </Card>
        )}

        {tab === "versions" && (
          <div className="space-y-5">
            <Card title="Version history" bodyClassName="p-0">
              {versions.data?.length ? (
                <div className="table-wrap">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Version</th>
                        <th>Current</th>
                        <th>Published</th>
                        <th>Size</th>
                        <th>Content hash</th>
                        <th>Fetched</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {versions.data.map((version) => (
                        <tr key={version.id}>
                          <td className="mono">v{version.version}</td>
                          <td>
                            {version.is_current ? (
                              <Badge tone="pass">current</Badge>
                            ) : (
                              <Badge>superseded</Badge>
                            )}
                          </td>
                          <td className="mono">{version.publication_date ?? "—"}</td>
                          <td className="mono">
                            {version.char_count ? version.char_count.toLocaleString() : "—"}
                          </td>
                          <td className="mono text-[11px] text-slate-500">
                            {version.content_hash ? `${version.content_hash.slice(0, 16)}…` : "—"}
                          </td>
                          <td>
                            {version.fetched_at
                              ? new Date(version.fetched_at).toLocaleString()
                              : "—"}
                          </td>
                          <td>
                            {version.id !== doc.id && (
                              <button
                                type="button"
                                className="btn-ghost text-[11.5px]"
                                onClick={() => {
                                  setCompareAgainst(version.id);
                                  setShowComparison(true);
                                }}
                              >
                                <GitCompare size={13} />
                                Compare
                              </button>
                            )}
                            {version.id === doc.id && (
                              <span className="text-[11px] text-slate-600">viewing</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  icon={History}
                  title="Only one version stored"
                  body="A re-extraction whose content hash differs creates a new version and retires the old one. Earlier versions are never overwritten."
                />
              )}
            </Card>

            {otherVersions.length > 0 && !showComparison && (
              <Card title="Old versus new comparison">
                <p className="text-[13px] text-slate-400">
                  Compare this version against an earlier one clause by clause. Matching is by the
                  regulator's own numbering, with a content-similarity fallback so a renumbered
                  provision reads as modified rather than as one deletion plus one addition.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <select
                    className="input w-56"
                    value={compareAgainst ?? ""}
                    onChange={(event) => setCompareAgainst(event.target.value || undefined)}
                  >
                    <option value="">Previous version (default)</option>
                    {otherVersions.map((version) => (
                      <option key={version.id} value={version.id}>
                        v{version.version}
                        {version.publication_date ? ` · ${version.publication_date}` : ""}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => setShowComparison(true)}
                  >
                    <GitCompare size={15} />
                    Compare versions
                  </button>
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => analyse.mutate(compareAgainst ?? otherVersions[0]?.id)}
                    disabled={analyse.isPending}
                    title="Ask Gemini to report only what materially changed"
                  >
                    {analyse.isPending ? <Spinner /> : <Brain size={15} />}
                    AI change analysis
                  </button>
                </div>
              </Card>
            )}

            {showComparison && (
              <Card
                title="Clause comparison"
                actions={
                  <button
                    type="button"
                    className="btn-ghost text-[12px]"
                    onClick={() => setShowComparison(false)}
                  >
                    Close
                  </button>
                }
                bodyClassName="p-0"
              >
                {comparison.isLoading ? (
                  <div className="p-5">
                    <LoadingBlock label="Comparing versions" rows={4} />
                  </div>
                ) : comparison.error ? (
                  <div className="p-5">
                    <ErrorState error={comparison.error} />
                  </div>
                ) : comparison.data ? (
                  <>
                    <div className="flex flex-wrap items-center gap-3 border-b border-white/[.06] px-5 py-3 text-[12px]">
                      <span className="text-slate-400">
                        v{comparison.data.previous.version} → v{comparison.data.current.version}
                      </span>
                      <Badge tone="warn">{comparison.data.modified} modified</Badge>
                      <Badge tone="pass">{comparison.data.added} added</Badge>
                      <Badge tone="fail">{comparison.data.removed} removed</Badge>
                      <Badge>{comparison.data.unchanged} unchanged</Badge>
                    </div>
                    <ul className="divide-y divide-white/[.05]">
                      {comparison.data.entries
                        .filter((entry) => entry.change !== "UNCHANGED")
                        .map((entry, index) => (
                          <DiffRow key={`${entry.clause_reference}-${index}`} entry={entry} />
                        ))}
                    </ul>
                    {comparison.data.modified +
                      comparison.data.added +
                      comparison.data.removed ===
                      0 && (
                      <EmptyState
                        icon={GitCompare}
                        title="No material differences"
                        body="Every clause matched with identical content. The regulator republished the document without changing its obligations."
                      />
                    )}
                  </>
                ) : null}
              </Card>
            )}
          </div>
        )}

        {tab === "text" && (
          <Card title="Extracted text (as retrieved by TinyFish Fetch)">
            {doc.raw_text ? (
              <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-lg border border-white/[.07] bg-navy-950/60 p-4 font-mono text-[11.5px] leading-relaxed text-slate-400">
                {doc.raw_text}
              </pre>
            ) : (
              <EmptyState
                icon={FileText}
                title="No text stored"
                body="This document has been discovered but not yet retrieved."
              />
            )}
          </Card>
        )}
      </div>
    </>
  );
}

function DiffRow({ entry }: { entry: ClauseDiffEntry }) {
  const tone =
    entry.change === "ADDED"
      ? { badge: "pass" as const, border: "border-emerald-500/40 bg-emerald-500/[.05]" }
      : entry.change === "REMOVED"
        ? { badge: "fail" as const, border: "border-rose-500/40 bg-rose-500/[.05]" }
        : { badge: "warn" as const, border: "border-amber-500/40 bg-amber-500/[.05]" };

  return (
    <li className="px-5 py-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge tone={tone.badge}>{entry.change}</Badge>
        <span className="mono text-[12px] text-slate-300">{entry.clause_reference}</span>
        {entry.heading && (
          <span className="text-[12px] text-slate-400">· {entry.heading}</span>
        )}
        {entry.similarity !== null && entry.change === "MODIFIED" && (
          <span className="mono ml-auto text-[11px] text-slate-500">
            {Math.round(entry.similarity * 100)}% similar
          </span>
        )}
      </div>
      <div className="grid gap-2 lg:grid-cols-2">
        {entry.previous_text && (
          <div>
            <SectionLabel>Previous</SectionLabel>
            <p className="border-l-2 border-rose-500/40 bg-rose-500/[.05] px-3 py-2 font-mono text-[11.5px] leading-relaxed text-slate-400">
              {entry.previous_text}
            </p>
          </div>
        )}
        {entry.current_text && (
          <div>
            <SectionLabel>Current</SectionLabel>
            <p className={`border-l-2 px-3 py-2 font-mono text-[11.5px] leading-relaxed text-slate-300 ${tone.border}`}>
              {entry.current_text}
            </p>
          </div>
        )}
      </div>
    </li>
  );
}
