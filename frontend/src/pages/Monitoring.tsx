import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, Bar as RBar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader, Stat, Td, Th } from "../components/ui";
import { fmtMs, fmtPct, fmtUsd, label } from "../format";

interface Overview {
  window_days: number;
  documents: { total: number; by_status: Record<string, number>; needs_review: number };
  series: Record<string, number | string>[];
  extraction: { count: number; fresh: number; reused: number; cost_per_doc_usd: number | null; latency_ms: { p50: number | null; p95: number | null }; job_wall_ms: { p50: number | null; p95: number | null }; escalation_rate: number | null; modes: Record<string, number> };
  llm: { calls: number; errors: number; error_rate: number | null; cache_hit_rate: number | null; prompt_cache_share: number | null; cost_usd: number; eval_cost_usd: number; by_purpose: Record<string, Record<string, number>> };
  pages: { by_route: Record<string, number>; ocr_confidence_p10: number | null; ocr_confidence_p50: number | null };
  fields: { field: string; critical: boolean; n: number; null_rate: number; grounded_rate: number | null; mean_confidence: number; review_rate: number; reviewed: number; correction_rate: number | null }[];
  reviews: { total: number; corrections: number };
}
interface Drift { basis: string; reference: { n: number }; current: { n: number }; features: { feature: string; psi: number; status: string; reference: Record<string, number>; current: Record<string, number> }[]; note?: string }
interface Canary { runs: { id: string; at: string; quality: number | null; cost_per_1k: number | null }[]; alert: string | null }

export default function Monitoring() {
  const ov = useQuery({ queryKey: ["mon"], queryFn: () => api<Overview>("/api/monitoring/overview?days=30"), refetchInterval: 30000 });
  const drift = useQuery({ queryKey: ["drift"], queryFn: () => api<Drift>("/api/monitoring/drift") });
  const canary = useQuery({ queryKey: ["canary"], queryFn: () => api<Canary>("/api/monitoring/canary") });
  if (ov.isLoading) return <Loading />;
  if (ov.error) return <ErrorBox error={ov.error} />;
  const o = ov.data!;
  return (
    <>
      <PageHeader title="Monitoring" subtitle="No ground truth in production — so live quality is read from proxies that track it (grounding, review and null rates, confidence) and from the one real label stream: human corrections." />
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Extractions" value={o.extraction.count} sub={`${o.extraction.reused} reused (dedup)`} />
        <Stat label="Cost / document" value={fmtUsd(o.extraction.cost_per_doc_usd)} sub={`$${o.llm.cost_usd.toFixed(3)} production · $${o.llm.eval_cost_usd.toFixed(2)} eval, ${o.window_days} d`} />
        <Stat label="Latency p50" value={fmtMs(o.extraction.latency_ms.p50)} sub={`p95 ${fmtMs(o.extraction.latency_ms.p95)}`} />
        <Stat label="Escalation rate" value={fmtPct(o.extraction.escalation_rate)} sub="groups sent to the strong tier" />
        <Stat label="LLM error rate" value={fmtPct(o.llm.error_rate)} sub={`${o.llm.errors} of ${o.llm.calls} calls`} tone={(o.llm.error_rate ?? 0) > 0.05 ? "bad" : "default"} />
        <Stat label="Prompt cache" value={fmtPct(o.llm.prompt_cache_share)} sub="share of prompt tokens cached" tone="good" />
        <Stat label="Corrections" value={o.reviews.corrections} sub={`of ${o.reviews.total} reviewed fields`} />
      </div>

      <div className="mb-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card title="Volume & spend per day">
          {o.series.length === 0 ? <Empty title="No activity yet" /> : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={o.series} margin={{ left: -10, right: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="day" fontSize={11} /><YAxis yAxisId="l" fontSize={11} /><YAxis yAxisId="r" orientation="right" fontSize={11} />
                <Tooltip /><Legend />
                <Area yAxisId="l" type="monotone" dataKey="extractions" stroke="#4f46e5" fill="#c7d2fe" name="extractions" />
                <Area yAxisId="l" type="monotone" dataKey="uploads" stroke="#0ea5e9" fill="#bae6fd" name="uploads" />
                <Area yAxisId="r" type="monotone" dataKey="llm_cost_usd" stroke="#f59e0b" fill="#fde68a" name="LLM $" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>
        <Card title="How pages were read" subtitle="Text layer is free; OCR is cheap; a VLM page costs image tokens">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={Object.entries(o.pages.by_route).map(([route, n]) => ({ route, n }))} margin={{ left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="route" fontSize={11} /><YAxis fontSize={11} /><Tooltip />
              <RBar dataKey="n" fill="#6366f1" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="text-xs text-slate-500">OCR confidence p10 {fmtPct(o.pages.ocr_confidence_p10)} · p50 {fmtPct(o.pages.ocr_confidence_p50)}</div>
        </Card>
      </div>

      <Card title="Field health" subtitle="Latest production extractions. A correction rate above zero is a measured error rate on reviewed fields." padded={false} className="mb-6">
        <table className="w-full">
          <thead className="border-b border-slate-100"><tr><Th>Field</Th><Th className="text-right">n</Th><Th className="text-right">Null rate</Th><Th className="text-right">Quote grounded</Th><Th className="text-right">Mean confidence</Th><Th className="text-right">Sent to review</Th><Th className="text-right">Reviewed</Th><Th className="text-right">Correction rate</Th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {o.fields.map((f) => (
              <tr key={f.field}><Td className="font-medium">{label(f.field)} {f.critical && <Badge tone="brand">critical</Badge>}</Td>
                <Td className="tabular text-right">{f.n}</Td><Td className="tabular text-right">{fmtPct(f.null_rate)}</Td>
                <Td className="tabular text-right">{fmtPct(f.grounded_rate)}</Td><Td className="tabular text-right">{fmtPct(f.mean_confidence)}</Td>
                <Td className="tabular text-right">{fmtPct(f.review_rate)}</Td><Td className="tabular text-right">{f.reviewed}</Td>
                <Td className={`tabular text-right ${(f.correction_rate ?? 0) > 0.1 ? "font-semibold text-rose-600" : ""}`}>{fmtPct(f.correction_rate)}</Td></tr>
            ))}
          </tbody>
        </table>
        {o.fields.length === 0 && <Empty title="No production extractions yet" />}
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Drift (PSI)" subtitle={drift.data ? `${drift.data.basis}: reference n=${drift.data.reference.n}, current n=${drift.data.current.n}. < 0.1 stable · 0.1–0.25 moderate · > 0.25 significant` : ""} padded={false}>
          {drift.isLoading ? <Loading /> : drift.data?.note ? <div className="p-5 text-sm text-slate-500">{drift.data.note}</div> : (
            <table className="w-full">
              <thead className="border-b border-slate-100"><tr><Th>Feature</Th><Th className="text-right">PSI</Th><Th>Status</Th><Th>Reference → current</Th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {(drift.data?.features ?? []).slice(0, 18).map((f) => (
                  <tr key={f.feature}><Td className="font-mono text-xs">{f.feature}</Td><Td className="tabular text-right">{f.psi.toFixed(3)}</Td>
                    <Td><Badge tone={f.status === "significant" ? "red" : f.status === "moderate" ? "amber" : "green"}>{f.status}</Badge></Td>
                    <Td className="text-xs text-slate-500">{Object.keys({ ...f.reference, ...f.current }).slice(0, 4).map((k) => `${k} ${fmtPct(f.reference[k] ?? 0, 0)}→${fmtPct(f.current[k] ?? 0, 0)}`).join(" · ")}</Td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Canary" subtitle="Fixed golden documents re-run on a schedule: a drop with no code change means the model moved">
          {canary.data?.alert && <div className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{canary.data.alert}</div>}
          {(canary.data?.runs ?? []).length === 0 ? <div className="text-sm text-slate-500">No canary runs yet. Build the canary set on the Evaluation page and set DOCINTEL_CANARY_INTERVAL_HOURS.</div> : (
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={canary.data!.runs.map((r) => ({ at: r.at.slice(5, 16).replace("T", " "), quality: r.quality }))} margin={{ left: -20 }}>
                <XAxis dataKey="at" fontSize={10} /><YAxis domain={[0.5, 1]} fontSize={10} /><Tooltip formatter={(v) => fmtPct(Number(v))} />
                <Area dataKey="quality" stroke="#16a34a" fill="#bbf7d0" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>
    </>
  );
}
