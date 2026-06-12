"""Shared alias normalization for voice- and STT-friendly name lookups."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TypeVar


CanonicalNameT = TypeVar("CanonicalNameT", bound=str)


def normalize_alias_key(text: str) -> str:
    """Return casefolded text with every whitespace run removed.

    Spoken Korean and English commands arrive from speech-to-text with
    unstable casing and spacing, so every entity-name alias lookup must
    compare raw input on one stable normalized key.
    """

    return "".join(text.casefold().split())


def resolve_aliased_name(
    value: object,
    canonical_names: Collection[str],
    alias_map: Mapping[str, CanonicalNameT],
) -> CanonicalNameT | None:
    """Return a canonical name for raw input via exact or alias lookup.

    Resolution order is strictly additive over the historical per-module
    lookups: a stripped exact canonical match wins first, then a
    casefolded whitespace-free alias lookup. Alias keys that themselves
    contain casing or whitespace are normalized before comparison, so
    every previously resolvable spelling keeps its canonical result.
    """

    if type(value) is not str:
        return None
    candidate = value.strip()
    if candidate in canonical_names:
        return candidate
    normalized_candidate = normalize_alias_key(candidate)
    resolved_name = alias_map.get(normalized_candidate)
    if resolved_name is not None:
        return resolved_name
    for alias, canonical_name in alias_map.items():
        if normalize_alias_key(alias) == normalized_candidate:
            return canonical_name
    return None
