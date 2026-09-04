"""Remotive: public remote-jobs feed. https://github.com/remotive-com/remote-jobs-api

One GET per run returns the whole feed; keyword and age filtering happen client-side. Remotive asks for at
most a few requests a day and for listings to link back to remotive.com and name Remotive as the source.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from postingsqa.http import get_json
from postingsqa.models import Job
from postingsqa.parsing import clean, parse_salary
from postingsqa.sources.base import BaseSource, html_to_text, iso_date, recent_enough, title_matches

API = "https://remotive.com/api/remote-jobs"


def parse_jobs(payload: dict, keywords: list[str] | None = None, max_age_days: int | None = None, today: date | None = None) -> list[Job]:
    jobs: list[Job] = []
    for r in (payload or {}).get("jobs") or []:
        kw = title_matches(r.get("title"), keywords) if keywords else None
        if keywords and not kw:
            continue
        posted = iso_date(r.get("publication_date"))
        if not recent_enough(posted, max_age_days, today):
            continue
        salary = parse_salary(r.get("salary") or None)
        job_type = (r.get("job_type") or "").replace("_", "-") or None
        jobs.append(Job(
            source="remotive",
            source_id=str(r.get("id")) if r.get("id") is not None else None,
            title=clean(r.get("title")),
            company=clean(r.get("company_name")),
            location=clean(r.get("candidate_required_location")) or "Remote",
            url=r.get("url"),
            remote=True,
            posted_at=posted,
            posted_raw=r.get("publication_date"),
            employment_type=job_type,
            description=html_to_text(r.get("description")),
            search_query=kw,
            **salary,
        ))
    return jobs


class RemotiveSource(BaseSource):
    name = "remotive"
    attribution = ("Remotive", "https://remotive.com")
    description = "Remote jobs feed (public API, no key; one request per run)"

    def __init__(self, config, session=None):
        super().__init__(config, session)
        self._payload: dict | None = None

    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        if self._payload is None:
            params = {"category": self.options.get("category")} if self.options.get("category") else None
            self._payload = get_json(API, params, source=self.name, timeout=self.config.browser.timeout_seconds) or {}
            self.log.info("feed has %s jobs", self._payload.get("total-job-count") or len(self._payload.get("jobs") or []))
        yield from parse_jobs(self._payload, [keyword], self.config.search.max_age_days)
