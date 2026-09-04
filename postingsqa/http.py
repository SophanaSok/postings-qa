"""Minimal JSON HTTP client for the API-based sources (stdlib only; no browser involved).

Identifies itself honestly, retries transient failures with backoff, and turns hard failures into
SourceBlocked so BaseSource.run() can stop the source and keep whatever was already collected.
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from postingsqa import __version__
from postingsqa.errors import SourceBlocked

log = logging.getLogger(__name__)

REPO_URL = "https://github.com/SophanaSok/postings-qa"
USER_AGENT = f"postings-qa/{__version__} (+{REPO_URL})"


def get_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, *,
             source: str = "http", timeout: float = 30, retries: int = 3) -> Any:
    """GET `url` and decode JSON. Returns None on 404. Raises SourceBlocked on 401/403, other 4xx,
    non-JSON bodies, or when retries on 429/5xx/network errors are exhausted."""
    full = url + ("?" + urlencode({k: v for k, v in params.items() if v is not None}, doseq=True) if params else "")
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    last = "no attempts"
    for attempt in range(retries):
        try:
            with urlopen(Request(full, headers=hdrs), timeout=timeout) as resp:
                body = resp.read()
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise SourceBlocked(f"{source}: non-JSON response from {url} (wrong endpoint or credentials?)")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (401, 403):
                raise SourceBlocked(f"{source}: HTTP {exc.code} from {url} (check API credentials)") from None
            if exc.code == 429 or exc.code >= 500:
                last = f"HTTP {exc.code}"
            else:
                raise SourceBlocked(f"{source}: HTTP {exc.code} from {url}") from None
        except (URLError, TimeoutError, OSError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        wait = 2 * (2 ** attempt) + random.uniform(0, 1)
        log.warning("%s: %s, retrying in %.0fs", source, last, wait)
        time.sleep(wait)
    raise SourceBlocked(f"{source}: gave up after {retries} attempts ({last})")


def pause(delay_range: tuple[float, float], factor: float = 1.0) -> None:
    """Sleep a random interval from `delay_range` scaled by `factor` (shared pacing for all sources)."""
    lo, hi = delay_range
    time.sleep(max(0.0, random.uniform(lo, hi) * factor))
