"""Greenhouse Job Board API: public careers pages of companies that use Greenhouse.
https://developers.greenhouse.io/job-board.html

Configure the boards to follow in config.yaml: ``greenhouse: { enabled: true, boards: [gitlab, stripe] }``.
One GET per board per run; keyword and age filtering happen client-side.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from postingsqa.http import get_json
from postingsqa.models import Job
from postingsqa.parsing import clean
from postingsqa.sources.base import BaseSource, html_to_text, iso_date, recent_enough, title_matches

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
DEFAULT_BOARDS = ["gitlab", "stripe", "airbnb"]


def _salary(ranges: list[dict] | None) -> dict:
    if not ranges:
        return {}
    p = ranges[0]
    lo = p.get("min_cents")
    hi = p.get("max_cents")
    if lo is None and hi is None:
        return {}
    lo = lo / 100 if lo is not None else None
    hi = hi / 100 if hi is not None else None
    return {
        "salary_min": lo, "salary_max": hi, "salary_currency": p.get("currency_type") or "USD", "salary_period": "year",
        "salary_raw": f"{lo:,.0f} - {hi:,.0f} {p.get('currency_type') or 'USD'} ({p.get('title') or 'salary range'})" if lo is not None and hi is not None else None,
    }


def parse_jobs(payload: dict, board: str, keywords: list[str] | None = None, max_age_days: int | None = None, today: date | None = None) -> list[Job]:
    jobs: list[Job] = []
    for r in (payload or {}).get("jobs") or []:
        kw = title_matches(r.get("title"), keywords) if keywords else None
        if keywords and not kw:
            continue
        posted = iso_date(r.get("first_published") or r.get("updated_at"))
        if not recent_enough(posted, max_age_days, today):
            continue
        location = clean((r.get("location") or {}).get("name"))
        offices = [o.get("name") or "" for o in r.get("offices") or []]
        meta = {m.get("name"): m.get("value") for m in r.get("metadata") or [] if m.get("name")}
        workplace = str(meta.get("Workplace Type") or "")
        remote = True if "remote" in f"{location or ''} {workplace} {' '.join(offices)}".lower() else None
        jobs.append(Job(
            source="greenhouse",
            source_id=str(r.get("id")) if r.get("id") is not None else None,
            title=clean(r.get("title")),
            company=clean(r.get("company_name")) or board.replace("-", " ").title(),
            location=location,
            url=r.get("absolute_url"),
            remote=remote,
            posted_at=posted,
            posted_raw=r.get("first_published") or r.get("updated_at"),
            employment_type=clean(str(meta["Employment Type"])) if meta.get("Employment Type") else None,
            description=html_to_text(r.get("content"), unescape=True),
            search_query=kw,
            **_salary(r.get("pay_input_ranges")),
        ))
    return jobs


class GreenhouseSource(BaseSource):
    name = "greenhouse"
    attribution = ("Greenhouse job boards", "https://www.greenhouse.com")
    description = "Careers pages of companies on Greenhouse (public API, no key; set `boards`)"

    def __init__(self, config, session=None):
        super().__init__(config, session)
        self._payloads: dict[str, dict] = {}

    @property
    def boards(self) -> list[str]:
        boards = self.options.get("boards") or DEFAULT_BOARDS
        return [str(b).strip() for b in boards if str(b).strip()]

    def _payload(self, board: str) -> dict:
        if board not in self._payloads:
            if self._payloads:
                self.pace(0.3)
            data = get_json(API.format(board=board), {"content": "true", "pay_transparency": "true"}, source=self.name,
                            timeout=self.config.browser.timeout_seconds)
            if data is None:
                self.log.warning("board %r not found (404); check the token in config.yaml", board)
                data = {}
            self._payloads[board] = data
            self.log.info("board %r: %d postings", board, len(data.get("jobs") or []))
        return self._payloads[board]

    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        for board in self.boards:
            yield from parse_jobs(self._payload(board), board, [keyword], self.config.search.max_age_days)
