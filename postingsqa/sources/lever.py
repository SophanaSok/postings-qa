"""Lever Postings API: public careers pages of companies that use Lever. https://github.com/lever/postings-api

Configure the sites to follow in config.yaml: ``lever: { enabled: true, sites: [spotify, palantir] }`` or a
mapping ``sites: { spotify: Spotify }`` to control the displayed company name. One GET per site per run.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timezone

from postingsqa.http import get_json
from postingsqa.models import Job
from postingsqa.parsing import clean
from postingsqa.sources.base import BaseSource, html_to_text, recent_enough, title_matches

API = "https://api.lever.co/v0/postings/{site}"
DEFAULT_SITES = ["spotify", "palantir"]


def site_map(option) -> dict[str, str]:
    """`sites` option → {slug: display name}."""
    if not option:
        option = DEFAULT_SITES
    if isinstance(option, dict):
        return {str(k).strip(): str(v).strip() or str(k).strip().title() for k, v in option.items() if str(k).strip()}
    return {str(s).strip(): str(s).strip().replace("-", " ").title() for s in option if str(s).strip()}


def _posted(ms) -> date | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def _salary(sr: dict | None) -> dict:
    if not sr or (sr.get("min") is None and sr.get("max") is None):
        return {}
    interval = str(sr.get("interval") or "").lower()
    period = "hour" if "hour" in interval else "month" if "month" in interval else "week" if "week" in interval else "year"
    return {"salary_min": sr.get("min"), "salary_max": sr.get("max"), "salary_currency": sr.get("currency") or "USD",
            "salary_period": period, "salary_raw": f"{sr.get('min')} - {sr.get('max')} {sr.get('currency') or ''} {interval}".strip()}


def parse_postings(payload: list, site: str, company: str | None = None, keywords: list[str] | None = None,
                   max_age_days: int | None = None, today: date | None = None) -> list[Job]:
    jobs: list[Job] = []
    for r in payload or []:
        kw = title_matches(r.get("text"), keywords) if keywords else None
        if keywords and not kw:
            continue
        posted = _posted(r.get("createdAt"))
        if not recent_enough(posted, max_age_days, today):
            continue
        cats = r.get("categories") or {}
        workplace = str(r.get("workplaceType") or "").lower()
        remote = True if workplace == "remote" else False if workplace in ("hybrid", "onsite", "on-site") else None
        parts = [r.get("descriptionPlain") or html_to_text(r.get("description"))]
        for section in r.get("lists") or []:
            body = html_to_text(section.get("content"))
            if body:
                parts.append(f"{section.get('text') or ''}\n{body}".strip())
        parts.append(r.get("additionalPlain") or html_to_text(r.get("additional")))
        jobs.append(Job(
            source="lever",
            source_id=r.get("id"),
            title=clean(r.get("text")),
            company=company or site.replace("-", " ").title(),
            location=clean(cats.get("location")),
            url=r.get("hostedUrl"),
            remote=remote,
            posted_at=posted,
            posted_raw=str(r.get("createdAt")) if r.get("createdAt") else None,
            employment_type=clean(cats.get("commitment")),
            description="\n\n".join(p for p in parts if p) or None,
            search_query=kw,
            **_salary(r.get("salaryRange")),
        ))
    return jobs


class LeverSource(BaseSource):
    name = "lever"
    attribution = ("Lever job boards", "https://www.lever.co")
    description = "Careers pages of companies on Lever (public API, no key; set `sites`)"

    def __init__(self, config, session=None):
        super().__init__(config, session)
        self._payloads: dict[str, list] = {}

    def _payload(self, site: str) -> list:
        if site not in self._payloads:
            if self._payloads:
                self.pace(0.3)
            data = get_json(API.format(site=site), {"mode": "json"}, source=self.name, timeout=self.config.browser.timeout_seconds)
            if data is None:
                self.log.warning("site %r not found (404); check the slug in config.yaml", site)
                data = []
            self._payloads[site] = data if isinstance(data, list) else []
            self.log.info("site %r: %d postings", site, len(self._payloads[site]))
        return self._payloads[site]

    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        for site, company in site_map(self.options.get("sites")).items():
            yield from parse_postings(self._payload(site), site, company, [keyword], self.config.search.max_age_days)
