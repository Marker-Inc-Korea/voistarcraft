"""Real StarCraft commander execution surfaces.

The project keeps ToyCraft only as an offline test harness. The semantic SC2
contracts are importable without ToyCraft, StarCraft II, or python-sc2. Planner
and runtime adapters are loaded lazily because they translate the existing
Intent DSL payload classes.
"""

from __future__ import annotations

from typing import Any

from starcraft_commander.contracts import (
    SC2_ACTION_TYPES,
    SC2ActionType,
    SC2CommandAction,
    SC2CommandPlan,
    SC2ExecutionError,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
)

_EXECUTOR_EXPORTS = {
    "DEFAULT_SC2_ACTION_PLANNER",
    "SC2ActionPlanner",
    "SC2ActionPlannerInterface",
    "SC2ExecutorBoundaryInterface",
    "SC2_INTENT_ACTION_TYPE_MAP",
    "SC2RuntimeExecutor",
    "SC2RuntimeExecutorInterface",
    "build_sc2_execution_plan",
}

__all__ = [
    "DEFAULT_SC2_ACTION_PLANNER",
    "SC2_ACTION_TYPES",
    "SC2ActionPlanner",
    "SC2ActionPlannerInterface",
    "SC2ActionType",
    "SC2CommandAction",
    "SC2CommandPlan",
    "SC2ExecutorBoundaryInterface",
    "SC2ExecutionError",
    "SC2ExecutionPlan",
    "SC2_INTENT_ACTION_TYPE_MAP",
    "SC2PlanExecutionResult",
    "SC2RuntimeExecutor",
    "SC2RuntimeExecutorInterface",
    "build_sc2_execution_plan",
]


def __getattr__(name: str) -> Any:
    """Load planner/runtime surfaces only when callers ask for them."""

    if name in _EXECUTOR_EXPORTS:
        from starcraft_commander import sc2_executor

        value = getattr(sc2_executor, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
