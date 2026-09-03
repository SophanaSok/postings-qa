"""Indeed adapter: search pages via a persistent browser context, embedded mosaic JSON first, DOM fallback."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from jobbot.models import Job
from jobbot.parsing import clean, parse_relative_date, parse_salary
from jobbot.sources.base import BrowserSource

BASE = "https://www.indeed.com"
PAGE_SIZE = 10
_MOSAIC_RE = re.compile(r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*')
_INITIAL_DATA_RE = re.compile(r"window\._initialData\s*=\s*")


def _extract_json(html: str, marker: re.Pattern) -> dict | None:
    m = marker.search(html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, m.end())
        return obj
    except json.JSONDecodeError:
        return None


def _salary_from_result(r: dict) -> dict:
    ext = r.get("extractedSalary") or {}
    # Indeed's structured salary is occasionally garbage (max=-1, or 1..10); only trust positive numbers
    lo = ext.get("min") if isinstance(ext.get("min"), (int, float)) and ext.get("min") > 0 else None
    hi = ext.get("max") if isinstance(ext.get("max"), (int, float)) and ext.get("max") > 0 else None
    if lo or hi:
        period = {"yearly": "year", "hourly": "hour", "monthly": "month", "weekly": "week", "daily": "day"}.get(str(ext.get("type", "")).lower(), "year")
        return {"salary_min": float(lo or hi), "salary_max": float(hi or lo), "salary_currency": "USD", "salary_period": period, "salary_is_estimate": bool(r.get("estimatedSalary")), "salary_raw": (r.get("salarySnippet") or {}).get("text")}
    snippet = (r.get("salarySnippet") or {}).get("text") or (r.get("estimatedSalary") or {}).get("formattedRange")
    parsed = parse_salary(snippet)
    if r.get("estimatedSalary") and parsed["salary_min"] is not None:
        parsed["salary_is_estimate"] = True
    return parsed


def parse_mosaic(html: str, keyword: str | None = None) -> list[Job]:
    """Parse jobs from the `mosaic-provider-jobcards` JSON blob embedded in a search page."""
    data = _extract_json(html, _MOSAIC_RE)
    if not data:
        return []
    results = (((data.get("metaData") or {}).get("mosaicProviderJobCardsModel") or {}).get("results")) or []
    jobs = []
    for r in results:
        jk = r.get("jobkey")
        if not jk:
            continue
        posted = None
        if r.get("pubDate"):
            posted = datetime.fromtimestamp(int(r["pubDate"]) / 1000, tz=timezone.utc).date()
        elif r.get("formattedRelativeTime"):
            posted = parse_relative_date(r["formattedRelativeTime"])
        location = r.get("formattedLocation") or (r.get("jobLocationCity") or "")
        remote = bool(r.get("remoteLocation")) or (r.get("remoteWorkModel") or {}).get("type") == "REMOTE_ONLY" or None
        job_types = r.get("jobTypes") or []
        job = Job(
            source="indeed",
            source_id=jk,
            title=clean(r.get("displayTitle") or r.get("title")),
            company=clean(r.get("company") or r.get("truncatedCompany")),
            location=clean(location),
            url=f"{BASE}/viewjob?jk={jk}",
            remote=remote,
            posted_at=posted,
            posted_raw=r.get("formattedRelativeTime"),
            employment_type=", ".join(job_types) if job_types else None,
            search_query=keyword,
            **_salary_from_result(r),
        )
        jobs.append(job)
    return jobs


def parse_cards_dom(html: str, keyword: str | None = None) -> list[Job]:
    """DOM fallback for search pages when the mosaic blob is absent."""
    soup = BeautifulSoup(html, "lxml")
    jobs = []
    for card in soup.select("div.job_seen_beacon, [data-testid=slider_item], li div.cardOutline"):
        link = card.select_one("h2.jobTitle a[data-jk], a[data-jk], a[href*='/viewjob']")
        if not link:
            continue
        jk = link.get("data-jk")
        if not jk:
            m = re.search(r"jk=([0-9a-f]+)", link.get("href", ""))
            jk = m.group(1) if m else None
        if not jk:
            continue
        title_el = card.select_one("h2.jobTitle span[title], h2.jobTitle span, h2 a")
        company_el = card.select_one("[data-testid=company-name], .companyName")
        loc_el = card.select_one("[data-testid=text-location], .companyLocation")
        date_el = card.select_one("[data-testid=myJobsStateDate], .date")
        salary_el = card.select_one("[data-testid=attribute_snippet_testid], .salary-snippet-container, .estimated-salary")
        job = Job(
            source="indeed",
            source_id=jk,
            title=clean(title_el.get_text()) if title_el else clean(link.get_text()),
            company=clean(company_el.get_text()) if company_el else None,
            location=clean(loc_el.get_text()) if loc_el else None,
            url=f"{BASE}/viewjob?jk={jk}",
            posted_raw=clean(date_el.get_text()) if date_el else None,
            posted_at=parse_relative_date(date_el.get_text()) if date_el else None,
            search_query=keyword,
        )
        if salary_el:
            for k, v in parse_salary(salary_el.get_text()).items():
                setattr(job, k, v)
        jobs.append(job)
    return jobs


def parse_search_page(html: str, keyword: str | None = None) -> list[Job]:
    return parse_mosaic(html, keyword) or parse_cards_dom(html, keyword)


def parse_job_detail(html: str) -> dict:
    """Description, salary/type from a /viewjob page (JSON first, DOM fallback)."""
    out: dict = {}
    data = _extract_json(html, _INITIAL_DATA_RE)
    if data:
        model = ((data.get("jobInfoWrapperModel") or {}).get("jobInfoModel")) or {}
        desc = (model.get("sanitizedJobDescription") or {}).get("content") if isinstance(model.get("sanitizedJobDescription"), dict) else model.get("sanitizedJobDescription")
        if desc:
            out["description"] = clean(BeautifulSoup(desc, "lxml").get_text("\n"))
        comp = (data.get("salaryInfoModel") or {}).get("salaryText") or (model.get("jobMetadataHeaderModel") or {}).get("jobTitle")
        if comp:
            out.update({k: v for k, v in parse_salary(comp).items() if v is not None})
    if "description" not in out:
        soup = BeautifulSoup(html, "lxml")
        desc_el = soup.select_one("#jobDescriptionText, [data-testid=jobDescriptionText]")
        if desc_el:
            out["description"] = clean(desc_el.get_text("\n"))
        sal = soup.select_one("[data-testid=jobsearch-SalaryInfoAndJobType], #salaryInfoAndJobType")
        if sal:
            out.update({k: v for k, v in parse_salary(sal.get_text()).items() if v is not None})
    return out


class IndeedSource(BrowserSource):
    name = "indeed"
    wait_selectors = "#mosaic-provider-jobcards, div.job_seen_beacon, #jobDescriptionText, .jobsearch-NoResult-messageContainer"

    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        for page_no in range(max_pages):
            params = {"q": keyword, "l": location, "fromage": min(self.config.search.max_age_days, 14), "start": page_no * PAGE_SIZE, "sort": "date"}
            html = self._goto(f"{BASE}/jobs?{urlencode(params)}")
            jobs = parse_search_page(html, keyword)
            if not jobs:
                if page_no == 0:
                    self.log.warning("no job cards parsed; saved %s", self.session.dump_debug("indeed", html, "search-empty"))
                break
            yield from jobs
            if len(jobs) < PAGE_SIZE:
                break
            self.session.delay()

    def fetch_detail(self, job: Job) -> None:
        # /viewjob redirects guests to a sign-in page (and that visit gets the profile flagged); the search
        # page with `vjk=<jobkey>` renders the same job in its detail pane without login.
        params = {"q": job.search_query or self.config.search.keywords[0], "l": self.config.search.location, "vjk": job.source_id}
        self.session.delay(0.5)
        html = self._goto(f"{BASE}/jobs?{urlencode(params)}", "#jobDescriptionText")
        detail = parse_job_detail(html)
        if not detail.get("description"):
            self.session.dump_debug("indeed", html, f"detail-{job.source_id}")
        for k, v in detail.items():
            if v is not None:
                setattr(job, k, v)
        self.session.delay(0.6)
