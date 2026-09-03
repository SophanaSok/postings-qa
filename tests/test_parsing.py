from datetime import date

from jobbot.parsing import parse_relative_date, parse_salary


def test_parse_salary_variants():
    s = parse_salary("$94,000.00/yr - $140,000.00/yr")
    assert (s["salary_min"], s["salary_max"], s["salary_period"], s["salary_currency"]) == (94000, 140000, "year", "USD")
    s = parse_salary("$40 - $60 an hour")
    assert (s["salary_min"], s["salary_max"], s["salary_period"]) == (40, 60, "hour")
    s = parse_salary("Estimated: $85K - $110K a year")
    assert (s["salary_min"], s["salary_max"], s["salary_is_estimate"]) == (85000, 110000, True)
    s = parse_salary("From $120,000 a year")
    assert (s["salary_min"], s["salary_max"]) == (120000, 120000)
    s = parse_salary("£45,000 per annum")
    assert (s["salary_currency"], s["salary_period"]) == ("GBP", "year")
    s = parse_salary("Up to $75 hourly")
    assert s["salary_period"] == "hour"
    s = parse_salary("$100K–$120K")
    assert (s["salary_min"], s["salary_max"], s["salary_period"]) == (100000, 120000, "year")
    assert parse_salary("Competitive")["salary_min"] is None
    assert parse_salary(None)["salary_raw"] is None


def test_parse_relative_date():
    today = date(2026, 9, 3)
    assert parse_relative_date("3 days ago", today) == date(2026, 8, 31)
    assert parse_relative_date("Posted 30+ days ago", today) == date(2026, 8, 4)
    assert parse_relative_date("2 weeks ago", today) == date(2026, 8, 20)
    assert parse_relative_date("Just now", today) == today
    assert parse_relative_date("5 hours ago", today) == today
    assert parse_relative_date("Active 1 month ago", today) == date(2026, 8, 4)
    assert parse_relative_date("24h", today) == today
    assert parse_relative_date("3d", today) == date(2026, 8, 31)
    assert parse_relative_date("30d+", today) == date(2026, 8, 4)
    assert parse_relative_date("garbage", today) is None
