import type { Money } from "./types";

export const FIELD_LABELS: Record<string, string> = {
  document_type: "Document type",
  title: "Title",
  parties: "Parties",
  signatories: "Signatories",
  effective_date: "Effective date",
  expiration_type: "Term type",
  expiration_date: "Expiration date",
  initial_term_months: "Initial term (months)",
  auto_renewal: "Auto-renewal",
  renewal_term_months: "Renewal term (months)",
  non_renewal_notice: "Notice to prevent renewal",
  termination_for_convenience: "Termination for convenience",
  termination_notice_days: "Termination notice (days)",
  contract_value: "Contract value",
  payment_terms_days: "Payment terms (days)",
  line_items: "Line items",
  governing_law: "Governing law",
  is_executed: "Executed",
  initial_term_end: "Initial term end",
  non_renewal_notice_days: "Notice (days)",
  notice_deadline: "Cancel-by (initial term)",
  value_period: "Value period",
};

export const GROUP_LABELS: Record<string, string> = {
  parties: "Parties & signatures",
  term: "Term, renewal & termination",
  commercial: "Commercial",
  law: "Document & governing law",
};

export const label = (f: string) => FIELD_LABELS[f] ?? f.replace(/_/g, " ");

export function fmtDate(d?: string | null): string {
  if (!d) return "—";
  const dt = new Date(d.length === 10 ? d + "T00:00:00" : d);
  if (Number.isNaN(dt.getTime())) return d;
  return dt.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function fmtDateTime(d?: string | null): string {
  if (!d) return "—";
  const dt = new Date(d);
  return dt.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function fmtMoney(v?: Money | null, period?: string | null): string {
  if (!v) return "—";
  const digits = v.currency === "VND" ? 0 : 2;
  const n = v.amount.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const per = period && !["total", "one_time"].includes(period) ? ` / ${period.replace("ly", "").replace("annual", "year")}` : "";
  return `${v.currency} ${n}${per}`;
}

export const fmtPct = (v?: number | null, digits = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`);
export const fmtNum = (v?: number | null, digits = 0) =>
  v === null || v === undefined ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
export const fmtUsd = (v?: number | null, digits = 4) => (v === null || v === undefined ? "—" : `$${v.toFixed(digits)}`);
export const fmtMs = (v?: number | null) =>
  v === null || v === undefined ? "—" : v >= 60000 ? `${(v / 60000).toFixed(1)} min` : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`;

export function fmtValue(field: string, v: unknown): string {
  if (v === null || v === undefined || (Array.isArray(v) && v.length === 0)) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (field.endsWith("_date") || field === "initial_term_end" || field === "notice_deadline") return fmtDate(String(v));
  if (field === "contract_value" && typeof v === "object" && v && "amount" in v) return fmtMoney(v as Money);
  if (field === "non_renewal_notice" && typeof v === "object" && v && "amount" in v) {
    const n = v as { amount: number; unit: string };
    return `${n.amount} ${n.unit}`;
  }
  if (Array.isArray(v)) {
    return v
      .map((x) => (typeof x === "object" && x ? String((x as Record<string, unknown>).name ?? (x as Record<string, unknown>).description ?? JSON.stringify(x)) : String(x)))
      .join(" · ");
  }
  if (typeof v === "object") return JSON.stringify(v);
  return String(v).replace(/_/g, " ");
}

export const LANG: Record<string, string> = { en: "English", vi: "Vietnamese", bilingual: "EN + VI", unknown: "—" };
