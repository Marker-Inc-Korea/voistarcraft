import asyncio
import unittest

import starcraft_commander as package_exports
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
from toycraft_commander.intents import (
    BuildStructureIntent,
    DefendIntent,
    GatherResourceIntent,
    HarassIntent,
    ScoutIntent,
    SummarizeStateIntent,
    TrainArmyIntent,
    TrainWorkerIntent,
)


class StarCraftCommanderPackageSurfaceTest(unittest.TestCase):
    def test_package_exports_real_sc2_executor_boundary(self) -> None:
        self.assertIs(DEFAULT_SC2_ACTION_PLANNER, package_exports.DEFAULT_SC2_ACTION_PLANNER)
        self.assertIs(SC2ActionPlanner, package_exports.SC2ActionPlanner)
        self.assertIs(SC2ActionPlannerInterface, package_exports.SC2ActionPlannerInterface)
        self.assertIs(SC2ActionType, package_exports.SC2ActionType)
        self.assertIs(SC2CommandAction, package_exports.SC2CommandAction)
        self.assertIs(SC2ExecutionPlan, package_exports.SC2ExecutionPlan)
        self.assertIs(SC2PlanExecutionResult, package_exports.SC2PlanExecutionResult)
        self.assertIs(SC2RuntimeExecutor, package_exports.SC2RuntimeExecutor)
        self.assertIs(SC2RuntimeExecutorInterface, package_exports.SC2RuntimeExecutorInterface)
        self.assertIs(build_sc2_execution_plan, package_exports.build_sc2_execution_plan)


class SC2ActionPlannerTest(unittest.TestCase):
    def test_planner_implements_interface(self) -> None:
        self.assertIsInstance(DEFAULT_SC2_ACTION_PLANNER, SC2ActionPlannerInterface)

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
