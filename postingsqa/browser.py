"""Playwright session management: persistent contexts, light hardening, challenge detection."""

from __future__ import annotations

import logging
import random
import re
import time
from pathlib import Path

from playwright.sync_api import APIRequestContext, BrowserContext, Page, Playwright, sync_playwright

from postingsqa.config import Config

log = logging.getLogger(__name__)


class SourceBlocked(Exception):
    """Raised when a site presents a bot challenge / auth wall that we cannot pass unattended."""


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
    """Owns one Playwright instance; hands out per-source persistent contexts and API contexts."""

    def __init__(self, config: Config, headed: bool | None = None):
        self.config = config
        self.headed = config.browser.headed if headed is None else headed
        self._pw: Playwright | None = None
        self._contexts: list[BrowserContext] = []
        self._requests: list[APIRequestContext] = []
        self._ua: str | None = None

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "BrowserSession":
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

    @property
    def user_agent(self) -> str:
        """A desktop Chrome UA matching the bundled Chromium major version (so UA and fingerprint agree)."""
        if self._ua is None:
            browser = self.pw.chromium.launch(headless=True)
            major = browser.version.split(".")[0]
            browser.close()
            self._ua = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        return self._ua

    # -- factories --------------------------------------------------------

    def context(self, source: str) -> BrowserContext:
        profile = self.config.resolve(self.config.browser.profile_dir) / source
        profile.mkdir(parents=True, exist_ok=True)
        ctx = self.pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=not self.headed,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=self.user_agent,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id=_local_timezone(),
            ignore_default_args=["--enable-automation"],
        )
        ctx.set_default_timeout(self.config.browser.timeout_seconds * 1000)
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        self._contexts.append(ctx)
        return ctx

    def api(self, base_url: str | None = None) -> APIRequestContext:
        """Browser-less HTTP client (used for LinkedIn's guest endpoints)."""
        req = self.pw.request.new_context(
            base_url=base_url,
            user_agent=self.user_agent,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
            timeout=self.config.browser.timeout_seconds * 1000,
        )
        self._requests.append(req)
        return req

    # -- helpers ----------------------------------------------------------

    def delay(self, factor: float = 1.0) -> None:
        lo, hi = self.config.browser.delay_seconds
        time.sleep(random.uniform(lo, hi) * factor)

    def ensure_not_blocked(self, page: Page, source: str, wait_seconds: float = 120) -> None:
        """Raise SourceBlocked on a challenge page; in headed mode, give the user time to solve it first."""
        if not is_challenge(page):
            return
        if not self.headed:
            raise SourceBlocked(f"{source}: bot challenge detected (re-run with --headed to solve it once)")
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
