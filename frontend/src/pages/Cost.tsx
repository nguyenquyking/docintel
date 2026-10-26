import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { CartesianGrid, Label, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { api, post, put } from "../api";
import { Badge, Button, Card, ErrorBox, Input, Loading, PageHeader, Td, Th } from "../components/ui";
import { fmtNum, fmtPct } from "../format";

type Pricing = Record<string, { input: number; cached_input: number; output: number }>;
interface Measured { measured: Record<string, number | null | Record<string, number>>; pricing: Pricing }
interface Projection { total_usd: number; per_1k_docs_usd: number; effective_docs: number; pages: number; breakdown_usd: Record<string, number>; tokens: Record<string, number> }
interface Breakeven { self_host_price_per_m: number; vendor_price_per_m: number; breakeven_tokens_per_month_m: number | null; monthly_cost_vendor: number; monthly_cost_self_host: number; monthly_savings: number; verdict: string }
interface Pareto { points: { id: string; name: string; set: string; split: string; quality: number; cost_per_1k: number; latency_p50_ms: number | null; preset: string; on_frontier: boolean }[] }

const usd = (v: number) => `$${v.toLocaleString("en-US", { maximumFractionDigits: v < 10 ? 2 : 0 })}`;

function Field({ label, value, onChange, hint }: { label: string; value: string; onChange: (v: string) => void; hint?: string }) {
  return (
    <label className="block text-xs">
      <span className="text-slate-600">{label}</span>
      <Input value={value} onChange={(e) => onChange(e.target.value)} className="mt-0.5 w-full" />
      {hint && <span className="text-[11px] text-slate-400">{hint}</span>}
    </label>
  );
}

export default function Cost() {
  const qc = useQueryClient();
  const measured = useQuery({ queryKey: ["cost-measured"], queryFn: () => api<Measured>("/api/cost/measured") });
  const pareto = useQuery({ queryKey: ["pareto"], queryFn: () => api<Pareto>("/api/cost/pareto") });
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [p, setP] = useState<Record<string, string>>({
    n_docs: "1000000", pages_per_doc: "10", tokens_per_page: "650", sent_fraction: "1", output_tokens_per_doc: "1200",
    prompt_overhead_tokens: "2000", prompt_cache_share: "0.3", escalation_rate: "0.15", scanned_share: "0.2",
    vlm_share_of_scanned: "0.5", vlm_tokens_per_page: "1100", ocr_cost_per_1k_pages: "0", dedup_rate: "0.1", batch_discount: "0",
  });
  const [b, setB] = useState<Record<string, string>>({ gpu_hour_usd: "2.5", throughput_tokens_per_s: "10000", utilization: "0.6", fixed_monthly_usd: "15000", vendor_price_per_m: "3", monthly_tokens_m: "2000" });

  useEffect(() => {
    const m = measured.data?.measured;
    if (!m || !Object.keys(m).length) return;
    setPricing(measured.data!.pricing);
    const num = (k: string) => (typeof m[k] === "number" ? String(m[k]) : undefined);
    setP((old) => ({
      ...old,
      pages_per_doc: num("pages_per_doc") ?? old.pages_per_doc,
      tokens_per_page: num("tokens_per_page_all") ?? old.tokens_per_page,
      sent_fraction: num("sent_fraction") ?? old.sent_fraction,
      output_tokens_per_doc: num("output_tokens_per_doc") ?? old.output_tokens_per_doc,
      prompt_overhead_tokens: num("prompt_overhead_tokens") ?? old.prompt_overhead_tokens,
      prompt_cache_share: num("prompt_cache_share") ?? old.prompt_cache_share,
      escalation_rate: num("escalation_rate") ?? old.escalation_rate,
      scanned_share: num("scanned_page_share") ?? old.scanned_share,
      vlm_share_of_scanned: num("vlm_share_of_scanned") ?? old.vlm_share_of_scanned,
      vlm_tokens_per_page: num("vlm_tokens_per_page") ?? old.vlm_tokens_per_page,
      dedup_rate: num("dedup_rate") ?? old.dedup_rate,
    }));
  }, [measured.data]);

  const project = useMutation({ mutationFn: () => post<Projection>("/api/cost/project", { ...Object.fromEntries(Object.entries(p).map(([k, v]) => [k, Number(v)])), pricing }) });
  const breakeven = useMutation({ mutationFn: () => post<Breakeven>("/api/cost/breakeven", Object.fromEntries(Object.entries(b).map(([k, v]) => [k, Number(v)]))) });
  const savePricing = useMutation({ mutationFn: () => put("/api/settings/pricing", pricing), onSuccess: () => qc.invalidateQueries({ queryKey: ["cost-measured"] }) });

  const m = measured.data?.measured ?? {};
  const points = pareto.data?.points ?? [];
  return (
    <>
      <PageHeader title="Cost" subtitle="What the corpus costs, which configuration gives the best quality per dollar, and when our own model would beat vendor pricing — from measured pipeline statistics, with prices as the only assumption." />
      <div className="mb-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Quality per dollar" subtitle="Each point is an eval run (same golden set ⇒ comparable). Frontier = no other run is both cheaper and better.">
          {points.length === 0 ? <div className="py-10 text-center text-sm text-slate-500">Run evaluations with different presets to populate the frontier.</div> : (
            <ResponsiveContainer width="100%" height={300}>
              <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis type="number" dataKey="cost_per_1k" fontSize={11} tickFormatter={(v) => `$${v}`}><Label value="$ per 1,000 documents" position="insideBottom" offset={-12} fontSize={11} /></XAxis>
                <YAxis type="number" dataKey="quality" domain={["auto", 1]} fontSize={11} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                <ZAxis range={[90, 90]} />
                <Tooltip content={({ payload }) => payload?.[0] ? (
                  <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow">
                    <div className="font-semibold">{(payload[0].payload as Pareto["points"][0]).name}</div>
                    <div>{fmtPct((payload[0].payload as Pareto["points"][0]).quality)} · ${(payload[0].payload as Pareto["points"][0]).cost_per_1k.toFixed(2)}/1k</div>
                  </div>) : null} />
                <Scatter data={points.filter((x) => !x.on_frontier)} fill="#94a3b8" />
                <Scatter data={points.filter((x) => x.on_frontier).sort((a, c) => a.cost_per_1k - c.cost_per_1k)} fill="#4f46e5" line={{ stroke: "#4f46e5" }} />
              </ScatterChart>
            </ResponsiveContainer>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5">{points.filter((x) => x.on_frontier).map((x) => <Badge key={x.id} tone="brand">{x.name}: {fmtPct(x.quality)} @ ${x.cost_per_1k.toFixed(2)}</Badge>)}</div>
        </Card>
        <Card title="Measured on this pipeline">
          {measured.isLoading ? <Loading /> : (
            <dl className="space-y-1.5 text-sm">
              {([
                ["Documents processed", fmtNum(m.documents as number)], ["Pages / document", fmtNum(m.pages_per_doc as number, 1)],
                ["Tokens / page", Object.entries((m.tokens_per_page as Record<string, number>) ?? {}).map(([k, v]) => `${k} ${Math.round(v)}`).join(" · ")],
                ["Share of document sent", fmtPct(m.sent_fraction as number)], ["Output tokens / doc", fmtNum(m.output_tokens_per_doc as number)],
                ["Escalation rate", fmtPct(m.escalation_rate as number)], ["Scanned pages", fmtPct(m.scanned_page_share as number)],
                ["…of which VLM", fmtPct(m.vlm_share_of_scanned as number)], ["VLM tokens / page", fmtNum(m.vlm_tokens_per_page as number)],
                ["Duplicates", fmtPct(m.dedup_rate as number)], ["Prompt cache share", fmtPct(m.prompt_cache_share as number)],
                ["Reasoning share of output", fmtPct(m.reasoning_share_of_output as number)],
              ] as [string, string][]).map(([k, v]) => <div key={k} className="flex justify-between gap-2"><dt className="text-slate-500">{k}</dt><dd className="tabular text-right font-medium">{v || "—"}</dd></div>)}
            </dl>
          )}
        </Card>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card title="Price table ($ per 1M tokens)" subtitle="Defaults are placeholders — enter the prices of your contract.">
          {pricing && (
            <table className="w-full">
              <thead><tr><Th>Tier</Th><Th>Input</Th><Th>Cached</Th><Th>Output</Th></tr></thead>
              <tbody>{Object.entries(pricing).map(([tier, pr]) => (
                <tr key={tier}><Td className="font-medium">{tier}</Td>
                  {(["input", "cached_input", "output"] as const).map((k) => (
                    <Td key={k}><Input value={String(pr[k])} className="w-20" onChange={(e) => setPricing({ ...pricing, [tier]: { ...pr, [k]: Number(e.target.value) } })} /></Td>
                  ))}</tr>
              ))}</tbody>
            </table>
          )}
          <Button className="mt-3" onClick={() => savePricing.mutate()}>Save prices</Button>
          {savePricing.isSuccess && <span className="ml-2 text-xs text-emerald-700">saved — new calls use these prices</span>}
        </Card>

        <Card className="xl:col-span-2" title="Corpus projection" subtitle="Pre-filled from the measurements; change any assumption and recompute.">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="Documents" value={p.n_docs} onChange={(v) => setP({ ...p, n_docs: v })} />
            <Field label="Pages / doc" value={p.pages_per_doc} onChange={(v) => setP({ ...p, pages_per_doc: v })} />
            <Field label="Tokens / page" value={p.tokens_per_page} onChange={(v) => setP({ ...p, tokens_per_page: v })} />
            <Field label="Share of doc sent" value={p.sent_fraction} onChange={(v) => setP({ ...p, sent_fraction: v })} hint="retrieval for long docs" />
            <Field label="Output tokens / doc" value={p.output_tokens_per_doc} onChange={(v) => setP({ ...p, output_tokens_per_doc: v })} />
            <Field label="Prompt overhead / call" value={p.prompt_overhead_tokens} onChange={(v) => setP({ ...p, prompt_overhead_tokens: v })} hint="instructions + schema" />
            <Field label="Prompt cache share" value={p.prompt_cache_share} onChange={(v) => setP({ ...p, prompt_cache_share: v })} />
            <Field label="Escalation rate" value={p.escalation_rate} onChange={(v) => setP({ ...p, escalation_rate: v })} />
            <Field label="Scanned page share" value={p.scanned_share} onChange={(v) => setP({ ...p, scanned_share: v })} />
            <Field label="VLM share of scans" value={p.vlm_share_of_scanned} onChange={(v) => setP({ ...p, vlm_share_of_scanned: v })} />
            <Field label="Duplicate share" value={p.dedup_rate} onChange={(v) => setP({ ...p, dedup_rate: v })} />
            <Field label="Batch discount" value={p.batch_discount} onChange={(v) => setP({ ...p, batch_discount: v })} hint="e.g. 0.5 for batch APIs" />
          </div>
          <Button variant="primary" className="mt-4" onClick={() => project.mutate()}>Project cost</Button>
          {project.error && <ErrorBox error={project.error} />}
          {project.data && (
            <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-6">
              <div className="col-span-2 rounded-lg bg-brand-50 px-4 py-3"><div className="text-xs uppercase text-brand-700">Total</div><div className="tabular text-2xl font-semibold text-brand-900">{usd(project.data.total_usd)}</div><div className="text-xs text-brand-700">{usd(project.data.per_1k_docs_usd)} per 1k docs · {fmtNum(project.data.effective_docs)} after dedup</div></div>
              {Object.entries(project.data.breakdown_usd).map(([k, v]) => (
                <div key={k} className="rounded-lg bg-slate-50 px-3 py-3"><div className="text-xs text-slate-500">{k.replace("_", " ")}</div><div className="tabular text-lg font-semibold">{usd(v)}</div></div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title="Own model vs vendor" subtitle="Self-hosted $/1M tokens = GPU $/h ÷ (tokens/s × 3600 × utilization ÷ 10⁶). Hosting alone rarely beats a small vendor model; the win is a distilled small model matching the expensive model's quality on our fields — which the golden set must prove.">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
          <Field label="GPU $ / hour" value={b.gpu_hour_usd} onChange={(v) => setB({ ...b, gpu_hour_usd: v })} />
          <Field label="Throughput tokens/s" value={b.throughput_tokens_per_s} onChange={(v) => setB({ ...b, throughput_tokens_per_s: v })} />
          <Field label="Utilization" value={b.utilization} onChange={(v) => setB({ ...b, utilization: v })} />
          <Field label="Fixed $ / month" value={b.fixed_monthly_usd} onChange={(v) => setB({ ...b, fixed_monthly_usd: v })} hint="people, on-call, reserved GPUs" />
          <Field label="Vendor $ / 1M (same quality)" value={b.vendor_price_per_m} onChange={(v) => setB({ ...b, vendor_price_per_m: v })} />
          <Field label="Monthly volume (M tokens)" value={b.monthly_tokens_m} onChange={(v) => setB({ ...b, monthly_tokens_m: v })} hint="incl. re-processing for new fields" />
        </div>
        <Button variant="primary" className="mt-4" onClick={() => breakeven.mutate()}>Compute break-even</Button>
        {breakeven.data && (
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-5 text-sm">
            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Self-host $/1M</div><div className="tabular text-lg font-semibold">${breakeven.data.self_host_price_per_m.toFixed(3)}</div></div>
            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Break-even volume</div><div className="tabular text-lg font-semibold">{breakeven.data.breakeven_tokens_per_month_m ? `${fmtNum(breakeven.data.breakeven_tokens_per_month_m)}M/mo` : "never"}</div></div>
            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Vendor / month</div><div className="tabular text-lg font-semibold">{usd(breakeven.data.monthly_cost_vendor)}</div></div>
            <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-xs text-slate-500">Self-host / month</div><div className="tabular text-lg font-semibold">{usd(breakeven.data.monthly_cost_self_host)}</div></div>
            <div className="rounded-lg bg-brand-50 px-3 py-2 text-brand-900"><div className="text-xs">Verdict</div><div className="font-semibold">{breakeven.data.verdict}</div></div>
          </div>
        )}
      </Card>
    </>
  );
}
