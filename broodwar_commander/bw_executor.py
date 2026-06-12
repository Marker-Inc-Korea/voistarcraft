"""Brood War (BWAPI) execution boundary for commander Intent DSL payloads.

This module mirrors ``starcraft_commander.sc2_executor`` at the pre-adapter
level: typed Intent DSL payloads become ordered semantic Brood War command
plans, and a duck-typed runtime executor applies them to a BWAPI-bot-like
object. It is intentionally importable without Brood War, BWAPI, or any other
optional dependency, so unit tests validate the whole boundary with pure
Python fakes.

The semantic plan/result contracts are reused directly from
``starcraft_commander.contracts``. As that module's docstring notes, the
contracts are independent of any live runtime import; their ``SC2``-prefixed
names and the ``requires_live_sc2`` flag are a naming coupling only. In this
module ``requires_live_sc2`` reads as "requires a live game runtime" and every
plan's audit marks the game as ``brood_war`` so logs stay unambiguous.

Honest limitation (the documented remaining step): a real BWAPI binding
adapter — the Brood War equivalent of
``starcraft_commander.python_sc2_adapter`` — cannot be built or validated here
because no StarCraft: Brood War + BWAPI environment exists in this repository.
See :data:`BW_RUNTIME_ADAPTER_REMAINING_STEP` for the exact contract such an
adapter must satisfy.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from starcraft_commander.contracts import (
    SC2_ACTION_TYPES,
    SC2ActionReport,
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionError,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)
from starcraft_commander.sc2_executor import (
    SC2_INTENT_ACTION_TYPE_MAP,
    SC2_SEMANTIC_TARGET_NAMES,
    SC2_TARGET_ALIASES,
    SC2ActionPlannerInterface,
    SC2ExecutorBoundaryInterface,
    SC2RuntimeExecutor,
    SC2RuntimeExecutorInterface,
)


# ---------------------------------------------------------------------------
# Reused game-agnostic contracts under Brood War boundary names.
# ---------------------------------------------------------------------------

BWActionType = SC2ActionType
"""Brood War plans reuse the semantic action-type enum unchanged."""

BWActionReport = SC2ActionReport
"""Brood War runtime adapters report per-action outcomes with the same shape."""

BWCommandAction = SC2CommandAction
"""One semantic Brood War command reuses the game-agnostic action contract."""

BWExecutionPlan = SC2ExecutionPlan
"""Ordered semantic Brood War command plan (same contract class)."""

BWCommandPlan = SC2ExecutionPlan
"""Public semantic Brood War command-plan alias."""

BWExecutionError = SC2ExecutionError
"""Structured Brood War planning/runtime error (same contract class)."""

BWPlanExecutionResult = SC2PlanExecutionResult
"""Structured Brood War plan execution result (same contract class)."""

BW_ACTION_TYPES: Final[frozenset[str]] = SC2_ACTION_TYPES
"""Stable public semantic action type values shared with the SC2 boundary."""


# ---------------------------------------------------------------------------
# Brood War Terran vocabulary registries (BWAPI type names).
# ---------------------------------------------------------------------------

BW_UNIT_TYPE_IDS: Final[dict[str, str]] = {
    "SCV": "Terran_SCV",
    "Marine": "Terran_Marine",
    "Vulture": "Terran_Vulture",
}
"""Intent unit names mapped to BWAPI ``UnitType`` names.

Brood War has a real Vulture, so the Vulture intent maps DIRECTLY to
``Terran_Vulture``. This is the executor promised by the SC2 module's note:
no Hellion stand-in happens at this boundary.
"""

BW_STRUCTURE_TYPE_IDS: Final[dict[str, str]] = {
    "Barracks": "Terran_Barracks",
    "Bunker": "Terran_Bunker",
    "Command Center": "Terran_Command_Center",
    "Factory": "Terran_Factory",
    "Refinery": "Terran_Refinery",
    "Supply Depot": "Terran_Supply_Depot",
}
"""Intent structure names mapped to BWAPI ``UnitType`` names."""

BW_PRODUCER_TYPE_IDS: Final[dict[str, str]] = {
    "SCV": "Terran_Command_Center",
    "Marine": "Terran_Barracks",
    "Vulture": "Terran_Factory",
}
"""Producer structure (as a BWAPI name) for each trainable intent unit."""

BW_SEMANTIC_TARGET_NAMES: Final[frozenset[str]] = SC2_SEMANTIC_TARGET_NAMES
"""Canonical semantic target names; the vocabulary is game-agnostic and is
shared object-identically with the SC2 boundary."""

BW_TARGET_ALIASES: Final[dict[str, str]] = SC2_TARGET_ALIASES
"""ToyCraft canonical map-location aliases reused object-identically from the
SC2 boundary. Unknown location targets are rejected, never passed through."""

BW_INTENT_ACTION_TYPE_MAP: Final[dict[str, tuple[str, ...]]] = SC2_INTENT_ACTION_TYPE_MAP
"""Stable Intent DSL to semantic action-type mapping, shared with SC2."""

BW_RUNTIME_ADAPTER_REMAINING_STEP: Final[str] = (
    "Remaining step: a real BWAPI binding adapter (the Brood War equivalent "
    "of starcraft_commander.python_sc2_adapter) must wrap a live BWAPI "
    "client behind the same seven async runtime methods used by "
    "BWRuntimeExecutor — assign_workers, build_structure, train_unit, "
    "move_group, attack_move, repair, and observe — or the "
    "execute_commander_action fallback. Building and validating that adapter "
    "requires a local StarCraft: Brood War + BWAPI environment, which is not "
    "available in this repository, so it is documented honestly instead of "
    "shipped as fake-tested-only code."
)
"""Documented honest limitation: what the real BWAPI adapter still requires."""


# ---------------------------------------------------------------------------
# Intent DSL -> semantic Brood War action builders (planner registry pattern).
# ---------------------------------------------------------------------------


def _gather_resource_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("GATHER_RESOURCE"),
            subject=BW_UNIT_TYPE_IDS["SCV"],
            target=str(_required_field(payload, "resource")),
            count=int(_required_field(payload, "worker_count")),
            metadata={"base": str(_required_field(payload, "base"))},
        ),
    )


def _build_structure_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    structure = str(_required_field(payload, "structure"))
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("BUILD_STRUCTURE"),
            subject=_structure_type_id(structure),
            target=_target_alias(str(_required_field(payload, "location"))),
            metadata={"source_structure": structure},
        ),
    )


def _train_worker_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("TRAIN_WORKER"),
            subject=BW_UNIT_TYPE_IDS["SCV"],
            count=int(_required_field(payload, "count")),
            metadata={"producer": BW_PRODUCER_TYPE_IDS["SCV"]},
        ),
    )


def _train_army_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    unit_type = str(_required_field(payload, "unit_type"))
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("TRAIN_ARMY"),
            subject=_unit_type_id(unit_type),
            count=int(_required_field(payload, "count")),
            metadata={
                "producer": _producer_type_id(unit_type),
                "source_unit": unit_type,
            },
        ),
    )


def _scout_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("SCOUT"),
            subject=str(_required_field(payload, "unit_group")),
            target=_target_alias(str(_required_field(payload, "target"))),
            metadata={"role": "scout"},
        ),
    )


def _summarize_state_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("SUMMARIZE_STATE"),
            subject="visible_state",
            target="narrator_snapshot",
            count=0,
        ),
    )


def _defend_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("DEFEND"),
            subject=str(_required_field(payload, "unit_group")),
            target=_target_alias(str(_required_field(payload, "location"))),
            metadata={"role": "defend"},
        ),
    )


def _repair_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    # REPAIR targets are entity names (for example "front bunker"), not map
    # locations, so they intentionally stay verbatim.
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("REPAIR"),
            subject=BW_UNIT_TYPE_IDS["SCV"],
            target=str(_required_field(payload, "target")),
            count=int(_required_field(payload, "worker_count")),
        ),
    )


def _expand_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("EXPAND"),
            subject=BW_STRUCTURE_TYPE_IDS["Command Center"],
            target=_target_alias(str(_required_field(payload, "location"))),
            metadata={"source_structure": "Command Center"},
        ),
    )


def _harass_actions(
    payload: object | Mapping[str, object],
) -> tuple[SC2CommandAction, ...]:
    return (
        SC2CommandAction(
            action_type=_action_type_for_intent("HARASS"),
            subject=str(_required_field(payload, "unit_group")),
            target=_target_alias(str(_required_field(payload, "target"))),
            metadata={"role": "harass"},
        ),
    )


_BW_INTENT_ACTION_BUILDERS: Final[
    dict[str, Callable[[object | Mapping[str, object]], tuple[SC2CommandAction, ...]]]
] = {
    "GATHER_RESOURCE": _gather_resource_actions,
    "BUILD_STRUCTURE": _build_structure_actions,
    "TRAIN_WORKER": _train_worker_actions,
    "TRAIN_ARMY": _train_army_actions,
    "SCOUT": _scout_actions,
    "SUMMARIZE_STATE": _summarize_state_actions,
    "DEFEND": _defend_actions,
    "REPAIR": _repair_actions,
    "EXPAND": _expand_actions,
    "HARASS": _harass_actions,
}
"""One action-builder function per supported Intent DSL value."""


BWActionPlannerInterface = SC2ActionPlannerInterface
"""Planner boundary protocol; identical (runtime-checkable) for both games."""


@dataclass(frozen=True)
class BWActionPlanner:
    """Default deterministic mapper from Intent DSL to Brood War actions."""

    def build_plan(self, payload: object | Mapping[str, object]) -> SC2ExecutionPlan:
        """Build a Brood War command plan from one typed Intent DSL payload."""

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
            audit={"game": "brood_war", "command_dialect": "bwapi"},
        )


DEFAULT_BW_ACTION_PLANNER: Final[BWActionPlanner] = BWActionPlanner()


def build_bw_execution_plan(
    payload: object | Mapping[str, object],
) -> SC2ExecutionPlan:
    """Build the default Brood War plan for a commander Intent DSL payload."""

    return DEFAULT_BW_ACTION_PLANNER.build_plan(payload)


# ---------------------------------------------------------------------------
# Runtime executor: reused, not forked.
# ---------------------------------------------------------------------------

BWRuntimeExecutorInterface = SC2RuntimeExecutorInterface
"""Runtime boundary protocol; identical (runtime-checkable) for both games."""

BWExecutorBoundaryInterface = SC2ExecutorBoundaryInterface
"""Lifecycle-aware executor boundary protocol shared with the SC2 boundary."""


class BWRuntimeExecutor(SC2RuntimeExecutor):
    """Lifecycle-aware async adapter around a BWAPI-bot-like runtime object.

    The dispatch logic is inherited unchanged from
    :class:`starcraft_commander.sc2_executor.SC2RuntimeExecutor`, which is
    already game-agnostic duck dispatch: a bound runtime object only needs to
    expose the seven async action methods (``assign_workers``,
    ``build_structure``, ``train_unit``, ``move_group``, ``attack_move``,
    ``repair``, ``observe``) or the ``execute_commander_action`` fallback,
    plus optional lifecycle hooks (``on_start``/``start`` and
    ``on_end``/``close``/``stop``). A future real BWAPI binding adapter must
    expose exactly that surface; see
    :data:`BW_RUNTIME_ADAPTER_REMAINING_STEP`. Lifecycle, structured error,
    partial-issuance, and observation semantics are intentionally identical
    to the SC2 boundary so narration and audit code can treat both games the
    same way.
    """


# ---------------------------------------------------------------------------
# Payload field helpers (same strict validation style as the SC2 planner).
# ---------------------------------------------------------------------------

_MISSING: Final[object] = object()


def _actions_for_payload(
    payload: object | Mapping[str, object],
    intent_name: str,
) -> tuple[SC2CommandAction, ...]:
    builder = _BW_INTENT_ACTION_BUILDERS.get(intent_name)
    if builder is None:
        raise ValueError(f"unsupported Brood War intent payload: {intent_name}")
    return builder(payload)


def _notes_for_payload(
    payload: object | Mapping[str, object],
    intent_name: str,
) -> tuple[str, ...]:
    notes = [
        "Brood War executor plans semantic BWAPI commands, not mouse clicks.",
        "Live execution requires StarCraft: Brood War plus a BWAPI runtime "
        "adapter; no real BWAPI binding ships in this package yet.",
    ]
    if intent_name == "TRAIN_ARMY" and str(_field(payload, "unit_type", "")) == "Vulture":
        notes.append(
            "Brood War maps the Vulture intent directly to Terran_Vulture; "
            "no Hellion stand-in is used."
        )
    return tuple(notes)


def _intent_name(payload: object | Mapping[str, object]) -> str:
    intent_name = str(_required_field(payload, "intent"))
    if not intent_name.strip():
        raise ValueError("Brood War intent payload must include a non-empty intent.")
    return intent_name


def _priority_label(payload: object | Mapping[str, object]) -> str:
    return str(_field(payload, "priority", "normal"))


def _constraints(payload: object | Mapping[str, object]) -> tuple[str, ...]:
    return tuple(str(item) for item in _field(payload, "constraints", ()))


def _required_field(payload: object | Mapping[str, object], field_name: str) -> Any:
    value = _field(payload, field_name, _MISSING)
    if value is _MISSING:
        raise ValueError(
            f"Brood War intent payload missing required field: {field_name}"
        )
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
        return BW_UNIT_TYPE_IDS[unit_name]
    except KeyError as exc:
        raise ValueError(f"unsupported Brood War unit: {unit_name}") from exc


def _structure_type_id(structure_name: str) -> str:
    try:
        return BW_STRUCTURE_TYPE_IDS[structure_name]
    except KeyError as exc:
        raise ValueError(f"unsupported Brood War structure: {structure_name}") from exc


def _producer_type_id(unit_name: str) -> str:
    try:
        return BW_PRODUCER_TYPE_IDS[unit_name]
    except KeyError as exc:
        raise ValueError(
            f"unsupported Brood War producer for unit: {unit_name}"
        ) from exc


def _target_alias(target: str) -> str:
    """Resolve a map-location target strictly to a semantic target name."""

    alias = BW_TARGET_ALIASES.get(target)
    if alias is not None:
        return alias
    if target in BW_SEMANTIC_TARGET_NAMES:
        return target
    supported = ", ".join(sorted({*BW_TARGET_ALIASES, *BW_SEMANTIC_TARGET_NAMES}))
    raise ValueError(
        f"unsupported Brood War target location: {target!r}. "
        f"Supported targets: {supported}."
    )


def _action_type_for_intent(intent: str) -> SC2ActionType:
    action_types = BW_INTENT_ACTION_TYPE_MAP[intent]
    if len(action_types) != 1:
        raise ValueError(f"Brood War intent emits multiple action types: {intent}")
    action_type = action_types[0]
    if action_type not in BW_ACTION_TYPES:
        raise ValueError(f"unsupported public Brood War action type: {action_type}")
    return SC2ActionType(action_type)
