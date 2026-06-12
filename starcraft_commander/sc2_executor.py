"""StarCraft II execution adapter for commander Intent DSL payloads.

This module is intentionally importable without StarCraft II or python-sc2
installed. Unit tests can validate command planning with pure Python fakes, while
real runtime code can pass a python-sc2 ``BotAI``-like object to
``SC2RuntimeExecutor.execute_plan``.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

from toycraft_commander.compat import StrEnum
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    ExpandIntent,
    GatherResourceIntent,
    HarassIntent,
    IntentPayload,
    RepairIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
)


SC2_UNIT_TYPE_IDS: Final[dict[str, str]] = {
    "SCV": "SCV",
    "Marine": "MARINE",
    "Vulture": "HELLION",
}
"""Intent unit names mapped to python-sc2 UnitTypeId attribute names.

SC2 has no Brood War Vulture. The closest Terran harassment stand-in for the SC2
MVP is Hellion, while the Brood War executor can later map Vulture directly.
"""

SC2_STRUCTURE_TYPE_IDS: Final[dict[str, str]] = {
    "Barracks": "BARRACKS",
    "Bunker": "BUNKER",
    "Command Center": "COMMANDCENTER",
    "Factory": "FACTORY",
    "Refinery": "REFINERY",
    "Supply Depot": "SUPPLYDEPOT",
}

SC2_PRODUCER_TYPE_IDS: Final[dict[str, str]] = {
    "SCV": "COMMANDCENTER",
    "Marine": "BARRACKS",
    "Vulture": "FACTORY",
}

SC2_TARGET_ALIASES: Final[dict[str, str]] = {
    "main": "self_main",
    "main base": "self_main",
    "main ramp": "self_ramp",
    "natural expansion": "self_natural",
    "enemy front": "enemy_ramp",
    "enemy main": "enemy_main",
    "enemy mineral line": "enemy_mineral_line",
    "enemy natural": "enemy_natural",
}


class SC2ActionType(StrEnum):
    """High-level StarCraft II API action categories."""

    ASSIGN_WORKERS = "assign_workers"
    BUILD_STRUCTURE = "build_structure"
    TRAIN_UNIT = "train_unit"
    MOVE_GROUP = "move_group"
    ATTACK_MOVE = "attack_move"
    REPAIR = "repair"
    OBSERVE = "observe"


@dataclass(frozen=True)
class SC2CommandAction:
    """One semantic StarCraft II API command derived from Intent DSL."""

    action_type: SC2ActionType
    subject: str
    target: str = ""
    count: int = 1
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        action_type = self.action_type
        if isinstance(action_type, str):
            action_type = SC2ActionType(action_type)
        if not self.subject.strip():
            raise ValueError("SC2 command action subject must be non-empty.")
        if self.count < 0:
            raise ValueError("SC2 command action count cannot be negative.")
        object.__setattr__(self, "action_type", action_type)
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready action payload for logs and UI adapters."""

        return {
            "action_type": self.action_type.value,
            "subject": self.subject,
            "target": self.target,
            "count": self.count,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SC2ExecutionPlan:
    """A deterministic plan for applying one commander intent to StarCraft II."""

    intent: str
    priority: str
    actions: tuple[SC2CommandAction, ...]
    constraints: tuple[str, ...] = ()
    requires_live_sc2: bool = True
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.intent.strip():
            raise ValueError("SC2 execution plan intent must be non-empty.")
        if not self.priority.strip():
            raise ValueError("SC2 execution plan priority must be non-empty.")
        object.__setattr__(self, "actions", tuple(self.actions))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "notes", tuple(self.notes))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready execution plan."""

        return {
            "intent": self.intent,
            "priority": self.priority,
            "constraints": list(self.constraints),
            "requires_live_sc2": self.requires_live_sc2,
            "notes": list(self.notes),
            "actions": [action.to_dict() for action in self.actions],
        }


@runtime_checkable
class SC2ActionPlannerInterface(Protocol):
    """Planner boundary from typed commander intent to SC2 command plan."""

    def build_plan(self, payload: IntentPayload | Mapping[str, object]) -> SC2ExecutionPlan:
        """Build a StarCraft II command plan without touching the live game."""


@dataclass(frozen=True)
class SC2ActionPlanner:
    """Default deterministic mapper from Intent DSL to StarCraft II actions."""

    def build_plan(self, payload: IntentPayload | Mapping[str, object]) -> SC2ExecutionPlan:
        """Build a StarCraft II command plan from one typed Intent DSL payload."""

        typed_payload = _coerce_intent_payload(payload)
        actions = _actions_for_payload(typed_payload)
        return SC2ExecutionPlan(
            intent=typed_payload.intent,
            priority=typed_payload.priority,
            constraints=typed_payload.constraints,
            actions=actions,
            notes=_notes_for_payload(typed_payload),
        )


DEFAULT_SC2_ACTION_PLANNER: Final[SC2ActionPlanner] = SC2ActionPlanner()


def build_sc2_execution_plan(
    payload: IntentPayload | Mapping[str, object],
) -> SC2ExecutionPlan:
    """Build the default StarCraft II plan for a commander Intent DSL payload."""

    return DEFAULT_SC2_ACTION_PLANNER.build_plan(payload)


@dataclass(frozen=True)
class SC2PlanExecutionResult:
    """Result of applying an SC2 execution plan to a runtime adapter."""

    plan: SC2ExecutionPlan
    attempted_actions: tuple[SC2CommandAction, ...]
    applied_actions: tuple[SC2CommandAction, ...]
    skipped_actions: tuple[SC2CommandAction, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        """Return whether every planned action was applied without errors."""

        return not self.errors and len(self.applied_actions) == len(self.plan.actions)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready runtime result."""

        return {
            "success": self.success,
            "plan": self.plan.to_dict(),
            "attempted_actions": [action.to_dict() for action in self.attempted_actions],
            "applied_actions": [action.to_dict() for action in self.applied_actions],
            "skipped_actions": [action.to_dict() for action in self.skipped_actions],
            "errors": list(self.errors),
        }


@runtime_checkable
class SC2RuntimeExecutorInterface(Protocol):
    """Runtime boundary for applying SC2 plans to a live API object."""

    async def execute_plan(self, bot: object, plan: SC2ExecutionPlan) -> SC2PlanExecutionResult:
        """Apply a planned command sequence to a python-sc2 BotAI-like object."""


@dataclass(frozen=True)
class SC2RuntimeExecutor:
    """Minimal async adapter around a python-sc2 ``BotAI``-like runtime."""

    async def execute_plan(
        self,
        bot: object,
        plan: SC2ExecutionPlan,
    ) -> SC2PlanExecutionResult:
        """Apply a planned command sequence to a live SC2 runtime adapter."""

        applied: list[SC2CommandAction] = []
        skipped: list[SC2CommandAction] = []
        errors: list[str] = []

        for action in plan.actions:
            try:
                did_apply = await _apply_action(bot, action)
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                errors.append(f"{action.action_type.value}:{exc}")
                skipped.append(action)
                continue
            if did_apply:
                applied.append(action)
            else:
                skipped.append(action)

        return SC2PlanExecutionResult(
            plan=plan,
            attempted_actions=plan.actions,
            applied_actions=tuple(applied),
            skipped_actions=tuple(skipped),
            errors=tuple(errors),
        )


def _actions_for_payload(payload: IntentPayload) -> tuple[SC2CommandAction, ...]:
    if isinstance(payload, GatherResourceIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.ASSIGN_WORKERS,
                subject="SCV",
                target=payload.resource,
                count=payload.worker_count,
                metadata={"base": payload.base},
            ),
        )
    if isinstance(payload, BuildStructureIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.BUILD_STRUCTURE,
                subject=_structure_type_id(payload.structure),
                target=_target_alias(payload.location),
                metadata={"source_structure": payload.structure},
            ),
        )
    if isinstance(payload, TrainWorkerIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.TRAIN_UNIT,
                subject="SCV",
                count=payload.count,
                metadata={"producer": SC2_PRODUCER_TYPE_IDS["SCV"]},
            ),
        )
    if isinstance(payload, TrainArmyIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.TRAIN_UNIT,
                subject=_unit_type_id(payload.unit_type),
                count=payload.count,
                metadata={
                    "producer": SC2_PRODUCER_TYPE_IDS[payload.unit_type],
                    "source_unit": payload.unit_type,
                },
            ),
        )
    if isinstance(payload, ScoutIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.MOVE_GROUP,
                subject=payload.unit_group,
                target=_target_alias(payload.target),
                metadata={"role": "scout"},
            ),
        )
    if isinstance(payload, DefendIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.ATTACK_MOVE,
                subject=payload.unit_group,
                target=_target_alias(payload.location),
                metadata={"role": "defend"},
            ),
        )
    if isinstance(payload, RepairIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.REPAIR,
                subject="SCV",
                target=payload.target,
                count=payload.worker_count,
            ),
        )
    if isinstance(payload, ExpandIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.BUILD_STRUCTURE,
                subject=SC2_STRUCTURE_TYPE_IDS["Command Center"],
                target=_target_alias(payload.location),
                metadata={"source_structure": "Command Center"},
            ),
        )
    if isinstance(payload, HarassIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.ATTACK_MOVE,
                subject=payload.unit_group,
                target=_target_alias(payload.target),
                metadata={"role": "harass"},
            ),
        )
    if isinstance(payload, SummarizeStateIntent):
        return (
            SC2CommandAction(
                action_type=SC2ActionType.OBSERVE,
                subject="visible_state",
                target="narrator_snapshot",
                count=0,
            ),
        )
    raise TypeError(f"unsupported Intent DSL payload for SC2: {type(payload)!r}")


def _notes_for_payload(payload: IntentPayload) -> tuple[str, ...]:
    notes = [
        "SC2 executor plans semantic API commands, not mouse clicks.",
        "Live execution requires StarCraft II plus a python-sc2 BotAI runtime.",
    ]
    if isinstance(payload, TrainArmyIntent) and payload.unit_type == "Vulture":
        notes.append("SC2 maps Brood War Vulture intent to Hellion for MVP harass.")
    return tuple(notes)


def _coerce_intent_payload(payload: IntentPayload | Mapping[str, object]) -> IntentPayload:
    if not isinstance(payload, Mapping):
        return payload

    intent = str(payload.get("intent", ""))
    if intent == "GATHER_RESOURCE":
        return GatherResourceIntent(
            resource=str(payload["resource"]),
            worker_count=int(payload["worker_count"]),
            base=str(payload["base"]),
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    if intent == "BUILD_STRUCTURE":
        return BuildStructureIntent(
            structure=str(payload["structure"]),  # type: ignore[arg-type]
            location=str(payload["location"]),
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    if intent == "TRAIN_WORKER":
        return TrainWorkerIntent(
            count=int(payload["count"]),
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    if intent == "TRAIN_ARMY":
        return TrainArmyIntent(
            unit_type=str(payload["unit_type"]),  # type: ignore[arg-type]
            count=int(payload["count"]),
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    if intent == "SCOUT":
        return ScoutIntent(
            target=str(payload["target"]),
            unit_group=str(payload["unit_group"]),
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    if intent == "SUMMARIZE_STATE":
        return SummarizeStateIntent(
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    if intent == "DEFEND":
        return DefendIntent(
            location=str(payload["location"]),
            unit_group=str(payload["unit_group"]),
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    if intent == "REPAIR":
        return RepairIntent(
            target=str(payload["target"]),
            worker_count=int(payload["worker_count"]),
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    if intent == "EXPAND":
        return ExpandIntent(
            location=str(payload["location"]),
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    if intent == "HARASS":
        return HarassIntent(
            target=str(payload["target"]),
            unit_group=str(payload["unit_group"]),
            priority=str(payload.get("priority", "normal")),
            constraints=tuple(str(item) for item in payload.get("constraints", ())),
        )
    raise ValueError(f"unsupported SC2 intent payload: {intent}")


def _unit_type_id(unit_name: str) -> str:
    try:
        return SC2_UNIT_TYPE_IDS[unit_name]
    except KeyError as exc:
        raise ValueError(f"unsupported SC2 unit: {unit_name}") from exc


def _structure_type_id(structure_name: str) -> str:
    try:
        return SC2_STRUCTURE_TYPE_IDS[structure_name]
    except KeyError as exc:
        raise ValueError(f"unsupported SC2 structure: {structure_name}") from exc


def _target_alias(target: str) -> str:
    return SC2_TARGET_ALIASES.get(target, target)


async def _apply_action(bot: object, action: SC2CommandAction) -> bool:
    method_name = _method_name_for_action(action.action_type)
    method = getattr(bot, method_name, None)
    if method is None:
        method = getattr(bot, "execute_commander_action", None)
    if method is None:
        return False

    result = method(action)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return True
    return bool(result)


def _method_name_for_action(action_type: SC2ActionType) -> str:
    return {
        SC2ActionType.ASSIGN_WORKERS: "assign_workers",
        SC2ActionType.BUILD_STRUCTURE: "build_structure",
        SC2ActionType.TRAIN_UNIT: "train_unit",
        SC2ActionType.MOVE_GROUP: "move_group",
        SC2ActionType.ATTACK_MOVE: "attack_move",
        SC2ActionType.REPAIR: "repair",
        SC2ActionType.OBSERVE: "observe",
    }[action_type]


def first_or_none(items: Sequence[Any]) -> Any | None:
    """Return the first item from a sequence-like object, or ``None``."""

    return items[0] if items else None
