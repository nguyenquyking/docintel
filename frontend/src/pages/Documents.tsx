import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, FileUp, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { Badge, Button, Card, Confidence, Empty, ErrorBox, Input, Loading, PageHeader, Select, StatusBadge, Td, Th } from "../components/ui";
import { LANG, fmtDate, fmtDateTime } from "../format";
import type { DocSummary } from "../types";

function RouteMix({ mix }: { mix: Record<string, number> }) {
  const parts = Object.entries(mix);
  if (!parts.length) return <span className="text-slate-400">—</span>;
  const tone = { text_layer: "gray", ocr: "blue", vlm: "violet" } as const;
  return (
    <div className="flex flex-wrap gap-1">
      {parts.map(([k, n]) => <Badge key={k} tone={tone[k as keyof typeof tone] ?? "gray"} title={`${n} page(s) via ${k}`}>{k === "text_layer" ? "text" : k} {n}</Badge>)}
    </div>
  );
}

export default function Documents() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [lane, setLane] = useState("interactive");
  const [message, setMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const q = useQuery({
    queryKey: ["documents", status, search],
    queryFn: () => api<{ total: number; documents: DocSummary[] }>(`/api/documents?limit=500${status ? `&status=${status}` : ""}${search ? `&q=${encodeURIComponent(search)}` : ""}`),
    refetchInterval: (query) => (query.state.data?.documents.some((d) => ["queued", "processing"].includes(d.status)) ? 2500 : 15000),
  });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      fd.append("lane", lane);
      return api<{ documents: { id?: string; filename: string; duplicate?: boolean; error?: string }[] }>("/api/documents", { method: "POST", body: fd });
    },
    onSuccess: (r) => {
      const ok = r.documents.filter((d) => d.id).length;
      const dup = r.documents.filter((d) => d.duplicate).length;
      const errs = r.documents.filter((d) => d.error);
      setMessage(`${ok} queued on the ${lane} lane${dup ? ` · ${dup} duplicate(s): reused without a model call` : ""}${errs.length ? ` · ${errs.length} rejected (${errs[0].error})` : ""}`);
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const onFiles = (list: FileList | null) => {
    const files = Array.from(list ?? []).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    if (files.length) upload.mutate(files);
  };

  return (
    <>
      <PageHeader title="Documents" subtitle="Every upload is parsed page by page (text layer, local OCR or a vision model), extracted with evidence, and verified."
        actions={<>
          <Select value={lane} onChange={setLane} options={[{ value: "interactive", label: "Interactive lane" }, { value: "backfill", label: "Backfill lane" }]} />
          <Button variant="primary" onClick={() => fileRef.current?.click()}><Upload className="size-4" /> Upload PDFs</Button>
          <input ref={fileRef} type="file" accept="application/pdf" multiple hidden onChange={(e) => onFiles(e.target.files)} />
        </>} />

      <div onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); onFiles(e.dataTransfer.files); }}
        className={`mb-4 flex items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-5 text-sm transition ${dragging ? "border-brand-500 bg-brand-50 text-brand-700" : "border-slate-300 bg-white text-slate-500"}`}>
        <FileUp className="size-4" /> Drop contracts here — identical files are detected by content hash and never processed twice.
      </div>
      {message && <div className="mb-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message}</div>}
      {upload.error && <ErrorBox error={upload.error} />}

      <Card padded={false} title={`${q.data?.total ?? 0} documents`} actions={
        <div className="flex gap-2">
          <Input placeholder="Filter by title or party" value={search} onChange={(e) => setSearch(e.target.value)} />
          <Select value={status} onChange={setStatus} options={[{ value: "", label: "All statuses" }, ...["queued", "processing", "ready", "needs_review", "failed"].map((s) => ({ value: s, label: s.replace("_", " ") }))]} />
        </div>}>
        {q.isLoading ? <Loading /> : q.error ? <div className="p-4"><ErrorBox error={q.error} /></div> : q.data!.documents.length === 0 ? (
          <Empty title="No documents yet" hint="Upload a PDF agreement to see its parties, term, renewal and value extracted with evidence." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-slate-100"><tr><Th>Document</Th><Th>Type</Th><Th>Language</Th><Th>Pages</Th><Th>Parsed via</Th><Th>Status</Th><Th>Confidence</Th><Th>Cancel-by</Th><Th>Added</Th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {q.data!.documents.map((d) => (
                  <tr key={d.id} className="hover:bg-slate-50">
                    <Td className="max-w-sm">
                      <Link to={`/documents/${d.id}`} className="font-medium text-brand-700 hover:underline">{d.title || d.filename}</Link>
                      <div className="truncate text-xs text-slate-500" title={d.parties.join(" · ")}>{d.parties.join(" · ") || d.filename}</div>
                      {d.duplicate_of && <Badge tone="gray" className="mt-1"><Copy className="size-3" /> duplicate</Badge>}
                    </Td>
                    <Td className="text-slate-600">{d.doc_type?.replace(/_/g, " ") ?? "—"}</Td>
                    <Td className="text-slate-600">{LANG[d.language ?? "unknown"] ?? d.language}</Td>
                    <Td className="tabular text-slate-600">{d.page_count ?? "—"}</Td>
                    <Td><RouteMix mix={d.route_mix} /></Td>
                    <Td><StatusBadge status={d.status} />{d.error && d.status === "failed" && <div className="mt-1 max-w-[16rem] truncate text-xs text-rose-600" title={d.error}>{d.error}</div>}</Td>
                    <Td><Confidence value={d.confidence} /></Td>
                    <Td className="tabular text-slate-600">{fmtDate(d.next_notice_deadline)}</Td>
                    <Td className="whitespace-nowrap text-xs text-slate-500">{fmtDateTime(d.created_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
