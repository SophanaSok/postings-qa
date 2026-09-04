"""Shared text parsers: salary strings, relative dates, whitespace cleanup."""

from __future__ import annotations

import re
from datetime import date, timedelta

_WS = re.compile(r"\s+")


def clean(text: str | None) -> str | None:
    if text is None:
        return None
    t = _WS.sub(" ", text).strip()
    return t or None


_CURRENCY = {"$": "USD", "US$": "USD", "USD": "USD", "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP", "CA$": "CAD", "C$": "CAD", "A$": "AUD"}
_NUM = r"(?:US|CA|C|A)?\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kK])?"
_RANGE_RE = re.compile(rf"(?P<cur>US\$|CA\$|C\$|A\$|\$|€|£|USD|EUR|GBP)?\s*{_NUM}(?:\s*(?:-|–|—|to)\s*(?:US\$|CA\$|C\$|A\$|\$|€|£)?\s*{_NUM})?", re.I)
_PERIOD_RE = re.compile(r"\b(?:per|an?|/)\s*(hour|hr|year|yr|annum|month|mo|week|wk|day)\b|\b(hourly|yearly|annually|monthly|weekly|daily)\b", re.I)
_PERIOD_MAP = {
    "hour": "hour", "hr": "hour", "hourly": "hour",
    "year": "year", "yr": "year", "annum": "year", "yearly": "year", "annually": "year",
    "month": "month", "mo": "month", "monthly": "month",
    "week": "week", "wk": "week", "weekly": "week",
    "day": "day", "daily": "day",
}


def _to_number(num: str, k: str | None) -> float:
    val = float(num.replace(",", ""))
    return val * 1000 if k else val


def parse_salary(text: str | None) -> dict:
    """Parse strings like '$94,000.00/yr - $140,000.00/yr', '$40 - $60 an hour', 'Estimated: $85K - $110K a year'.

    Returns dict with salary_min, salary_max, salary_currency, salary_period, salary_is_estimate, salary_raw.
    Values are None when nothing parseable is found.
    """
    out = {"salary_min": None, "salary_max": None, "salary_currency": None, "salary_period": None, "salary_is_estimate": False, "salary_raw": clean(text)}
    if not text:
        return out
    t = text.replace(" ", " ")
    out["salary_is_estimate"] = bool(re.search(r"estimat|glassdoor est|indeed est", t, re.I))
    pm = _PERIOD_RE.search(t)
    m = _RANGE_RE.search(_PERIOD_RE.sub(" ", t))  # strip '/yr', 'an hour' so ranges join cleanly
    if not m:
        return out
    cur, n1, k1, n2, k2 = m.groups()
    lo = _to_number(n1, k1)
    hi = _to_number(n2, k2) if n2 else None
    if lo < 5 and hi is None:  # a lone tiny number is not a salary
        return out
    out["salary_min"] = lo
    out["salary_max"] = hi if hi is not None else lo
    if hi is not None and lo > hi:
        out["salary_min"], out["salary_max"] = hi, lo
    out["salary_currency"] = _CURRENCY.get((cur or "").upper() if cur and cur.isalpha() else (cur or ""), "USD" if ("$" in t or not cur) else None)
    if pm:
        out["salary_period"] = _PERIOD_MAP[(pm.group(1) or pm.group(2)).lower()]
    else:
        out["salary_period"] = "hour" if out["salary_max"] < 500 else "year"
    return out


_REL_RE = re.compile(r"(\d+)\+?\s*(minute|min|hour|hr|day|week|month|year)s?\s*ago", re.I)
_COMPACT_RE = re.compile(r"^\s*(\d+)\s*([hdwmy])\+?\s*$", re.I)  # Glassdoor style: 24h, 3d, 30d+


def parse_relative_date(text: str | None, today: date | None = None) -> date | None:
    """'3 days ago', '2 weeks ago', 'Just now', 'Today', 'Posted 30+ days ago', 'Active 1 day ago' -> date."""
    if not text:
        return None
    today = today or date.today()  # local date, consistent with qa.checks.posting_age
    t = text.lower()
    if any(w in t for w in ("just now", "today", "just posted", "moments ago")):
        return today
    if "yesterday" in t:
        return today - timedelta(days=1)
    m = _REL_RE.search(t)
    if not m:
        cm = _COMPACT_RE.match(t)
        if not cm:
            return None
        n = int(cm.group(1))
        unit = {"h": "hour", "d": "day", "w": "week", "m": "month", "y": "year"}[cm.group(2).lower()]
    else:
        n, unit = int(m.group(1)), m.group(2)
    if unit.startswith(("minute", "min", "hour", "hr")):
        return today
    days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
    return today - timedelta(days=n * days)


def parse_iso_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None
