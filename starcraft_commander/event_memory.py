"""Thread-safe command event memory for the StarCraft II commander.

This is the original flow's final stage ("narrator / event memory"): every
:class:`~starcraft_commander.live_pipeline.SC2CommandOutcome` that the live
pipeline produces is recorded here as an immutable :class:`CommanderEvent`
with a monotonically increasing sequence number and the in-game time at which
it happened. The memory is a bounded ring buffer so a long session never
grows without limit, and it is guarded by one :class:`threading.Lock` so the
game loop, the voice thread, and a web GUI poller can all touch it safely.

The module is stdlib-only and intentionally importable without StarCraft II,
python-sc2, anthropic, faster-whisper, or sounddevice installed. Recorded
outcomes are always duck-typed: anything carrying ``command_text``,
``status``, and ``narration`` attributes (or a plain mapping with the same
keys) can be recorded, so the memory never imports the live pipeline.

``korean_summary`` renders the most recent events as commander-facing Korean
lines for future SUMMARIZE_STATE enrichment and the GUI history panel. The
memory only ever stores what actually happened; it never rewrites a blocked
or clarification outcome into anything else.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable


DEFAULT_MAX_EVENTS: Final[int] = 200
"""Default ring-buffer capacity of one :class:`CommanderEventMemory`."""

KOREAN_EMPTY_MEMORY_SUMMARY: Final[str] = "최근 명령 0건: 기록된 명령이 없습니다."
"""Commander-facing Korean summary line used when no events were recorded."""

_NARRATION_HEAD_MAX_CHARS: Final[int] = 60
"""Maximum narration characters quoted per ``korean_summary`` line."""

_EVENT_SOURCE_FIELD_NAMES: Final[tuple[str, ...]] = (
    "command_text",
    "status",
    "narration",
)
"""Outcome attributes (or mapping keys) every recordable outcome must carry.

These mirror the required ``SC2CommandOutcome`` fields in
``starcraft_commander/live_pipeline.py``; the optional ``intent_dsl`` mapping
(and its ``"intent"`` key) plus ``plan.intent_name`` are read opportunistically
to fill :attr:`CommanderEvent.intent_name`.
"""


def _validate_positive_int(name: str, value: object) -> int:
    """Return ``value`` when it is a positive non-bool int, else raise."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}.")
    return value


def _normalize_game_time(value: object) -> float | None:
    """Coerce one game-time value to a finite non-negative float or None."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            "game_time_seconds must be a number or None, got "
            f"{type(value).__name__}."
        )
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0.0:
        raise ValueError(
            f"game_time_seconds must be finite and >= 0, got {seconds!r}."
        )
    return seconds


def _narration_head(narration: str) -> str:
    """Return the first narration line, truncated for one-line summaries."""

    head = narration.strip().splitlines()[0].strip()
    if len(head) > _NARRATION_HEAD_MAX_CHARS:
        return head[: _NARRATION_HEAD_MAX_CHARS - 1] + "…"
    return head


@dataclass(frozen=True)
class CommanderEvent:
    """One immutable recorded command outcome with its memory sequence number.

    ``seq`` starts at 1 and increases monotonically per
    :class:`CommanderEventMemory`; it survives ring-buffer trimming, so GUI
    pollers can use ``since(seq)`` cursors safely. ``detail`` carries
    JSON-ready structured context (for example the resolved Intent DSL
    document) and is defensively copied.
    """

    seq: int
    command_text: str
    status: str
    narration: str
    intent_name: str = ""
    game_time_seconds: float | None = None
    detail: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_positive_int("seq", self.seq)
        object.__setattr__(self, "command_text", str(self.command_text))
        if not str(self.status).strip():
            raise ValueError("commander event status must be non-empty.")
        object.__setattr__(self, "status", str(self.status))
        if not str(self.narration).strip():
            raise ValueError("commander event narration must be non-empty.")
        object.__setattr__(self, "narration", str(self.narration))
        object.__setattr__(self, "intent_name", str(self.intent_name))
        object.__setattr__(
            self,
            "game_time_seconds",
            _normalize_game_time(self.game_time_seconds),
        )
        if not isinstance(self.detail, Mapping):
            raise TypeError(
                "commander event detail must be a mapping, got "
                f"{type(self.detail).__name__}."
            )
        object.__setattr__(self, "detail", dict(self.detail))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready event payload for GUI history endpoints."""

        return {
            "seq": self.seq,
            "command_text": self.command_text,
            "status": self.status,
            "narration": self.narration,
            "intent_name": self.intent_name,
            "game_time_seconds": self.game_time_seconds,
            "detail": dict(self.detail),
        }


@runtime_checkable
class CommanderEventRecorderInterface(Protocol):
    """Structural seam for anything that records command outcomes."""

    def record(
        self,
        outcome: object,
        game_time_seconds: float | None = None,
    ) -> CommanderEvent:
        """Record one command outcome and return the stored event."""
        ...


class CommanderEventMemory:
    """Bounded, thread-safe ring buffer of recorded commander events.

    ``record`` accepts either an ``SC2CommandOutcome``-shaped duck-typed
    object (``.command_text`` / ``.status`` / ``.narration``, optional
    ``.intent_dsl`` mapping and ``.plan.intent_name``) or a plain mapping with
    the same keys, so callers never need to import the live pipeline. All
    reads and writes share one :class:`threading.Lock`; sequence numbers are
    assigned under that lock, start at 1, and never repeat — not even after
    :meth:`clear` — so ``since(seq)`` cursors held by GUI pollers stay valid
    for the lifetime of the memory.
    """

    def __init__(self, max_events: int = DEFAULT_MAX_EVENTS) -> None:
        self.max_events = _validate_positive_int("max_events", max_events)
        self._lock = threading.Lock()
        self._events: deque[CommanderEvent] = deque(maxlen=self.max_events)
        self._last_seq = 0

    def record(
        self,
        outcome: object,
        game_time_seconds: float | None = None,
    ) -> CommanderEvent:
        """Record one outcome (duck-typed object or mapping) as an event.

        An explicit ``game_time_seconds`` argument wins over any
        ``"game_time_seconds"`` carried inside a mapping outcome. The oldest
        events are trimmed once ``max_events`` is exceeded.
        """

        command_text, status, narration, intent_name, detail, mapped_time = (
            _extract_event_source(outcome)
        )
        seconds = _normalize_game_time(
            game_time_seconds if game_time_seconds is not None else mapped_time
        )
        with self._lock:
            self._last_seq += 1
            event = CommanderEvent(
                seq=self._last_seq,
                command_text=command_text,
                status=status,
                narration=narration,
                intent_name=intent_name,
                game_time_seconds=seconds,
                detail=detail,
            )
            self._events.append(event)
        return event

    def recent(self, n: int) -> tuple[CommanderEvent, ...]:
        """Return up to the last ``n`` events, oldest first."""

        if isinstance(n, bool) or not isinstance(n, int):
            raise TypeError(f"n must be an int, got {type(n).__name__}.")
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n}.")
        if n == 0:
            return ()
        with self._lock:
            events = tuple(self._events)
        return events[-n:]

    def since(self, seq: int) -> tuple[CommanderEvent, ...]:
        """Return every stored event whose ``seq`` is strictly greater.

        ``since(latest_seq())`` is always empty; ``since(0)`` returns every
        event still inside the ring buffer (trimmed events are gone for
        good).
        """

        if isinstance(seq, bool) or not isinstance(seq, int):
            raise TypeError(f"seq must be an int, got {type(seq).__name__}.")
        with self._lock:
            return tuple(event for event in self._events if event.seq > seq)

    def latest_seq(self) -> int:
        """Return the highest sequence number ever assigned (0 if none)."""

        with self._lock:
            return self._last_seq

    def clear(self) -> None:
        """Drop all stored events; the sequence counter keeps increasing."""

        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        """Return how many events are currently stored."""

        with self._lock:
            return len(self._events)

    def to_dicts(self) -> tuple[dict[str, object], ...]:
        """Return every stored event as a JSON-ready dict, oldest first."""

        with self._lock:
            events = tuple(self._events)
        return tuple(event.to_dict() for event in events)

    def korean_summary(self, n: int = 5) -> str:
        """Render the most recent ``n`` events as Korean commander lines.

        Format: a ``최근 명령 N건:`` header followed by one line per event
        carrying its sequence number, status, and the narration head. Used
        for future SUMMARIZE_STATE enrichment and the GUI history panel.
        """

        _validate_positive_int("n", n)
        events = self.recent(n)
        if not events:
            return KOREAN_EMPTY_MEMORY_SUMMARY
        lines = [f"최근 명령 {len(events)}건:"]
        for event in events:
            lines.append(
                f"- #{event.seq} [{event.status}] {_narration_head(event.narration)}"
            )
        return "\n".join(lines)


def _extract_event_source(
    outcome: object,
) -> tuple[str, str, str, str, dict[str, object], object]:
    """Pull event fields out of one mapping or duck-typed outcome object.

    Returns ``(command_text, status, narration, intent_name, detail,
    game_time_seconds)``; the trailing game time is only ever non-None for
    mapping outcomes carrying a ``"game_time_seconds"`` key.
    """

    if isinstance(outcome, Mapping):
        command_text = str(outcome.get("command_text", ""))
        status = outcome.get("status")
        narration = outcome.get("narration")
        intent_name = str(outcome.get("intent_name", "") or "")
        intent_dsl = outcome.get("intent_dsl")
        raw_detail = outcome.get("detail")
        mapped_time = outcome.get("game_time_seconds")
    else:
        command_text = str(getattr(outcome, "command_text", ""))
        status = getattr(outcome, "status", None)
        narration = getattr(outcome, "narration", None)
        intent_name = ""
        plan = getattr(outcome, "plan", None)
        if plan is not None:
            intent_name = str(getattr(plan, "intent_name", "") or "")
        intent_dsl = getattr(outcome, "intent_dsl", None)
        raw_detail = None
        mapped_time = None
    if status is None or not str(status).strip():
        raise ValueError(
            "recordable outcomes must carry a non-empty 'status' "
            f"(expected fields: {', '.join(_EVENT_SOURCE_FIELD_NAMES)})."
        )
    if narration is None or not str(narration).strip():
        raise ValueError(
            "recordable outcomes must carry a non-empty 'narration' "
            f"(expected fields: {', '.join(_EVENT_SOURCE_FIELD_NAMES)})."
        )
    if not intent_name and isinstance(intent_dsl, Mapping):
        intent_name = str(intent_dsl.get("intent", "") or "")
    detail: dict[str, object]
    if raw_detail is not None:
        if not isinstance(raw_detail, Mapping):
            raise TypeError(
                "outcome 'detail' must be a mapping, got "
                f"{type(raw_detail).__name__}."
            )
        detail = dict(raw_detail)
    elif isinstance(intent_dsl, Mapping):
        detail = {"intent_dsl": dict(intent_dsl)}
    else:
        detail = {}
    return command_text, str(status), str(narration), intent_name, detail, mapped_time
