import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { api, post } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageHeader, Select, Stat, StatusBadge, Td, Th } from "../components/ui";
import { fmtDateTime, fmtMs } from "../format";

interface JobRow {
  id: string; kind: string; document_id: string | null; lane: string; status: string; attempts: number; max_attempts: number;
  progress: Record<string, unknown>; last_error: string | null; run_after: string; created_at: string; started_at: string | null; finished_at: string | null;
  payload: Record<string, unknown>;
}
interface JobsData { jobs: JobRow[]; stats: { lanes: Record<string, Record<string, number>>; oldest_queued_age_s: number | null }; worker: { alive: boolean; slots: number; running: Record<string, string>; worker_id: string } | null }

export default function Jobs() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["jobs", status], queryFn: () => api<JobsData>(`/api/jobs${status ? `?status=${status}` : ""}`), refetchInterval: 3000 });
  const requeue = useMutation({ mutationFn: (id: string) => post(`/api/jobs/${id}/requeue`), onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }) });
  const cancel = useMutation({ mutationFn: (id: string) => post(`/api/jobs/${id}/cancel`), onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const lane = (name: string) => d.stats.lanes[name] ?? {};
  return (
    <>
      <PageHeader title="Jobs" subtitle="Database-backed queue: interactive before backfill (backfill still gets every 4th slot), leases with heartbeat, retries with backoff and jitter for transient errors, dead letter for the rest." />
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Worker" value={d.worker?.alive ? "online" : "offline"} tone={d.worker?.alive ? "good" : "bad"} sub={d.worker ? `${Object.keys(d.worker.running).length}/${d.worker.slots} slots busy` : "start the API or `docintel worker`"} />
        <Stat label="Interactive" value={(lane("interactive").queued ?? 0) + (lane("interactive").running ?? 0)} sub={`${lane("interactive").queued ?? 0} queued · ${lane("interactive").running ?? 0} running`} />
        <Stat label="Backfill" value={(lane("backfill").queued ?? 0) + (lane("backfill").running ?? 0)} sub={`${lane("backfill").queued ?? 0} queued · ${lane("backfill").running ?? 0} running`} />
        <Stat label="Dead letter" value={(lane("interactive").dead ?? 0) + (lane("backfill").dead ?? 0)} tone={(lane("interactive").dead ?? 0) + (lane("backfill").dead ?? 0) ? "bad" : "default"} sub="need a human" />
        <Stat label="Oldest queued" value={d.stats.oldest_queued_age_s != null ? fmtMs(d.stats.oldest_queued_age_s * 1000) : "—"} sub="queue age" />
      </div>
      <Card padded={false} title="Recent jobs" actions={<Select value={status} onChange={setStatus} options={[{ value: "", label: "All" }, ...["queued", "running", "succeeded", "dead", "cancelled"].map((s) => ({ value: s, label: s }))]} />}>
        {d.jobs.length === 0 ? <Empty title="No jobs" /> : (
          <table className="w-full">
            <thead className="border-b border-slate-100"><tr><Th>Job</Th><Th>Lane</Th><Th>Status</Th><Th>Attempts</Th><Th>Stage</Th><Th>Created</Th><Th>Error</Th><Th></Th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {d.jobs.map((j) => (
                <tr key={j.id} className="hover:bg-slate-50">
                  <Td className="text-xs"><div className="font-medium">{j.kind.replace("_", " ")}</div>
                    {j.document_id ? <Link to={`/documents/${j.document_id}`} className="font-mono text-brand-700 hover:underline">{j.document_id}</Link> :
                      j.payload?.run_id ? <Link to={`/eval/runs/${String(j.payload.run_id)}`} className="font-mono text-brand-700 hover:underline">run {String(j.payload.run_id)}</Link> : null}</Td>
                  <Td><Badge tone={j.lane === "interactive" ? "brand" : "gray"}>{j.lane}</Badge></Td>
                  <Td><StatusBadge status={j.status} /></Td>
                  <Td className="tabular text-xs">{j.attempts}/{j.max_attempts}</Td>
                  <Td className="text-xs text-slate-600">{String(j.progress?.stage ?? "—")}{j.progress?.reused ? " (reused)" : ""}</Td>
                  <Td className="whitespace-nowrap text-xs text-slate-500">{fmtDateTime(j.created_at)}</Td>
                  <Td className="max-w-xs truncate text-xs text-rose-600" title={j.last_error ?? ""}>{j.last_error?.split("\n")[0]}</Td>
                  <Td className="whitespace-nowrap text-right">
                    {["dead", "cancelled"].includes(j.status) && <Button size="sm" onClick={() => requeue.mutate(j.id)}><RotateCcw className="size-3" /> Requeue</Button>}
                    {j.status === "queued" && <Button size="sm" variant="ghost" onClick={() => cancel.mutate(j.id)}><XCircle className="size-3" /> Cancel</Button>}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}
