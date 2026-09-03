from datetime import date
from pathlib import Path

from jobbot.browser import is_challenge_html
from jobbot.sources.indeed import parse_job_detail, parse_search_page

FIX = Path(__file__).parent / "fixtures" / "indeed"


def test_parse_search_page_from_mosaic_blob():
    jobs = parse_search_page((FIX / "search.html").read_text(), keyword="QA Engineer")
    assert [j.source_id for j in jobs] == ["2e3db50bf121d627", "c10ebd0ae75e92f3", "df6ea4fbfd1a1a32"]
    j = jobs[0]
    assert j.source == "indeed" and j.title == "Test Systems & Controls Engineer"
    assert j.company == "DeltaHawk Engines, Inc." and j.location == "Racine, WI 53404"
    assert j.url == "https://www.indeed.com/viewjob?jk=2e3db50bf121d627"
    assert j.posted_at == date(2026, 8, 27)
    assert (j.salary_min, j.salary_max, j.salary_period, j.salary_is_estimate) == (75000, 97051.09, "year", False)


def test_parse_detail_pane():
    d = parse_job_detail((FIX / "detail.html").read_text())
    assert len(d["description"]) > 500
    assert (d["salary_min"], d["salary_max"], d["salary_period"]) == (95000, 145000, "year")


def test_blocked_page_detected():
    assert is_challenge_html((FIX / "blocked.html").read_text(), "Just a moment...")
    assert not is_challenge_html((FIX / "search.html").read_text(), "QA Engineer Jobs | Indeed")
