import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, Check, FlaskConical, Pencil, RefreshCw, Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { api, post } from "../api";
import PdfViewer, { type Highlight } from "../components/PdfViewer";
import {
  AlertBadge, Badge, Button, Card, Confidence, ErrorBox, Grounded, Loading, Select, StatusBadge, Tabs, Td, Th,
} from "../components/ui";
import { GROUP_LABELS, LANG, fmtDate, fmtDateTime, fmtMoney, fmtMs, fmtNum, fmtUsd, fmtValue, label } from "../format";
import type { DocDetail, FieldResult } from "../types";

type Tab = "fields" | "facts" | "pages" | "provenance" | "history";

export default function DocumentDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["document", id],
    queryFn: () => api<DocDetail>(`/api/documents/${id}`),
    refetchInterval: (query) => (["queued", "processing"].includes(query.state.data?.document.status ?? "") ? 2000 : false),
  });
  const [tab, setTab] = useState<Tab>("fields");
  const [page, setPage] = useState(1);
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [preset, setPreset] = useState("cascade");
  const refresh = () => qc.invalidateQueries({ queryKey: ["document", id] });
  const reprocess = useMutation({ mutationFn: () => post(`/api/documents/${id}/reprocess`, { preset }), onSuccess: refresh });
  const approveAll = useMutation({ mutationFn: () => post(`/api/documents/${id}/approve-all`), onSuccess: refresh });
  const promote = useMutation({ mutationFn: () => post<{ labeled_fields: string[] }>(`/api/eval/promote/${id}`) });

  const pageSizes = useMemo(() => Object.fromEntries((q.data?.pages ?? []).map((p) => [p.page_no, { width: p.width, height: p.height }])), [q.data]);

  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const doc = d.document;

  const focus = (pageNo: number | null, boxes: number[][]) => {
    if (!pageNo) return;
    setPage(pageNo);
    setHighlight({ page: pageNo, boxes });
  };

  return (
    <div className="flex h-[calc(100vh-7.5rem)] flex-col">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/documents" className="mb-1 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700"><ArrowLeft className="size-3" /> Documents</Link>
          <h1 className="truncate text-xl font-semibold text-ink">{doc.title || doc.filename}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <StatusBadge status={doc.status} />
            {doc.agreement_status && <StatusBadge status={doc.agreement_status} />}
            <AlertBadge level={doc.alert} />
            <span>{doc.filename}</span>·<span>{doc.page_count ?? "?"} pages</span>·<span>{LANG[doc.language ?? "unknown"]}</span>
            {doc.duplicate_of && <><span>·</span><Link className="text-brand-700 underline" to={`/documents/${doc.duplicate_of}`}>duplicate of an earlier upload</Link></>}
          </div>
          {doc.error && <div className="mt-2"><ErrorBox error={doc.error} /></div>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={preset} onChange={setPreset} options={["cascade", "fast", "strong", "long-context", "retrieval-only"].map((p) => ({ value: p, label: p }))} />
          <Button onClick={() => reprocess.mutate()} disabled={reprocess.isPending}><RefreshCw className="size-4" /> Re-extract</Button>
          <Button onClick={() => approveAll.mutate()} disabled={!doc.needs_review}><Check className="size-4" /> Approve all</Button>
          <Button onClick={() => promote.mutate()} title="Reviewed fields become golden labels"><FlaskConical className="size-4" /> Add to golden set</Button>
        </div>
      </div>
      {promote.data && <div className="mb-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">Added to the “reviewed” golden set with {promote.data.labeled_fields.length} labeled field(s).</div>}
      {promote.error && <div className="mb-3"><ErrorBox error={promote.error} /></div>}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div className="min-h-[480px] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <PdfViewer url={`/api/documents/${id}/file`} page={page} onPageChange={(p) => { setPage(p); }} highlight={highlight} pageSizes={pageSizes} />
        </div>
        <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="px-4 pt-2">
            <Tabs value={tab} onChange={setTab} tabs={[
              { id: "fields", label: <>Fields {d.fields.some((f) => f.needs_review && !f.review) && <span className="ml-1 rounded-full bg-amber-100 px-1.5 text-xs text-amber-800">{d.fields.filter((f) => f.needs_review && !f.review).length}</span>}</> },
              { id: "facts", label: "Facts" }, { id: "pages", label: "Pages" }, { id: "provenance", label: "Provenance" }, { id: "history", label: "History" },
            ]} />
          </div>
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto p-4">
            {!d.extraction && <div className="text-sm text-slate-500">{["queued", "processing"].includes(doc.status) ? "Processing… fields appear here when extraction finishes." : "No extraction yet."}</div>}
            {d.extraction && tab === "fields" && <FieldsPanel doc={d} onFocus={focus} onChange={refresh} />}
            {d.extraction && tab === "facts" && <FactsPanel d={d} />}
            {tab === "pages" && <PagesPanel d={d} onOpen={(p) => { setPage(p); setHighlight(null); }} />}
            {d.extraction && tab === "provenance" && <ProvenancePanel d={d} />}
            {tab === "history" && <HistoryPanel d={d} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function FieldsPanel({ doc, onFocus, onChange }: { doc: DocDetail; onFocus: (p: number | null, boxes: number[][]) => void; onChange: () => void }) {
  const groups = ["parties", "term", "commercial", "law"];
  const warnings = doc.extraction?.warnings ?? [];
  return (
    <div className="space-y-5">
      {warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <div className="mb-1 font-semibold">Consistency checks</div>
          <ul className="list-disc space-y-0.5 pl-4">{warnings.map((w, i) => <li key={i}>{w.message}</li>)}</ul>
        </div>
      )}
      {groups.map((g) => {
        const fields = doc.fields.filter((f) => f.group === g);
        if (!fields.length) return null;
        const sel = doc.extraction?.selected_pages?.[g];
        return (
          <div key={g}>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{GROUP_LABELS[g]}</h3>
              {sel && <span className="text-[11px] text-slate-400" title={Object.entries(sel.reasons).map(([p, r]) => `p.${p}: ${r}`).join("\n")}>
                {sel.escalated ? <Badge tone="violet"><Sparkles className="size-3" /> escalated to strong tier</Badge> : null}{" "}
                read {sel.pages.length === doc.document.page_count ? "all pages" : `${sel.pages.length} of ${doc.document.page_count} pages`}
              </span>}
            </div>
            <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {fields.map((f) => <FieldRow key={f.field} docId={doc.document.id} f={f} onFocus={onFocus} onChange={onChange} />)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FieldRow({ docId, f, onFocus, onChange }: { docId: string; f: FieldResult; onFocus: (p: number | null, boxes: number[][]) => void; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const value = f.effective_value;
  const review = useMutation({
    mutationFn: (body: { action: string; value?: unknown }) => post(`/api/documents/${docId}/review`, { field: f.field, ...body }),
    onSuccess: () => { setEditing(false); onChange(); },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });
  useEffect(() => { setDraft(JSON.stringify(value ?? null, null, 1)); }, [value]);
  const save = () => {
    try {
      review.mutate({ action: "correct", value: JSON.parse(draft) });
    } catch {
      setError("Not valid JSON — e.g. \"2025-01-31\", 60, true, {\"amount\": 60, \"unit\": \"days\"}");
    }
  };
  const ev = f.evidence.filter((e) => e.quote || e.page);
  return (
    <div className={clsx("px-3 py-2.5", f.needs_review && !f.review && "bg-amber-50/60")}>
      <div className="flex items-start gap-3">
        <div className="w-40 shrink-0">
          <div className="text-xs font-medium text-slate-700">{label(f.field)}</div>
          <div className="mt-1 flex items-center gap-1.5">
            <Grounded value={f.grounded} />
            <Confidence value={f.review ? 1 : f.confidence} compact />
            {f.tier === "strong" && <Badge tone="violet">strong</Badge>}
          </div>
        </div>
        <div className="min-w-0 flex-1">
          {!editing ? (
            <div className={clsx("text-sm", value === null || value === undefined || (Array.isArray(value) && !value.length) ? "text-slate-400" : "text-ink")}>
              {f.field === "line_items" && Array.isArray(value) && value.length ? (
                <table className="mt-0.5 w-full text-xs">
                  <tbody>{(value as Record<string, unknown>[]).map((li, i) => (
                    <tr key={i} className="border-b border-slate-100 last:border-0">
                      <td className="py-0.5 pr-2">{String(li.description)}</td>
                      <td className="tabular py-0.5 pr-2 text-right text-slate-500">{fmtNum(li.quantity as number, 2)} ×</td>
                      <td className="tabular py-0.5 pr-2 text-right text-slate-500">{fmtNum(li.unit_price as number, 2)}</td>
                      <td className="tabular py-0.5 text-right">{fmtNum(li.amount as number, 2)}</td>
                    </tr>))}</tbody>
                </table>
              ) : fmtValue(f.field, value)}
              {f.field === "effective_date" && f.meta?.basis === "last_signature" && <span className="ml-2 text-xs text-slate-500">(date of the last signature)</span>}
              {f.field === "contract_value" && typeof f.meta?.period === "string" && value != null && <span className="ml-2 text-xs text-slate-500">{String(f.meta.period).replace("_", " ")}</span>}
              {f.review && <Badge tone={f.review.action === "correct" ? "blue" : "green"} className="ml-2">{f.review.action === "correct" ? "corrected" : "approved"}</Badge>}
            </div>
          ) : (
            <div className="space-y-1.5">
              <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={Math.min(8, draft.split("\n").length + 1)}
                className="w-full rounded-md border border-slate-300 p-1.5 font-mono text-xs" />
              {error && <div className="text-xs text-rose-600">{error}</div>}
              <div className="flex gap-1.5">
                <Button size="sm" variant="primary" onClick={save}><Check className="size-3" /> Save correction</Button>
                <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setError(null); }}><X className="size-3" /> Cancel</Button>
              </div>
            </div>
          )}
          {ev.map((e, i) => (
            <button key={i} onClick={() => onFocus(e.page, e.boxes)} disabled={!e.page}
              className="mt-1.5 block w-full rounded-md border-l-2 border-brand-300 bg-slate-50 px-2 py-1 text-left text-xs text-slate-600 hover:bg-brand-50">
              {e.quote ? <>“{e.quote.length > 220 ? e.quote.slice(0, 220) + "…" : e.quote}”</> : <span className="text-slate-400">no quote</span>}
              {e.page && <span className="ml-1.5 font-medium text-brand-700">p.{e.page}</span>}
              {e.page_corrected_from && <span className="ml-1 text-amber-700">(model cited p.{e.page_corrected_from})</span>}
            </button>
          ))}
          {f.flags.length > 0 && <div className="mt-1.5 flex flex-wrap gap-1">{f.flags.map((fl, i) => <Badge key={i} tone="amber">{fl}</Badge>)}</div>}
        </div>
        {!editing && (
          <div className="flex shrink-0 gap-1">
            {f.needs_review && !f.review && <Button size="sm" onClick={() => review.mutate({ action: "approve" })} title="The value is right"><Check className="size-3" /></Button>}
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)} title="Correct the value"><Pencil className="size-3" /></Button>
          </div>
        )}
      </div>
    </div>
  );
}

function FactsPanel({ d }: { d: DocDetail }) {
  const f = d.facts;
  const rows: [string, string][] = [
    ["Agreement status", String(f.status ?? "—")],
    ["Executed", fmtValue("is_executed", f.is_executed)],
    ["Effective date", fmtDate(f.effective_date as string)],
    ["Term type", fmtValue("expiration_type", f.expiration_type)],
    ["Initial term ends", fmtDate(f.initial_term_end as string)],
    ["Current term ends", fmtDate(f.current_term_end as string)],
    ["Auto-renewal", fmtValue("auto_renewal", f.auto_renewal)],
    ["Renewal term", f.renewal_term_months ? `${f.renewal_term_months} months` : "—"],
    ["Notice to prevent renewal", f.non_renewal_notice_days ? `${f.non_renewal_notice_days} days` : "—"],
    ["Next cancel-by date", fmtDate(f.next_notice_deadline as string)],
    ["Termination for convenience", fmtValue("termination_for_convenience", f.termination_for_convenience)],
    ["Contract value", fmtMoney(f.contract_value ?? null, f.value_period as string)],
    ["Payment terms", f.payment_terms_days ? `${f.payment_terms_days} days` : "—"],
    ["Governing law", String(f.governing_law ?? "—")],
  ];
  return (
    <div>
      <p className="mb-3 text-xs text-slate-500">Derived by code from the extracted values and any human corrections — the model never computes dates.</p>
      <dl className="divide-y divide-slate-100 rounded-lg border border-slate-200">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4 px-3 py-2 text-sm"><dt className="text-slate-500">{k}</dt><dd className="tabular text-right font-medium text-ink">{v}</dd></div>
        ))}
      </dl>
      {f.alert ? <div className="mt-3 text-sm">Alert: <AlertBadge level={f.alert as string} /> {f.alert_days !== null && <span className="text-slate-500">({String(f.alert_days)} days)</span>}</div> : null}
    </div>
  );
}

function PagesPanel({ d, onOpen }: { d: DocDetail; onOpen: (p: number) => void }) {
  const tone = { text_layer: "gray", ocr: "blue", vlm: "violet" } as const;
  return (
    <table className="w-full">
      <thead><tr className="border-b border-slate-100"><Th>Page</Th><Th>Route</Th><Th>Why</Th><Th>OCR</Th></tr></thead>
      <tbody className="divide-y divide-slate-100">
        {d.pages.map((p) => (
          <tr key={p.page_no} className="cursor-pointer hover:bg-slate-50" onClick={() => onOpen(p.page_no)}>
            <Td className="tabular">{p.page_no}</Td>
            <Td><Badge tone={tone[p.route as keyof typeof tone] ?? "gray"}>{p.route === "text_layer" ? "text layer" : p.route}</Badge>
              {p.has_table && <Badge className="ml-1">table</Badge>}{p.struck_words > 0 && <Badge tone="amber" className="ml-1">{p.struck_words} struck</Badge>}</Td>
            <Td className="text-xs text-slate-600">{p.route_reason}</Td>
            <Td className="tabular text-xs">{p.ocr_confidence != null ? `${Math.round(p.ocr_confidence * 100)}%` : "—"}</Td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ProvenancePanel({ d }: { d: DocDetail }) {
  const e = d.extraction!;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 text-sm">
        {([
          ["Mode", e.mode.replace("_", " ")], ["Model calls", e.llm_calls], ["Tokens", `${fmtNum(e.prompt_tokens)} in · ${fmtNum(e.completion_tokens)} out`],
          ["Cost", fmtUsd(e.cost_usd)], ["Latency", fmtMs(e.latency_ms)], ["Escalated", e.escalated_groups.join(", ") || "—"],
          ["Pipeline", e.pipeline_version], ["Schema", e.schema_version], ["Prompt", e.prompt_version], ["Extracted", fmtDateTime(e.created_at)],
        ] as [string, React.ReactNode][]).map(([k, v]) => (
          <div key={k} className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-[11px] uppercase tracking-wide text-slate-500">{k}</div><div className="font-medium text-ink">{v}</div></div>
        ))}
      </div>
      {e.reused_from && <div className="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-800">Reused from an identical file processed earlier — no model call.</div>}
      <Card title="Pages sent per field group" padded={false}>
        <table className="w-full"><tbody className="divide-y divide-slate-100">
          {Object.entries(e.selected_pages).map(([g, sel]) => (
            <tr key={g}><Td className="font-medium">{GROUP_LABELS[g] ?? g}</Td><Td className="text-xs text-slate-600">
              {sel.pages.length > 12 ? `${sel.pages.length} pages` : sel.pages.join(", ")} · ~{fmtNum(sel.tokens)} tokens · {sel.tier}
              {Object.keys(sel.reasons).length > 0 && !sel.reasons["*"] && <div className="mt-1 text-slate-400">{Object.entries(sel.reasons).slice(0, 6).map(([p, r]) => `p.${p}: ${r}`).join(" · ")}</div>}
            </Td></tr>
          ))}
        </tbody></table>
      </Card>
      <Card title="Model calls" padded={false}>
        <table className="w-full">
          <thead><tr className="border-b border-slate-100"><Th>Purpose</Th><Th>Tier</Th><Th>Tokens</Th><Th>Cost</Th><Th>Latency</Th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {d.llm_calls.map((c, i) => (
              <tr key={i}><Td className="text-xs">{c.purpose}{c.status !== "ok" && <Badge tone="red" className="ml-1">{c.status}</Badge>}{c.cache_hit && <Badge className="ml-1">cache</Badge>}</Td>
                <Td className="text-xs">{c.tier}{c.effort ? ` · ${c.effort}` : ""}</Td>
                <Td className="tabular text-xs">{fmtNum(c.prompt_tokens)}{c.cached_tokens ? ` (${fmtNum(c.cached_tokens)} cached)` : ""} → {fmtNum(c.completion_tokens)}</Td>
                <Td className="tabular text-xs">{fmtUsd(c.cost_usd)}</Td><Td className="tabular text-xs">{fmtMs(c.latency_ms)}</Td></tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function HistoryPanel({ d }: { d: DocDetail }) {
  if (!d.reviews.length && !d.jobs.length) return <div className="text-sm text-slate-500">No reviews yet.</div>;
  return (
    <div className="space-y-4">
      <ul className="space-y-2">
        {d.reviews.map((r, i) => (
          <li key={i} className="rounded-lg border border-slate-200 px-3 py-2 text-sm">
            <div className="flex justify-between"><span><b>{label(r.field)}</b> {r.action === "correct" ? "corrected" : "approved"} by {r.reviewer}</span><span className="text-xs text-slate-500">{fmtDateTime(r.at)}</span></div>
            {r.action === "correct" && <div className="mt-1 text-xs text-slate-600">{fmtValue(r.field, r.old_value)} → <b>{fmtValue(r.field, r.new_value)}</b></div>}
          </li>
        ))}
      </ul>
      <Card title="Processing jobs" padded={false}>
        <table className="w-full"><tbody className="divide-y divide-slate-100">
          {d.jobs.map((j) => (
            <tr key={j.id}><Td><StatusBadge status={j.status} /></Td><Td className="text-xs">{j.lane} · attempt {j.attempts}</Td>
              <Td className="text-xs text-slate-500">{fmtDateTime(j.created_at)}</Td><Td className="max-w-xs truncate text-xs text-rose-600" title={j.last_error ?? ""}>{j.last_error}</Td></tr>
          ))}
        </tbody></table>
      </Card>
    </div>
  );
}
