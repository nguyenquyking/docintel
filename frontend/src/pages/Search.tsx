import { useMutation } from "@tanstack/react-query";
import { MessageSquareText, Search as SearchIcon } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { post } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, Grounded, Input, Loading, PageHeader, Select, StatusBadge } from "../components/ui";
import { fmtUsd } from "../format";

interface Hit {
  chunk_id: number; document_id: string; title: string; filename: string; page_start: number; page_end: number;
  section: string | null; text: string; score: number; doc_type: string | null; status: string | null;
}
interface Answer {
  answer: string; citations: { document_id: string; page: number; quote: string; grounded: boolean; title: string }[];
  passages: Hit[]; cost_usd: number;
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [mode, setMode] = useState<"search" | "ask">("search");
  const [docType, setDocType] = useState("");
  const [status, setStatus] = useState("");
  const [auto, setAuto] = useState("");
  const [deadline, setDeadline] = useState("");
  const [party, setParty] = useState("");
  const filters = {
    doc_type: docType || undefined, status: status || undefined, party: party || undefined,
    auto_renewal: auto === "" ? undefined : auto === "yes", deadline_within_days: deadline ? Number(deadline) : undefined,
  };
  const search = useMutation({ mutationFn: (q: string) => post<{ results: Hit[] }>("/api/search", { query: q, filters, limit: 25 }) });
  const ask = useMutation({ mutationFn: (q: string) => post<Answer>("/api/ask", { question: q, filters }) });

  useEffect(() => {
    const q = params.get("q");
    if (q) { setQuery(q); search.mutate(q); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setParams({ q: query });
    if (mode === "ask") ask.mutate(query); else search.mutate(query);
  };

  return (
    <>
      <PageHeader title="Search & ask" subtitle="Hybrid retrieval (BM25 + multilingual embeddings) over section-aware chunks, filtered by extracted facts. Answers cite pages, and every cited quote is checked against the page." />
      <form onSubmit={submit} className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg bg-slate-100 p-0.5">
          {(["search", "ask"] as const).map((m) => (
            <button type="button" key={m} onClick={() => setMode(m)}
              className={`rounded-md px-3 py-1 text-sm font-medium ${mode === m ? "bg-white text-ink shadow-sm" : "text-slate-500"}`}>
              {m === "search" ? "Search" : "Ask"}
            </button>
          ))}
        </div>
        <Input value={query} onChange={(e) => setQuery(e.target.value)} className="min-w-[24rem] flex-1"
          placeholder={mode === "ask" ? "e.g. Which agreements renew automatically and how much notice do they need?" : "e.g. termination for convenience notice"} />
        <Button variant="primary" type="submit">{mode === "ask" ? <MessageSquareText className="size-4" /> : <SearchIcon className="size-4" />}{mode === "ask" ? "Ask" : "Search"}</Button>
      </form>
      <div className="mb-5 flex flex-wrap gap-2">
        <Select value={docType} onChange={setDocType} options={[{ value: "", label: "Any type" }, ...["services_agreement", "nda", "lease", "purchase_agreement", "sow"].map((t) => ({ value: t, label: t.replace(/_/g, " ") }))]} />
        <Select value={status} onChange={setStatus} options={[{ value: "", label: "Any status" }, ...["active", "draft", "expired", "evergreen"].map((t) => ({ value: t, label: t }))]} />
        <Select value={auto} onChange={setAuto} options={[{ value: "", label: "Auto-renewal: any" }, { value: "yes", label: "Auto-renews" }, { value: "no", label: "Does not auto-renew" }]} />
        <Select value={deadline} onChange={setDeadline} options={[{ value: "", label: "Cancel-by: any" }, { value: "30", label: "Cancel-by ≤ 30 days" }, { value: "90", label: "Cancel-by ≤ 90 days" }, { value: "365", label: "Cancel-by ≤ 1 year" }]} />
        <Input placeholder="Party contains…" value={party} onChange={(e) => setParty(e.target.value)} />
      </div>

      {mode === "ask" && (ask.isPending ? <Loading label="Retrieving passages and asking the model…" /> : ask.error ? <ErrorBox error={ask.error} /> : ask.data && (
        <Card title="Answer" subtitle={`${ask.data.passages.length} passages retrieved · ${fmtUsd(ask.data.cost_usd)}`} className="mb-6">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">{ask.data.answer}</p>
          {ask.data.citations.length > 0 && (
            <ul className="mt-4 space-y-2">
              {ask.data.citations.map((c, i) => (
                <li key={i} className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs">
                  <Grounded value={c.grounded} />
                  <div><Link to={`/documents/${c.document_id}`} className="font-medium text-brand-700 hover:underline">{c.title}</Link> <span className="text-slate-500">p.{c.page}</span>
                    <div className="mt-0.5 text-slate-600">“{c.quote}”</div>
                    {!c.grounded && <div className="mt-0.5 text-rose-600">quote not found on that page — treat with care</div>}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ))}

      {mode === "search" && (search.isPending ? <Loading label="Searching…" /> : search.error ? <ErrorBox error={search.error} /> : search.data && (
        search.data.results.length === 0 ? <Empty title="No matching passages" hint="Try other words, remove filters, or switch to Ask." /> : (
          <div className="space-y-3">
            {search.data.results.map((h) => (
              <Card key={h.chunk_id} padded>
                <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <Link to={`/documents/${h.document_id}`} className="text-sm font-medium text-brand-700 hover:underline">{h.title}</Link>
                  <span>p.{h.page_start}{h.page_end !== h.page_start ? `–${h.page_end}` : ""}</span>
                  {h.section && <Badge>{h.section.slice(0, 60)}</Badge>}
                  {h.doc_type && <Badge tone="brand">{h.doc_type.replace(/_/g, " ")}</Badge>}
                  <StatusBadge status={h.status} />
                </div>
                <p className="line-clamp-4 text-sm text-slate-700">{h.text}</p>
              </Card>
            ))}
          </div>
        )
      ))}
    </>
  );
}
