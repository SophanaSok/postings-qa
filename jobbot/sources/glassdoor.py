"""Glassdoor adapter: drive the SRCH page in a persistent context, capture the GraphQL job-search
responses in flight, fall back to DOM parsing."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

from bs4 import BeautifulSoup

from jobbot.browser import SourceBlocked
from jobbot.models import Job
from jobbot.parsing import clean, parse_relative_date, parse_salary
from jobbot.sources.base import BrowserSource

BASE = "https://www.glassdoor.com"
PAGE_SIZE = 30  # Glassdoor lists ~30 per page
GRAPH_MARKERS = ("/graph", "jobSearchResultsQuery", "JobSearchResultsQuery")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def build_search_url(keyword: str, location: str, page: int = 1, max_age_days: int | None = None) -> str:
    """`/Job/united-states-qa-engineer-jobs-SRCH_IL.0,13_IN1_KO14,25.htm` — IL/KO are character spans of the
    slug for location and keyword, IN1 = United States. Non-US locations still work via the same span scheme
    as a text search (Glassdoor resolves them server-side)."""
    loc, kw = _slug(location), _slug(keyword)
    il_end = len(loc)
    ko_start, ko_end = il_end + 1, il_end + 1 + len(kw)
    url = f"{BASE}/Job/{loc}-{kw}-jobs-SRCH_IL.0,{il_end}_IN1_KO{ko_start},{ko_end}"
    if page > 1:
        url += f"_IP{page}"
    url += ".htm"
    if max_age_days:
        for cap in (1, 3, 7, 14, 30):
            if max_age_days <= cap:
                url += f"?fromAge={cap}"
                break
    return url


def _walk(obj: Any):
    """Yield every dict nested anywhere in a JSON structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def parse_graph_payload(payload: Any, keyword: str | None = None) -> list[Job]:
    """Extract jobs from a jobSearchResults GraphQL/BFF response body (dict or JSON string)."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    jobs = []
    seen = set()
    for d in _walk(payload):
        jv = d.get("jobview") if isinstance(d.get("jobview"), dict) else None
        if not jv:
            continue
        job_info = jv.get("job") or {}
        header = jv.get("header") or {}
        overview = jv.get("overview") or {}
        listing_id = str(job_info.get("listingId") or header.get("jobListingId") or d.get("jobListingId") or "")
        if not listing_id or listing_id in seen:
            continue
        seen.add(listing_id)
        title = header.get("jobTitleText") or job_info.get("jobTitleText") or header.get("normalizedJobTitle")
        company = header.get("employerNameFromSearch") or (header.get("employer") or {}).get("name") or (overview.get("name"))
        location = header.get("locationName") or job_info.get("locationName")
        age = header.get("ageInDays")
        posted = (date.today() - timedelta(days=int(age))) if isinstance(age, (int, float)) else None
        url = header.get("jobViewUrl") or header.get("seoJobLink") or ""
        if url and url.startswith("/"):
            url = BASE + url
        if not url:
            url = f"{BASE}/job-listing/j?jl={listing_id}"
        pay = header.get("payPeriodAdjustedPay") or {}
        job = Job(
            source="glassdoor",
            source_id=listing_id,
            title=clean(title),
            company=clean(company),
            location=clean(location),
            url=url,
            posted_at=posted,
            posted_raw=f"{int(age)}d" if isinstance(age, (int, float)) else None,
            search_query=keyword,
        )
        if pay.get("p10") or pay.get("p90") or pay.get("p50"):
            period = str(header.get("payPeriod") or "ANNUAL").upper()
            job.salary_min = float(pay.get("p10") or pay.get("p50"))
            job.salary_max = float(pay.get("p90") or pay.get("p50"))
            job.salary_currency = (header.get("payCurrency") or "USD")
            job.salary_period = {"ANNUAL": "year", "HOURLY": "hour", "MONTHLY": "month", "WEEKLY": "week", "DAILY": "day"}.get(period, "year")
            job.salary_is_estimate = bool(header.get("salarySource") not in ("EMPLOYER_PROVIDED", "EMPLOYER")) if header.get("salarySource") else True
            job.salary_raw = f"{job.salary_min:,.0f}-{job.salary_max:,.0f} {job.salary_currency}/{job.salary_period}"
        jobs.append(job)
    return jobs


def parse_cards_dom(html: str, keyword: str | None = None) -> list[Job]:
    soup = BeautifulSoup(html, "lxml")
    jobs = []
    seen: set[str] = set()
    for card in soup.select("li[data-test=jobListing], li[data-jobid]"):
        listing_id = card.get("data-jobid") or card.get("data-id")
        link = card.select_one("[data-test=job-title], a[data-test=job-link], a[href*='/job-listing/']")
        if not listing_id and link:
            m = re.search(r"jl=(\d+)|jobListingId=(\d+)", link.get("href", ""))
            listing_id = next((g for g in m.groups() if g), None) if m else None
        if not listing_id or str(listing_id) in seen:
            continue
        seen.add(str(listing_id))
        url = link.get("href") if link else None
        if url and url.startswith("/"):
            url = BASE + url
        company_el = card.select_one("[class*=EmployerProfile_compactEmployerName], [data-test=employer-name], [class*=employer-name]")
        loc_el = card.select_one("[data-test=emp-location], [class*=JobCard_location]")
        sal_el = card.select_one("[data-test=detailSalary], [class*=JobCard_salaryEstimate]")
        age_el = card.select_one("[data-test=job-age], [class*=JobCard_listingAge]")
        job = Job(
            source="glassdoor",
            source_id=str(listing_id),
            title=clean(link.get_text()) if link else None,
            company=clean(company_el.get_text()) if company_el else None,
            location=clean(loc_el.get_text()) if loc_el else None,
            url=url or f"{BASE}/job-listing/j?jl={listing_id}",
            posted_raw=clean(age_el.get_text()) if age_el else None,
            posted_at=parse_relative_date(age_el.get_text()) if age_el else None,
            search_query=keyword,
        )
        if sal_el:
            parsed = parse_salary(sal_el.get_text())
            if "employer" not in sal_el.get_text().lower():
                parsed["salary_is_estimate"] = True
            for k, v in parsed.items():
                setattr(job, k, v)
        jobs.append(job)
    return jobs


def parse_job_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}
    desc = soup.select_one("[data-test=jobDescriptionContent], [class*=JobDetails_jobDescription], #JobDescriptionContainer, [class*=jobDescriptionContent]")
    if desc:
        out["description"] = clean(desc.get_text("\n"))
    sal = soup.select_one("[data-test=detailSalary], [class*=SalaryEstimate_salaryRange], [class*=JobDetails_salary]")
    if sal:
        parsed = parse_salary(sal.get_text())
        if parsed["salary_min"] is not None:
            out.update({k: v for k, v in parsed.items() if v is not None})
    return out


class GlassdoorSource(BrowserSource):
    name = "glassdoor"
    wait_selectors = "li[data-test=jobListing], li[data-jobid], [data-test=jobDescriptionContent], [class*=JobDetails_jobDescription]"
    DETAIL_SELECTOR = "[data-test=jobDescriptionContent], [class*=JobDetails_jobDescription], #JobDescriptionContainer"

    def __init__(self, config, session):
        super().__init__(config, session)
        self._captured: list[Any] = []

    def _on_new_page(self, page) -> None:
        page.on("response", self._on_response)

    def _on_response(self, response) -> None:
        url = response.url
        if not any(m in url for m in GRAPH_MARKERS):
            return
        try:
            body = response.text()
        except Exception:
            return
        if "jobListings" in body or "jobview" in body:
            self._captured.append(body)

    def _after_load(self) -> None:
        self._dismiss_modal()

    def _dismiss_modal(self) -> None:
        for sel in ("[data-test=modal-close]", "button[aria-label=Close]", "[alt=Close]", ".modal_closeIcon", "[class*=CloseButton]"):
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=500):
                    btn.click(timeout=2000)
                    return
            except Exception:
                continue
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass

    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        for page_no in range(1, max_pages + 1):
            url = build_search_url(keyword, location, page_no, self.config.search.max_age_days)
            self._captured.clear()
            html = self._goto(url, "li[data-test=jobListing], li[data-jobid]", settle_ms=1500)
            jobs: list[Job] = []
            for body in list(self._captured):
                jobs.extend(parse_graph_payload(body, keyword))
            if not jobs:
                jobs = parse_cards_dom(html, keyword)
            if not jobs:
                if page_no == 1:
                    self.log.warning("no job cards parsed; saved %s", self.session.dump_debug("glassdoor", html, "search-empty"))
                break
            yield from jobs
            if len(jobs) < 10:
                break
            self.session.delay()

    def _detail_from_pane(self, job: Job) -> dict | None:
        """Click the job's card on the current results page so the detail pane loads via XHR (no navigation,
        so no fresh Cloudflare check). Returns None when the card is not on the current page."""
        try:
            card = self.page.locator(f'li[data-jobid="{job.source_id}"]').first
            if card.count() == 0:
                return None
            card.scroll_into_view_if_needed(timeout=3000)
            card.click(timeout=3000)
            self.page.wait_for_timeout(2500)
            self._dismiss_modal()
            self.session.ensure_not_blocked(self.page, self.name)
            detail = parse_job_detail(self.page.content())
            return detail if detail.get("description") else None
        except SourceBlocked:
            raise
        except Exception as exc:
            self.log.debug("pane detail failed for %s: %s", job.source_id, exc)
            return None

    def fetch_detail(self, job: Job) -> None:
        detail = self._detail_from_pane(job)
        if detail is None:
            # Direct job pages sit behind a Cloudflare challenge far more often than the search page does;
            # try once, and let the blocked handling in BaseSource.run stop the detail phase if it fails.
            html = self._goto(job.url, self.DETAIL_SELECTOR)
            detail = parse_job_detail(html)
            if not detail.get("description"):
                self.session.dump_debug("glassdoor", html, f"detail-{job.source_id}")
        for k, v in detail.items():
            if v is not None:
                setattr(job, k, v)
        self.session.delay(0.6)
