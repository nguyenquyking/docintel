import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { Badge, Bar, Card, ErrorBox, Loading, PageHeader, Stat, StatusBadge, Td, Th } from "../components/ui";
import { fmtMs, fmtPct, fmtValue, label } from "../format";
import type { EvalRunDetail } from "../types";

const CRITICAL = ["parties", "is_executed", "effective_date", "initial_term_end", "auto_renewal", "non_renewal_notice_days", "notice_deadline", "contract_value"];

interface Item { golden_doc_id: string; external_id: string; document_id: string; field: string; gold: unknown; pred: unknown; outcome: string; correct: boolean; confidence: number | null; retrieval_hit: boolean | null; evidence_page_ok: boolean | null }

export default function EvalRun() {
  const { id } = useParams();
  const q = useQuery({
    queryKey: ["eval-run", id], queryFn: () => api<EvalRunDetail>(`/api/eval/runs/${id}`),
    refetchInterval: (query) => (["queued", "running"].includes(query.state.data?.status ?? "") ? 3000 : false),
  });
  const [field, setField] = useState<string | null>(null);
  const errors = useQuery({
    queryKey: ["eval-items", id, field], enabled: !!field,
    queryFn: () => api<{ items: Item[] }>(`/api/eval/runs/${id}/items?field=${field}&only_errors=true`),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data!;
  const m = r.metrics;
  if (!m || !m.fields) {
    return (<><PageHeader title={r.name} subtitle={`${r.set_name}/${r.split}`} /><Card><StatusBadge status={r.status} /> <span className="ml-2 text-sm text-slate-600">{r.done_docs}/{r.n_docs} documents</span>{r.error && <div className="mt-3"><ErrorBox error={r.error} /></div>}</Card></>);
  }
  const fieldRows = Object.entries(m.fields);
  return (
    <>
      <Link to="/eval" className="mb-1 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700"><ArrowLeft className="size-3" /> Evaluation</Link>
      <PageHeader title={r.name} subtitle={<>{r.set_name}/{r.split} · preset <b>{r.preset}</b> · {m.docs} documents{m.failed_docs ? `, ${m.failed_docs} failed` : ""} · modes {Object.entries(m.modes).map(([k, v]) => `${k} ${v}`).join(", ")}</>} />
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Critical-field accuracy" value={fmtPct(m.quality)} tone="brand" sub="mean over 8 critical fields" />
        <Stat label="Documents fully right" value={fmtPct(m.doc_level_critical_accuracy)} sub="all critical fields correct" />
        <Stat label="$ / 1k documents" value={m.cost_per_1k_docs != null ? `$${m.cost_per_1k_docs.toFixed(2)}` : "—"} sub={`run total $${m.cost_total_usd.toFixed(3)}`} />
        <Stat label="Latency p50 / p95" value={fmtMs(m.latency_ms.p50)} sub={`p95 ${fmtMs(m.latency_ms.p95)}`} />
        <Stat label="Evidence recall" value={fmtPct(m.evidence_recall)} sub="gold page among pages sent" />
        <Stat label="Calibration error" value={m.calibration.ece.toFixed(3)} sub={`ECE over ${m.calibration.n} fields`} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Per field" subtitle="Accuracy with 95% Wilson interval; null is a label (tn), a wrong value counts as fp + fn. Click a row for its errors." padded={false}>
          <table className="w-full">
            <thead className="border-b border-slate-100"><tr><Th>Field</Th><Th className="w-48">Accuracy</Th><Th className="text-right">P</Th><Th className="text-right">R</Th><Th className="text-right">F1</Th><Th>Outcomes</Th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {fieldRows.map(([f, s]) => (
                <tr key={f} onClick={() => setField(f)} className={`cursor-pointer hover:bg-slate-50 ${field === f ? "bg-brand-50" : ""}`}>
                  <Td className="whitespace-nowrap font-medium">{label(f)} {CRITICAL.includes(f) && <Badge tone="brand">critical</Badge>}</Td>
                  <Td>
                    <div className="flex items-center gap-2"><span className="tabular w-12 text-right text-sm font-medium">{fmtPct(s.accuracy)}</span>
                      <div className="flex-1"><Bar value={s.accuracy ?? 0} tone={(s.accuracy ?? 0) >= 0.95 ? "good" : (s.accuracy ?? 0) >= 0.85 ? "warn" : "bad"} /></div></div>
                    <div className="tabular mt-0.5 text-[11px] text-slate-400">CI {fmtPct(s.accuracy_ci[0])}–{fmtPct(s.accuracy_ci[1])} · n={s.n}</div>
                  </Td>
                  <Td className="tabular text-right text-xs">{fmtPct(s.precision)}</Td>
                  <Td className="tabular text-right text-xs">{fmtPct(s.recall)}</Td>
                  <Td className="tabular text-right text-xs">{fmtPct(s.f1)}</Td>
                  <Td className="text-xs text-slate-500">{Object.entries(s.outcomes).map(([k, v]) => `${k} ${v}`).join(" · ")}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <div className="space-y-6">
          <Card title="Calibration" subtitle="Confidence vs observed accuracy (diagonal = perfectly calibrated)">
            <ResponsiveContainer width="100%" height={220}>
              <ScatterChart margin={{ top: 8, right: 8, bottom: 8, left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" dataKey="confidence" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}`} fontSize={11} />
                <YAxis type="number" dataKey="accuracy" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}`} fontSize={11} />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#94a3b8" strokeDasharray="4 4" />
                <Tooltip formatter={(v) => fmtPct(Number(v))} />
                <Scatter data={m.calibration.bins} fill="#4f46e5" />
              </ScatterChart>
            </ResponsiveContainer>
          </Card>
          <Card title="Slices" subtitle="Critical-field accuracy by stratum — aggregates hide regressions on slices">
            <div className="space-y-3">
              {Object.entries(m.slices).map(([k, vals]) => (
                <div key={k}>
                  <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{k}</div>
                  {Object.entries(vals).map(([v, st]) => (
                    <div key={v} className="flex items-center gap-2 text-xs">
                      <span className="w-28 truncate text-slate-600">{v}</span>
                      <div className="flex-1"><Bar value={st.accuracy ?? 0} tone={(st.accuracy ?? 0) >= 0.95 ? "good" : "warn"} /></div>
                      <span className="tabular w-20 text-right">{fmtPct(st.accuracy)} <span className="text-slate-400">n={st.n}</span></span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </Card>
          {Object.keys(m.ocr).length > 0 && (
            <Card title="Scanned pages: text fidelity" subtitle="Bag-of-words F1 vs ground truth; the gap to the accent-folded score is lost diacritics">
              <table className="w-full"><thead><tr><Th>Language</Th><Th className="text-right">Word F1</Th><Th className="text-right">Accent-folded</Th></tr></thead>
                <tbody>{Object.entries(m.ocr).map(([lang, o]) => (
                  <tr key={lang}><Td>{lang} <span className="text-xs text-slate-400">({o.docs} docs)</span></Td><Td className="tabular text-right">{fmtPct(o.word_f1)}</Td><Td className="tabular text-right">{fmtPct(o.word_f1_folded)}</Td></tr>
                ))}</tbody></table>
            </Card>
          )}
        </div>
      </div>

      {field && (
        <Card className="mt-6" title={`Errors: ${label(field)}`} padded={false}>
          {errors.isLoading ? <Loading /> : (errors.data?.items ?? []).length === 0 ? <div className="p-5 text-sm text-slate-500">No errors on this field.</div> : (
            <table className="w-full">
              <thead className="border-b border-slate-100"><tr><Th>Document</Th><Th>Outcome</Th><Th>Gold</Th><Th>Predicted</Th><Th>Confidence</Th><Th>Gold page sent?</Th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {errors.data!.items.map((it) => (
                  <tr key={it.golden_doc_id}>
                    <Td><Link to={`/documents/${it.document_id}`} className="text-brand-700 hover:underline">{it.external_id}</Link></Td>
                    <Td><Badge tone={it.outcome === "fp" ? "red" : it.outcome === "fn" ? "amber" : "violet"}>{it.outcome}</Badge></Td>
                    <Td className="text-xs">{fmtValue(field, it.gold)}</Td><Td className="text-xs">{fmtValue(field, it.pred)}</Td>
                    <Td className="tabular text-xs">{it.confidence != null ? fmtPct(it.confidence, 0) : "—"}</Td>
                    <Td className="text-xs">{it.retrieval_hit === null ? "—" : it.retrieval_hit ? "yes" : "no — retrieval miss"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      <Card className="mt-6" title="Documents" padded={false}>
        <div className="max-h-96 overflow-y-auto scroll-thin">
          <table className="w-full">
            <thead className="sticky top-0 bg-white"><tr className="border-b border-slate-100"><Th>Document</Th><Th>Language</Th><Th>Length</Th><Th>Tags</Th><Th>Critical fields</Th><Th className="text-right">Cost</Th><Th className="text-right">Latency</Th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {r.docs.map((d) => (
                <tr key={d.golden_doc_id}>
                  <Td><Link to={`/documents/${d.document_id}`} className="text-brand-700 hover:underline">{d.external_id}</Link></Td>
                  <Td className="text-xs">{String(d.strata.language ?? "")}{d.strata.scanned ? " · scan" : ""}</Td>
                  <Td className="text-xs">{String(d.strata.length ?? "")} ({String(d.strata.pages ?? "?")}p)</Td>
                  <Td className="text-xs">{((d.strata.tags as string[]) ?? []).map((t) => <Badge key={t} className="mr-1">{t}</Badge>)}</Td>
                  <Td>{d.error ? <Badge tone="red" title={d.error}>error</Badge> : d.critical_correct ? <Badge tone="green">all correct</Badge> : <Badge tone="amber">errors</Badge>}</Td>
                  <Td className="tabular text-right text-xs">${d.cost_usd.toFixed(4)}</Td>
                  <Td className="tabular text-right text-xs">{fmtMs(d.latency_ms)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
