import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardCheck } from "lucide-react";
import { Link } from "react-router";
import { api, post } from "../api";
import { Badge, Button, Card, Confidence, Empty, ErrorBox, Grounded, Loading, PageHeader, Td, Th } from "../components/ui";
import { fmtValue, label } from "../format";

interface ReviewItem {
  document_id: string; title: string; field: string; critical: boolean; value: unknown; confidence: number;
  flags: string[]; grounded: boolean | null; page: number | null; quote: string | null;
}

export default function Review() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["review"], queryFn: () => api<{ total: number; items: ReviewItem[] }>("/api/review") });
  const approve = useMutation({
    mutationFn: (i: ReviewItem) => post(`/api/documents/${i.document_id}/review`, { field: i.field, action: "approve" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["review"] }); qc.invalidateQueries({ queryKey: ["review-count"] }); },
  });
  return (
    <>
      <PageHeader title="Review queue" subtitle="Fields the pipeline is not sure about, critical fields first. Every decision is a label: corrections measure live accuracy and can become golden data." />
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : (
        <Card padded={false} title={`${q.data!.total} field(s) waiting`}>
          {q.data!.items.length === 0 ? (
            <Empty icon={<ClipboardCheck className="size-6 text-emerald-400" />} title="Nothing to review" hint="Fields below the confidence threshold, with an ungrounded quote or a failed consistency check, land here." />
          ) : (
            <table className="w-full">
              <thead className="border-b border-slate-100"><tr><Th>Document</Th><Th>Field</Th><Th>Value</Th><Th>Why</Th><Th>Confidence</Th><Th></Th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {q.data!.items.map((i) => (
                  <tr key={`${i.document_id}-${i.field}`} className="hover:bg-slate-50">
                    <Td className="max-w-[14rem]"><Link to={`/documents/${i.document_id}`} className="font-medium text-brand-700 hover:underline">{i.title}</Link></Td>
                    <Td className="whitespace-nowrap">{label(i.field)} {i.critical && <Badge tone="brand">critical</Badge>}</Td>
                    <Td className="max-w-xs">
                      <div className="text-ink">{fmtValue(i.field, i.value)}</div>
                      {i.quote && <div className="mt-1 line-clamp-2 text-xs text-slate-500">“{i.quote}” {i.page && <span className="text-brand-700">p.{i.page}</span>}</div>}
                    </Td>
                    <Td className="max-w-xs">
                      <div className="flex items-center gap-1.5 text-xs text-slate-600"><Grounded value={i.grounded} />{i.grounded === false ? "quote not on the page" : i.grounded === null ? "no evidence quote" : "quote verified"}</div>
                      {i.flags.map((f, k) => <Badge key={k} tone="amber" className="mt-1 mr-1">{f}</Badge>)}
                    </Td>
                    <Td><Confidence value={i.confidence} /></Td>
                    <Td className="whitespace-nowrap text-right">
                      <Button size="sm" onClick={() => approve.mutate(i)}><Check className="size-3" /> Approve</Button>{" "}
                      <Link to={`/documents/${i.document_id}`} className="text-xs text-brand-700 hover:underline">Open</Link>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </>
  );
}
