"""Real StarCraft commander execution surfaces.

The project keeps ToyCraft only as an offline test harness. Runtime StarCraft
control starts here: Intent DSL payloads are translated into SC2 API command
plans and can be applied by an adapter around a python-sc2 ``BotAI`` instance.
"""

from starcraft_commander.sc2_executor import (
    DEFAULT_SC2_ACTION_PLANNER,
    SC2ActionPlanner,
    SC2ActionPlannerInterface,
    SC2ActionType,
    SC2CommandAction,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
    SC2RuntimeExecutor,
    SC2RuntimeExecutorInterface,
    build_sc2_execution_plan,
)

__all__ = [
    "DEFAULT_SC2_ACTION_PLANNER",
    "SC2ActionPlanner",
    "SC2ActionPlannerInterface",
    "SC2ActionType",
    "SC2CommandAction",
    "SC2ExecutionPlan",
    "SC2PlanExecutionResult",
    "SC2RuntimeExecutor",
    "SC2RuntimeExecutorInterface",
    "build_sc2_execution_plan",
]
