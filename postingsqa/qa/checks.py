"""Data-QA checks. Each check takes (job, config) and returns a rejection reason or None.

A check may also return a *flag* (soft warning) by returning ("flag", "text"); flags do not reject.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Callable
from urllib.parse import urlparse

from postingsqa.config import QAConfig
from postingsqa.models import Job, normalize

Check = Callable[[Job, QAConfig], str | tuple[str, str] | None]

SOURCE_HOSTS = {
    "linkedin": ("linkedin.com",),
    "indeed": ("indeed.com",),
    "glassdoor": ("glassdoor.com",),
}

HOURS_PER_YEAR = 2080
PERIOD_TO_YEAR = {"year": 1, "month": 12, "week": 52, "day": 260, "hour": HOURS_PER_YEAR}


def required_fields(job: Job, cfg: QAConfig) -> str | None:
    missing = [f for f in ("title", "company", "url") if not (getattr(job, f) or "").strip()]
    if missing:
        return f"missing {', '.join(missing)}"
    return None


def url_valid(job: Job, cfg: QAConfig) -> str | None:
    if not job.url:
        return None  # required_fields already covers it
    parsed = urlparse(job.url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "malformed url"
    hosts = SOURCE_HOSTS.get(job.source, ())
    if hosts and not any(parsed.netloc.endswith(h) for h in hosts):
        return f"url host {parsed.netloc} does not match source {job.source}"
    return None


def title_relevance(job: Job, cfg: QAConfig) -> str | None:
    title = normalize(job.title)
    if not title:
        return None
    padded = f" {title} "
    excluded = [k for k in cfg.exclude_keywords if f" {normalize(k)} " in padded]
    if excluded:
        return f"title contains excluded keyword: {excluded[0]}"
    if cfg.include_keywords and not any(normalize(k) in title for k in cfg.include_keywords):
        return "title matches no include keyword"
    return None


def location_match(job: Job, cfg: QAConfig) -> str | tuple[str, str] | None:
    if cfg.remote_ok and job.remote:
        return None
    if not cfg.locations:
        return None
    loc = job.location or ""
    if not loc.strip():
        return ("flag", "location unknown")
    if any(needle.lower() in loc.lower() for needle in cfg.locations):
        return None
    return f"location '{loc}' outside configured locations"


def posting_age(job: Job, cfg: QAConfig) -> str | tuple[str, str] | None:
    if job.posted_at is None:
        return ("flag", "posted date unknown")
    age = (date.today() - job.posted_at).days
    if age < -1:
        return f"posted date {job.posted_at} is in the future"
    if age > cfg.max_age_days:
        return f"posted {age} days ago (max {cfg.max_age_days})"
    return None


def annualize(amount: float, period: str | None) -> float:
    return amount * PERIOD_TO_YEAR.get(period or "year", 1)


def salary_sanity(job: Job, cfg: QAConfig) -> str | tuple[str, str] | None:
    lo, hi = job.salary_min, job.salary_max
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo > hi:
        return f"salary min {lo:g} > max {hi:g}"
    if job.salary_currency and job.salary_currency.upper() != "USD":
        return ("flag", f"salary in {job.salary_currency}, bounds not checked")
    bmin, bmax = cfg.salary_bounds_usd_year
    for val in (lo, hi):
        if val is None:
            continue
        yearly = annualize(val, job.salary_period)
        if yearly < bmin or yearly > bmax:
            return f"salary {val:g}/{job.salary_period or 'year'} outside plausible range"
    if job.salary_is_estimate:
        return ("flag", "salary is a site estimate")
    return None


def description_quality(job: Job, cfg: QAConfig) -> str | tuple[str, str] | None:
    desc = (job.description or "").strip()
    if not desc:
        return ("flag", "no description fetched")
    if len(desc) < cfg.min_description_chars:
        return f"description too short ({len(desc)} chars)"
    for pat in cfg.spam_patterns:
        if re.search(pat, desc, re.I):
            return f"description matches spam pattern '{pat}'"
    return None


def staffing_agency(job: Job, cfg: QAConfig) -> str | None:
    company = job.company or ""
    if not company:
        return None
    if any(normalize(b) == normalize(company) for b in cfg.blocked_companies):
        return f"company '{company}' is blocked"
    for pat in cfg.agency_patterns:
        if re.search(pat, company, re.I):
            return f"company '{company}' looks like a staffing agency ({pat})"
    return None


# Order matters for the reported reason: structural problems first, relevance last.
CHECKS: list[tuple[str, Check]] = [
    ("required_fields", required_fields),
    ("url_valid", url_valid),
    ("title_relevance", title_relevance),
    ("staffing_agency", staffing_agency),
    ("location_match", location_match),
    ("posting_age", posting_age),
    ("salary_sanity", salary_sanity),
    ("description_quality", description_quality),
]
