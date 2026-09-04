"""Source adapter registry.

Every module in this package that defines a ``BaseSource`` subclass with a unique ``name`` is a source; drop
a file in, and it is discovered. Enabled/disabled defaults live in ``postingsqa.config.SOURCE_DEFAULTS``
(a test keeps the two in sync).
"""

from __future__ import annotations

import importlib
import pkgutil

from postingsqa.sources.base import BaseSource

# Display order: API sources first, then the opt-in scrapers. Unknown names sort last alphabetically.
PREFERRED_ORDER = ("remotive", "greenhouse", "lever", "usajobs", "adzuna", "linkedin", "indeed", "glassdoor")

_REGISTRY: dict[str, type[BaseSource]] = {}
_IMPORT_ERRORS: dict[str, str] = {}


def _load() -> None:
    import postingsqa.sources as pkg

    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        try:
            mod = importlib.import_module(f"postingsqa.sources.{info.name}")
        except ImportError as exc:  # optional dependency missing
            _IMPORT_ERRORS[info.name] = str(exc)
            continue
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, BaseSource) and getattr(obj, "name", "base") != "base" and obj.__module__ == mod.__name__:
                _REGISTRY[obj.name] = obj


def _order(name: str) -> tuple[int, str]:
    return (PREFERRED_ORDER.index(name) if name in PREFERRED_ORDER else len(PREFERRED_ORDER), name)


def all_sources() -> dict[str, type[BaseSource]]:
    if not _REGISTRY:
        _load()
    return {name: _REGISTRY[name] for name in sorted(_REGISTRY, key=_order)}


def get_source(name: str) -> type[BaseSource]:
    sources = all_sources()
    if name not in sources:
        hint = f" ({_IMPORT_ERRORS[name]})" if name in _IMPORT_ERRORS else ""
        raise KeyError(f"unknown source {name!r}{hint}; available: {', '.join(sources)}")
    return sources[name]


def attributions(names) -> list[tuple[str, str]]:
    """(label, url) credits for the sources present in a result set, in display order."""
    out = []
    for name in sorted(set(names), key=_order):
        cls = all_sources().get(name)
        if cls and cls.attribution:
            out.append(cls.attribution)
    return out


def attribution_text(names) -> str:
    """'Listings via Remotive (https://remotive.com), ...' for the sources present in a result set."""
    parts = [f"{label} ({url})" for label, url in attributions(names)]
    return "Listings via " + ", ".join(parts) if parts else ""
