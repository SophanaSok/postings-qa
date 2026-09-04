"""Playwright session for the opt-in scrapers: persistent per-site contexts and challenge detection.

Only imported when a scraper is enabled. Playwright is an optional dependency (`uv sync --extra scrapers`).
There is deliberately no fingerprint spoofing here: the browser presents itself as what it is, and a bot
challenge stops the source (see BrowserSource).
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from postingsqa.config import Config
from postingsqa.errors import SourceBlocked  # noqa: F401  (re-exported for adapters)
from postingsqa.http import USER_AGENT, pause

if TYPE_CHECKING:
    from playwright.sync_api import APIRequestContext, BrowserContext, Page, Playwright

log = logging.getLogger(__name__)

INSTALL_HINT = "Playwright is not installed. Run `uv sync --extra scrapers` and `uv run playwright install chromium`."

CHALLENGE_PATTERNS = re.compile(
    r"just a moment|cf-chl|challenge-platform|verify you are human|additional verification required|"
    r"attention required!|access denied|request blocked|hcaptcha|/authwall|please enable cookies|"
    r"security verification|we've detected unusual",
    re.I,
)
CHALLENGE_TITLES = re.compile(r"just a moment|attention required|access denied|security check|authwall", re.I)


def is_challenge_html(html: str | None, title: str | None = None) -> bool:
    if title and CHALLENGE_TITLES.search(title):
        return True
    if not html:
        return False
    # only look at the top of the document: real job pages are long, challenge pages are short
    return bool(CHALLENGE_PATTERNS.search(html[:20000])) and len(html) < 200_000


def is_challenge(page: Page) -> bool:
    try:
        if "/authwall" in page.url or "/checkpoint/challenge" in page.url:
            return True
        return is_challenge_html(page.content(), page.title())
    except Exception:  # navigation in flight
        return False


class BrowserSession:
    """Owns one Playwright instance; hands out per-source persistent contexts and API request contexts."""

    def __init__(self, config: Config, headed: bool | None = None):
        self.config = config
        self.headed = config.browser.headed if headed is None else headed
        self._pw: Playwright | None = None
        self._contexts: list[BrowserContext] = []
        self._requests: list[APIRequestContext] = []

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "BrowserSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(INSTALL_HINT) from exc
        self._pw = sync_playwright().start()
        return self

    def __exit__(self, *exc) -> None:
        for ctx in self._contexts:
            try:
                ctx.close()
            except Exception:
                pass
        for req in self._requests:
            try:
                req.dispose()
            except Exception:
                pass
        if self._pw:
            self._pw.stop()

    @property
    def pw(self) -> Playwright:
        assert self._pw is not None, "use BrowserSession as a context manager"
        return self._pw

    # -- factories --------------------------------------------------------

    def context(self, source: str) -> BrowserContext:
        """A persistent Chromium context per source (cookies survive between runs, like a normal browser)."""
        profile = self.config.resolve(self.config.browser.profile_dir) / source
        profile.mkdir(parents=True, exist_ok=True)
        ctx = self.pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=not self.headed,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id=_local_timezone(),
        )
        ctx.set_default_timeout(self.config.browser.timeout_seconds * 1000)
        self._contexts.append(ctx)
        return ctx

    def api(self, base_url: str | None = None, extra_headers: dict[str, str] | None = None) -> APIRequestContext:
        """Browser-less HTTP client identified by the project's own User-Agent."""
        req = self.pw.request.new_context(
            base_url=base_url,
            user_agent=USER_AGENT,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9", **(extra_headers or {})},
            timeout=self.config.browser.timeout_seconds * 1000,
        )
        self._requests.append(req)
        return req

    # -- helpers ----------------------------------------------------------

    def delay(self, factor: float = 1.0) -> None:
        pause(self.config.browser.delay_seconds, factor)

    def ensure_not_blocked(self, page: Page, source: str, wait_seconds: float = 120) -> None:
        """Raise SourceBlocked on a challenge page; in headed mode, give the user time to solve it first."""
        if not is_challenge(page):
            return
        if not self.headed:
            raise SourceBlocked(f"{source}: bot challenge detected; this source is stopped for the run")
        log.warning("%s: challenge page shown — solve it in the browser window (waiting up to %ss)", source, int(wait_seconds))
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(2)
            if not is_challenge(page):
                log.info("%s: challenge cleared", source)
                return
        raise SourceBlocked(f"{source}: challenge not cleared within {int(wait_seconds)}s")

    def dump_debug(self, source: str, html: str, label: str = "page") -> Path:
        out = self.config.resolve("data/debug")
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{source}-{label}-{int(time.time())}.html"
        path.write_text(html)
        return path


def _local_timezone() -> str:
    try:
        return Path("/etc/localtime").resolve().as_posix().split("zoneinfo/")[-1]
    except Exception:
        return "America/New_York"
