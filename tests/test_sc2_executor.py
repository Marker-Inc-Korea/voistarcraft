import asyncio
import json
import subprocess
import sys
import unittest

import starcraft_commander as package_exports
from starcraft_commander.sc2_executor import (
    DEFAULT_SC2_ACTION_PLANNER,
    SC2_ACTION_TYPES,
    SC2_INTENT_ACTION_TYPE_MAP,
    SC2ActionPlanner,
    SC2ActionPlannerInterface,
    SC2ActionType,
    SC2CommandAction,
    SC2CommandPlan,
    SC2ExecutorBoundaryInterface,
    SC2ExecutionError,
    SC2ExecutionPlan,
    SC2PlanExecutionResult,
    SC2RuntimeExecutor,
    SC2RuntimeExecutorInterface,
    build_sc2_execution_plan,
)
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    ExpandIntent,
    GatherResourceIntent,
    HarassIntent,
    RepairIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
)


CANONICAL_SC2_PLANNER_PAYLOADS = (
    GatherResourceIntent(resource="minerals", worker_count=4, base="main"),
    BuildStructureIntent(structure="Supply Depot", location="main ramp"),
    TrainWorkerIntent(count=2),
    TrainArmyIntent(unit_type="Marine", count=3),
    ScoutIntent(target="enemy main", unit_group="worker_scout"),
    SummarizeStateIntent(),
    DefendIntent(location="main ramp", unit_group="marines"),
    RepairIntent(target="front bunker", worker_count=2),
    ExpandIntent(location="natural expansion"),
    HarassIntent(target="enemy mineral line", unit_group="marines"),
)

LEGACY_INTERNAL_ACTION_TYPE_NAMES = frozenset(
    {
        "advance_time",
        "apply_damage",
        "assign_builder",
        "build",
        "execute_build_structure",
        "execute_gather_resource",
        "execute_harass",
        "execute_train_army",
        "execute_train_worker",
        "gather_resource",
        "move_units",
        "queue_construction",
        "queue_production",
        "reserve_supply",
        "spend_resources",
        "train",
    }
)


class StarCraftCommanderPackageSurfaceTest(unittest.TestCase):
    def test_package_exports_real_sc2_executor_boundary(self) -> None:
        self.assertIs(DEFAULT_SC2_ACTION_PLANNER, package_exports.DEFAULT_SC2_ACTION_PLANNER)
        self.assertIs(SC2_ACTION_TYPES, package_exports.SC2_ACTION_TYPES)
        self.assertIs(
            SC2_INTENT_ACTION_TYPE_MAP,
            package_exports.SC2_INTENT_ACTION_TYPE_MAP,
        )
        self.assertIs(SC2ActionPlanner, package_exports.SC2ActionPlanner)
        self.assertIs(SC2ActionPlannerInterface, package_exports.SC2ActionPlannerInterface)
        self.assertIs(SC2ActionType, package_exports.SC2ActionType)
        self.assertIs(SC2CommandAction, package_exports.SC2CommandAction)
        self.assertIs(SC2CommandPlan, package_exports.SC2CommandPlan)
        self.assertIs(
            SC2ExecutorBoundaryInterface,
            package_exports.SC2ExecutorBoundaryInterface,
        )
        self.assertIs(SC2ExecutionError, package_exports.SC2ExecutionError)
        self.assertIs(SC2ExecutionPlan, package_exports.SC2ExecutionPlan)
        self.assertIs(SC2PlanExecutionResult, package_exports.SC2PlanExecutionResult)
        self.assertIs(SC2RuntimeExecutor, package_exports.SC2RuntimeExecutor)
        self.assertIs(SC2RuntimeExecutorInterface, package_exports.SC2RuntimeExecutorInterface)
        self.assertIs(build_sc2_execution_plan, package_exports.build_sc2_execution_plan)

    def test_package_exports_stable_public_sc2_action_type_set(self) -> None:
        self.assertEqual(
            frozenset(
                {
                    "assign_workers",
                    "train_unit",
                    "build_structure",
                    "move_group",
                    "attack_move",
                    "repair",
                    "observe",
                },
            ),
            SC2_ACTION_TYPES,
        )
        self.assertEqual(
            SC2_ACTION_TYPES,
            frozenset(action_type.value for action_type in SC2ActionType),
        )

    def test_intent_action_type_mapping_uses_only_stable_public_names(self) -> None:
        self.assertEqual(
            {
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
            },
            SC2_INTENT_ACTION_TYPE_MAP,
        )
        for intent_name, action_types in SC2_INTENT_ACTION_TYPE_MAP.items():
            with self.subTest(intent_name=intent_name):
                self.assertTrue(action_types)
                self.assertLessEqual(set(action_types), SC2_ACTION_TYPES)

    def test_contract_package_import_does_not_load_toycraft_or_sc2_runtime(self) -> None:
        script = (
            "import json, sys; "
            "import starcraft_commander; "
            "print(json.dumps({"
            "'toycraft_loaded': 'toycraft_commander' in sys.modules, "
            "'contracts_loaded': 'starcraft_commander.contracts' in sys.modules, "
            "'executor_loaded': 'starcraft_commander.sc2_executor' in sys.modules"
            "}, sort_keys=True))"
        )

        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)

        self.assertFalse(payload["toycraft_loaded"])
        self.assertTrue(payload["contracts_loaded"])
        self.assertFalse(payload["executor_loaded"])

    def test_contract_schema_uses_ordered_semantic_json_ready_fields(self) -> None:
        plan = SC2CommandPlan(
            intent_name="TRAIN_ARMY",
            priority="high",
            constraints=("barracks_ready",),
            ordered_actions=(
                SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE", count=2),
                SC2CommandAction(
                    "attack_move",
                    subject="marines",
                    target="enemy_natural",
                ),
            ),
            audit={"source": "unit-test"},
        )

        payload = plan.to_dict()

        self.assertEqual("TRAIN_ARMY", payload["intent_name"])
        self.assertEqual(75, payload["priority"])
        self.assertEqual("high", payload["priority_label"])
        self.assertEqual(payload["ordered_actions"], payload["actions"])
        self.assertEqual(
            ["train_unit", "attack_move"],
            [action["action_type"] for action in payload["ordered_actions"]],
        )
        self.assertEqual(["barracks_ready"], payload["ordered_actions"][0]["constraints"])
        json.dumps(payload)

    def test_command_action_rejects_unknown_action_type_with_clear_error(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "SC2 command action action_type must be one of: .*Unknown action_type: 'warp_in_unit'",
        ):
            SC2CommandAction("warp_in_unit", subject="ZEALOT")

    def test_execution_error_rejects_unknown_action_type_with_clear_error(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "SC2 execution error action_type must be one of: .*Unknown action_type: 'mouse_click'",
        ):
            SC2ExecutionError(message="bad action", action_type="mouse_click")

    def test_result_contract_exposes_attempted_applied_skipped_errors_and_audit(self) -> None:
        action = SC2CommandAction(SC2ActionType.OBSERVE, subject="visible_state", count=0)
        plan = SC2ExecutionPlan(intent="SUMMARIZE_STATE", actions=(action,))
        result = SC2PlanExecutionResult(
            plan=plan,
            attempted=(action,),
            skipped=(action,),
            errors=(
                SC2ExecutionError(
                    message="observe unavailable",
                    action_type=SC2ActionType.OBSERVE,
                    action_index=0,
                    exception_type="RuntimeError",
                ),
            ),
            audit={"runtime_adapter": "FakeBot"},
        )

        payload = result.to_dict()

        self.assertFalse(result.success)
        self.assertEqual(result.attempted_actions, result.attempted)
        self.assertEqual(result.applied_actions, result.applied)
        self.assertEqual(result.skipped_actions, result.skipped)
        self.assertEqual(payload["attempted"], payload["attempted_actions"])
        self.assertEqual(payload["skipped"], payload["skipped_actions"])
        self.assertEqual("observe unavailable", payload["errors"][0]["message"])
        self.assertEqual({"runtime_adapter": "FakeBot"}, payload["audit"])
        json.dumps(payload)

    def test_empty_result_is_safe_failure_not_success(self) -> None:
        plan = SC2ExecutionPlan(intent_name="EMPTY", ordered_actions=())
        result = SC2PlanExecutionResult(plan=plan)

        self.assertFalse(result.success)
        self.assertEqual([], result.to_dict()["attempted"])


class SC2ActionPlannerTest(unittest.TestCase):
    def test_planner_implements_interface(self) -> None:
        self.assertIsInstance(DEFAULT_SC2_ACTION_PLANNER, SC2ActionPlannerInterface)

    def test_all_canonical_intents_emit_stable_public_action_type_names(self) -> None:
        for payload in CANONICAL_SC2_PLANNER_PAYLOADS:
            with self.subTest(intent_name=payload.intent):
                plan = build_sc2_execution_plan(payload)
                serialized_action_types = tuple(
                    action["action_type"]
                    for action in plan.to_dict()["ordered_actions"]
                )

                self.assertEqual(
                    SC2_INTENT_ACTION_TYPE_MAP[payload.intent],
                    serialized_action_types,
                )
                self.assertLessEqual(set(serialized_action_types), SC2_ACTION_TYPES)

    def test_generated_plans_reject_legacy_or_internal_action_type_names(self) -> None:
        for payload in CANONICAL_SC2_PLANNER_PAYLOADS:
            with self.subTest(intent_name=payload.intent):
                plan = build_sc2_execution_plan(payload)
                action_types = tuple(
                    action["action_type"]
                    for action in plan.to_dict()["ordered_actions"]
                )

                self.assertFalse(
                    LEGACY_INTERNAL_ACTION_TYPE_NAMES.intersection(action_types),
                    f"{payload.intent} emitted legacy/internal action types: {action_types}",
                )

    def test_train_worker_maps_to_command_center_scv_api_plan(self) -> None:
        plan = build_sc2_execution_plan(TrainWorkerIntent(count=2))

        self.assertEqual("TRAIN_WORKER", plan.intent)
        self.assertTrue(plan.requires_live_sc2)
        self.assertEqual(1, len(plan.actions))
        action = plan.actions[0]
        self.assertEqual(SC2ActionType.TRAIN_UNIT, action.action_type)
        self.assertEqual("SCV", action.subject)
        self.assertEqual(2, action.count)
        self.assertEqual("COMMANDCENTER", action.metadata["producer"])

    def test_build_structure_maps_to_sc2_structure_and_location_alias(self) -> None:
        plan = build_sc2_execution_plan(
            BuildStructureIntent(structure="Supply Depot", location="main ramp"),
        )

        self.assertEqual("BUILD_STRUCTURE", plan.intent)
        self.assertEqual(SC2ActionType.BUILD_STRUCTURE, plan.actions[0].action_type)
        self.assertEqual("SUPPLYDEPOT", plan.actions[0].subject)
        self.assertEqual("self_ramp", plan.actions[0].target)

    def test_train_army_maps_marine_to_barracks(self) -> None:
        plan = build_sc2_execution_plan(TrainArmyIntent(unit_type="Marine", count=3))

        self.assertEqual(SC2ActionType.TRAIN_UNIT, plan.actions[0].action_type)
        self.assertEqual("MARINE", plan.actions[0].subject)
        self.assertEqual("BARRACKS", plan.actions[0].metadata["producer"])

    def test_gather_resource_maps_to_worker_assignment(self) -> None:
        plan = build_sc2_execution_plan(
            GatherResourceIntent(resource="minerals", worker_count=4, base="main"),
        )

        self.assertEqual(SC2ActionType.ASSIGN_WORKERS, plan.actions[0].action_type)
        self.assertEqual("SCV", plan.actions[0].subject)
        self.assertEqual("minerals", plan.actions[0].target)
        self.assertEqual(4, plan.actions[0].count)

    def test_scout_defend_and_harass_map_to_unit_control_api_plans(self) -> None:
        scout = build_sc2_execution_plan(
            ScoutIntent(target="enemy main", unit_group="worker_scout"),
        )
        defend = build_sc2_execution_plan(
            DefendIntent(location="main ramp", unit_group="marines"),
        )
        harass = build_sc2_execution_plan(
            HarassIntent(target="enemy mineral line", unit_group="hellions"),
        )

        self.assertEqual(SC2ActionType.MOVE_GROUP, scout.actions[0].action_type)
        self.assertEqual("enemy_main", scout.actions[0].target)
        self.assertEqual(SC2ActionType.ATTACK_MOVE, defend.actions[0].action_type)
        self.assertEqual("self_ramp", defend.actions[0].target)
        self.assertEqual(SC2ActionType.ATTACK_MOVE, harass.actions[0].action_type)
        self.assertEqual("enemy_mineral_line", harass.actions[0].target)

    def test_summarize_state_is_observe_only_plan(self) -> None:
        plan = build_sc2_execution_plan(SummarizeStateIntent())

        self.assertEqual(SC2ActionType.OBSERVE, plan.actions[0].action_type)
        self.assertEqual(0, plan.actions[0].count)

    def test_mapping_payload_dicts_keeps_sc2_planner_decoupled_from_toy_runtime(self) -> None:
        plan = build_sc2_execution_plan(
            {
                "intent": "TRAIN_ARMY",
                "unit_type": "Marine",
                "count": 1,
                "priority": "high",
                "constraints": ["do_not_sacrifice_army"],
            },
        )

        self.assertEqual("high", plan.priority)
        self.assertEqual(("do_not_sacrifice_army",), plan.constraints)
        self.assertEqual("MARINE", plan.actions[0].subject)

    def test_plan_serializes_for_ui_or_api_logs(self) -> None:
        plan = build_sc2_execution_plan(TrainWorkerIntent(count=1))
        payload = plan.to_dict()

        self.assertEqual("TRAIN_WORKER", payload["intent"])
        self.assertEqual("train_unit", payload["actions"][0]["action_type"])
        self.assertIn("SC2 executor plans semantic API commands", payload["notes"][0])


class SC2RuntimeExecutorTest(unittest.TestCase):
    def test_runtime_executor_implements_interface(self) -> None:
        self.assertIsInstance(SC2RuntimeExecutor(), SC2RuntimeExecutorInterface)
        self.assertIsInstance(SC2RuntimeExecutor(), SC2ExecutorBoundaryInterface)

    def test_lifecycle_execute_contract_uses_bound_bot_and_preserves_order(self) -> None:
        class FakeBot:
            def __init__(self) -> None:
                self.calls = []

            async def on_start(self):
                self.calls.append(("on_start",))

            async def train_unit(self, action):
                self.calls.append(("train_unit", action.subject, action.count))
                return True

            async def attack_move(self, action):
                self.calls.append(("attack_move", action.subject, action.target))
                return True

            async def on_end(self):
                self.calls.append(("on_end",))

        async def run_lifecycle():
            bot = FakeBot()
            executor = SC2RuntimeExecutor()
            plan = SC2ExecutionPlan(
                intent="demo",
                actions=(
                    SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE", count=2),
                    SC2CommandAction(
                        SC2ActionType.ATTACK_MOVE,
                        subject="marines",
                        target="enemy_natural",
                    ),
                ),
            )

            await executor.start(bot)
            result = await executor.execute(plan)
            await executor.close()
            return bot, executor, result

        bot, executor, result = asyncio.run(run_lifecycle())

        self.assertFalse(executor.is_started)
        self.assertTrue(result.success)
        self.assertEqual(
            [
                ("on_start",),
                ("train_unit", "MARINE", 2),
                ("attack_move", "marines", "enemy_natural"),
                ("on_end",),
            ],
            bot.calls,
        )
        self.assertEqual(
            ["train_unit", "attack_move"],
            [action.action_type.value for action in result.attempted_actions],
        )
        json.dumps(result.to_dict())

    def test_lifecycle_execute_without_bound_bot_returns_structured_failure(self) -> None:
        plan = build_sc2_execution_plan(TrainWorkerIntent(count=1))

        result = asyncio.run(SC2RuntimeExecutor().execute(plan))

        self.assertFalse(result.success)
        self.assertEqual((), result.attempted_actions)
        self.assertEqual(plan.actions, result.skipped_actions)
        self.assertEqual("MissingRuntimeAdapter", result.errors[0].exception_type)
        self.assertEqual(None, result.to_dict()["audit"]["runtime_adapter"])
        json.dumps(result.to_dict())

    def test_runtime_executor_calls_bot_action_methods(self) -> None:
        class FakeBot:
            def __init__(self) -> None:
                self.calls = []

            async def train_unit(self, action):
                self.calls.append(("train_unit", action.subject, action.count))
                return True

            async def attack_move(self, action):
                self.calls.append(("attack_move", action.subject, action.target))
                return True

        plan = SC2ExecutionPlan(
            intent="demo",
            priority="normal",
            actions=(
                SC2CommandAction(SC2ActionType.TRAIN_UNIT, subject="MARINE", count=2),
                SC2CommandAction(
                    SC2ActionType.ATTACK_MOVE,
                    subject="marines",
                    target="enemy_natural",
                ),
            ),
        )
        bot = FakeBot()

        result = asyncio.run(SC2RuntimeExecutor().execute_plan(bot, plan))

        self.assertTrue(result.success)
        self.assertEqual(
            [
                ("train_unit", "MARINE", 2),
                ("attack_move", "marines", "enemy_natural"),
            ],
            bot.calls,
        )
        self.assertEqual(2, len(result.applied_actions))

    def test_runtime_executor_skips_missing_bot_capabilities(self) -> None:
        class FakeBot:
            pass

        plan = build_sc2_execution_plan(TrainWorkerIntent(count=1))

        result = asyncio.run(SC2RuntimeExecutor().execute_plan(FakeBot(), plan))

        self.assertFalse(result.success)
        self.assertEqual((), result.applied_actions)
        self.assertEqual(plan.actions, result.skipped_actions)
