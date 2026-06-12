import asyncio
import json
import pathlib
import subprocess
import sys
import unittest

import broodwar_commander as package_exports
from broodwar_commander.bw_executor import (
    BW_ACTION_TYPES,
    BW_INTENT_ACTION_TYPE_MAP,
    BW_PRODUCER_TYPE_IDS,
    BW_RUNTIME_ADAPTER_REMAINING_STEP,
    BW_SEMANTIC_TARGET_NAMES,
    BW_STRUCTURE_TYPE_IDS,
    BW_TARGET_ALIASES,
    BW_UNIT_TYPE_IDS,
    BWActionPlanner,
    BWActionPlannerInterface,
    BWActionReport,
    BWActionType,
    BWCommandAction,
    BWCommandPlan,
    BWExecutionError,
    BWExecutionPlan,
    BWExecutorBoundaryInterface,
    BWPlanExecutionResult,
    BWRuntimeExecutor,
    BWRuntimeExecutorInterface,
    DEFAULT_BW_ACTION_PLANNER,
    build_bw_execution_plan,
)
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
from toycraft_commander.map import MAP_LOCATION_NAMES
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


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The typed TrainArmyIntent DSL only allows Marine today, so the Vulture
# payload uses the Mapping form the planner also accepts. That keeps the
# direct Terran_Vulture pin independent of ToyCraft DSL evolution.
VULTURE_TRAIN_PAYLOAD = {"intent": "TRAIN_ARMY", "unit_type": "Vulture", "count": 3}

CANONICAL_BW_PLANNER_CASES = (
    (
        GatherResourceIntent(resource="minerals", worker_count=4, base="main"),
        ("assign_workers", "Terran_SCV", "minerals", 4),
    ),
    (
        BuildStructureIntent(structure="Supply Depot", location="main ramp"),
        ("build_structure", "Terran_Supply_Depot", "self_ramp", 1),
    ),
    (
        TrainWorkerIntent(count=2),
        ("train_unit", "Terran_SCV", "", 2),
    ),
    (
        TrainArmyIntent(unit_type="Marine", count=3),
        ("train_unit", "Terran_Marine", "", 3),
    ),
    (
        ScoutIntent(target="enemy main", unit_group="worker_scout"),
        ("move_group", "worker_scout", "enemy_main", 1),
    ),
    (
        SummarizeStateIntent(),
        ("observe", "visible_state", "narrator_snapshot", 0),
    ),
    (
        DefendIntent(location="main ramp", unit_group="marines"),
        ("attack_move", "marines", "self_ramp", 1),
    ),
    (
        RepairIntent(target="front bunker", worker_count=2),
        ("repair", "Terran_SCV", "front bunker", 2),
    ),
    (
        ExpandIntent(location="natural expansion"),
        ("build_structure", "Terran_Command_Center", "self_natural", 1),
    ),
    (
        HarassIntent(target="enemy mineral line", unit_group="vultures"),
        ("attack_move", "vultures", "enemy_mineral_line", 1),
    ),
)


class BroodWarPackageSurfaceTest(unittest.TestCase):
    def test_package_exports_real_bw_executor_boundary(self) -> None:
        self.assertIs(BW_ACTION_TYPES, package_exports.BW_ACTION_TYPES)
        self.assertIs(BW_INTENT_ACTION_TYPE_MAP, package_exports.BW_INTENT_ACTION_TYPE_MAP)
        self.assertIs(BW_PRODUCER_TYPE_IDS, package_exports.BW_PRODUCER_TYPE_IDS)
        self.assertIs(
            BW_RUNTIME_ADAPTER_REMAINING_STEP,
            package_exports.BW_RUNTIME_ADAPTER_REMAINING_STEP,
        )
        self.assertIs(BW_SEMANTIC_TARGET_NAMES, package_exports.BW_SEMANTIC_TARGET_NAMES)
        self.assertIs(BW_STRUCTURE_TYPE_IDS, package_exports.BW_STRUCTURE_TYPE_IDS)
        self.assertIs(BW_TARGET_ALIASES, package_exports.BW_TARGET_ALIASES)
        self.assertIs(BW_UNIT_TYPE_IDS, package_exports.BW_UNIT_TYPE_IDS)
        self.assertIs(BWActionPlanner, package_exports.BWActionPlanner)
        self.assertIs(BWActionPlannerInterface, package_exports.BWActionPlannerInterface)
        self.assertIs(BWActionReport, package_exports.BWActionReport)
        self.assertIs(BWActionType, package_exports.BWActionType)
        self.assertIs(BWCommandAction, package_exports.BWCommandAction)
        self.assertIs(BWCommandPlan, package_exports.BWCommandPlan)
        self.assertIs(BWExecutionError, package_exports.BWExecutionError)
        self.assertIs(BWExecutionPlan, package_exports.BWExecutionPlan)
        self.assertIs(
            BWExecutorBoundaryInterface,
            package_exports.BWExecutorBoundaryInterface,
        )
        self.assertIs(BWPlanExecutionResult, package_exports.BWPlanExecutionResult)
        self.assertIs(BWRuntimeExecutor, package_exports.BWRuntimeExecutor)
        self.assertIs(BWRuntimeExecutorInterface, package_exports.BWRuntimeExecutorInterface)
        self.assertIs(DEFAULT_BW_ACTION_PLANNER, package_exports.DEFAULT_BW_ACTION_PLANNER)
        self.assertIs(build_bw_execution_plan, package_exports.build_bw_execution_plan)

    def test_bw_boundary_reuses_game_agnostic_sc2_contracts(self) -> None:
        contract_aliases = (
            ("BW_ACTION_TYPES", BW_ACTION_TYPES, SC2_ACTION_TYPES),
            ("BWActionReport", BWActionReport, SC2ActionReport),
            ("BWActionType", BWActionType, SC2ActionType),
            ("BWCommandAction", BWCommandAction, SC2CommandAction),
            ("BWCommandPlan", BWCommandPlan, SC2ExecutionPlan),
            ("BWExecutionError", BWExecutionError, SC2ExecutionError),
            ("BWExecutionPlan", BWExecutionPlan, SC2ExecutionPlan),
            ("BWPlanExecutionResult", BWPlanExecutionResult, SC2PlanExecutionResult),
        )
        for name, bw_symbol, sc2_symbol in contract_aliases:
            with self.subTest(symbol=name):
                self.assertIs(bw_symbol, sc2_symbol)

    def test_semantic_target_vocabulary_is_shared_object_identically(self) -> None:
        self.assertIs(BW_TARGET_ALIASES, SC2_TARGET_ALIASES)
        self.assertIs(BW_SEMANTIC_TARGET_NAMES, SC2_SEMANTIC_TARGET_NAMES)
        self.assertIs(BW_INTENT_ACTION_TYPE_MAP, SC2_INTENT_ACTION_TYPE_MAP)

    def test_boundary_protocols_are_shared_with_sc2(self) -> None:
        self.assertIs(BWActionPlannerInterface, SC2ActionPlannerInterface)
        self.assertIs(BWRuntimeExecutorInterface, SC2RuntimeExecutorInterface)
        self.assertIs(BWExecutorBoundaryInterface, SC2ExecutorBoundaryInterface)

    def test_package_import_loads_no_optional_or_runtime_modules(self) -> None:
        script = (
            "import json, sys; "
            "import broodwar_commander; "
            "print(json.dumps({"
            "'anthropic_loaded': 'anthropic' in sys.modules, "
            "'sc2_loaded': 'sc2' in sys.modules, "
            "'sounddevice_loaded': 'sounddevice' in sys.modules, "
            "'faster_whisper_loaded': 'faster_whisper' in sys.modules, "
            "'toycraft_loaded': 'toycraft_commander' in sys.modules, "
            "'sc2_executor_loaded': 'starcraft_commander.sc2_executor' in sys.modules, "
            "'bw_executor_loaded': 'broodwar_commander.bw_executor' in sys.modules, "
            "'contracts_loaded': 'starcraft_commander.contracts' in sys.modules"
            "}, sort_keys=True))"
        )

        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        payload = json.loads(completed.stdout)

        self.assertFalse(payload["anthropic_loaded"])
        self.assertFalse(payload["sc2_loaded"])
        self.assertFalse(payload["sounddevice_loaded"])
        self.assertFalse(payload["faster_whisper_loaded"])
        self.assertFalse(payload["toycraft_loaded"])
        self.assertFalse(payload["sc2_executor_loaded"])
        self.assertFalse(payload["bw_executor_loaded"])
        self.assertTrue(payload["contracts_loaded"])

    def test_remaining_step_documents_real_bwapi_adapter_honestly(self) -> None:
        self.assertIn("BWAPI", BW_RUNTIME_ADAPTER_REMAINING_STEP)
        self.assertIn("python_sc2_adapter", BW_RUNTIME_ADAPTER_REMAINING_STEP)
        for method_name in (
            "assign_workers",
            "build_structure",
            "train_unit",
            "move_group",
            "attack_move",
            "repair",
            "observe",
            "execute_commander_action",
        ):
            with self.subTest(method=method_name):
                self.assertIn(method_name, BW_RUNTIME_ADAPTER_REMAINING_STEP)


class BWVocabularyRegistryTest(unittest.TestCase):
    def test_unit_type_registry_uses_bwapi_terran_names(self) -> None:
        self.assertEqual(
            {
                "SCV": "Terran_SCV",
                "Marine": "Terran_Marine",
                "Vulture": "Terran_Vulture",
            },
            BW_UNIT_TYPE_IDS,
        )

    def test_vulture_maps_directly_without_hellion_stand_in(self) -> None:
        self.assertEqual("Terran_Vulture", BW_UNIT_TYPE_IDS["Vulture"])
        for type_name in BW_UNIT_TYPE_IDS.values():
            with self.subTest(type_name=type_name):
                self.assertNotIn("HELLION", type_name.upper())

    def test_structure_type_registry_uses_bwapi_terran_names(self) -> None:
        self.assertEqual(
            {
                "Barracks": "Terran_Barracks",
                "Bunker": "Terran_Bunker",
                "Command Center": "Terran_Command_Center",
                "Factory": "Terran_Factory",
                "Refinery": "Terran_Refinery",
                "Supply Depot": "Terran_Supply_Depot",
            },
            BW_STRUCTURE_TYPE_IDS,
        )

    def test_producer_registry_points_each_unit_at_a_known_structure(self) -> None:
        self.assertEqual(
            {
                "SCV": "Terran_Command_Center",
                "Marine": "Terran_Barracks",
                "Vulture": "Terran_Factory",
            },
            BW_PRODUCER_TYPE_IDS,
        )
        self.assertEqual(set(BW_UNIT_TYPE_IDS), set(BW_PRODUCER_TYPE_IDS))
        for unit_name, producer in BW_PRODUCER_TYPE_IDS.items():
            with self.subTest(unit=unit_name):
                self.assertIn(producer, BW_STRUCTURE_TYPE_IDS.values())


class BWActionPlannerTest(unittest.TestCase):
    def test_planner_implements_shared_interface(self) -> None:
        self.assertIsInstance(DEFAULT_BW_ACTION_PLANNER, BWActionPlannerInterface)
        self.assertIsInstance(DEFAULT_BW_ACTION_PLANNER, SC2ActionPlannerInterface)
        self.assertIsInstance(BWActionPlanner(), BWActionPlannerInterface)

    def test_all_canonical_intents_plan_bwapi_commands(self) -> None:
        for payload, expected in CANONICAL_BW_PLANNER_CASES:
            with self.subTest(intent_name=payload.intent):
                expected_type, expected_subject, expected_target, expected_count = expected
                plan = build_bw_execution_plan(payload)

                self.assertEqual(payload.intent, plan.intent_name)
                self.assertEqual(1, len(plan.ordered_actions))
                action = plan.ordered_actions[0]
                self.assertEqual(expected_type, action.action_type.value)
                self.assertEqual(expected_subject, action.subject)
                self.assertEqual(expected_target, action.target)
                self.assertEqual(expected_count, action.count)
                self.assertEqual(
                    BW_INTENT_ACTION_TYPE_MAP[payload.intent],
                    tuple(
                        serialized["action_type"]
                        for serialized in plan.to_dict()["ordered_actions"]
                    ),
                )

    def test_train_army_vulture_maps_directly_to_terran_vulture(self) -> None:
        plan = build_bw_execution_plan(VULTURE_TRAIN_PAYLOAD)

        action = plan.ordered_actions[0]
        self.assertEqual(BWActionType.TRAIN_UNIT, action.action_type)
        self.assertEqual("Terran_Vulture", action.subject)
        self.assertEqual(3, action.count)
        self.assertEqual("Terran_Factory", action.metadata["producer"])
        self.assertEqual("Vulture", action.metadata["source_unit"])
        joined_notes = " ".join(plan.notes)
        self.assertIn("Terran_Vulture", joined_notes)
        self.assertIn("directly", joined_notes)
        self.assertIn("no Hellion stand-in", joined_notes)

    def test_train_worker_maps_to_command_center_scv_plan(self) -> None:
        plan = build_bw_execution_plan(TrainWorkerIntent(count=2))

        self.assertEqual("TRAIN_WORKER", plan.intent)
        self.assertTrue(plan.requires_live_sc2)
        action = plan.ordered_actions[0]
        self.assertEqual(BWActionType.TRAIN_UNIT, action.action_type)
        self.assertEqual("Terran_SCV", action.subject)
        self.assertEqual("Terran_Command_Center", action.metadata["producer"])

    def test_plan_notes_declare_semantic_bwapi_commands(self) -> None:
        plan = build_bw_execution_plan(TrainArmyIntent(unit_type="Marine", count=1))

        self.assertIn("Brood War executor plans semantic BWAPI commands", plan.notes[0])
        self.assertIn("BWAPI runtime adapter", plan.notes[1])
        for note in plan.notes:
            with self.subTest(note=note):
                self.assertNotIn("Vulture", note)

    def test_plan_audit_marks_brood_war_bwapi_dialect(self) -> None:
        plan = build_bw_execution_plan(SummarizeStateIntent())

        self.assertEqual(
            {"game": "brood_war", "command_dialect": "bwapi"},
            dict(plan.audit),
        )

    def test_mapping_payloads_keep_planner_decoupled_from_toy_runtime(self) -> None:
        plan = build_bw_execution_plan(
            {
                "intent": "TRAIN_ARMY",
                "unit_type": "Vulture",
                "count": 1,
                "priority": "high",
                "constraints": ["do_not_sacrifice_army"],
            },
        )

        self.assertEqual("high", plan.priority)
        self.assertEqual(("do_not_sacrifice_army",), plan.constraints)
        self.assertEqual("Terran_Vulture", plan.ordered_actions[0].subject)

    def test_plan_to_dict_json_round_trips(self) -> None:
        plan = build_bw_execution_plan(
            BuildStructureIntent(structure="Barracks", location="main base"),
        )

        payload = plan.to_dict()

        self.assertEqual(payload, json.loads(json.dumps(payload)))
        self.assertEqual("BUILD_STRUCTURE", payload["intent_name"])
        self.assertEqual("Terran_Barracks", payload["ordered_actions"][0]["subject"])
        self.assertEqual("self_main", payload["ordered_actions"][0]["target"])
        self.assertEqual(payload["ordered_actions"], payload["actions"])
        self.assertEqual({"game": "brood_war", "command_dialect": "bwapi"}, payload["audit"])

    def test_location_intents_reject_unknown_targets_with_alternatives(self) -> None:
        unknown_payloads = (
            BuildStructureIntent(structure="Supply Depot", location="atlantis"),
            ExpandIntent(location="atlantis"),
            ScoutIntent(target="atlantis", unit_group="worker_scout"),
            DefendIntent(location="atlantis", unit_group="marines"),
            HarassIntent(target="atlantis", unit_group="vultures"),
        )

        for payload in unknown_payloads:
            with self.subTest(intent_name=payload.intent):
                with self.assertRaises(ValueError) as caught:
                    build_bw_execution_plan(payload)

                message = str(caught.exception)
                self.assertIn("unsupported Brood War target location: 'atlantis'", message)
                self.assertIn("Supported targets:", message)
                self.assertIn("natural expansion", message)
                self.assertIn("enemy_main", message)

    def test_every_canonical_map_location_plans_to_semantic_target(self) -> None:
        for location_name in MAP_LOCATION_NAMES:
            with self.subTest(location=location_name):
                plan = build_bw_execution_plan(
                    DefendIntent(location=location_name, unit_group="marines"),
                )

                self.assertIn(plan.ordered_actions[0].target, BW_SEMANTIC_TARGET_NAMES)

    def test_already_semantic_targets_pass_through_unchanged(self) -> None:
        for semantic_target in sorted(BW_SEMANTIC_TARGET_NAMES):
            with self.subTest(target=semantic_target):
                plan = build_bw_execution_plan(
                    ScoutIntent(target=semantic_target, unit_group="worker_scout"),
                )

                self.assertEqual(semantic_target, plan.ordered_actions[0].target)

    def test_repair_target_and_gather_resource_stay_verbatim(self) -> None:
        repair = build_bw_execution_plan(
            RepairIntent(target="front bunker", worker_count=2),
        )
        gather = build_bw_execution_plan(
            GatherResourceIntent(resource="minerals", worker_count=4, base="main"),
        )

        self.assertEqual("front bunker", repair.ordered_actions[0].target)
        self.assertEqual("minerals", gather.ordered_actions[0].target)

    def test_unknown_unit_structure_and_intent_are_rejected(self) -> None:
        cases = (
            (
                {"intent": "TRAIN_ARMY", "unit_type": "Zealot", "count": 1},
                "unsupported Brood War unit: Zealot",
            ),
            (
                {"intent": "BUILD_STRUCTURE", "structure": "Pylon", "location": "main base"},
                "unsupported Brood War structure: Pylon",
            ),
            (
                {"intent": "DANCE"},
                "unsupported Brood War intent payload: DANCE",
            ),
        )
        for payload, expected_message in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError) as caught:
                    build_bw_execution_plan(payload)

                self.assertIn(expected_message, str(caught.exception))

    def test_missing_required_fields_are_rejected_with_field_name(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "Brood War intent payload missing required field: unit_type",
        ):
            build_bw_execution_plan({"intent": "TRAIN_ARMY", "count": 1})


class BWRuntimeExecutorTest(unittest.TestCase):
    def test_runtime_executor_reuses_sc2_dispatch_without_forking(self) -> None:
        executor = BWRuntimeExecutor()

        self.assertIsInstance(executor, BWRuntimeExecutorInterface)
        self.assertIsInstance(executor, BWExecutorBoundaryInterface)
        self.assertIsInstance(executor, SC2RuntimeExecutor)
        for method_name in ("start", "execute", "execute_plan", "close"):
            with self.subTest(method=method_name):
                self.assertIs(
                    getattr(BWRuntimeExecutor, method_name),
                    getattr(SC2RuntimeExecutor, method_name),
                )

    def test_lifecycle_execute_contract_uses_bound_bot_and_preserves_order(self) -> None:
        class RecordingBWAPIBot:
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
            bot = RecordingBWAPIBot()
            executor = BWRuntimeExecutor()
            plan = BWExecutionPlan(
                intent="demo",
                actions=(
                    BWCommandAction(
                        BWActionType.TRAIN_UNIT,
                        subject="Terran_Vulture",
                        count=2,
                    ),
                    BWCommandAction(
                        BWActionType.ATTACK_MOVE,
                        subject="vultures",
                        target="enemy_mineral_line",
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
                ("train_unit", "Terran_Vulture", 2),
                ("attack_move", "vultures", "enemy_mineral_line"),
                ("on_end",),
            ],
            bot.calls,
        )
        self.assertEqual(
            ["train_unit", "attack_move"],
            [action.action_type.value for action in result.attempted_actions],
        )
        payload = result.to_dict()
        self.assertEqual(payload, json.loads(json.dumps(payload)))

    def test_execute_without_bound_runtime_returns_structured_failure(self) -> None:
        plan = build_bw_execution_plan(TrainWorkerIntent(count=1))

        result = asyncio.run(BWRuntimeExecutor().execute(plan))

        self.assertFalse(result.success)
        self.assertEqual((), result.attempted_actions)
        self.assertEqual(plan.ordered_actions, result.skipped_actions)
        self.assertEqual("MissingRuntimeAdapter", result.errors[0].exception_type)
        self.assertIsNone(result.to_dict()["audit"]["runtime_adapter"])

    def test_missing_bot_capability_error_shape_is_preserved(self) -> None:
        class EmptyBWAPIBot:
            pass

        plan = build_bw_execution_plan(TrainWorkerIntent(count=1))

        result = asyncio.run(BWRuntimeExecutor().execute_plan(EmptyBWAPIBot(), plan))

        self.assertFalse(result.success)
        self.assertEqual((), result.applied_actions)
        self.assertEqual(plan.ordered_actions, result.skipped_actions)
        self.assertEqual(1, len(result.errors))
        error = result.errors[0]
        self.assertEqual("MissingBotCapability", error.exception_type)
        self.assertEqual(BWActionType.TRAIN_UNIT, error.action_type)
        self.assertEqual(0, error.action_index)
        self.assertEqual({"expected_method": "train_unit"}, dict(error.metadata))
        self.assertIn("execute_commander_action", error.message)
        json.dumps(result.to_dict())

    def test_execute_commander_action_fallback_is_dispatched(self) -> None:
        class FallbackBWAPIBot:
            def __init__(self) -> None:
                self.calls = []

            async def execute_commander_action(self, action):
                self.calls.append((action.action_type.value, action.subject))
                return True

        plan = build_bw_execution_plan(VULTURE_TRAIN_PAYLOAD)
        bot = FallbackBWAPIBot()

        result = asyncio.run(BWRuntimeExecutor().execute_plan(bot, plan))

        self.assertTrue(result.success)
        self.assertEqual([("train_unit", "Terran_Vulture")], bot.calls)

    def test_observe_mapping_return_surfaces_observation_in_audit(self) -> None:
        snapshot = {"minerals": 250, "gas": 88, "supply_used": 17}

        class ObservingBWAPIBot:
            async def observe(self, action):
                return dict(snapshot)

        plan = build_bw_execution_plan(SummarizeStateIntent())

        result = asyncio.run(BWRuntimeExecutor().execute_plan(ObservingBWAPIBot(), plan))

        self.assertTrue(result.success)
        self.assertEqual(plan.ordered_actions, result.applied_actions)
        self.assertEqual({"0": snapshot}, result.audit["observations"])
        json.dumps(result.to_dict())

    def test_partial_action_report_downgrades_success_with_structured_error(self) -> None:
        class PartialBWAPIBot:
            async def train_unit(self, action):
                return BWActionReport(
                    True, requested_count=3, issued_count=1, detail="unaffordable"
                )

        plan = build_bw_execution_plan(VULTURE_TRAIN_PAYLOAD)

        result = asyncio.run(BWRuntimeExecutor().execute_plan(PartialBWAPIBot(), plan))

        self.assertFalse(result.success)
        self.assertEqual(plan.ordered_actions, result.applied_actions)
        self.assertEqual(1, len(result.errors))
        error = result.errors[0]
        self.assertEqual("PartialActionApplication", error.exception_type)
        self.assertEqual(3, error.metadata["requested_count"])
        self.assertEqual(1, error.metadata["issued_count"])

    def test_raising_bot_method_is_captured_as_structured_error(self) -> None:
        class RaisingBWAPIBot:
            async def train_unit(self, action):
                raise RuntimeError("supply blocked")

        plan = build_bw_execution_plan(TrainWorkerIntent(count=1))

        result = asyncio.run(BWRuntimeExecutor().execute_plan(RaisingBWAPIBot(), plan))

        self.assertFalse(result.success)
        self.assertEqual(plan.ordered_actions, result.skipped_actions)
        self.assertEqual("RuntimeError", result.errors[0].exception_type)
        self.assertEqual("supply blocked", result.errors[0].message)
        self.assertEqual(0, result.errors[0].action_index)

    def test_start_clears_lifecycle_errors_from_previous_cycle(self) -> None:
        class FailingStartBWAPIBot:
            async def on_start(self):
                raise RuntimeError("hook boom")

            async def train_unit(self, action):
                return True

        class HealthyBWAPIBot:
            async def train_unit(self, action):
                return True

        plan = build_bw_execution_plan(TrainWorkerIntent(count=1))

        async def run_two_cycles():
            executor = BWRuntimeExecutor()
            await executor.start(FailingStartBWAPIBot())
            poisoned = await executor.execute(plan)
            await executor.start(HealthyBWAPIBot())
            clean = await executor.execute(plan)
            return poisoned, clean

        poisoned, clean = asyncio.run(run_two_cycles())

        self.assertFalse(poisoned.success)
        self.assertEqual("RuntimeError", poisoned.errors[0].exception_type)
        self.assertEqual(
            {"lifecycle_hook": "on_start"},
            dict(poisoned.errors[0].metadata),
        )
        self.assertTrue(clean.success)
        self.assertEqual((), clean.errors)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
