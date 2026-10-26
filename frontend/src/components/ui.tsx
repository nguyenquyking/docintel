import clsx from "clsx";
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="truncate text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, subtitle, actions, children, className, padded = true }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; padded?: boolean;
}) {
  return (
    <section className={clsx("rounded-xl border border-slate-200 bg-white shadow-sm", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className={clsx(padded && "p-5")}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone = "default", icon }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: "default" | "good" | "warn" | "bad" | "brand"; icon?: ReactNode;
}) {
  const tones = {
    default: "text-ink", good: "text-emerald-600", warn: "text-amber-600", bad: "text-rose-600", brand: "text-brand-700",
  };
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3.5 shadow-sm">
      <div className="flex items-center justify-between text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
        {icon}
      </div>
      <div className={clsx("tabular mt-1.5 text-2xl font-semibold", tones[tone])}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

const BADGE_TONES = {
  gray: "bg-slate-100 text-slate-700 ring-slate-200",
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  red: "bg-rose-50 text-rose-700 ring-rose-200",
  blue: "bg-sky-50 text-sky-700 ring-sky-200",
  violet: "bg-violet-50 text-violet-700 ring-violet-200",
  brand: "bg-brand-50 text-brand-700 ring-brand-200",
};
export type Tone = keyof typeof BADGE_TONES;

export function Badge({ tone = "gray", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset", BADGE_TONES[tone], className)}>
      {children}
    </span>
  );
}

const STATUS: Record<string, [Tone, string]> = {
  queued: ["gray", "Queued"], processing: ["blue", "Processing"], ready: ["green", "Ready"],
  needs_review: ["amber", "Needs review"], failed: ["red", "Failed"], running: ["blue", "Running"],
  succeeded: ["green", "Succeeded"], dead: ["red", "Dead letter"], cancelled: ["gray", "Cancelled"], done: ["green", "Done"],
  active: ["green", "Active"], draft: ["violet", "Draft"], expired: ["gray", "Expired"], evergreen: ["blue", "Evergreen"],
  unknown: ["gray", "Unknown"],
};

export function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return <span className="text-slate-400">—</span>;
  const [tone, text] = STATUS[status] ?? ["gray", status];
  return (
    <Badge tone={tone}>
      {status === "processing" || status === "running" ? <Loader2 className="size-3 animate-spin" /> : null}
      {text}
    </Badge>
  );
}

const ALERTS: Record<string, [Tone, string]> = {
  plan: ["blue", "Plan"], decide: ["amber", "Decide now"], missed: ["red", "Deadline missed"], expiring: ["violet", "Expiring"],
};

export function AlertBadge({ level }: { level?: string | null }) {
  if (!level) return null;
  const [tone, text] = ALERTS[level] ?? ["gray", level];
  return <Badge tone={tone}>{text}</Badge>;
}

export function Confidence({ value, compact }: { value?: number | null; compact?: boolean }) {
  if (value === null || value === undefined) return <span className="text-slate-400">—</span>;
  const pct = Math.round(value * 100);
  const color = value >= 0.85 ? "bg-emerald-500" : value >= 0.7 ? "bg-amber-400" : "bg-rose-500";
  return (
    <span className="inline-flex items-center gap-1.5" title={`confidence ${pct}%`}>
      {!compact && (
        <span className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-200">
          <span className={clsx("block h-full rounded-full", color)} style={{ width: `${pct}%` }} />
        </span>
      )}
      <span className="tabular text-xs text-slate-600">{pct}%</span>
    </span>
  );
}

export function Grounded({ value }: { value: boolean | null | undefined }) {
  if (value === true) return <CheckCircle2 className="size-4 text-emerald-500" aria-label="quote found on the page" />;
  if (value === false) return <XCircle className="size-4 text-rose-500" aria-label="quote not found on the page" />;
  return <CircleDashed className="size-4 text-slate-300" aria-label="no quote" />;
}

export function Empty({ title, hint, icon }: { title: string; hint?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      {icon}
      <div className="text-sm font-medium text-slate-700">{title}</div>
      {hint && <div className="max-w-md text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 text-sm text-slate-500">
      <Loader2 className="size-4 animate-spin" /> {label}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  );
}

export function Button({ children, onClick, variant = "secondary", disabled, type = "button", title, className, size = "md" }: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "ghost" | "danger"; disabled?: boolean;
  type?: "button" | "submit"; title?: string; className?: string; size?: "sm" | "md";
}) {
  const styles = {
    primary: "bg-brand-600 text-white hover:bg-brand-700 shadow-sm",
    secondary: "bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50",
    ghost: "text-slate-600 hover:bg-slate-100",
    danger: "bg-white text-rose-600 ring-1 ring-inset ring-rose-200 hover:bg-rose-50",
  };
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick}
      className={clsx("inline-flex items-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm", styles[variant], className)}>
      {children}
    </button>
  );
}

export function Select({ value, onChange, options, className }: {
  value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; className?: string;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className={clsx("rounded-lg border-0 bg-white py-1.5 pl-2.5 pr-8 text-sm text-slate-700 ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-500", className)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input {...props}
      className={clsx("rounded-lg border-0 bg-white px-2.5 py-1.5 text-sm text-slate-800 ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-brand-500", props.className)} />
  );
}

export function Tabs<T extends string>({ value, onChange, tabs }: { value: T; onChange: (t: T) => void; tabs: { id: T; label: ReactNode }[] }) {
  return (
    <div className="flex gap-1 border-b border-slate-200">
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)}
          className={clsx("-mb-px border-b-2 px-3 py-2 text-sm font-medium transition",
            value === t.id ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-700")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return <th className={clsx("px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500", className)}>{children}</th>;
}

export function Td({ children, className, title }: { children?: ReactNode; className?: string; title?: string }) {
  return <td title={title} className={clsx("px-3 py-2 align-top text-sm", className)}>{children}</td>;
}

export function Bar({ value, max = 1, tone = "brand" }: { value: number; max?: number; tone?: "brand" | "good" | "bad" | "warn" }) {
  const colors = { brand: "bg-brand-500", good: "bg-emerald-500", bad: "bg-rose-500", warn: "bg-amber-400" };
  return (
    <span className="block h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
      <span className={clsx("block h-full rounded-full", colors[tone])} style={{ width: `${Math.max(0, Math.min(1, value / max)) * 100}%` }} />
    </span>
  );
}
