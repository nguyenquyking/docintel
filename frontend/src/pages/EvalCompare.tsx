import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api";
import { Badge, Card, ErrorBox, Loading, PageHeader, Td, Th } from "../components/ui";
import { fmtMs, fmtPct, fmtValue, label } from "../format";
import type { GateReport } from "../types";

export default function EvalCompare() {
  const [params] = useSearchParams();
  const base = params.get("base");
  const cand = params.get("candidate");
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["gate", base, cand], queryFn: () => api<GateReport>(`/api/eval/compare?base=${base}&candidate=${cand}`), enabled: !!base && !!cand });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data!;
  return (
    <>
      <Link to="/eval" className="mb-1 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700"><ArrowLeft className="size-3" /> Evaluation</Link>
      <PageHeader title="Regression gate" subtitle={<>Paired comparison on the same golden documents: McNemar’s exact test on the documents that flip, plus a paired bootstrap CI. Fails when a critical field drops significantly (α = {r.rules.alpha}) or any field drops more than {fmtPct(r.rules.max_drop, 0)}.</>} />
      <div className={clsx("mb-6 flex items-center gap-3 rounded-xl border px-5 py-4", r.passed ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50")}>
        {r.passed ? <ShieldCheck className="size-7 text-emerald-600" /> : <ShieldAlert className="size-7 text-rose-600" />}
        <div>
          <div className={clsx("text-lg font-semibold", r.passed ? "text-emerald-800" : "text-rose-800")}>{r.passed ? "Gate passed" : "Gate failed — the candidate makes something worse"}</div>
          <div className="text-sm text-slate-600">
            <b>{r.base.name}</b> ({fmtPct(r.base.quality)}, ${r.base.cost_per_1k?.toFixed(2)}/1k, {fmtMs(r.base.latency_p50_ms)}) → <b>{r.candidate.name}</b> ({fmtPct(r.candidate.quality)}, ${r.candidate.cost_per_1k?.toFixed(2)}/1k, {fmtMs(r.candidate.latency_p50_ms)})
          </div>
          {r.cost_note && <div className="text-sm text-rose-700">{r.cost_note}</div>}
        </div>
      </div>
      <Card padded={false}>
        <table className="w-full">
          <thead className="border-b border-slate-100"><tr><Th>Field</Th><Th className="text-right">Base</Th><Th className="text-right">Candidate</Th><Th className="text-right">Δ</Th><Th className="text-right">95% CI of Δ</Th><Th className="text-right">p (McNemar)</Th><Th className="text-right">Broke / fixed</Th><Th>Verdict</Th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {r.fields.map((f) => (
              <>
                <tr key={f.field} onClick={() => setOpen(open === f.field ? null : f.field)} className={clsx("cursor-pointer hover:bg-slate-50", f.fail && "bg-rose-50/60")}>
                  <Td className="font-medium">{label(f.field)} {f.critical && <Badge tone="brand">critical</Badge>}</Td>
                  <Td className="tabular text-right">{fmtPct(f.base_accuracy)}</Td>
                  <Td className="tabular text-right">{fmtPct(f.cand_accuracy)}</Td>
                  <Td className={clsx("tabular text-right font-medium", f.delta < 0 ? "text-rose-600" : f.delta > 0 ? "text-emerald-600" : "")}>{f.delta > 0 ? "+" : ""}{fmtPct(f.delta)}</Td>
                  <Td className="tabular text-right text-xs text-slate-500">[{fmtPct(f.ci[0])}, {fmtPct(f.ci[1])}]</Td>
                  <Td className="tabular text-right text-xs">{f.p_value < 0.001 ? "<0.001" : f.p_value.toFixed(3)}</Td>
                  <Td className="tabular text-right text-xs">{f.broke.length} / {f.fixed.length}</Td>
                  <Td>{f.fail ? <Badge tone="red">regression</Badge> : <Badge tone="green">ok</Badge>}</Td>
                </tr>
                {open === f.field && (f.broke.length > 0 || f.fixed.length > 0) && (
                  <tr key={f.field + "-x"}><td colSpan={8} className="bg-slate-50 px-5 py-3">
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 text-xs">
                      {([["Broke (right → wrong)", f.broke, "text-rose-700"], ["Fixed (wrong → right)", f.fixed, "text-emerald-700"]] as const).map(([title, list, cls]) => (
                        <div key={title}><div className={clsx("mb-1 font-semibold", cls)}>{title}</div>
                          {list.length === 0 ? <div className="text-slate-400">none</div> : list.map((x) => (
                            <div key={x.golden_doc_id} className="mb-1"><b>{x.doc}</b>: gold {fmtValue(f.field, x.gold)} · candidate {fmtValue(f.field, x.pred)}</div>
                          ))}</div>
                      ))}
                    </div>
                  </td></tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
