# Cost: corpus, quality per dollar, own model vs vendor

At corpus scale the price per document decides what ships. The pipeline meters every
model call, so the cost model runs on measured inputs; the price table is the only
assumption (placeholders by default — set your contract's prices on the Cost page).

## Per document

```
cost/doc ≈ Σ calls [ (prompt − cached) × p_in + cached × p_cached + completion × p_out ]
```

`completion` includes reasoning tokens. What drives it, all measured on the Cost page:

* **pages × tokens per page** — Vietnamese costs more tokens per character than English;
* **share of the document sent** — retrieval for long documents sends a third or less;
* **prompt overhead** — instructions + schema per call; served from the provider's
  prompt cache after the first call (static prefix first, document last);
* **output tokens** — evidence quotes are most of them; JSON stays compact;
* **escalation rate** — share of groups re-run on the strong tier;
* **scanned share × VLM share** — a VLM page costs image tokens plus instructions; local
  OCR is near-free;
* **duplicates** — identical bytes are never processed twice.

## Corpus projection

`cost/model.py::project` and the Cost page:

```
docs_eff  = N × (1 − dedup)
fast      = docs_eff × (pages × tokens/page × sent + overhead) priced with cache share, + output
strong    = fast-input × escalation × context-multiplier, + output share
vision    = pages × scanned × vlm-share × (image + instruction tokens, output)
ocr       = pages × scanned × (1 − vlm-share) × OCR price
total     = (fast + strong + vision) × (1 − batch discount) + ocr
```

## Quality per dollar

Each eval run is a point (cost per 1,000 documents, critical-field accuracy) on the same
golden set and split. The frontier is the set of runs no other run beats on both axes;
ship the cheapest run above the quality floor of every critical field and slice — not
just the average.

## Own model vs vendor

```
self-host $/1M tokens = GPU $/hour ÷ (tokens/s × 3600 × utilization ÷ 10⁶)
break-even volume     = fixed monthly cost ÷ (vendor $/1M − self-host $/1M)
```

A GPU at $2.5/h serving 10k tokens/s at 70 % utilization costs ≈ $0.10 per 1M tokens —
about the price of a small vendor model. Self-hosting therefore wins only when a small
model **matches the expensive model's quality on our fields**: distill it on teacher
outputs filtered by the same verification checks, prove parity per field and slice on the
golden set through the regression gate, keep the vendor for the low-confidence tail
(the cascade), and count re-processing (every new field is a corpus re-run) in the
monthly volume.
