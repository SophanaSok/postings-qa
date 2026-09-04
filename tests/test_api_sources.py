"""API adapters: pure parsers against saved fixtures, plus credential and registry behaviour."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from postingsqa.config import SOURCE_DEFAULTS, Config, config_from_raw
from postingsqa.sources import adzuna, all_sources, attribution_text, get_source, greenhouse, lever, remotive, usajobs
from postingsqa.sources.base import html_to_text, iso_date, recent_enough, title_matches

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name / "search.json").read_text())


# -- helpers ------------------------------------------------------------------------------------

def test_helpers():
    assert title_matches("Senior QA Engineer", ["data analyst", "qa engineer"]) == "qa engineer"
    assert title_matches("Forklift Operator", ["qa"]) is None
    assert iso_date("2026-08-31T17:56:36-04:00") == date(2026, 8, 31)
    assert iso_date("garbage") is None and iso_date(None) is None
    assert recent_enough(date(2026, 9, 1), 7, today=date(2026, 9, 3))
    assert not recent_enough(date(2026, 8, 1), 7, today=date(2026, 9, 3))
    assert recent_enough(None, 7)
    assert html_to_text("<p>Hello&nbsp;<b>world</b></p><ul><li>one</li><li>two</li></ul>") == "Hello world\n\none\ntwo"
    assert html_to_text("&lt;div&gt;escaped&lt;/div&gt;", unescape=True) == "escaped"


# -- remotive -----------------------------------------------------------------------------------

def test_remotive_parse():
    jobs = remotive.parse_jobs(load("remotive"))
    assert len(jobs) == 4 and len({j.id for j in jobs}) == 4
    by_title = {j.title: j for j in jobs}
    hourly = by_title["Content Reviewer - English US"]
    assert hourly.salary_min == 14 and hourly.salary_period == "hour" and hourly.salary_currency == "USD"
    head = by_title["Head of Marketing & Communications"]
    assert (head.salary_min, head.salary_max, head.salary_period) == (150_000, 230_000, "year")
    assert all(j.remote is True and j.source == "remotive" and j.url.startswith("https://remotive.com/") for j in jobs)
    assert hourly.posted_at == date(2026, 8, 21) and hourly.employment_type == "part-time"
    assert "<" not in (head.description or "") and len(head.description) > 50
    # client-side keyword + age filtering
    assert [j.title for j in remotive.parse_jobs(load("remotive"), ["marketing"])] == ["Head of Marketing & Communications"]
    assert remotive.parse_jobs(load("remotive"), None, max_age_days=3, today=date(2026, 9, 3)) == []
    assert len(remotive.parse_jobs(load("remotive"), None, max_age_days=14, today=date(2026, 8, 22))) == 4


# -- greenhouse ---------------------------------------------------------------------------------

def test_greenhouse_parse():
    jobs = greenhouse.parse_jobs(load("greenhouse"), "gitlab")
    assert len(jobs) == 4 and len({j.id for j in jobs}) == 4
    with_pay = [j for j in jobs if j.salary_min]
    assert with_pay and with_pay[0].salary_min == 139_200 and with_pay[0].salary_period == "year" and with_pay[0].salary_currency == "USD"
    assert all(j.remote is True for j in jobs if "Remote" in (j.location or ""))
    assert all(j.company for j in jobs) and all(j.url.startswith("http") for j in jobs)
    assert all("&lt;" not in (j.description or "") and "<div" not in (j.description or "") for j in jobs)
    assert all(j.posted_at is not None for j in jobs)
    assert [j.title for j in greenhouse.parse_jobs(load("greenhouse"), "gitlab", ["account executive"])] == ["Account Executive - Italy"]


# -- lever --------------------------------------------------------------------------------------

def test_lever_parse():
    payload = load("lever")
    jobs = lever.parse_postings(payload, "spotify", "Spotify")
    assert len(jobs) == 4 and len({j.id for j in jobs}) == 4
    first = jobs[0]
    assert first.company == "Spotify" and first.url.startswith("https://jobs.lever.co/")
    assert first.posted_at == datetime.fromtimestamp(payload[0]["createdAt"] / 1000, tz=timezone.utc).date()
    assert {j.remote for j in jobs} == {False}  # fixture has hybrid + onsite only
    assert all(j.description for j in jobs)
    assert lever.site_map(["spotify", "palantir"]) == {"spotify": "Spotify", "palantir": "Palantir"}
    assert lever.site_map({"leverdemo-8": "Lever Demo"}) == {"leverdemo-8": "Lever Demo"}
    assert lever._salary({"min": 100, "max": 150, "currency": "USD", "interval": "per-hour-wage"})["salary_period"] == "hour"


# -- usajobs ------------------------------------------------------------------------------------

def test_usajobs_parse_and_skip_without_key(monkeypatch):
    payload = load("usajobs")
    jobs = usajobs.parse_search(payload, "QA")
    assert len(jobs) == 2
    qa, da = jobs
    assert qa.company == "Department of the Treasury" and qa.salary_min == 99_200 and qa.salary_period == "year"
    assert qa.remote is False and qa.employment_type == "Full-time" and qa.posted_at == date(2026, 8, 28)
    assert "Designs and executes test plans." in qa.description and "specialized experience" in qa.description
    assert da.remote is True and da.salary_period == "hour" and da.salary_min == 35.5 and "<p>" not in da.description
    assert usajobs.page_count(payload) == 2
    monkeypatch.delenv(usajobs.ENV_KEY, raising=False)
    monkeypatch.delenv(usajobs.ENV_EMAIL, raising=False)
    assert list(usajobs.USAJobsSource(Config()).search("QA", "United States", 2)) == []


# -- adzuna -------------------------------------------------------------------------------------

def test_adzuna_parse_and_skip_without_key(monkeypatch):
    jobs = adzuna.parse_search(load("adzuna"), "QA", "us")
    assert len(jobs) == 2
    a, b = jobs
    assert a.title == "QA Automation Engineer" and "<strong>" not in a.description
    assert a.url.startswith("https://www.adzuna.com/land/ad/") and a.company == "Acme Robotics"
    assert (a.salary_min, a.salary_max, a.salary_currency, a.salary_period) == (95_000, 120_000, "USD", "year")
    assert a.salary_is_estimate is False and b.salary_is_estimate is True
    assert a.employment_type == "Full-time" and b.employment_type == "Part-time"
    assert a.posted_at == date(2026, 9, 2) and b.remote is True  # inferred from location "Remote"
    monkeypatch.delenv(adzuna.ENV_ID, raising=False)
    monkeypatch.delenv(adzuna.ENV_KEY, raising=False)
    assert list(adzuna.AdzunaSource(Config()).search("QA", "United States", 2)) == []


# -- registry / config --------------------------------------------------------------------------

def test_registry_matches_config_defaults():
    sources = all_sources()
    assert set(sources) == set(SOURCE_DEFAULTS)
    assert list(sources)[:5] == ["remotive", "greenhouse", "lever", "usajobs", "adzuna"]
    for name, cls in sources.items():
        assert cls.name == name and cls.kind in ("api", "scraper") and cls.description
        if cls.kind == "api":
            assert cls.attribution and not cls.uses_playwright
        else:
            assert cls.uses_playwright
    with pytest.raises(KeyError):
        get_source("monster")
    assert attribution_text(["lever", "remotive"]).startswith("Listings via Remotive (https://remotive.com), Lever")
    assert attribution_text(["linkedin"]) == ""


def test_config_defaults_and_source_options():
    cfg = Config()
    assert cfg.enabled_sources == ["remotive", "greenhouse", "lever", "usajobs"]
    assert not any(all_sources()[n].uses_playwright for n in cfg.enabled_sources)  # default run needs no Playwright
    cfg = config_from_raw({"sources": {
        "greenhouse": {"enabled": True, "boards": ["gitlab"]},
        "lever": False,
        "adzuna": {"enabled": True, "country": "gb"},
        "indeed": {"enabled": True},
    }})
    assert cfg.sources["lever"] is False and cfg.sources["adzuna"] is True and cfg.sources["indeed"] is True
    assert cfg.sources["linkedin"] is False and cfg.sources["remotive"] is True
    assert cfg.source_options["greenhouse"] == {"boards": ["gitlab"]}
    assert cfg.source_options["adzuna"] == {"country": "gb"} and cfg.source_options["remotive"] == {}
    assert greenhouse.GreenhouseSource(cfg).boards == ["gitlab"]


def test_cli_rejects_unknown_source():
    from argparse import Namespace

    from postingsqa.cli import _apply_overrides

    cfg = Config()
    with pytest.raises(ValueError, match="unknown source"):
        _apply_overrides(cfg, Namespace(source="remotive,monster"))
    _apply_overrides(cfg, Namespace(source="lever,glassdoor"))
    assert cfg.enabled_sources == ["lever", "glassdoor"]
