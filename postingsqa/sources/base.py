"""Base classes for source adapters.

Two kinds of adapter share one contract:
- API sources (``kind = "api"``): plain HTTP + JSON via ``postingsqa.http``; no browser, no Playwright.
- Scrapers (``kind = "scraper"``): read public web pages through a Playwright session. Off by default; see
  README "Responsible use".
"""

from __future__ import annotations

import html as htmllib
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import date, timedelta
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from postingsqa.errors import SourceBlocked
from postingsqa.http import pause
from postingsqa.models import Job

if TYPE_CHECKING:
    from postingsqa.browser import BrowserSession
    from postingsqa.config import Config

log = logging.getLogger(__name__)


# -- helpers shared by feed-style API adapters -------------------------------------------------

def title_matches(title: str | None, keywords: list[str]) -> str | None:
    """Return the first keyword whose words all appear in the title (case-insensitive, any order), else None.

    'QA Engineer' matches 'Senior QA Automation Engineer'; 'Data Analyst' does not match 'Data Engineer'.
    """
    words = set(re.findall(r"[a-z0-9+#]+", (title or "").lower()))
    for kw in keywords:
        parts = re.findall(r"[a-z0-9+#]+", kw.lower())
        if parts and all(p in words for p in parts):
            return kw
    return None


def iso_date(text: str | None) -> date | None:
    """'2026-08-21T05:54:39', '2026-08-31T17:56:36-04:00', '2026-06-05T00:00:00Z' → date (None if unparseable)."""
    if not text or len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def recent_enough(posted: date | None, max_age_days: int | None, today: date | None = None) -> bool:
    if posted is None or not max_age_days:
        return True
    return (today or date.today()) - posted <= timedelta(days=max_age_days)


_WS = re.compile(r"[ \t\r\f\v\xa0]+")
_NL = re.compile(r"\n\s*\n\s*\n+")


def html_to_text(fragment: str | None, unescape: bool = False) -> str | None:
    """HTML → readable plain text (paragraph breaks kept). `unescape` first for entity-escaped payloads."""
    if not fragment:
        return None
    if unescape:
        fragment = htmllib.unescape(fragment)
    soup = BeautifulSoup(fragment, "lxml")
    for br in soup.find_all(["br"]):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "ul", "ol", "tr"]):
        block.insert_before("\n")
        block.insert_after("\n")
    for item in soup.find_all("li"):
        item.insert_before("\n")
    text = _WS.sub(" ", soup.get_text())
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _NL.sub("\n\n", text).strip()
    return text or None


# -- contract ----------------------------------------------------------------------------------

class BaseSource(ABC):
    name: str = "base"
    kind: str = "api"                     # "api" | "scraper"
    uses_playwright: bool = False         # True → cli opens a BrowserSession for this run
    attribution: tuple[str, str] | None = None  # (label, url) shown wherever listings are displayed
    description: str = ""                 # one line for the UI / README

    def __init__(self, config: Config, session: BrowserSession | None = None):
        self.config = config
        self.session = session
        self.log = logging.getLogger(f"postingsqa.sources.{self.name}")
        self.blocked_reason: str | None = None

    @property
    def options(self) -> dict:
        """Per-source settings from the `sources:` block of config.yaml (everything except `enabled`)."""
        return self.config.source_options.get(self.name, {})

    def pace(self, factor: float = 1.0) -> None:
        pause(self.config.browser.delay_seconds, factor)

    @abstractmethod
    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        """Yield jobs for one keyword."""

    def fetch_detail(self, job: Job) -> None:
        """Populate description / salary / posted date from a detail view. Default: listings are complete."""

    def close(self) -> None:
        """Release per-source resources. Optional."""

    def run(self) -> list[Job]:
        """Search every configured keyword, dedupe by id, fetch details up to max_details.

        Never raises SourceBlocked: a block sets `blocked_reason` and whatever was collected so far is
        returned, so one blocked phase does not throw away good data. Other per-keyword errors are logged.
        """
        cfg = self.config.search
        seen: dict[str, Job] = {}
        try:
            for keyword in cfg.keywords:
                self.log.info("searching %r in %r", keyword, cfg.location)
                count = 0
                try:
                    for job in self.search(keyword, cfg.location, cfg.max_pages):
                        job.search_query = job.search_query or keyword
                        if job.id not in seen:
                            seen[job.id] = job
                            count += 1
                except SourceBlocked as exc:
                    self.blocked_reason = str(exc)
                    self.log.error("%s (stopping; %d jobs collected)", exc, len(seen))
                    break
                except Exception as exc:  # keep going with the other keywords
                    self.log.error("search %r failed: %s", keyword, exc)
                    self.log.debug("traceback", exc_info=True)
                self.log.info("%d new jobs for %r", count, keyword)

            jobs = list(seen.values())
            needs_detail = [j for j in jobs if not j.description]
            if cfg.fetch_descriptions and needs_detail and not self.blocked_reason and self.fetch_detail_supported:
                targets = needs_detail[: cfg.max_details]
                self.log.info("fetching details for %d/%d jobs", len(targets), len(jobs))
                for i, job in enumerate(targets, 1):
                    try:
                        self.fetch_detail(job)
                    except SourceBlocked as exc:
                        self.blocked_reason = f"{exc} (during detail fetch; {i - 1}/{len(targets)} details done)"
                        self.log.error("%s", self.blocked_reason)
                        break
                    except Exception as exc:
                        self.log.warning("detail fetch failed for %s: %s", job.url, exc)
                        self.log.debug("traceback", exc_info=True)
                    if i % 10 == 0:
                        self.log.info("details %d/%d", i, len(targets))
            return jobs
        finally:
            self.close()

    @property
    def fetch_detail_supported(self) -> bool:
        return type(self).fetch_detail is not BaseSource.fetch_detail


class BrowserSource(BaseSource):
    """Adapter that reads public pages in a persistent Chromium context.

    A bot challenge stops the source for this run (SourceBlocked): nothing is retried or worked around.
    In headed mode the user may solve the challenge in the visible window; the persistent profile keeps
    the resulting cookie, exactly as a normal browser would.
    """

    kind = "scraper"
    uses_playwright = True
    wait_selectors: str = "body"

    def __init__(self, config: Config, session: BrowserSession | None = None):
        super().__init__(config, session)
        if session is None:
            raise RuntimeError(f"{self.name}: browser sources need a BrowserSession")
        self._ctx = None
        self._page = None

    @property
    def page(self):
        if self._page is None:
            self._ctx = self.session.context(self.name)
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            self._on_new_page(self._page)
        return self._page

    def _on_new_page(self, page) -> None:
        """Hook for subclasses (e.g. attach response listeners)."""

    def _goto(self, url: str, wait_selector: str | None = None, settle_ms: int = 0) -> str:
        wait_selector = wait_selector or self.wait_selectors
        self.page.goto(url, wait_until="domcontentloaded")
        self.session.ensure_not_blocked(self.page, self.name)
        try:
            self.page.wait_for_selector(wait_selector, timeout=15000)
        except Exception:
            pass
        if settle_ms:
            self.page.wait_for_timeout(settle_ms)
        self._after_load()
        self.session.ensure_not_blocked(self.page, self.name)
        return self.page.content()

    def _after_load(self) -> None:
        """Hook: dismiss modals etc."""

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
        self._ctx = self._page = None
