"""Site adapter registry."""

from __future__ import annotations

from jobbot.sources.base import BaseSource

_REGISTRY: dict[str, type[BaseSource]] = {}


def register(cls: type[BaseSource]) -> type[BaseSource]:
    _REGISTRY[cls.name] = cls
    return cls


def get_source(name: str) -> type[BaseSource]:
    if not _REGISTRY:
        _load()
    return _REGISTRY[name]


def all_sources() -> dict[str, type[BaseSource]]:
    if not _REGISTRY:
        _load()
    return dict(_REGISTRY)


def _load() -> None:
    from jobbot.sources import linkedin  # noqa: F401

    try:
        from jobbot.sources import indeed, glassdoor  # noqa: F401
    except ImportError:
        pass
    for mod in (linkedin, indeed, glassdoor):
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, BaseSource) and obj is not BaseSource and getattr(obj, "name", "base") != "base":
                _REGISTRY[obj.name] = obj
