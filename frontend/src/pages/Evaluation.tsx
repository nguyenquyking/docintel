import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, Play, Scale } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, post } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Input, Loading, PageHeader, Select, StatusBadge, Td, Th } from "../components/ui";
import { fmtDateTime, fmtMs, fmtPct } from "../format";
import type { EvalRunRow, GoldenSetSummary } from "../types";

export default function Evaluation() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const sets = useQuery({ queryKey: ["golden-sets"], queryFn: () => api<{ sets: GoldenSetSummary[] }>("/api/eval/sets") });
  const runs = useQuery({
    queryKey: ["eval-runs"], queryFn: () => api<{ runs: EvalRunRow[] }>("/api/eval/runs"),
    refetchInterval: (q) => (q.state.data?.runs.some((r) => ["queued", "running"].includes(r.status)) ? 3000 : 20000),
  });
  const [goldenSet, setGoldenSet] = useState("synthetic-v1");
  const [split, setSplit] = useState("test");
  const [preset, setPreset] = useState("cascade");
  const [runName, setRunName] = useState("");
  const [cache, setCache] = useState(true);
  const [picked, setPicked] = useState<string[]>([]);
  const [cuadN, setCuadN] = useState("25");

  const start = useMutation({
    mutationFn: () => post<{ run_id: string }>("/api/eval/runs", { set_name: goldenSet, split, preset, name: runName || undefined, use_cache: cache }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval-runs"] }),
  });
  const synth = useMutation({ mutationFn: () => post("/api/eval/sets/synthetic", { name: "synthetic-v1", n: 60 }), onSuccess: () => qc.invalidateQueries({ queryKey: ["golden-sets"] }) });
  const cuad = useMutation({ mutationFn: () => post("/api/eval/sets/cuad", { name: "cuad", n: Number(cuadN) }), onSuccess: () => qc.invalidateQueries({ queryKey: ["golden-sets"] }) });
  const canary = useMutation({ mutationFn: () => post("/api/eval/sets/canary"), onSuccess: () => qc.invalidateQueries({ queryKey: ["golden-sets"] }) });

  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-2)));

  return (
    <>
      <PageHeader title="Evaluation" subtitle="Golden sets with defensible labels, runs that record every field outcome, and a paired regression gate for every prompt, model or pipeline change." />

      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {sets.isLoading ? <Loading /> : (sets.data?.sets ?? []).map((s) => (
          <Card key={s.name} title={<span className="flex items-center gap-2"><Database className="size-4 text-slate-400" />{s.name}</span>} subtitle={s.description}
            actions={<Badge tone={s.source === "cuad" ? "violet" : s.source === "reviewed" ? "green" : s.source === "canary" ? "amber" : "brand"}>{s.source}</Badge>}>
            <div className="text-sm text-slate-600"><b className="text-ink">{s.docs}</b> documents · {Object.entries(s.splits).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
            <div className="mt-3 space-y-1.5">
              {["language", "length", "scanned", "tags"].filter((k) => s.strata[k]).map((k) => (
                <div key={k} className="flex flex-wrap items-center gap-1 text-xs"><span className="w-16 text-slate-400">{k}</span>
                  {Object.entries(s.strata[k]).map(([v, n]) => <Badge key={v}>{v} {n}</Badge>)}</div>
              ))}
            </div>
          </Card>
        ))}
        <Card title="Add golden data">
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between gap-2"><span className="text-slate-600">Synthetic contracts (EN/VI, scans, long docs, hard cases)</span>
              <Button size="sm" onClick={() => synth.mutate()} disabled={synth.isPending}>{synth.isPending ? "Generating…" : "Generate 60"}</Button></div>
            <div className="flex items-center justify-between gap-2"><span className="text-slate-600">Real contracts from CUAD (CC BY 4.0, ~100 MB download)</span>
              <span className="flex gap-1"><Input value={cuadN} onChange={(e) => setCuadN(e.target.value)} className="w-14" /><Button size="sm" onClick={() => cuad.mutate()} disabled={cuad.isPending}>{cuad.isPending ? "Importing…" : "Import"}</Button></span></div>
            <div className="flex items-center justify-between gap-2"><span className="text-slate-600">Canary subset (scheduled re-runs detect provider-side model changes)</span>
              <Button size="sm" onClick={() => canary.mutate()}>Build</Button></div>
            <p className="text-xs text-slate-500">Reviewed production documents join the “reviewed” set from their document page.</p>
            {[synth.error, cuad.error].map((e, i) => e ? <ErrorBox key={i} error={e} /> : null)}
          </div>
        </Card>
      </div>

      <Card title="Start a run" className="mb-6">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={goldenSet} onChange={setGoldenSet} options={(sets.data?.sets ?? []).map((s) => ({ value: s.name, label: s.name }))} />
          <Select value={split} onChange={setSplit} options={["test", "dev", "all"].map((s) => ({ value: s, label: `split: ${s}` }))} />
          <Select value={preset} onChange={setPreset} options={[
            { value: "cascade", label: "cascade (fast → strong on low confidence)" }, { value: "fast", label: "fast tier only" },
            { value: "strong", label: "strong tier only" }, { value: "long-context", label: "long context (whole document)" },
            { value: "retrieval-only", label: "retrieval for every document" }]} />
          <Input placeholder="run name (optional)" value={runName} onChange={(e) => setRunName(e.target.value)} />
          <label className="flex items-center gap-1.5 text-sm text-slate-600"><input type="checkbox" checked={cache} onChange={(e) => setCache(e.target.checked)} /> reuse cached answers</label>
          <Button variant="primary" onClick={() => start.mutate()} disabled={start.isPending}><Play className="size-4" /> Run</Button>
        </div>
        <p className="mt-2 text-xs text-slate-500">Uncheck “reuse cached answers” and run the same preset twice to measure the noise floor: flips between identical runs are not regressions.</p>
        {start.error && <div className="mt-2"><ErrorBox error={start.error} /></div>}
      </Card>

      <Card title="Runs" padded={false} actions={
        <Button size="sm" variant="primary" disabled={picked.length !== 2} onClick={() => navigate(`/eval/compare?base=${picked[0]}&candidate=${picked[1]}`)}>
          <Scale className="size-3.5" /> Compare selected ({picked.length}/2)
        </Button>}>
        {runs.isLoading ? <Loading /> : (runs.data?.runs ?? []).length === 0 ? <Empty title="No runs yet" hint="Generate a golden set, then start a run." /> : (
          <table className="w-full">
            <thead className="border-b border-slate-100"><tr><Th></Th><Th>Run</Th><Th>Set</Th><Th>Preset</Th><Th>Status</Th><Th className="text-right">Critical-field accuracy</Th><Th className="text-right">$ / 1k docs</Th><Th className="text-right">p50 latency</Th><Th>Started</Th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {runs.data!.runs.map((r) => (
                <tr key={r.id} className="hover:bg-slate-50">
                  <Td><input type="checkbox" checked={picked.includes(r.id)} onChange={() => toggle(r.id)} /></Td>
                  <Td><Link to={`/eval/runs/${r.id}`} className="font-medium text-brand-700 hover:underline">{r.name}</Link></Td>
                  <Td className="text-xs text-slate-600">{r.set_name}/{r.split}</Td>
                  <Td><Badge>{r.preset}</Badge></Td>
                  <Td><StatusBadge status={r.status} />{["running", "queued"].includes(r.status) && <span className="ml-1 text-xs text-slate-500">{r.done_docs}/{r.n_docs}</span>}</Td>
                  <Td className="tabular text-right font-medium">{fmtPct(r.quality)}</Td>
                  <Td className="tabular text-right">{r.cost_per_1k != null ? `$${r.cost_per_1k.toFixed(2)}` : "—"}</Td>
                  <Td className="tabular text-right">{fmtMs(r.latency_p50_ms)}</Td>
                  <Td className="whitespace-nowrap text-xs text-slate-500">{fmtDateTime(r.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}
