"""Adzuna API: aggregated job ads. https://developer.adzuna.com/docs/search

Needs a free app id/key (https://developer.adzuna.com/signup): set ``PQA_ADZUNA_APP_ID`` and
``PQA_ADZUNA_APP_KEY``. Off by default: Adzuna's terms allow personal research only, cap usage at 250
calls/day, and require a "Jobs by Adzuna" label linking to Adzuna wherever ads are displayed (the UI and
workbook show it when this source is enabled). Postings link to Adzuna's redirect URL, as the terms require.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from postingsqa.http import get_json
from postingsqa.models import Job
from postingsqa.sources.base import BaseSource, html_to_text, iso_date

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
ENV_ID = "PQA_ADZUNA_APP_ID"
ENV_KEY = "PQA_ADZUNA_APP_KEY"
PAGE_SIZE = 50
MAX_CALLS_PER_RUN = 20  # daily quota is 250
CURRENCY = {"us": "USD", "gb": "GBP", "ca": "CAD", "au": "AUD", "nz": "NZD", "in": "INR", "br": "BRL", "mx": "MXN",
            "pl": "PLN", "sg": "SGD", "za": "ZAR", "ch": "CHF", "at": "EUR", "be": "EUR", "de": "EUR", "es": "EUR",
            "fr": "EUR", "it": "EUR", "nl": "EUR"}
CONTRACT_TIME = {"full_time": "Full-time", "part_time": "Part-time"}
GENERIC_LOCATIONS = {"", "united states", "us", "usa", "remote", "anywhere"}


def parse_search(payload: dict, keyword: str | None = None, country: str = "us") -> list[Job]:
    jobs: list[Job] = []
    for r in (payload or {}).get("results") or []:
        lo, hi = r.get("salary_min"), r.get("salary_max")
        has_salary = lo is not None or hi is not None
        jobs.append(Job(
            source="adzuna",
            source_id=str(r.get("id")) if r.get("id") is not None else None,
            title=html_to_text(r.get("title")),  # Adzuna wraps matched words in <strong>
            company=html_to_text((r.get("company") or {}).get("display_name")),
            location=html_to_text((r.get("location") or {}).get("display_name")),
            url=r.get("redirect_url"),
            posted_at=iso_date(r.get("created")),
            posted_raw=r.get("created"),
            salary_min=lo,
            salary_max=hi,
            salary_currency=CURRENCY.get(country, "USD") if has_salary else None,
            salary_period="year" if has_salary else None,
            salary_is_estimate=str(r.get("salary_is_predicted")) == "1",
            salary_raw=f"{lo} - {hi} {CURRENCY.get(country, '')}".strip() if has_salary else None,
            employment_type=CONTRACT_TIME.get(r.get("contract_time")),
            description=html_to_text(r.get("description")),
            search_query=keyword,
        ))
    return jobs


class AdzunaSource(BaseSource):
    name = "adzuna"
    attribution = ("Jobs by Adzuna", "https://www.adzuna.com")
    description = "Aggregated job ads (official API; free key, 250 calls/day, attribution required; off by default)"

    def __init__(self, config, session=None):
        super().__init__(config, session)
        self._calls = 0

    def credentials(self) -> tuple[str | None, str | None]:
        return os.environ.get(ENV_ID), os.environ.get(ENV_KEY)

    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        app_id, app_key = self.credentials()
        if not (app_id and app_key):
            self.log.warning("skipped: set %s and %s (free key: https://developer.adzuna.com/signup)", ENV_ID, ENV_KEY)
            return
        country = str(self.options.get("country") or "us").lower()
        for page in range(1, max_pages + 1):
            if self._calls >= MAX_CALLS_PER_RUN:
                self.log.warning("stopping at %d calls this run to stay inside Adzuna's 250/day quota", self._calls)
                return
            params = {"app_id": app_id, "app_key": app_key, "what": keyword, "results_per_page": PAGE_SIZE,
                      "max_days_old": self.config.search.max_age_days, "sort_by": "date", "content-type": "application/json"}
            if (location or "").strip().lower() not in GENERIC_LOCATIONS:
                params["where"] = location
            payload = get_json(API.format(country=country, page=page), params, source=self.name, timeout=self.config.browser.timeout_seconds)
            self._calls += 1
            jobs = parse_search(payload, keyword, country)
            yield from jobs
            if len(jobs) < PAGE_SIZE:
                break
            self.pace(0.5)
