import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity, ClipboardCheck, Coins, FileText, FlaskConical, LayoutDashboard, ListChecks, Search, Server, Tags,
} from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router";
import { api } from "../api";
import type { Health, Settings } from "../types";

const NAV = [
  { to: "/", label: "Agreements", icon: LayoutDashboard, end: true },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/classify", label: "Classify", icon: Tags },
  { to: "/review", label: "Review queue", icon: ClipboardCheck, badge: "review" },
  { to: "/search", label: "Search & ask", icon: Search },
  { to: "/eval", label: "Evaluation", icon: FlaskConical },
  { to: "/monitoring", label: "Monitoring", icon: Activity },
  { to: "/cost", label: "Cost", icon: Coins },
  { to: "/jobs", label: "Jobs", icon: ListChecks },
];

export default function Layout({ children }: { children: ReactNode }) {
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/api/settings"), staleTime: 60_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health"), refetchInterval: 15_000 });
  const review = useQuery({ queryKey: ["review-count"], queryFn: () => api<{ total: number }>("/api/review?limit=1"), refetchInterval: 20_000 });
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (q.trim()) navigate(`/search?q=${encodeURIComponent(q.trim())}`);
  };
  const worker = health.data?.worker;
  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <img src="/favicon.svg" className="size-8" alt="" />
          <div>
            <div className="text-[15px] font-semibold leading-tight text-ink">DocIntel</div>
            <div className="text-[11px] leading-tight text-slate-500">Agreement intelligence</div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map(({ to, label, icon: Icon, end, badge }) => (
            <NavLink key={to} to={to} end={end}
              className={({ isActive }) => clsx("flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition",
                isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900")}>
              <Icon className="size-4" />
              <span className="flex-1">{label}</span>
              {badge === "review" && (review.data?.total ?? 0) > 0 && (
                <span className="rounded-full bg-amber-100 px-1.5 text-xs font-semibold text-amber-800">{review.data?.total}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="space-y-2 border-t border-slate-100 px-5 py-4 text-xs text-slate-500">
          <div className="flex items-center gap-2">
            <Server className="size-3.5" />
            <span className="truncate">{settings.data?.llm_display_name ?? "…"}</span>
            <span className="ml-auto rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">{settings.data?.llm_provider}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={clsx("size-2 rounded-full", worker?.alive ? "bg-emerald-500" : "bg-slate-300")} />
            {worker?.alive ? `worker · ${Object.keys(worker.running).length}/${worker.slots} busy` : "worker offline"}
          </div>
          <div className="font-mono text-[10px] text-slate-400">
            pipeline {health.data?.versions.pipeline} · prompt {health.data?.versions.prompt}
          </div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center border-b border-slate-200 bg-white/80 px-8 backdrop-blur">
          <form onSubmit={submit} className="relative w-full max-w-lg">
            <Search className="absolute left-2.5 top-2.5 size-4 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search clauses, parties, terms…  (Enter)"
              className="w-full rounded-lg border-0 bg-slate-100 py-2 pl-8 pr-3 text-sm placeholder:text-slate-400 focus:bg-white focus:ring-2 focus:ring-brand-500" />
          </form>
        </header>
        <main className="scroll-thin flex-1 overflow-y-auto px-8 py-7">{children}</main>
      </div>
    </div>
  );
}
