import unittest

import toycraft_commander as package_exports
from toycraft_commander.feasibility import ProductionOrder, ToyCraftState
from toycraft_commander.intents import GatherResourceIntent, IntentValidationResult
from toycraft_commander.resources import ResourceState, SupplyState
from toycraft_commander.tactical_controller import (
    DEFAULT_TACTICAL_CONTROLLER,
    TacticalControllerInterface,
    ToyCraftTacticalController,
)


class TacticalControllerSurfaceTest(unittest.TestCase):
    def test_package_exports_tactical_controller_boundary(self) -> None:
        self.assertIs(
            DEFAULT_TACTICAL_CONTROLLER,
            package_exports.DEFAULT_TACTICAL_CONTROLLER,
        )
        self.assertIs(
            TacticalControllerInterface,
            package_exports.TacticalControllerInterface,
        )
        self.assertIs(ToyCraftTacticalController, package_exports.ToyCraftTacticalController)

    def test_default_controller_validates_executes_and_advances_time(self) -> None:
        controller = DEFAULT_TACTICAL_CONTROLLER
        state = ToyCraftState(
            resources=ResourceState(minerals=50, gas=0),
            supply=SupplyState(used_supply=5, supply_capacity=15),
            units={"SCV": 4},
            structures={"Command Center": 1},
            production_queues={"Command Center": 1},
            production_orders=(
                ProductionOrder(
                    unit_name="SCV",
                    producer="Command Center",
                    remaining_seconds=5,
                ),
            ),
        )
        payload = GatherResourceIntent(resource="minerals", worker_count=1, base="main")

        self.assertIsInstance(controller, TacticalControllerInterface)

        validation = controller.validate(payload, state)
        execution = controller.execute(payload, state)
        time_result = controller.advance_time(execution.after_state, 5)

        self.assertTrue(validation.executable)
        self.assertTrue(execution.executed)
        self.assertEqual(58, execution.after_state.resources.minerals)
        self.assertTrue(time_result.executed)
        self.assertEqual(5, time_result.after_state.units["SCV"])

    def test_controller_delegates_to_configured_validator_and_rule_engine(self) -> None:
        payload = GatherResourceIntent(resource="minerals", worker_count=1, base="main")
        state = ToyCraftState()
        validation_result = IntentValidationResult(executable=True, payload=payload)
        execution_result = object()
        time_result = object()

        class RecordingValidator:
            def __init__(self) -> None:
                self.calls = []

            def validate_intent(self, received_payload, received_state):
                self.calls.append((received_payload, received_state))
                return validation_result

        class RecordingRuleEngine:
            def __init__(self) -> None:
                self.execute_calls = []
                self.advance_calls = []

            def execute_intent(self, received_payload, received_state):
                self.execute_calls.append((received_payload, received_state))
                return execution_result

            def advance_time(self, received_state, seconds):
                self.advance_calls.append((received_state, seconds))
                return time_result

        validator = RecordingValidator()
        rule_engine = RecordingRuleEngine()
        controller = ToyCraftTacticalController(
            validator=validator,
            rule_engine=rule_engine,
        )

        self.assertEqual(validation_result, controller.validate(payload, state))
        self.assertIs(execution_result, controller.execute(payload, state))
        self.assertIs(time_result, controller.advance_time(state, 7))
        self.assertEqual([(payload, state)], validator.calls)
        self.assertEqual([(payload, state)], rule_engine.execute_calls)
        self.assertEqual([(state, 7)], rule_engine.advance_calls)


if __name__ == "__main__":
    unittest.main()
