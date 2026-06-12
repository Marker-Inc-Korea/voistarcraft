"""Brood War (BWAPI) commander execution surfaces.

This package mirrors ``starcraft_commander`` at the pre-adapter level for
StarCraft: Brood War. The semantic command-plan contracts are reused from
``starcraft_commander.contracts`` (game-agnostic despite their SC2-prefixed
names) and re-exported eagerly under Brood War boundary names because they
are pure stdlib. The planner and runtime-executor surfaces in
``broodwar_commander.bw_executor`` are loaded lazily on first attribute
access, so importing the package itself never pulls ToyCraft, the SC2
executor module, or any optional runtime dependency.

A real BWAPI binding adapter is the documented remaining step; see
``broodwar_commander.bw_executor.BW_RUNTIME_ADAPTER_REMAINING_STEP``.
"""

from __future__ import annotations

import importlib
from typing import Any, Final

from starcraft_commander.contracts import (
    SC2_ACTION_TYPES as BW_ACTION_TYPES,
    SC2ActionReport as BWActionReport,
    SC2ActionType as BWActionType,
    SC2CommandAction as BWCommandAction,
    SC2ExecutionError as BWExecutionError,
    SC2ExecutionPlan as BWExecutionPlan,
    SC2PlanExecutionResult as BWPlanExecutionResult,
)

BWCommandPlan = BWExecutionPlan
"""Public semantic Brood War command-plan alias (same contract class)."""

_LAZY_EXPORTS: Final[dict[str, str]] = {
    # Brood War Terran vocabulary registries (BWAPI type names).
    "BW_INTENT_ACTION_TYPE_MAP": "broodwar_commander.bw_executor",
    "BW_PRODUCER_TYPE_IDS": "broodwar_commander.bw_executor",
    "BW_RUNTIME_ADAPTER_REMAINING_STEP": "broodwar_commander.bw_executor",
    "BW_SEMANTIC_TARGET_NAMES": "broodwar_commander.bw_executor",
    "BW_STRUCTURE_TYPE_IDS": "broodwar_commander.bw_executor",
    "BW_TARGET_ALIASES": "broodwar_commander.bw_executor",
    "BW_UNIT_TYPE_IDS": "broodwar_commander.bw_executor",
    # Planner / runtime executor surfaces.
    "BWActionPlanner": "broodwar_commander.bw_executor",
    "BWActionPlannerInterface": "broodwar_commander.bw_executor",
    "BWExecutorBoundaryInterface": "broodwar_commander.bw_executor",
    "BWRuntimeExecutor": "broodwar_commander.bw_executor",
    "BWRuntimeExecutorInterface": "broodwar_commander.bw_executor",
    "DEFAULT_BW_ACTION_PLANNER": "broodwar_commander.bw_executor",
    "build_bw_execution_plan": "broodwar_commander.bw_executor",
}
"""Lazily loaded public symbols mapped to their defining modules."""

_EAGER_EXPORTS: Final[tuple[str, ...]] = (
    "BW_ACTION_TYPES",
    "BWActionReport",
    "BWActionType",
    "BWCommandAction",
    "BWCommandPlan",
    "BWExecutionError",
    "BWExecutionPlan",
    "BWPlanExecutionResult",
)
"""Contract symbols imported eagerly (stdlib-only, dependency-free)."""

__all__ = sorted({*_EAGER_EXPORTS, *_LAZY_EXPORTS})


def __getattr__(name: str) -> Any:
    """Load planner/runtime executor surfaces only when callers ask for them."""

    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy exports to ``dir()`` without importing them."""

    return sorted({*globals(), *__all__})
