"""StarCraft II execution adapter for commander Intent DSL payloads.

This module is intentionally importable without StarCraft II or python-sc2
installed. Unit tests can validate command planning with pure Python fakes, while
real runtime code can pass a python-sc2 ``BotAI``-like object to
``SC2RuntimeExecutor.execute_plan``.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

from starcraft_commander.contracts import (
    SC2_ACTION_TYPES,
    SC2ActionType,
    SC2CommandAction,
    SC2CommandPlan,
    SC2ExecutionError,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
    first_or_none,
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

SC2_INTENT_ACTION_TYPE_MAP: Final[dict[str, tuple[str, ...]]] = {
    "GATHER_RESOURCE": ("assign_workers",),
    "BUILD_STRUCTURE": ("build_structure",),
    "TRAIN_WORKER": ("train_unit",),
    "TRAIN_ARMY": ("train_unit",),
    "SCOUT": ("move_group",),
    "SUMMARIZE_STATE": ("observe",),
    "DEFEND": ("attack_move",),
    "REPAIR": ("repair",),
    "EXPAND": ("build_structure",),
    "HARASS": ("attack_move",),
}
"""Stable public semantic action type names emitted for each Intent DSL value."""


@runtime_checkable
class SC2ActionPlannerInterface(Protocol):
    """Planner boundary from typed commander intent to SC2 command plan."""

    def build_plan(self, payload: object | Mapping[str, object]) -> SC2ExecutionPlan:
        """Build a StarCraft II command plan without touching the live game."""


@dataclass(frozen=True)
class SC2ActionPlanner:
    """Default deterministic mapper from Intent DSL to StarCraft II actions."""

    def build_plan(self, payload: object | Mapping[str, object]) -> SC2ExecutionPlan:
        """Build a StarCraft II command plan from one typed Intent DSL payload."""

        intent_name = _intent_name(payload)
        priority = _priority_label(payload)
        constraints = _constraints(payload)
        actions = _actions_for_payload(payload, intent_name)
        return SC2ExecutionPlan(
            intent=intent_name,
            priority=priority,
            constraints=constraints,
            actions=actions,
            notes=_notes_for_payload(payload, intent_name),
        )


DEFAULT_SC2_ACTION_PLANNER: Final[SC2ActionPlanner] = SC2ActionPlanner()


def build_sc2_execution_plan(
    payload: object | Mapping[str, object],
) -> SC2ExecutionPlan:
    """Build the default StarCraft II plan for a commander Intent DSL payload."""

    return DEFAULT_SC2_ACTION_PLANNER.build_plan(payload)

@runtime_checkable
class SC2RuntimeExecutorInterface(Protocol):
    """Runtime boundary for applying SC2 plans to a live API object."""

    async def execute_plan(self, bot: object, plan: SC2ExecutionPlan) -> SC2PlanExecutionResult:
        """Apply a planned command sequence to a python-sc2 BotAI-like object."""


@runtime_checkable
class SC2ExecutorBoundaryInterface(Protocol):
    """Lifecycle-aware SC2 executor boundary used by API and bot adapters."""

    @property
    def is_started(self) -> bool:
        """Return whether the executor lifecycle has been started."""

    async def start(self, bot: object | None = None) -> None:
        """Bind and initialize a BotAI-like runtime adapter if one is provided."""

    async def execute(self, plan: SC2ExecutionPlan) -> SC2PlanExecutionResult:
        """Execute one ordered semantic SC2 command plan and return a result."""

    async def close(self) -> None:
        """Release runtime lifecycle resources without raising to callers."""


@dataclass
class SC2RuntimeExecutor:
    """Lifecycle-aware async adapter around a python-sc2 ``BotAI``-like runtime."""

    bot: object | None = None
    _started: bool = field(default=False, init=False, repr=False)
    _lifecycle_errors: list[SC2ExecutionError] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._started = self.bot is not None

    @property
    def is_started(self) -> bool:
        """Return whether the executor has an active lifecycle."""

        return self._started

    @property
    def lifecycle_errors(self) -> tuple[SC2ExecutionError, ...]:
        """Structured lifecycle errors captured without crashing callers."""

        return tuple(self._lifecycle_errors)

    async def start(self, bot: object | None = None) -> None:
        """Start the executor lifecycle and optionally bind a BotAI-like object."""

        if bot is not None:
            self.bot = bot
        self._started = True
        await _call_optional_lifecycle_hook(
            self.bot,
            ("on_start", "start"),
            self._lifecycle_errors,
        )

    async def execute(self, plan: SC2ExecutionPlan) -> SC2PlanExecutionResult:
        """Execute a semantic SC2 command plan against the bound runtime adapter."""

        if self.bot is None:
            return _missing_runtime_result(plan, self._started, self._lifecycle_errors)
        return await self._execute_with_bot(self.bot, plan)

    async def close(self) -> None:
        """Close the executor lifecycle while preserving structured errors."""

        await _call_optional_lifecycle_hook(
            self.bot,
            ("on_end", "close", "stop"),
            self._lifecycle_errors,
        )
        self._started = False

    async def execute_plan(
        self,
        bot: object,
        plan: SC2ExecutionPlan,
    ) -> SC2PlanExecutionResult:
        """Apply a planned command sequence to a live SC2 runtime adapter."""

        return await self._execute_with_bot(bot, plan)

    async def _execute_with_bot(
        self,
        bot: object,
        plan: SC2ExecutionPlan,
    ) -> SC2PlanExecutionResult:
        """Apply a planned command sequence to a live SC2 runtime adapter."""

        applied: list[SC2CommandAction] = []
        skipped: list[SC2CommandAction] = []
        errors: list[SC2ExecutionError] = []

        for action_index, action in enumerate(plan.actions):
            try:
                did_apply = await _apply_action(bot, action)
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                errors.append(
                    SC2ExecutionError(
                        message=str(exc),
                        action_type=action.action_type,
                        action_index=action_index,
                        exception_type=type(exc).__name__,
                    )
                )
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
            errors=tuple((*self._lifecycle_errors, *errors)),
            audit={
                "runtime_adapter": type(bot).__name__,
                "executor_started": self._started,
                "planned_action_count": len(plan.actions),
            },
        )


_MISSING: Final[object] = object()


def _actions_for_payload(
    payload: object | Mapping[str, object],
    intent_name: str,
) -> tuple[SC2CommandAction, ...]:
    if intent_name == "GATHER_RESOURCE":
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject="SCV",
                target=str(_required_field(payload, "resource")),
                count=int(_required_field(payload, "worker_count")),
                metadata={"base": str(_required_field(payload, "base"))},
            ),
        )
    if intent_name == "BUILD_STRUCTURE":
        structure = str(_required_field(payload, "structure"))
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject=_structure_type_id(structure),
                target=_target_alias(str(_required_field(payload, "location"))),
                metadata={"source_structure": structure},
            ),
        )
    if intent_name == "TRAIN_WORKER":
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject="SCV",
                count=int(_required_field(payload, "count")),
                metadata={"producer": SC2_PRODUCER_TYPE_IDS["SCV"]},
            ),
        )
    if intent_name == "TRAIN_ARMY":
        unit_type = str(_required_field(payload, "unit_type"))
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject=_unit_type_id(unit_type),
                count=int(_required_field(payload, "count")),
                metadata={
                    "producer": _producer_type_id(unit_type),
                    "source_unit": unit_type,
                },
            ),
        )
    if intent_name == "SCOUT":
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject=str(_required_field(payload, "unit_group")),
                target=_target_alias(str(_required_field(payload, "target"))),
                metadata={"role": "scout"},
            ),
        )
    if intent_name == "DEFEND":
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject=str(_required_field(payload, "unit_group")),
                target=_target_alias(str(_required_field(payload, "location"))),
                metadata={"role": "defend"},
            ),
        )
    if intent_name == "REPAIR":
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject="SCV",
                target=str(_required_field(payload, "target")),
                count=int(_required_field(payload, "worker_count")),
            ),
        )
    if intent_name == "EXPAND":
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject=SC2_STRUCTURE_TYPE_IDS["Command Center"],
                target=_target_alias(str(_required_field(payload, "location"))),
                metadata={"source_structure": "Command Center"},
            ),
        )
    if intent_name == "HARASS":
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject=str(_required_field(payload, "unit_group")),
                target=_target_alias(str(_required_field(payload, "target"))),
                metadata={"role": "harass"},
            ),
        )
    if intent_name == "SUMMARIZE_STATE":
        return (
            SC2CommandAction(
                action_type=_action_type_for_intent(intent_name),
                subject="visible_state",
                target="narrator_snapshot",
                count=0,
            ),
        )
    raise ValueError(f"unsupported SC2 intent payload: {intent_name}")


def _notes_for_payload(
    payload: object | Mapping[str, object],
    intent_name: str,
) -> tuple[str, ...]:
    notes = [
        "SC2 executor plans semantic API commands, not mouse clicks.",
        "Live execution requires StarCraft II plus a python-sc2 BotAI runtime.",
    ]
    if intent_name == "TRAIN_ARMY" and str(_field(payload, "unit_type", "")) == "Vulture":
        notes.append("SC2 maps Brood War Vulture intent to Hellion for MVP harass.")
    return tuple(notes)


def _intent_name(payload: object | Mapping[str, object]) -> str:
    intent_name = str(_required_field(payload, "intent"))
    if not intent_name.strip():
        raise ValueError("SC2 intent payload must include a non-empty intent.")
    return intent_name


def _priority_label(payload: object | Mapping[str, object]) -> str:
    return str(_field(payload, "priority", "normal"))


def _constraints(payload: object | Mapping[str, object]) -> tuple[str, ...]:
    return tuple(str(item) for item in _field(payload, "constraints", ()))


def _required_field(payload: object | Mapping[str, object], field_name: str) -> Any:
    value = _field(payload, field_name, _MISSING)
    if value is _MISSING:
        raise ValueError(f"SC2 intent payload missing required field: {field_name}")
    return value


def _field(
    payload: object | Mapping[str, object],
    field_name: str,
    default: object = _MISSING,
) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(field_name, default)
    return getattr(payload, field_name, default)


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


def _producer_type_id(unit_name: str) -> str:
    try:
        return SC2_PRODUCER_TYPE_IDS[unit_name]
    except KeyError as exc:
        raise ValueError(f"unsupported SC2 producer for unit: {unit_name}") from exc


def _target_alias(target: str) -> str:
    return SC2_TARGET_ALIASES.get(target, target)


def _action_type_for_intent(intent: str) -> SC2ActionType:
    action_types = SC2_INTENT_ACTION_TYPE_MAP[intent]
    if len(action_types) != 1:
        raise ValueError(f"SC2 intent emits multiple action types: {intent}")
    action_type = action_types[0]
    if action_type not in SC2_ACTION_TYPES:
        raise ValueError(f"unsupported public SC2 action type: {action_type}")
    return SC2ActionType(action_type)


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


async def _call_optional_lifecycle_hook(
    bot: object | None,
    hook_names: tuple[str, ...],
    errors: list[SC2ExecutionError],
) -> None:
    if bot is None:
        return
    for hook_name in hook_names:
        hook = getattr(bot, hook_name, None)
        if hook is None:
            continue
        try:
            result = hook()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            errors.append(
                SC2ExecutionError(
                    message=str(exc),
                    exception_type=type(exc).__name__,
                    metadata={"lifecycle_hook": hook_name},
                )
            )
        return


def _missing_runtime_result(
    plan: SC2ExecutionPlan,
    executor_started: bool,
    lifecycle_errors: tuple[SC2ExecutionError, ...],
) -> SC2PlanExecutionResult:
    missing_runtime_error = SC2ExecutionError(
        message="SC2 runtime adapter has not been bound to a BotAI-like object.",
        exception_type="MissingRuntimeAdapter",
        metadata={"executor_started": executor_started},
    )
    return SC2PlanExecutionResult(
        plan=plan,
        attempted_actions=(),
        applied_actions=(),
        skipped_actions=plan.actions,
        errors=(*lifecycle_errors, missing_runtime_error),
        audit={
            "runtime_adapter": None,
            "executor_started": executor_started,
            "planned_action_count": len(plan.actions),
        },
    )


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
