"""LinkedIn adapter using the public guest job-search endpoints (no login, no browser window)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from jobbot.browser import SourceBlocked, is_challenge_html
from jobbot.models import Job
from jobbot.parsing import clean, parse_iso_date, parse_relative_date, parse_salary
from jobbot.sources.base import BaseSource

BASE = "https://www.linkedin.com"
SEARCH_PATH = "/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_PATH = "/jobs-guest/jobs/api/jobPosting/{job_id}"
PAGE_SIZE = 10

_ID_RE = re.compile(r"(\d{6,})")


def _job_id_from(card) -> str | None:
    urn = card.get("data-entity-urn") or ""
    m = _ID_RE.search(urn)
    if m:
        return m.group(1)
    link = card.select_one("a[href*='/jobs/view/']")
    if link:
        m = _ID_RE.search(link["href"].split("?")[0].rstrip("/").rsplit("-", 1)[-1])
        if m:
            return m.group(1)
    return None


def parse_search_cards(html: str, keyword: str | None = None) -> list[Job]:
    """Parse the HTML fragment returned by the guest search endpoint into Jobs."""
    soup = BeautifulSoup(html, "lxml")
    jobs: list[Job] = []
    for card in soup.select("div.base-card, li > div[class*=base-card], div[data-entity-urn]"):
        job_id = _job_id_from(card)
        title = clean(card.select_one("[class*=_title]").get_text()) if card.select_one("[class*=_title]") else None
        company_el = card.select_one("[class*=_subtitle], [class*=company]")
        location_el = card.select_one("[class*=_location]")
        link = card.select_one("a[href*='/jobs/view/']")
        time_el = card.select_one("time")
        url = link["href"].split("?")[0] if link else (f"{BASE}/jobs/view/{job_id}" if job_id else None)
        posted = parse_iso_date(time_el.get("datetime")) if time_el else None
        salary_el = card.select_one("[class*=salary]")
        job = Job(
            source="linkedin",
            source_id=job_id,
            title=title,
            company=clean(company_el.get_text()) if company_el else None,
            location=clean(location_el.get_text()) if location_el else None,
            url=url,
            posted_at=posted,
            posted_raw=clean(time_el.get_text()) if time_el else None,
            search_query=keyword,
        )
        if salary_el:
            for k, v in parse_salary(salary_el.get_text()).items():
                setattr(job, k, v)
        jobs.append(job)
    return jobs


def parse_job_detail(html: str) -> dict:
    """Extract description, criteria, salary and posted date from the guest jobPosting fragment."""
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}
    desc = soup.select_one("[class*=description__text], [class*=show-more-less-html__markup], [class*=description] section")
    if desc:
        out["description"] = clean(desc.get_text("\n"))
    for item in soup.select("[class*=job-criteria-item], li[class*=criteria]"):
        header = item.select_one("h3, [class*=subheader]")
        value = item.select_one("span, [class*=criteria-text]")
        if not header or not value:
            continue
        h = clean(header.get_text()).lower()
        v = clean(value.get_text())
        if "seniority" in h:
            out["seniority"] = v
        elif "employment" in h:
            out["employment_type"] = v
    salary = soup.select_one("[class*=salary], [class*=compensation__salary]")
    if salary:
        out.update({k: v for k, v in parse_salary(salary.get_text()).items() if v is not None})
    posted = soup.select_one("[class*=posted-time], [class*=posted-date]")
    if posted:
        out["posted_raw"] = clean(posted.get_text())
        d = parse_relative_date(out["posted_raw"])
        if d:
            out["posted_at"] = d
    return out


class LinkedInSource(BaseSource):
    name = "linkedin"

    def __init__(self, config, session):
        super().__init__(config, session)
        self._api = None
        self._429s = 0

    @property
    def api(self):
        if self._api is None:
            self._api = self.session.api(base_url=BASE)
        return self._api

    def _get(self, path: str, params: dict | None = None) -> str | None:
        """GET with backoff on 429. Returns body, or None for 4xx that mean 'no more results'."""
        url = path + ("?" + urlencode(params) if params else "")
        for attempt in range(4):
            resp = self.api.get(url)
            if resp.status == 200:
                self._429s = 0
                body = resp.text()
                if is_challenge_html(body):
                    raise SourceBlocked("linkedin: challenge / auth wall returned by guest endpoint")
                return body
            if resp.status == 429:
                self._429s += 1
                if self._429s >= 6:
                    raise SourceBlocked("linkedin: rate limited repeatedly (HTTP 429)")
                wait = 5 * (2 ** attempt)
                self.log.warning("429 from linkedin, backing off %ss", wait)
                import time

                time.sleep(wait)
                continue
            if resp.status in (400, 404):
                return None
            if resp.status in (403, 999):
                raise SourceBlocked(f"linkedin: HTTP {resp.status}")
            self.log.warning("unexpected HTTP %s for %s", resp.status, url)
            return None
        return None

    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        params = {
            "keywords": keyword,
            "location": location,
            "f_TPR": f"r{self.config.search.max_age_days * 86400}",
            "start": 0,
        }
        for page in range(max_pages):
            params["start"] = page * PAGE_SIZE
            body = self._get(SEARCH_PATH, params)
            if not body or not body.strip():
                break
            jobs = parse_search_cards(body, keyword)
            if not jobs:
                if page == 0:
                    self.session.dump_debug("linkedin", body, "search-empty")
                break
            yield from jobs
            if len(jobs) < PAGE_SIZE:
                break
            self.session.delay()

    def fetch_detail(self, job: Job) -> None:
        if not job.source_id:
            return
        body = self._get(DETAIL_PATH.format(job_id=job.source_id))
        if not body:
            return
        detail = parse_job_detail(body)
        if not detail.get("description"):
            self.session.dump_debug("linkedin", body, f"detail-{job.source_id}")
        for k, v in detail.items():
            if v is not None and (k != "posted_at" or job.posted_at is None):
                setattr(job, k, v)
        self.session.delay(0.6)

    def close(self) -> None:
        if self._api is not None:
            self._api.dispose()
            self._api = None
