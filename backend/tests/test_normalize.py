from datetime import date

from docintel.text.lang import detect_language, document_language
from docintel.text.normalize import (
    amounts_equal,
    detect_currency,
    estimate_tokens,
    find_amounts,
    find_dates,
    find_integers,
    name_similarity,
    norm_jurisdiction,
    norm_org,
    normalize_for_match,
    parse_date,
    parse_number,
    quote_score,
)


def test_numbers_in_every_locale():
    assert parse_number("1.500.000.000") == 1_500_000_000
    assert parse_number("1.234.567,89") == 1234567.89
    assert parse_number("1,234,567.89") == 1234567.89
    assert parse_number("$5,000") == 5000
    assert parse_number("12,50") == 12.5
    assert parse_number("1 234 567") == 1234567


def test_amounts_with_scale_words():
    assert find_amounts("a fee of USD 1.5 million per year") == [1_500_000]
    assert 2_000_000_000 in find_amounts("tổng giá trị 2 tỷ đồng")
    assert amounts_equal("120,000.00", 120000)
    assert not amounts_equal("120,000.00", 12000)


def test_currency_detection():
    assert detect_currency("S$ 12,000") == "SGD"
    assert detect_currency("US$ 9") == "USD"
    assert detect_currency("1.500.000 VNĐ") == "VND"
    assert detect_currency("45.000.000 đồng") == "VND"
    assert detect_currency("€ 10") == "EUR"


def test_dates_any_format_and_locale():
    assert find_dates("effective as of January 15, 2024") == [date(2024, 1, 15)]
    assert find_dates("this 15th day of January, 2024") == [date(2024, 1, 15)]
    assert find_dates("có hiệu lực từ ngày 05 tháng 03 năm 2025") == [date(2025, 3, 5)]
    assert parse_date("03/04/2025") == date(2025, 4, 3)
    assert parse_date("03/04/2025", dayfirst=False) == date(2025, 3, 4)
    assert parse_date("25/12/2024", dayfirst=False) == date(2024, 12, 25)  # only one valid reading
    assert parse_date("15.01.2024") == date(2024, 1, 15)
    assert parse_date("not a date") is None


def test_integers_digits_and_words():
    assert {60} <= find_integers("sixty (60) days")
    assert 120 in find_integers("one hundred twenty days")
    assert 1 not in find_integers("USD 1,250.00")


def test_org_and_jurisdiction_names():
    assert norm_org("Acme, Inc.") == norm_org("ACME INC") == "acme"
    assert norm_org('Beta LLC ("Provider")') == "beta"
    assert norm_org("Công ty TNHH Thương mại Minh Long") == "thương mại minh long"
    assert name_similarity("Công ty TNHH Minh Long", "Minh Long Company Limited") == 1.0
    # lost diacritics are a real error, not a formatting difference
    assert name_similarity("Công ty TNHH Thương mại Minh Long", "Cong ty TNHH Thuong mai Minh Long") < 0.85
    assert norm_jurisdiction("the laws of the State of New York") == "new york"
    assert norm_jurisdiction("pháp luật nước Cộng hòa Xã hội Chủ nghĩa Việt Nam") == "vietnam"
    assert norm_jurisdiction("England & Wales") == "england and wales"


def test_quote_grounding_tolerates_ocr_noise_but_not_invention():
    page = "This Agreement shall automatically renew for successive one-year terms unless either party gives sixty (60) days notice."
    assert quote_score("shall automatica1ly re-\nnew for successive one-year terms", page) >= 0.9
    assert quote_score("either party may terminate for convenience on ninety (90) days notice", page) < 0.8
    assert normalize_for_match("“Effective  Date”") == '"effective date"'


def test_language_detection():
    assert detect_language("This Agreement shall be governed by the laws of the State of New York.") == "en"
    assert detect_language("Hợp đồng này được điều chỉnh bởi pháp luật nước Cộng hòa Xã hội Chủ nghĩa Việt Nam.") == "vi"
    assert detect_language("Hop dong nay duoc gia han tu dong them muoi hai thang theo thoa thuan cua cac ben") == "vi_noaccent"
    en = "This Agreement shall continue for twelve months and renew automatically.\n" * 5
    vi = "Hợp đồng này có hiệu lực trong mười hai tháng và tự động gia hạn.\n" * 5
    assert document_language([en, vi]) == "bilingual"


def test_token_estimate_is_higher_for_vietnamese():
    en = "the agreement shall renew automatically " * 20
    vi = "hợp đồng sẽ được tự động gia hạn thêm " * 20
    assert estimate_tokens(vi) / len(vi) > estimate_tokens(en) / len(en)
