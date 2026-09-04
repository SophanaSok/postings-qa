"""Base classes for site adapters."""

from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterator

from postingsqa.browser import BrowserSession, SourceBlocked
from postingsqa.config import Config
from postingsqa.models import Job

log = logging.getLogger(__name__)


class BaseSource(ABC):
    name: str = "base"

    def __init__(self, config: Config, session: BrowserSession):
        self.config = config
        self.session = session
        self.log = logging.getLogger(f"postingsqa.sources.{self.name}")
        self.blocked_reason: str | None = None

    @abstractmethod
    def search(self, keyword: str, location: str, max_pages: int) -> Iterator[Job]:
        """Yield jobs from the search result pages for one keyword."""

    @abstractmethod
    def fetch_detail(self, job: Job) -> None:
        """Populate description / salary / posted date from the job's detail view. Mutates job."""

    def close(self) -> None:
        """Release per-source resources (browser context). Optional."""

    def run(self) -> list[Job]:
        """Search every configured keyword, dedupe by id, fetch details up to max_details.

        Never raises SourceBlocked: a challenge sets `blocked_reason` and whatever was collected so far is
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
                    self.log.error("%s (stopping search; %d jobs collected)", exc, len(seen))
                    break
                except Exception as exc:  # keep going with the other keywords
                    self.log.error("search %r failed: %s", keyword, exc)
                    self.log.debug("traceback", exc_info=True)
                self.log.info("%d new jobs for %r", count, keyword)

            jobs = list(seen.values())
            if cfg.fetch_descriptions and jobs and not self.blocked_reason:
                targets = jobs[: cfg.max_details]
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


class BrowserSource(BaseSource):
    """Adapter that drives a real page in a persistent Chromium context.

    On a bot challenge in headless mode the persistent profile is usually what got flagged (Indeed and
    Glassdoor both tie the challenge to cookies), so we retry once with a wiped profile before giving up.
    In headed mode the user gets time to solve the challenge and the profile keeps the clearance cookie.
    """

    wait_selectors: str = "body"

    def __init__(self, config: Config, session: BrowserSession):
        super().__init__(config, session)
        self._ctx = None
        self._page = None
        self._profile_resets = 0

    @property
    def page(self):
        if self._page is None:
            self._ctx = self.session.context(self.name)
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            self._on_new_page(self._page)
        return self._page

    def _on_new_page(self, page) -> None:
        """Hook for subclasses (e.g. attach response listeners)."""

    def _reset_profile(self) -> None:
        self.close()
        profile = self.session.config.resolve(self.session.config.browser.profile_dir) / self.name
        shutil.rmtree(profile, ignore_errors=True)
        self._profile_resets += 1
        self.log.warning("challenge hit; retrying once with a fresh browser profile")

    def _goto(self, url: str, wait_selector: str | None = None, settle_ms: int = 0) -> str:
        wait_selector = wait_selector or self.wait_selectors
        for attempt in (1, 2):
            self.page.goto(url, wait_until="domcontentloaded")
            try:
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
            except SourceBlocked:
                if attempt == 1 and not self.session.headed and self._profile_resets == 0:
                    self._reset_profile()
                    continue
                raise
        raise AssertionError("unreachable")

    def _after_load(self) -> None:
        """Hook: dismiss modals etc."""

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
        self._ctx = self._page = None
