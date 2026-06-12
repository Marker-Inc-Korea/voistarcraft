"""Compatibility helpers for supported Python runtimes."""

from __future__ import annotations

from enum import Enum

try:
    from enum import StrEnum as StrEnum
except ImportError:  # pragma: no cover - exercised on Python < 3.11

    class StrEnum(str, Enum):
        """Small Python 3.10-compatible subset of enum.StrEnum.

        Matches the stdlib 3.11+ contract: ``str(member)`` and formatted
        output return the member value, and ``auto()`` produces the
        lower-cased member name.
        """

        def _generate_next_value_(name, start, count, last_values):  # noqa: N805
            """Return the lower-cased member name, matching enum.StrEnum."""

            return name.lower()

        __str__ = str.__str__
        __format__ = str.__format__

