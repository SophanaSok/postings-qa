"""USAJOBS Search API: US federal job announcements. https://developer.usajobs.gov/api-reference/get-api-search

Needs a free API key (https://developer.usajobs.gov/apirequest/): set ``PQA_USAJOBS_KEY`` and
``PQA_USAJOBS_EMAIL`` (the address the key was issued to; USAJOBS wants it as the User-Agent).
Terms: personal/internal use, credit USAJOBS, send users to USAJOBS to apply, no redistribution as a feed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from postingsqa.http import get_json
from postingsqa.models import Job
from postingsqa.parsing import clean
from postingsqa.sources.base import BaseSource, html_to_text, iso_date

API = "https://data.usajobs.gov/api/search"
ENV_KEY = "PQA_USAJOBS_KEY"
ENV_EMAIL = "PQA_USAJOBS_EMAIL"
PAGE_SIZE = 100
PERIODS = {"PA": "year", "PH": "hour", "PD": "day", "PW": "week", "PM": "month"}
GENERIC_LOCATIONS = {"", "united states", "us", "usa", "u.s.", "remote", "anywhere"}


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _text(v) -> str | None:
    if isinstance(v, list):
        v = "\n".join(str(x) for x in v if x)
    return html_to_text(str(v)) if v else None


def parse_search(payload: dict, keyword: str | None = None) -> list[Job]:
    items = ((payload or {}).get("SearchResult") or {}).get("SearchResultItems") or []
    jobs: list[Job] = []
    for it in items:
        d = it.get("MatchedObjectDescriptor") or {}
        rem = (d.get("PositionRemuneration") or [{}])[0]
        salary = {}
        lo, hi = _num(rem.get("MinimumRange")), _num(rem.get("MaximumRange"))
        if lo is not None or hi is not None:
            salary = {"salary_min": lo, "salary_max": hi, "salary_currency": "USD", "salary_period": PERIODS.get(rem.get("RateIntervalCode")),
                      "salary_raw": f"{rem.get('MinimumRange')} - {rem.get('MaximumRange')} {rem.get('Description') or rem.get('RateIntervalCode') or ''}".strip()}
        details = (d.get("UserArea") or {}).get("Details") or {}
        description = "\n\n".join(p for p in (_text(details.get("JobSummary")), _text(details.get("MajorDuties")),
                                              _text(details.get("Requirements")), _text(d.get("QualificationSummary"))) if p) or None
        remote_flag = details.get("RemoteIndicator")
        jobs.append(Job(
            source="usajobs",
            source_id=str(d.get("PositionID") or it.get("MatchedObjectId") or "") or None,
            title=clean(d.get("PositionTitle")),
            company=clean(d.get("OrganizationName")) or clean(d.get("DepartmentName")),
            location=clean(d.get("PositionLocationDisplay")),
            url=d.get("PositionURI"),
            remote=remote_flag if isinstance(remote_flag, bool) else None,
            posted_at=iso_date(d.get("PublicationStartDate")),
            posted_raw=d.get("PublicationStartDate"),
            employment_type=clean((d.get("PositionSchedule") or [{}])[0].get("Name")),
            description=description,
            search_query=keyword,
            **salary,
        ))
    return jobs


def page_count(payload: dict) -> int:
    try:
        return int(((payload.get("SearchResult") or {}).get("UserArea") or {}).get("NumberOfPages") or 1)
    except (TypeError, ValueError):
        return 1


class USAJobsSource(BaseSource):
    name = "usajobs"
    attribution = ("USAJOBS", "https://www.usajobs.gov")
    description = "US federal job announcements (official API; free key, set PQA_USAJOBS_KEY / PQA_USAJOBS_EMAIL)"

    def credentials(self) -> tuple[str | None, str | None]:
        return os.environ.get(ENV_KEY), os.environ.get(ENV_EMAIL)

    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        key, email = self.credentials()
        if not (key and email):
            self.log.warning("skipped: set %s and %s (free key: https://developer.usajobs.gov/apirequest/)", ENV_KEY, ENV_EMAIL)
            return
        headers = {"Authorization-Key": key, "User-Agent": email}
        for page in range(1, max_pages + 1):
            params = {"Keyword": keyword, "ResultsPerPage": PAGE_SIZE, "Page": page, "DatePosted": min(self.config.search.max_age_days, 60),
                      "SortField": "opendate", "SortDirection": "Desc"}
            if (location or "").strip().lower() not in GENERIC_LOCATIONS:
                params["LocationName"] = location
            payload = get_json(API, params, headers, source=self.name, timeout=self.config.browser.timeout_seconds)
            if not payload:
                break
            jobs = parse_search(payload, keyword)
            yield from jobs
            if not jobs or page >= page_count(payload):
                break
            self.pace(0.5)
