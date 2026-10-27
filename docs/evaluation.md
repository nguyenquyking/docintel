# Evaluation: what "accurate" means here, and how a change earns its way in

Extraction quality is a claim about a distribution. This document is the annotation
guideline and the measurement protocol; `backend/docintel/eval/metrics.py` is its
executable twin and the two must change together.

## 1. Golden sets

| Set | Source | Labels | Why it exists |
|---|---|---|---|
| `synthetic-v1` | `synth/generator.py` (seeded) | every field, plus the page each value is evidenced on | controllable coverage of the hard cases, publishable |
| `cuad` | CUAD v1 (The Atticus Project, CC BY 4.0) | the fields CUAD's answers determine, mapped to our definitions (see `datasets/cuad.py`) | real commercial contracts, real layouts, expert annotations |
| `reviewed` | production documents after human review | only fields a person approved or corrected | the golden set grows from real traffic |
| `canary` | fixed stratified subset of `synthetic-v1` | as source | scheduled re-runs detect provider-side model changes |

**Stratification.** Each golden document carries strata: language (en / vi / bilingual),
length (short ≤ 5 pages, medium, long 60–180 pages), scanned or born-digital, has a price
table, and hard-case tags — `amendment` (later document overrides the term, notice or
fee), `redline` (struck-through value still in the text layer), `draft` (empty signature
blocks), `last_signature` (effective date defined as the date of the last signature),
`notice_months`, `distractors` (liability caps, insurance "renewals", schedules with their
own effective dates). Metrics are reported per stratum: an aggregate can hide a
regression on one slice.

**Splits.** `dev` (one third) is for prompt and pipeline iteration and for fitting
calibration; `test` is for decisions. Fitting anything on `test` invalidates it.

**Mapping someone else's labels is a definition exercise.** CUAD's "Renewal Term" covers
renewal *options* as well as automatic renewals, its parties are *mentions* (aliases,
"X together with Y"), and some governing-law answers are conditional ("the state in which
the breach occurs"). Every disputed error was read against CUAD's own clause text before
the mapping was fixed: automatic renewal is labeled from the clause wording, parties are
cleaned to legal names, conditional jurisdictions stay unlabeled. Fields that cannot be
mapped faithfully are not graded.

**Privacy.** Customer documents are never committed and are only promoted into a golden
set through the review flow, which keeps them in the deployment's own database.

## 2. Field definitions (the guideline)

| Field | Definition | Notes |
|---|---|---|
| parties | contracting legal entities, as written | roles in parentheses are not part of the name; diacritics are part of the name |
| signatories | people named in the main agreement's signature blocks | amendment signatories excluded |
| is_executed | every signature block of the main agreement shows a signature | `[signed]` / `[stamp]` markers come from drawn marks next to signature lines |
| effective_date | date the agreement takes effect | "date of the last signature" ⇒ the latest signature date; unsigned ⇒ null |
| expiration_type | fixed_date / duration / evergreen / on_completion | |
| initial_term_end | end of the initial term | **E + N months − 1 day** for an N-month term from E; an explicit date wins; amended term wins |
| auto_renewal | renews unless a party acts | a document that never mentions renewal grades as "no" |
| renewal_term_months | length of each renewal term | |
| non_renewal_notice_days | notice needed to prevent renewal, in days | months × 30 for comparison; not the breach cure period |
| notice_deadline | last day to send notice for the initial term | term end − notice (calendar months when stated in months) |
| termination_for_convenience | a party may terminate without cause | termination for breach does not count; silent ⇒ "no" |
| contract_value | price or recurring fee, amount + ISO currency | not a liability cap, insurance amount or penalty; amended fee wins |
| value_period | monthly / quarterly / annual / total / one_time | |
| payment_terms_days | days to pay an invoice | rent payable in advance has none |
| governing_law | jurisdiction, canonical name | "the laws of the State of New York, USA" = "new york" |
| line_items | rows of the price table, excluding totals | matched by description and amount |

Dates, deadlines and normalized values are **derived by code** from what the model
extracted (`extraction/derive.py`), never asked of the model.

## 3. Outcomes and metrics

Per field and document:

* `tn` — absent in gold and prediction (correct: null is an answer);
* `tp` — present and equal after normalization;
* `fp` — a value the document does not contain (hallucination);
* `fn` — a value that is there, missed;
* `wrong` — both present, different: counted as **fp + fn**;
* `partial` — list fields matched only in part (item-level tp/fp/fn are kept).

Equality is typed: dates by calendar day (CUAD labels carry a 1-day tolerance), money by
currency and amount (±0.5 %), names by normalized similarity ≥ 0.85, jurisdictions by
canonical name, lists by greedy best-pair matching (Hungarian would be optimal; contracts
have 2–20 items).

Reported per run:

* **critical-field accuracy** — mean accuracy over the eight critical fields (parties,
  executed, effective date, term end, auto-renewal, notice days, notice deadline, value);
  the "quality" axis of the cost frontier;
* **documents fully right** — all critical fields correct; what a user experiences;
* per field: accuracy with a 95 % **Wilson** interval, precision, recall, F1, outcomes;
* **slices** by every stratum and hard-case tag;
* **evidence recall** — for grouped (retrieval) extraction, was the gold evidence page
  among the pages sent? A retrieval miss cannot be fixed by any prompt;
* **evidence page accuracy** — does the cited page match the gold page;
* **calibration** — reliability bins and ECE of field confidence;
* **text fidelity on scans** — bag-of-words F1 of the parsed text against the generator's
  ground truth, exact and accent-folded (the gap is diacritics lost by OCR);
* cost per 1,000 documents (nominal, cache hits included at their list cost) and latency
  p50 / p95.

**How many documents?** At 90 % accuracy the 95 % interval is ±5.9 points with 100
documents, ±2.9 with 400, ±1.9 with 1,000 (`stats.half_width`). Detecting a 2-point
change needs a paired design.

## 4. The regression gate

Every prompt, model, schema or pipeline change runs on the same golden documents as the
baseline and is compared **pairwise** (`eval/gate.py`):

* per field, `b` = documents that flip right → wrong, `c` = wrong → right;
* **McNemar's exact test** on (b, c), and a **paired bootstrap** CI of the change;
* **fail** if a critical field drops with p < 0.05, or any field drops by more than a hard
  margin (default 3 points) even if the test is underpowered;
* optional cost guard: fail if cost per 1k documents rises more than X %;
* the report always lists the flipped documents, so every regression has a name.

**Noise floor.** LLMs are not deterministic even at the same settings. Run the baseline
configuration twice without the answer cache (`--no-cache`): flips between identical
configurations are noise, and a change must beat them.

```bash
python -m docintel eval run --preset cascade --split test --name baseline
python -m docintel eval run --preset cascade --split test --name candidate --no-cache
python -m docintel eval gate <baseline-run-id> <candidate-run-id>   # exit code 1 on regression
```

## 5. Calibration

Field confidence starts as a heuristic over verification signals (quote grounded on the
cited page, value readable from its own quote, page route and OCR confidence, rule
violations, tier) and is mapped to P(correct) by **isotonic regression** fitted per field
on a dev run (`--calibrate`). The review threshold then means a precision target.
Calibrated values are kept inside [0.01, 0.99]: a finite golden set never proves
certainty.
