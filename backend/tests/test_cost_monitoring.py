from docintel.cost.model import breakeven, project
from docintel.extraction.confidence import apply_calibration, isotonic_fit
from docintel.extraction.derive import alert_level, derive_facts


def test_projection_matches_hand_arithmetic():
    prices = {t: {"input": 1.0, "cached_input": 0.1, "output": 4.0} for t in ("fast", "strong", "vision")}
    r = project({"n_docs": 1000, "pages_per_doc": 10, "tokens_per_page": 600, "sent_fraction": 1.0,
                 "prompt_overhead_tokens": 0, "output_tokens_per_doc": 400, "pricing": prices})
    # 1000 docs × 6000 tokens × $1/M + 1000 × 400 × $4/M = 6.0 + 1.6
    assert r["total_usd"] == 7.6 and r["per_1k_docs_usd"] == 7.6
    cheaper = project({"n_docs": 1000, "pages_per_doc": 10, "tokens_per_page": 600, "sent_fraction": 0.3,
                       "prompt_overhead_tokens": 0, "output_tokens_per_doc": 400, "dedup_rate": 0.2,
                       "batch_discount": 0.5, "pricing": prices})
    assert cheaper["total_usd"] < r["total_usd"] / 3


def test_breakeven():
    r = breakeven({"gpu_hour_usd": 2.5, "throughput_tokens_per_s": 10_000, "utilization": 0.7,
                   "fixed_monthly_usd": 15_000, "vendor_price_per_m": 3.0, "monthly_tokens_m": 1000})
    assert abs(r["self_host_price_per_m"] - 0.0992) < 0.001
    assert 5000 < r["breakeven_tokens_per_month_m"] < 5300 and "stay with the vendor" in r["verdict"]


def test_isotonic_calibration_is_monotone():
    pts = [(0.2, False)] * 8 + [(0.2, True)] * 2 + [(0.6, True)] * 6 + [(0.6, False)] * 4 + [(0.9, True)] * 19 + [(0.9, False)]
    table = isotonic_fit(pts)
    probs = [p for _, p in table]
    assert probs == sorted(probs) and apply_calibration(0.95, table) == 0.95
    assert apply_calibration(0.1, table) == 0.2


def test_derived_dates_and_alerts():
    from datetime import date
    facts = derive_facts({"effective_date": "2024-01-15", "expiration_type": "duration", "initial_term_months": 24,
                          "auto_renewal": True, "renewal_term_months": 12,
                          "non_renewal_notice": {"amount": 2, "unit": "months"},
                          "signatories": [{"name": "A", "is_signed": True}]}, today=date(2026, 6, 1))
    assert facts["initial_term_end"] == "2026-01-14" and facts["notice_deadline"] == "2025-11-14"
    assert facts["current_term_end"] == "2027-01-14" and facts["next_notice_deadline"] == "2026-11-14"
    assert facts["non_renewal_notice_days"] == 60 and facts["status"] == "active"
    assert alert_level(facts, today=date(2026, 10, 1)) == ("plan", 44)
    assert alert_level(facts, today=date(2026, 11, 1)) == ("decide", 13)
    assert alert_level(facts, today=date(2026, 12, 1))[0] == "missed"
    draft = derive_facts({"signatories": [{"name": "A", "is_signed": False}]})
    assert draft["status"] == "draft" and draft["is_executed"] is False
