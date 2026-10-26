export type Money = { amount: number; currency: string };

export interface DocSummary {
  id: string;
  filename: string;
  title: string | null;
  status: string;
  error: string | null;
  source: string;
  lane: string;
  page_count: number | null;
  language: string | null;
  doc_type: string | null;
  route_mix: Record<string, number>;
  duplicate_of: string | null;
  needs_review: boolean;
  confidence: number | null;
  parties: string[];
  agreement_status: string | null;
  current_term_end: string | null;
  next_notice_deadline: string | null;
  auto_renewal: boolean | null;
  contract_value: Money | null;
  value_period: string | null;
  alert: string | null;
  alert_days: number | null;
  created_at: string | null;
  processed_at: string | null;
}

export interface Evidence {
  quote: string | null;
  page: number | null;
  grounded: boolean | null;
  score: number | null;
  boxes: number[][];
  page_corrected_from: number | null;
}

export interface FieldResult {
  field: string;
  group: string;
  value: unknown;
  effective_value: unknown;
  meta: Record<string, unknown>;
  evidence: Evidence[];
  grounded: boolean | null;
  value_in_quote: boolean | null;
  flags: string[];
  confidence: number;
  raw_confidence: number;
  tier: string;
  needs_review: boolean;
  critical: boolean;
  review: { action: string; value: unknown; note: string | null; at: string } | null;
}

export interface PageInfo {
  page_no: number;
  width: number;
  height: number;
  route: string;
  route_reason: string;
  ocr_engine: string | null;
  ocr_confidence: number | null;
  language: string | null;
  has_table: boolean;
  struck_words: number;
  char_count: number;
}

export interface LLMCallRow {
  purpose: string;
  tier: string;
  model: string;
  effort: string | null;
  status: string;
  prompt_tokens: number;
  cached_tokens: number;
  completion_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  latency_ms: number;
  cache_hit: boolean;
  error: string | null;
  at: string;
}

export interface ExtractionMeta {
  id: string;
  mode: string;
  created_at: string;
  config: Record<string, unknown>;
  pipeline_version: string;
  schema_version: string;
  prompt_version: string;
  escalated_groups: string[];
  selected_pages: Record<string, { pages: number[]; reasons: Record<string, string>; tokens: number; tier: string; escalated?: boolean }>;
  warnings: { code: string; fields: string[]; message: string }[];
  llm_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  latency_ms: number;
  reused_from: string | null;
}

export type Facts = Record<string, unknown> & {
  status?: string;
  parties?: string[];
  contract_value?: Money | null;
};

export interface DocDetail {
  document: DocSummary;
  facts: Facts;
  extraction: ExtractionMeta | null;
  fields: FieldResult[];
  pages: PageInfo[];
  reviews: { field: string; action: string; old_value: unknown; new_value: unknown; note: string | null; reviewer: string; at: string }[];
  llm_calls: LLMCallRow[];
  jobs: { id: string; status: string; attempts: number; lane: string; progress: Record<string, unknown>; last_error: string | null; created_at: string }[];
}

export interface Settings {
  llm_provider: string;
  llm_display_name: string;
  llm_configured: boolean;
  tiers: Record<string, { effort: string | null; enabled?: boolean }>;
  embeddings_provider: string;
  ocr_backend: string;
  ocr_min_confidence: number;
  full_doc_max_tokens: number;
  escalation: boolean;
  escalate_below: number;
  review_below: number;
  pricing: Record<string, { input: number; cached_input: number; output: number }>;
  rate_limit: { rpm: number; tpm: number; requests_available: number; tokens_available: number };
}

export interface Health {
  ok: boolean;
  version: string;
  worker: { worker_id: string; at: string; alive: boolean; slots: number; running: Record<string, string> } | null;
  versions: Record<string, string>;
}

export interface EvalRunRow {
  id: string;
  name: string;
  set_name: string;
  split: string;
  status: string;
  preset: string | null;
  config: Record<string, unknown>;
  n_docs: number;
  done_docs: number;
  quality: number | null;
  cost_per_1k: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface FieldSummary {
  n: number;
  accuracy: number | null;
  accuracy_ci: [number, number];
  precision: number | null;
  recall: number | null;
  f1: number | null;
  tp: number;
  fp: number;
  fn: number;
  outcomes: Record<string, number>;
}

export interface RunMetrics {
  docs: number;
  failed_docs: number;
  quality: number | null;
  doc_level_critical_accuracy: number | null;
  fields: Record<string, FieldSummary>;
  slices: Record<string, Record<string, { n: number; accuracy: number | null }>>;
  cost_per_1k_docs: number | null;
  cost_total_usd: number;
  latency_ms: { p50: number | null; p95: number | null };
  calibration: { ece: number; n: number; bins: { bin: string; confidence: number; accuracy: number; n: number }[] };
  evidence_recall: number | null;
  evidence_page_accuracy: number | null;
  modes: Record<string, number>;
  ocr: Record<string, { docs: number; word_f1: number; word_f1_folded: number }>;
}

export interface EvalRunDetail extends EvalRunRow {
  metrics: RunMetrics;
  docs: { golden_doc_id: string; external_id: string; document_id: string; strata: Record<string, unknown>; cost_usd: number; latency_ms: number; critical_correct: boolean | null; error: string | null }[];
}

export interface GoldenSetSummary {
  name: string;
  source: string;
  description: string;
  docs: number;
  splits: Record<string, number>;
  strata: Record<string, Record<string, number>>;
  created_at: string;
}

export interface GateRow {
  field: string;
  critical: boolean;
  n: number;
  base_accuracy: number;
  cand_accuracy: number;
  delta: number;
  p_value: number;
  ci: [number, number];
  broke: { doc: string; golden_doc_id: string; gold: unknown; pred: unknown }[];
  fixed: { doc: string; golden_doc_id: string; gold: unknown; pred: unknown }[];
  fail: boolean;
}

export interface GateReport {
  passed: boolean;
  base: { id: string; name: string; quality: number | null; cost_per_1k: number | null; latency_p50_ms: number | null };
  candidate: { id: string; name: string; quality: number | null; cost_per_1k: number | null; latency_p50_ms: number | null };
  fields: GateRow[];
  rules: { alpha: number; max_drop: number; critical: string[] };
  cost_note: string | null;
}
