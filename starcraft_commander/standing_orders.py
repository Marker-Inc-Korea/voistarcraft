"""In-game-loop standing orders: continuous SCV production and supply guard.

The original plan keeps fast micro in deterministic code and never calls the
LLM (or any interpreter) per game frame. This module turns the interpreter
constraints ``keep SCV production continuous`` and ``prevent supply block``
into real per-tick CODE policies: a :class:`StandingOrderController` is
registered once per user utterance (from the interpreted payload's
constraints) and then driven from ``BotAI.on_step`` by the live pipeline.
Today the narrator honestly discloses ``지속 생산 미지원`` for these
constraints; this controller is the runtime that makes the continuous
behavior real.

Design rules honored here:

* The caller throttles (``on_step`` invokes ``tick`` every N steps); ``tick``
  itself is cheap, bounded (at most one order per Command Center, at most one
  Supply Depot), and re-entrant.
* Bot objects are always duck-typed. Missing attributes, hostile properties,
  and absent methods degrade into an honest Korean ``skipped_reason`` —
  ``tick`` never raises because of a bot object.
* python-sc2 is only lazy-imported for ``UnitTypeId``/``Point2`` niceties and
  its absence silently falls back to plain strings/tuples, so this module
  imports with zero optional dependencies installed.
* The constraint strings are mirrored from ``toycraft_commander.interpreter``
  (``KEEP_WORKER_PRODUCTION_CONSTRAINT`` / ``PREVENT_SUPPLY_BLOCK_CONSTRAINT``)
  instead of imported: ``starcraft_commander`` keeps the interpreter as its
  single legitimate toycraft import (in ``live_pipeline``), and the test
  suite cross-checks the mirrored strings against the interpreter's actual
  emitted constraints so drift fails loudly.
"""

from __future__ import annotations

import inspect
import math
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable


STANDING_ORDER_KINDS: Final[tuple[str, ...]] = (
    "keep_worker_production",
    "prevent_supply_block",
)
"""Stable public standing-order kind names, in canonical tick order."""

KEEP_WORKER_PRODUCTION_CONSTRAINT_TEXT: Final[str] = "keep SCV production continuous"
"""Mirror of ``toycraft_commander.interpreter.KEEP_WORKER_PRODUCTION_CONSTRAINT``."""

PREVENT_SUPPLY_BLOCK_CONSTRAINT_TEXT: Final[str] = "prevent supply block"
"""Mirror of ``toycraft_commander.interpreter.PREVENT_SUPPLY_BLOCK_CONSTRAINT``."""

CONSTRAINT_TO_STANDING_ORDER: Final[dict[str, str]] = {
    KEEP_WORKER_PRODUCTION_CONSTRAINT_TEXT: "keep_worker_production",
    PREVENT_SUPPLY_BLOCK_CONSTRAINT_TEXT: "prevent_supply_block",
}
"""Exact interpreter constraint strings mapped to standing-order kinds."""

STANDING_ORDER_KOREAN_LABELS: Final[dict[str, str]] = {
    "keep_worker_production": "지속 SCV 생산",
    "prevent_supply_block": "보급 차단 방지",
}
"""Korean labels for narration and GUI status lines."""

SUPPLY_BLOCK_THRESHOLD: Final[int] = 3
"""``supply_left`` at or below this value triggers an emergency Supply Depot."""

SCV_UNIT_TYPE_NAME: Final[str] = "SCV"
"""python-sc2 ``UnitTypeId`` name trained by ``keep_worker_production``."""

SUPPLY_DEPOT_TYPE_NAME: Final[str] = "SUPPLYDEPOT"
"""python-sc2 ``UnitTypeId`` name built by ``prevent_supply_block``."""

COMMAND_CENTER_TYPE_NAME: Final[str] = "COMMANDCENTER"
"""Normalized structure type name accepted as an SCV producer."""

SUPPLY_DEPOT_START_LOCATION_OFFSET: Final[tuple[float, float]] = (4.0, 4.0)
"""Fallback depot placement offset from ``bot.start_location``.

Real python-sc2 ``bot.build`` searches for a valid placement around ``near``,
so a small constant offset away from the town hall footprint is enough; the
ramp top is preferred whenever ``bot.main_base_ramp`` exists.
"""

SKIPPED_NO_COMMAND_CENTER: Final[str] = "사령부 없음"
"""Skip reason: no ready idle Command Center can train an SCV."""

SKIPPED_UNAFFORDABLE: Final[str] = "자원 부족"
"""Skip reason: ``bot.can_afford`` refused the order explicitly."""

SKIPPED_SUPPLY_BLOCKED: Final[str] = "보급 부족"
"""Skip reason: no supply left to start SCV production."""

SKIPPED_SUPPLY_COMFORTABLE: Final[str] = "보급 여유 충분"
"""Skip reason: supply headroom is above the depot trigger threshold."""

SKIPPED_DEPOT_IN_PROGRESS: Final[str] = "보급고 건설 중"
"""Skip reason: a Supply Depot is already pending or under construction."""

SKIPPED_SUPPLY_UNREADABLE: Final[str] = "보급 상태 확인 불가"
"""Skip reason: ``bot.supply_left`` is missing or not a number."""

SKIPPED_NO_PLACEMENT_ANCHOR: Final[str] = "건설 위치 확인 불가"
"""Skip reason: neither ramp top nor start location yields a build anchor."""

SKIPPED_BUILD_UNAVAILABLE: Final[str] = "건설 명령 불가"
"""Skip reason: the bot exposes no callable ``build`` method."""

SKIPPED_TRAIN_UNAVAILABLE: Final[str] = "훈련 명령 불가"
"""Skip reason: no Command Center exposed a usable ``train`` method."""

SKIPPED_ORDER_REFUSED: Final[str] = "명령이 거부됨"
"""Skip reason: the bot explicitly refused the issued order."""

SKIPPED_BOT_ERROR_PREFIX: Final[str] = "봇 오류로 건너뜀"
"""Skip reason prefix when a hostile bot object raised during the tick."""

KOREAN_STATUS_PREFIX: Final[str] = "상비 명령"
"""Prefix of every :meth:`StandingOrderController.korean_status` line."""

KOREAN_STATUS_NONE: Final[str] = "상비 명령: 없음"
"""Status line shown while no standing order is active."""

_UNSET: Final[object] = object()
"""Internal sentinel distinguishing missing attributes from ``None`` values."""


@dataclass(frozen=True)
class StandingOrderTick:
    """Honest outcome of one standing-order pass over one bot tick.

    Exactly one of the two outcome channels is populated: either at least one
    action label in ``actions_issued`` (orders really went out) or a
    non-empty Korean ``skipped_reason`` (nothing was issued, and why). A tick
    that issued nothing without a reason — or claimed both — is rejected at
    construction so silence can never be narrated as success.
    """

    kind: str
    actions_issued: tuple[str, ...] = ()
    skipped_reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in STANDING_ORDER_KINDS:
            raise ValueError(
                f"StandingOrderTick kind must be one of {STANDING_ORDER_KINDS}, "
                f"got {self.kind!r}."
            )
        actions = tuple(self.actions_issued)
        if any(type(action) is not str or not action for action in actions):
            raise ValueError(
                "StandingOrderTick actions_issued must contain non-empty strings."
            )
        object.__setattr__(self, "actions_issued", actions)
        if type(self.skipped_reason) is not str:
            raise ValueError("StandingOrderTick skipped_reason must be a string.")
        if actions and self.skipped_reason:
            raise ValueError(
                "StandingOrderTick cannot both issue actions and carry a "
                "skipped_reason."
            )
        if not actions and not self.skipped_reason:
            raise ValueError(
                "StandingOrderTick that issued nothing must carry an honest "
                "skipped_reason."
            )

    @property
    def issued(self) -> bool:
        """Return whether this tick really issued at least one order."""

        return bool(self.actions_issued)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready tick outcome payload."""

        return {
            "kind": self.kind,
            "actions_issued": list(self.actions_issued),
            "skipped_reason": self.skipped_reason,
            "issued": self.issued,
        }


@runtime_checkable
class StandingOrderControllerInterface(Protocol):
    """Boundary used by the live pipeline and ``on_step`` integrations."""

    def register(self, kind: str) -> bool:
        """Activate one standing order; ``False`` when already active."""

    def cancel(self, kind: str) -> bool:
        """Deactivate one standing order; ``False`` when not active."""

    def active_kinds(self) -> tuple[str, ...]:
        """Return active standing-order kinds in canonical order."""

    def register_from_payload(self, payload: object) -> tuple[str, ...]:
        """Register standing orders found in a payload's constraints."""

    async def tick(self, bot: object) -> tuple["StandingOrderTick", ...]:
        """Run one cheap pass of every active standing order on the bot."""

    def korean_status(self) -> str:
        """Return the Korean standing-order status line for narration/GUI."""


class StandingOrderController:
    """Thread-safe registry and per-tick driver for standing orders.

    ``register``/``cancel``/``register_from_payload`` happen once per user
    utterance (interpreter side); ``tick`` happens inside the game loop
    (``BotAI.on_step``), throttled by the caller. The internal lock only
    guards the active-kind set — order issuance itself never holds the lock,
    so a slow bot cannot stall registration from another thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def register(self, kind: str) -> bool:
        """Activate ``kind``; return ``False`` when it was already active."""

        _validate_kind(kind)
        with self._lock:
            if kind in self._active:
                return False
            self._active.add(kind)
            return True

    def cancel(self, kind: str) -> bool:
        """Deactivate ``kind``; return ``False`` when it was not active."""

        _validate_kind(kind)
        with self._lock:
            if kind not in self._active:
                return False
            self._active.discard(kind)
            return True

    def active_kinds(self) -> tuple[str, ...]:
        """Return the active standing-order kinds in canonical order."""

        with self._lock:
            active = frozenset(self._active)
        return tuple(kind for kind in STANDING_ORDER_KINDS if kind in active)

    def register_from_payload(self, payload: object) -> tuple[str, ...]:
        """Scan ``payload.constraints`` and register matching standing orders.

        Constraints are read duck-typed from a mapping key or attribute and
        matched exactly against :data:`CONSTRAINT_TO_STANDING_ORDER`. Only
        *newly* registered kinds are returned, in canonical order, so callers
        can narrate genuine activations and stay silent about re-requests.
        """

        newly_registered: set[str] = set()
        for constraint in _payload_constraints(payload):
            kind = CONSTRAINT_TO_STANDING_ORDER.get(constraint)
            if kind is not None and self.register(kind):
                newly_registered.add(kind)
        return tuple(
            kind for kind in STANDING_ORDER_KINDS if kind in newly_registered
        )

    async def tick(self, bot: object) -> tuple[StandingOrderTick, ...]:
        """Run one bounded pass of every active standing order against the bot.

        The caller throttles (call this every N ``on_step`` iterations); each
        pass issues at most one train order per Command Center and at most
        one Supply Depot. Hostile bot objects (missing attributes, raising
        properties) degrade into honest ``skipped_reason`` ticks — this
        method never raises because of the bot.
        """

        ticks: list[StandingOrderTick] = []
        for kind in self.active_kinds():
            try:
                if kind == "keep_worker_production":
                    ticks.append(await _tick_keep_worker_production(bot))
                else:
                    ticks.append(await _tick_prevent_supply_block(bot))
            except Exception as exc:  # noqa: BLE001 - hostile bot containment
                ticks.append(
                    StandingOrderTick(
                        kind=kind,
                        skipped_reason=(
                            f"{SKIPPED_BOT_ERROR_PREFIX}: {type(exc).__name__}"
                        ),
                    )
                )
        return tuple(ticks)

    def korean_status(self) -> str:
        """Return the Korean status line, e.g. ``상비 명령: 지속 SCV 생산 활성``."""

        active = self.active_kinds()
        if not active:
            return KOREAN_STATUS_NONE
        parts = ", ".join(
            f"{STANDING_ORDER_KOREAN_LABELS[kind]} 활성" for kind in active
        )
        return f"{KOREAN_STATUS_PREFIX}: {parts}"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready controller status payload."""

        return {
            "active_kinds": list(self.active_kinds()),
            "korean_status": self.korean_status(),
        }


def _validate_kind(kind: str) -> None:
    """Reject unknown standing-order kinds loudly (programmer error)."""

    if kind not in STANDING_ORDER_KINDS:
        raise ValueError(
            f"unknown standing order kind: {kind!r}. "
            f"Supported kinds: {', '.join(STANDING_ORDER_KINDS)}."
        )


def _payload_constraints(payload: object) -> tuple[str, ...]:
    """Read a payload's constraint strings duck-typed; hostile inputs yield ()."""

    if isinstance(payload, Mapping):
        raw = payload.get("constraints", ())
    else:
        try:
            raw = getattr(payload, "constraints", ())
        except Exception:  # noqa: BLE001 - hostile payload containment
            return ()
    if raw is None or isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        return ()
    try:
        return tuple(item for item in raw if type(item) is str)
    except Exception:  # noqa: BLE001 - hostile iterable containment
        return ()


async def _tick_keep_worker_production(bot: object) -> StandingOrderTick:
    """Train at most one SCV per ready idle Command Center this tick."""

    kind = "keep_worker_production"
    command_centers = _ready_idle_command_centers(bot)
    if not command_centers:
        return StandingOrderTick(kind=kind, skipped_reason=SKIPPED_NO_COMMAND_CENTER)
    supply_left = _read_supply_left(bot)
    if supply_left is not None and supply_left <= 0:
        return StandingOrderTick(kind=kind, skipped_reason=SKIPPED_SUPPLY_BLOCKED)
    type_id = _resolve_unit_type(bot, SCV_UNIT_TYPE_NAME)
    budget = len(command_centers)
    if supply_left is not None:
        budget = min(budget, supply_left)
    issued: list[str] = []
    skipped_reason = SKIPPED_TRAIN_UNAVAILABLE
    for command_center in command_centers:
        if len(issued) >= budget:
            break
        if not await _is_affordable(bot, type_id):
            skipped_reason = SKIPPED_UNAFFORDABLE
            break
        train = getattr(command_center, "train", None)
        if not callable(train):
            continue
        if await _issue_unit_order(bot, train, type_id):
            issued.append(_order_label("train_scv", command_center))
        else:
            skipped_reason = SKIPPED_ORDER_REFUSED
    if issued:
        return StandingOrderTick(kind=kind, actions_issued=tuple(issued))
    return StandingOrderTick(kind=kind, skipped_reason=skipped_reason)


async def _tick_prevent_supply_block(bot: object) -> StandingOrderTick:
    """Start exactly one emergency Supply Depot when supply runs out."""

    kind = "prevent_supply_block"
    supply_left = _read_supply_left(bot)
    if supply_left is None:
        return StandingOrderTick(kind=kind, skipped_reason=SKIPPED_SUPPLY_UNREADABLE)
    if supply_left > SUPPLY_BLOCK_THRESHOLD:
        return StandingOrderTick(
            kind=kind, skipped_reason=SKIPPED_SUPPLY_COMFORTABLE
        )
    type_id = _resolve_unit_type(bot, SUPPLY_DEPOT_TYPE_NAME)
    if await _depot_in_progress(bot, type_id):
        return StandingOrderTick(kind=kind, skipped_reason=SKIPPED_DEPOT_IN_PROGRESS)
    if not await _is_affordable(bot, type_id):
        return StandingOrderTick(kind=kind, skipped_reason=SKIPPED_UNAFFORDABLE)
    anchor = _depot_placement_anchor(bot)
    if anchor is None:
        return StandingOrderTick(
            kind=kind, skipped_reason=SKIPPED_NO_PLACEMENT_ANCHOR
        )
    build = getattr(bot, "build", None)
    if not callable(build):
        return StandingOrderTick(kind=kind, skipped_reason=SKIPPED_BUILD_UNAVAILABLE)
    if await _call_bot_operation(build, type_id, near=anchor):
        return StandingOrderTick(kind=kind, actions_issued=("build_supplydepot",))
    return StandingOrderTick(kind=kind, skipped_reason=SKIPPED_ORDER_REFUSED)


def _ready_idle_command_centers(bot: object) -> list[object]:
    """Collect ready idle Command Centers from ``bot.structures`` duck-typed."""

    group = getattr(bot, "structures", None)
    if group is None:
        return []
    ready_attr = getattr(group, "ready", None)
    idle_attr = getattr(ready_attr, "idle", None) if ready_attr is not None else None
    if idle_attr is not None:
        candidates = _materialize(idle_attr)
    else:
        candidates = [
            entry
            for entry in _materialize(group)
            if _truthy_flag(entry, "is_ready", default=True)
            and _truthy_flag(entry, "is_idle", default=True)
        ]
    return [
        entry
        for entry in candidates
        if _entity_type_name(entry) == COMMAND_CENTER_TYPE_NAME
    ]


async def _depot_in_progress(bot: object, type_id: object) -> bool:
    """Detect a pending or in-construction Supply Depot duck-typed."""

    already_pending = getattr(bot, "already_pending", None)
    if callable(already_pending):
        pending = already_pending(type_id)
        if inspect.isawaitable(pending):
            pending = await pending
        if _is_real_number(pending) and float(pending) > 0:
            return True
    for structure in _materialize(getattr(bot, "structures", None)):
        if _entity_type_name(structure) != SUPPLY_DEPOT_TYPE_NAME:
            continue
        if not _truthy_flag(structure, "is_ready", default=True):
            return True
    return False


def _depot_placement_anchor(bot: object) -> object | None:
    """Pick the depot build anchor: ramp top, else offset start location.

    The ramp-top and start-location objects are passed through untouched so a
    real python-sc2 ``Point2`` stays a ``Point2``. The start-location
    fallback offsets by :data:`SUPPLY_DEPOT_START_LOCATION_OFFSET`, built as
    a ``Point2`` when python-sc2 is installed and a plain ``(x, y)`` tuple
    otherwise (offline fakes just record it).
    """

    ramp = getattr(bot, "main_base_ramp", None)
    top_center = getattr(ramp, "top_center", None) if ramp is not None else None
    if _is_point_like(top_center):
        return top_center
    start = getattr(bot, "start_location", None)
    if not _is_point_like(start):
        return None
    offset_x, offset_y = SUPPLY_DEPOT_START_LOCATION_OFFSET
    x = float(start.x) + offset_x
    y = float(start.y) + offset_y
    try:
        from sc2.position import Point2
    except ImportError:
        return (x, y)
    return Point2((x, y))


def _resolve_unit_type(bot: object, type_name: str) -> object:
    """Resolve a UnitTypeId name without ever raising.

    Resolution order: a duck-typed ``bot.unit_type_id_resolver`` callable,
    then the real python-sc2 ``UnitTypeId`` enum (lazy import), then the
    plain name string itself. Standing orders run inside the game loop, so a
    resolution gap degrades to the string instead of raising.
    """

    resolver = getattr(bot, "unit_type_id_resolver", None)
    if callable(resolver):
        try:
            return resolver(type_name)
        except Exception:  # noqa: BLE001 - hostile resolver containment
            return type_name
    try:
        from sc2.ids.unit_typeid import UnitTypeId
    except ImportError:
        return type_name
    try:
        return UnitTypeId[type_name]
    except KeyError:
        return type_name


async def _is_affordable(bot: object, type_id: object) -> bool:
    """Check ``bot.can_afford`` when present; refuse only an explicit ``no``."""

    can_afford = getattr(bot, "can_afford", None)
    if not callable(can_afford):
        return True
    result = can_afford(type_id)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return True
    return bool(result)


async def _issue_unit_order(
    bot: object,
    order_method: Callable[..., object],
    *args: object,
) -> bool:
    """Issue one unit order, routing through ``bot.do`` when present."""

    command = order_method(*args)
    if inspect.isawaitable(command):
        command = await command
    if command is None:
        return True
    if not command:
        return False
    do = getattr(bot, "do", None)
    if callable(do):
        outcome = do(command)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return outcome is None or bool(outcome)
    return True


async def _call_bot_operation(
    operation: Callable[..., object],
    *args: object,
    **kwargs: object,
) -> bool:
    """Call one bot-level operation, treating ``None``/truthy results as done."""

    result = operation(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result is None or bool(result)


def _order_label(prefix: str, entity: object) -> str:
    """Build a stable per-order audit label, tagged when the unit has a tag."""

    tag = getattr(entity, "tag", None)
    if isinstance(tag, int) and not isinstance(tag, bool):
        return f"{prefix}@{tag}"
    return prefix


def _read_supply_left(bot: object) -> int | None:
    """Read ``bot.supply_left`` as an int, or ``None`` when unreadable."""

    value = getattr(bot, "supply_left", None)
    if not _is_real_number(value):
        return None
    return int(value)


def _materialize(value: object) -> list[object]:
    """Materialize a Units-like iterable defensively (never strings)."""

    if value is None or isinstance(value, (str, bytes)):
        return []
    if not isinstance(value, Iterable):
        return []
    return list(value)


def _entity_type_name(entity: object) -> str | None:
    """Read the normalized type name from ``.name`` or ``.type_id.name``."""

    name = _normalized_name(getattr(entity, "name", None))
    if name is not None:
        return name
    type_id = getattr(entity, "type_id", None)
    if type_id is None:
        return None
    return _normalized_name(getattr(type_id, "name", None))


def _normalized_name(value: object) -> str | None:
    """Uppercase a name, dropping whitespace and underscores, for matching."""

    if type(value) is not str:
        return None
    normalized = "".join(value.split()).replace("_", "").upper()
    return normalized or None


def _truthy_flag(entity: object, attribute: str, *, default: bool) -> bool:
    """Read a boolean-ish unit flag, defaulting when the attribute is absent."""

    value = getattr(entity, attribute, _UNSET)
    if value is _UNSET:
        return default
    return bool(value)


def _is_real_number(value: object) -> bool:
    """Return whether a value is a finite real number (bool excluded)."""

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_point_like(candidate: object) -> bool:
    """Return whether an object carries numeric ``x``/``y`` coordinates."""

    if candidate is None:
        return False
    return _is_real_number(getattr(candidate, "x", None)) and _is_real_number(
        getattr(candidate, "y", None)
    )
