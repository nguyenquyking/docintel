import { useQuery } from "@tanstack/react-query";
import { AlarmClock, CalendarClock, ClipboardCheck, FileSignature, FileText, Repeat } from "lucide-react";
import { Link } from "react-router";
import { api } from "../api";
import { AlertBadge, Badge, Card, Empty, ErrorBox, Loading, PageHeader, Stat, StatusBadge, Td, Th } from "../components/ui";
import { fmtDate, fmtMoney, label } from "../format";
import type { DocSummary } from "../types";

interface DashboardData {
  today: string;
  kpis: Record<string, number>;
  annual_value_by_currency: Record<string, number>;
  alerts: DocSummary[];
  data_gaps: { document_id: string; title: string; missing: string[] }[];
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  recent: DocSummary[];
}

export default function Dashboard() {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<DashboardData>("/api/dashboard"), refetchInterval: 15_000 });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const k = d.kpis;
  return (
    <>
      <PageHeader title="Agreements" subtitle={`What every contract commits you to — and what needs a decision. Today: ${fmtDate(d.today)}`} />
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Documents" value={k.documents} sub={`${k.processed} processed · ${k.in_pipeline} in pipeline`} icon={<FileText className="size-4 text-slate-400" />} />
        <Stat label="Active" value={k.active} sub={`${k.expired} expired`} tone="good" icon={<FileSignature className="size-4 text-emerald-500" />} />
        <Stat label="Auto-renewing" value={k.auto_renewing} sub="active, renew unless cancelled" icon={<Repeat className="size-4 text-brand-500" />} />
        <Stat label="Decide now" value={k.decide_now} sub="cancel-by ≤ 30 days or missed" tone={k.decide_now ? "warn" : "default"} icon={<AlarmClock className="size-4 text-amber-500" />} />
        <Stat label="Needs review" value={k.needs_review} sub="low-confidence fields" tone={k.needs_review ? "warn" : "default"} icon={<ClipboardCheck className="size-4 text-amber-500" />} />
        <Stat label="Drafts" value={k.drafts} sub="unsigned: no renewal alerts" icon={<FileText className="size-4 text-violet-400" />} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Upcoming renewal decisions"
          subtitle="Layered alerts: plan (≤ 90 days before the cancel-by date), decide (≤ 30 days), missed (it will renew)." padded={false}>
          {d.alerts.length === 0 ? (
            <Empty icon={<CalendarClock className="size-6 text-slate-300" />} title="No deadlines in the next 90 days"
              hint="Upload agreements — auto-renewal terms and notice periods are extracted and turned into cancel-by dates." />
          ) : (
            <table className="w-full">
              <thead className="border-b border-slate-100"><tr><Th>Agreement</Th><Th>Parties</Th><Th>Alert</Th><Th>Cancel-by</Th><Th>Term ends</Th><Th className="text-right">Days</Th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {d.alerts.map((a) => (
                  <tr key={a.id} className="hover:bg-slate-50">
                    <Td><Link to={`/documents/${a.id}`} className="font-medium text-brand-700 hover:underline">{a.title || a.filename}</Link></Td>
                    <Td className="max-w-xs truncate text-slate-600" title={a.parties.join(" · ")}>{a.parties.join(" · ")}</Td>
                    <Td><AlertBadge level={a.alert} /></Td>
                    <Td className="tabular">{fmtDate(a.next_notice_deadline)}</Td>
                    <Td className="tabular">{fmtDate(a.current_term_end)}</Td>
                    <Td className="tabular text-right">{a.alert_days ?? "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <div className="space-y-6">
          <Card title="Committed value per year" subtitle="Active recurring agreements, annualized">
            {Object.keys(d.annual_value_by_currency).length === 0 ? <div className="text-sm text-slate-500">—</div> : (
              <ul className="space-y-2">
                {Object.entries(d.annual_value_by_currency).map(([cur, v]) => (
                  <li key={cur} className="flex justify-between text-sm"><span className="text-slate-500">{cur}</span>
                    <span className="tabular font-semibold text-ink">{fmtMoney({ amount: v, currency: cur })}</span></li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="By status">
            <div className="flex flex-wrap gap-2">
              {Object.entries(d.by_status).map(([s, n]) => (
                <span key={s} className="inline-flex items-center gap-1.5"><StatusBadge status={s} /><span className="tabular text-sm text-slate-600">{n}</span></span>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              {Object.entries(d.by_type).map(([t, n]) => <Badge key={t}>{t.replace(/_/g, " ")} · {n}</Badge>)}
            </div>
          </Card>
        </div>

        <Card className="xl:col-span-2" title="Recently added" padded={false}>
          <table className="w-full">
            <thead className="border-b border-slate-100"><tr><Th>Document</Th><Th>Type</Th><Th>Status</Th><Th>Value</Th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {d.recent.map((r) => (
                <tr key={r.id} className="hover:bg-slate-50">
                  <Td><Link to={`/documents/${r.id}`} className="font-medium text-brand-700 hover:underline">{r.title || r.filename}</Link></Td>
                  <Td className="text-slate-600">{r.doc_type?.replace(/_/g, " ") ?? "—"}</Td>
                  <Td><StatusBadge status={r.status} /> {r.agreement_status && <StatusBadge status={r.agreement_status} />}</Td>
                  <Td className="tabular text-slate-600">{fmtMoney(r.contract_value, r.value_period)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.recent.length === 0 && <Empty title="Nothing here yet" hint={<>Go to <Link className="text-brand-700 underline" to="/documents">Documents</Link> and upload PDFs.</>} />}
        </Card>

        <Card title="Data gaps" subtitle="Executed agreements missing key terms — check them first">
          {d.data_gaps.length === 0 ? <div className="text-sm text-slate-500">None — every processed agreement has its key terms.</div> : (
            <ul className="space-y-2.5">
              {d.data_gaps.map((g) => (
                <li key={g.document_id} className="text-sm">
                  <Link to={`/documents/${g.document_id}`} className="font-medium text-brand-700 hover:underline">{g.title}</Link>
                  <div className="mt-1 flex flex-wrap gap-1">{g.missing.map((m) => <Badge key={m} tone="amber">{label(m)}</Badge>)}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
