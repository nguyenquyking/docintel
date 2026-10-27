import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, FileUp, Image as ImageIcon, Loader2, Pencil, Plus, RotateCcw, Save, Tags, Trash2, Upload, X } from "lucide-react";
import { useRef, useState } from "react";
import { api, del, put } from "../api";
import { Badge, Button, Card, Confidence, Empty, ErrorBox, Grounded, Input, Loading, PageHeader, Select } from "../components/ui";
import type { ClassifyResult, DocType, DocTypes } from "../types";

type Item = { key: string; filename: string; state: "running" | "done" | "error"; result?: ClassifyResult; error?: string };

const PAGE_OPTIONS = [0, 1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: String(n) }));

function PagesRead({ r }: { r: ClassifyResult }) {
  const chips: React.ReactNode[] = [];
  r.pages.forEach((p, i) => {
    if (i > 0 && p.number !== r.pages[i - 1].number + 1) {
      chips.push(<span key={`gap-${p.number}`} className="px-0.5 text-slate-400">… {p.number - r.pages[i - 1].number - 1} skipped …</span>);
    }
    chips.push(
      <Badge key={p.number} tone={p.route === "image" ? "violet" : "gray"} title={p.route === "image" ? "no text layer: read as an image" : "text layer"}>
        {p.route === "image" && <ImageIcon className="size-3" />}p.{p.number}
      </Badge>,
    );
  });
  return <div className="flex flex-wrap items-center gap-1 text-xs">{chips}<span className="ml-1 text-slate-400">of {r.pages_total}</span></div>;
}

function ResultCard({ item }: { item: Item }) {
  const r = item.result;
  return (
    <Card className="overflow-hidden" padded={false}>
      <div className="flex items-center gap-3 border-b border-slate-100 px-5 py-3">
        <div className="min-w-0 flex-1 truncate text-sm font-medium text-ink" title={item.filename}>{item.filename}</div>
        {r && (
          <div className="tabular shrink-0 text-xs text-slate-500">
            {(r.latency_ms / 1000).toFixed(1)} s · {(r.usage.prompt_tokens + r.usage.completion_tokens).toLocaleString()} tokens
            {r.cache_hit ? " · cached" : ` · $${r.cost_usd.toFixed(4)}`}
          </div>
        )}
      </div>
      <div className="px-5 py-4">
        {item.state === "running" && <div className="flex items-center gap-2 text-sm text-slate-500"><Loader2 className="size-4 animate-spin" /> Reading the first and last pages…</div>}
        {item.state === "error" && <ErrorBox error={item.error} />}
        {r && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <span className={clsx("rounded-lg px-3 py-1.5 text-lg font-semibold tracking-tight",
                r.document_type === "OTHER" ? "bg-slate-100 text-slate-700" : "bg-brand-50 text-brand-700")}>{r.document_type}</span>
              <span className="text-sm text-slate-700">{r.label}</span>
              <Confidence value={r.confidence} />
              {r.needs_review
                ? <Badge tone="amber" title={r.review_reasons.join(" · ")}><AlertTriangle className="size-3" /> Needs review</Badge>
                : <Badge tone="green">Confident</Badge>}
              {r.runner_up && <span className="text-xs text-slate-500">runner-up <span className="font-mono">{r.runner_up}</span></span>}
            </div>
            <div className="grid gap-x-6 gap-y-1 text-xs text-slate-600 sm:grid-cols-[auto_1fr]">
              <span className="text-slate-400">Title</span><span className="text-slate-700">{r.title ?? "—"}</span>
              <span className="text-slate-400">Language</span><span>{r.language ?? "—"}</span>
              <span className="text-slate-400">Pages read</span><PagesRead r={r} />
              <span className="text-slate-400">Why</span><span className="text-slate-700">{r.reasoning}</span>
            </div>
            {r.evidence.length > 0 && (
              <ul className="space-y-1.5 rounded-lg bg-slate-50 px-3 py-2.5">
                {r.evidence.map((e, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5 shrink-0"><Grounded value={e.grounded} /></span>
                    <span className="tabular shrink-0 text-xs leading-5 text-slate-400">p.{e.page}</span>
                    <span className="text-slate-700">“{e.quote}”{e.route === "image" && <span className="ml-1.5 text-xs text-violet-600">(read from the image)</span>}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function Taxonomy() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["doc-types"], queryFn: () => api<DocTypes>("/api/classify/types") });
  const [draft, setDraft] = useState<DocType[] | null>(null);
  const save = useMutation({
    mutationFn: (types: DocType[]) => put<DocTypes>("/api/classify/types", { types }),
    onSuccess: (d) => { qc.setQueryData(["doc-types"], d); setDraft(null); },
  });
  const reset = useMutation({
    mutationFn: () => del<DocTypes>("/api/classify/types"),
    onSuccess: (d) => { qc.setQueryData(["doc-types"], d); setDraft(null); },
  });
  const edit = (i: number, patch: Partial<DocType>) => setDraft((d) => d && d.map((t, j) => (j === i ? { ...t, ...patch } : t)));

  return (
    <Card title={<span className="flex items-center gap-1.5"><Tags className="size-4" /> Document types</span>}
      subtitle={q.data ? `${q.data.types.length} types + OTHER · ${q.data.custom ? "custom taxonomy" : "default taxonomy"} · the model reads these definitions; no training` : undefined}
      actions={draft ? (
        <div className="flex gap-2">
          <Button size="sm" onClick={() => setDraft(null)}><X className="size-3.5" /> Cancel</Button>
          <Button size="sm" variant="primary" disabled={save.isPending} onClick={() => save.mutate(draft)}><Save className="size-3.5" /> Save</Button>
        </div>
      ) : (
        <div className="flex gap-2">
          {q.data?.custom && <Button size="sm" variant="ghost" disabled={reset.isPending} onClick={() => reset.mutate()}><RotateCcw className="size-3.5" /> Defaults</Button>}
          <Button size="sm" disabled={!q.data} onClick={() => setDraft(q.data!.types.map((t) => ({ ...t })))}><Pencil className="size-3.5" /> Edit</Button>
        </div>
      )}>
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {(save.error || reset.error) && <div className="mb-3"><ErrorBox error={save.error ?? reset.error} /></div>}
      {draft ? (
        <div className="space-y-2">
          {draft.map((t, i) => (
            <div key={i} className="grid grid-cols-[9rem_11rem_1fr_auto] items-start gap-2">
              <Input value={t.code} placeholder="CODE" className="font-mono uppercase" onChange={(e) => edit(i, { code: e.target.value })} />
              <Input value={t.label} placeholder="Label" onChange={(e) => edit(i, { label: e.target.value })} />
              <textarea value={t.definition} rows={2} placeholder="What it is, and the cues that tell it apart from similar types"
                onChange={(e) => edit(i, { definition: e.target.value })}
                className="rounded-lg border-0 bg-white px-2.5 py-1.5 text-sm text-slate-800 ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-brand-500" />
              <Button size="sm" variant="ghost" title="Remove" onClick={() => setDraft((d) => d && d.filter((_, j) => j !== i))}><Trash2 className="size-3.5" /></Button>
            </div>
          ))}
          <Button size="sm" onClick={() => setDraft((d) => d && [...d, { code: "", label: "", definition: "" }])}><Plus className="size-3.5" /> Add type</Button>
        </div>
      ) : (
        <div className="grid gap-x-6 gap-y-2.5 md:grid-cols-2">
          {q.data?.types.map((t) => (
            <div key={t.code} className="min-w-0 text-sm">
              <span className="mr-2 font-mono text-xs font-semibold text-brand-700">{t.code}</span>
              <span className="font-medium text-slate-800">{t.label}</span>
              <p className="line-clamp-2 text-xs text-slate-500" title={t.definition}>{t.definition}</p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function Classify() {
  const [items, setItems] = useState<Item[]>([]);
  const [head, setHead] = useState("3");
  const [tail, setTail] = useState("2");
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const update = (key: string, patch: Partial<Item>) => setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...patch } : it)));

  const run = (list: FileList | null) => {
    const files = Array.from(list ?? []).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    if (!files.length) return;
    const stamp = Date.now();
    const batch: Item[] = files.map((f, i) => ({ key: `${stamp}-${i}`, filename: f.name, state: "running" }));
    setItems((prev) => [...batch, ...prev]);
    files.forEach((f, i) => {
      const fd = new FormData();
      fd.append("files", f);
      fd.append("head", head);
      fd.append("tail", tail);
      api<{ results: ClassifyResult[] }>("/api/classify", { method: "POST", body: fd })
        .then(({ results: [r] }) => update(batch[i].key, r.error ? { state: "error", error: r.error } : { state: "done", result: r }))
        .catch((e: unknown) => update(batch[i].key, { state: "error", error: e instanceof Error ? e.message : String(e) }));
    });
    if (fileRef.current) fileRef.current.value = "";
  };

  const done = items.filter((i) => i.result);
  const counts = done.reduce<Record<string, number>>((acc, i) => ({ ...acc, [i.result!.document_type]: (acc[i.result!.document_type] ?? 0) + 1 }), {});
  const running = items.filter((i) => i.state === "running").length;
  const review = done.filter((i) => i.result!.needs_review).length;

  return (
    <>
      <PageHeader title="Classify" subtitle="What is this document? Decided from the pages where documents say so: the first pages (title, parties, purpose) and the last (signatures, stamps). Scanned pages are read as images."
        actions={<>
          <span className="text-sm text-slate-500">First</span>
          <Select value={head} onChange={setHead} options={PAGE_OPTIONS} />
          <span className="text-sm text-slate-500">+ last</span>
          <Select value={tail} onChange={setTail} options={PAGE_OPTIONS} />
          <span className="text-sm text-slate-500">pages</span>
          <Button variant="primary" onClick={() => fileRef.current?.click()}><Upload className="size-4" /> Classify PDFs</Button>
          <input ref={fileRef} type="file" accept="application/pdf" multiple hidden onChange={(e) => run(e.target.files)} />
        </>} />

      <div onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); run(e.dataTransfer.files); }}
        className={`mb-4 flex items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-6 text-sm transition ${dragging ? "border-brand-500 bg-brand-50 text-brand-700" : "border-slate-300 bg-white text-slate-500"}`}>
        <FileUp className="size-4" /> Drop PDFs here: contracts, NDAs, visas, passports, invoices… Nothing is stored.
      </div>

      {items.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-sm text-slate-600">
          <span className="font-medium text-ink">{done.length} classified</span>
          {running > 0 && <span className="flex items-center gap-1 text-slate-500"><Loader2 className="size-3.5 animate-spin" /> {running} running</span>}
          {review > 0 && <Badge tone="amber">{review} need review</Badge>}
          {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([code, n]) => <Badge key={code} tone="brand">{code} · {n}</Badge>)}
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setItems([])}>Clear</Button>
        </div>
      )}

      <div className="space-y-3">
        {items.length === 0
          ? <Card><Empty title="No documents classified yet" icon={<Tags className="size-8 text-slate-300" />}
              hint="Drop one or many PDFs. Each gets one type from the taxonomy below, a confidence, and the quotes that decided it, checked against the page text." /></Card>
          : items.map((it) => <ResultCard key={it.key} item={it} />)}
        <Taxonomy />
      </div>
    </>
  );
}
