from datetime import date
from pathlib import Path

from postingsqa.browser import is_challenge_html
from postingsqa.sources.linkedin import parse_job_detail, parse_search_cards

FIX = Path(__file__).parent / "fixtures" / "linkedin"


def test_parse_search_cards():
    jobs = parse_search_cards((FIX / "search.html").read_text(), keyword="QA Engineer")
    assert len(jobs) == 3
    j = jobs[0]
    assert j.source == "linkedin" and j.source_id == "4382210948"
    assert j.title == "QA Engineer" and j.company == "Exacta Systems" and j.location == "Austin, TX"
    assert j.url == "https://www.linkedin.com/jobs/view/qa-engineer-at-exacta-systems-4382210948"
    assert j.posted_at == date(2026, 8, 28)
    assert j.search_query == "QA Engineer"
    assert len({j.id for j in jobs}) == 3


def test_parse_job_detail():
    d = parse_job_detail((FIX / "detail.html").read_text())
    assert len(d["description"]) > 500
    assert d["seniority"] == "Not Applicable" and d["employment_type"] == "Full-time"
    assert d["posted_raw"] == "6 days ago" and d["posted_at"] is not None


def test_blocked_page_detected():
    assert is_challenge_html((FIX / "blocked.html").read_text(), "Just a moment...")
    assert not is_challenge_html((FIX / "search.html").read_text())
    assert not is_challenge_html((FIX / "detail.html").read_text())
