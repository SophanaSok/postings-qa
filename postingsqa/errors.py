"""Exceptions shared by the source layer."""


class SourceBlocked(Exception):
    """A source cannot be read any further this run: bot challenge, auth wall, repeated rate limiting,
    or an API refusing our credentials. The adapter keeps what it already collected and stops."""
