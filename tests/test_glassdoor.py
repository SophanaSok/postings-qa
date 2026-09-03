from datetime import date, timedelta
from pathlib import Path

from jobbot.browser import is_challenge_html
from jobbot.sources.glassdoor import build_search_url, parse_cards_dom, parse_graph_payload

FIX = Path(__file__).parent / "fixtures" / "glassdoor"


def test_build_search_url():
    assert build_search_url("QA Engineer", "United States", 1, 7) == "https://www.glassdoor.com/Job/united-states-qa-engineer-jobs-SRCH_IL.0,13_IN1_KO14,25.htm?fromAge=7"
    assert build_search_url("QA Engineer", "United States", 2) == "https://www.glassdoor.com/Job/united-states-qa-engineer-jobs-SRCH_IL.0,13_IN1_KO14,25_IP2.htm"


def test_parse_cards_dom():
    jobs = parse_cards_dom((FIX / "search.html").read_text(), keyword="QA Engineer")
    assert len(jobs) == 3 and len({j.source_id for j in jobs}) == 3
    j = jobs[1]
    assert j.source_id == "1010250035349" and j.title == "Test Automation Engineer III"
    assert j.company.startswith("Hewlett Packard Enterprise") and j.location == "San Juan, PR"
    assert j.url.startswith("https://www.glassdoor.com/job-listing/")
    assert j.posted_at == date.today()  # "24h"
    assert (j.salary_min, j.salary_max, j.salary_is_estimate) == (63000, 99000, True)
    assert jobs[0].salary_is_estimate is False  # "(Employer provided)"


def test_parse_graph_payload_shape():
    payload = {"data": {"jobListings": {"jobListings": [{"jobview": {
        "job": {"listingId": 123},
        "header": {"jobTitleText": "QA Engineer", "employerNameFromSearch": "Acme", "locationName": "Austin, TX", "ageInDays": 2,
                   "jobViewUrl": "/job-listing/qa-engineer-acme-JV_KO0,11.htm?jl=123", "payPeriod": "ANNUAL", "payCurrency": "USD",
                   "payPeriodAdjustedPay": {"p10": 80000, "p50": 95000, "p90": 110000}, "salarySource": "ESTIMATED"},
    }}]}}}
    jobs = parse_graph_payload(payload, "QA Engineer")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.source_id == "123" and j.company == "Acme" and j.posted_at == date.today() - timedelta(days=2)
    assert j.url == "https://www.glassdoor.com/job-listing/qa-engineer-acme-JV_KO0,11.htm?jl=123"
    assert (j.salary_min, j.salary_max, j.salary_period, j.salary_is_estimate) == (80000, 110000, "year", True)


def test_blocked_page_detected():
    assert is_challenge_html((FIX / "blocked.html").read_text(), "Just a moment...")
    assert not is_challenge_html((FIX / "search.html").read_text())
