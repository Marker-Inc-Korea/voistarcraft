import unittest

import toycraft_commander as package_exports
from toycraft_commander.executor import (
    DEFAULT_TOYCRAFT_EXECUTOR,
    execute_toycraft_intent,
)
from toycraft_commander.failure import (
    build_parsing_failure_report,
    build_rule_execution_failure_report,
)
from toycraft_commander.feasibility import DEFAULT_FEASIBILITY_VALIDATOR, ToyCraftState
from toycraft_commander.intents import (
    FeasibilityErrorReason,
    FeasibilityIssue,
    GatherResourceIntent,
    IntentValidationResult,
)
from toycraft_commander.interpreter import (
    DEFAULT_COMMAND_INTERPRETER,
    CommandInterpretationResult,
)
from toycraft_commander.pipeline import (
    DEFAULT_COMMAND_PIPELINE_NARRATOR,
    DEFAULT_COMMAND_PROCESSING_PIPELINE,
    CommandPipelineNarrator,
    CommandPipelineNarratorInterface,
    CommandProcessingPipeline,
    CommandProcessingPipelineInterface,
    CommandProcessingRequest,
    CommandProcessingResponse,
    CommandProcessingStatus,
    process_command,
)
from toycraft_commander.resources import ResourceState, SupplyState


def pipeline_state() -> ToyCraftState:
    return ToyCraftState(
        resources=ResourceState(minerals=50, gas=0),
        supply=SupplyState(used_supply=4, supply_capacity=15),
        units={"SCV": 4},
        structures={"Command Center": 1},
    )


class CommandProcessingPipelineSurfaceTest(unittest.TestCase):
    def test_package_exports_pipeline_contract(self) -> None:
        self.assertIs(
            DEFAULT_COMMAND_PIPELINE_NARRATOR,
            package_exports.DEFAULT_COMMAND_PIPELINE_NARRATOR,
        )
        self.assertIs(
            DEFAULT_COMMAND_PROCESSING_PIPELINE,
            package_exports.DEFAULT_COMMAND_PROCESSING_PIPELINE,
        )
        self.assertIs(CommandPipelineNarrator, package_exports.CommandPipelineNarrator)
        self.assertIs(
            CommandPipelineNarratorInterface,
            package_exports.CommandPipelineNarratorInterface,
        )
        self.assertIs(CommandProcessingPipeline, package_exports.CommandProcessingPipeline)
        self.assertIs(
            CommandProcessingPipelineInterface,
            package_exports.CommandProcessingPipelineInterface,
        )
        self.assertIs(CommandProcessingRequest, package_exports.CommandProcessingRequest)
        self.assertIs(CommandProcessingResponse, package_exports.CommandProcessingResponse)
        self.assertIs(CommandProcessingStatus, package_exports.CommandProcessingStatus)
        self.assertIs(process_command, package_exports.process_command)
        self.assertIs(
            build_rule_execution_failure_report,
            package_exports.build_rule_execution_failure_report,
        )

    def test_default_pipeline_implements_interface_and_processes_command(self) -> None:
        state = pipeline_state()

        self.assertIsInstance(
            DEFAULT_COMMAND_PROCESSING_PIPELINE,
            CommandProcessingPipelineInterface,
        )

        response = process_command("미네랄에 일꾼 두 기 붙여", state)

        self.assertEqual(CommandProcessingStatus.EXECUTED, response.status)
        self.assertTrue(response.executed)
        self.assertTrue(response.state_changed)
        self.assertEqual("GATHER_RESOURCE", response.intent)
        self.assertEqual(66, response.after_state.resources.minerals)
        self.assertIsNotNone(response.validation)
        self.assertIsNotNone(response.execution)
        self.assertIsNotNone(response.narrator_response)
        self.assertIn("자원 채취", response.narration)

        payload = response.to_dict()
        self.assertEqual("executed", payload["status"])
        self.assertEqual("GATHER_RESOURCE", payload["intent_dsl"]["intent"])
        self.assertEqual("executable", payload["validation"]["status"])
        self.assertEqual("GATHER_RESOURCE", payload["execution"]["intent"])
        self.assertTrue(payload["execution"]["executed"])
        self.assertIn(
            "gather_resource",
            [
                action["action_type"]
                for action in payload["execution"]["executed_actions"]
            ],
        )
        self.assertTrue(payload["execution"]["state_delta"]["has_changes"])


class CommandProcessingPipelineIntegrationTest(unittest.TestCase):
    def test_successful_command_flow_calls_real_components_in_order(self) -> None:
        state = pipeline_state()
        calls = []

        class RecordingInterpreter:
            def interpret(self, command_text):
                result = DEFAULT_COMMAND_INTERPRETER.interpret(command_text)
                calls.append(("interpret", command_text, result.payload.intent))
                return result

        class RecordingValidator:
            def validate_intent(self, payload, received_state):
                result = DEFAULT_FEASIBILITY_VALIDATOR.validate_intent(
                    payload,
                    received_state,
                )
                calls.append(
                    (
                        "validate",
                        payload.intent,
                        received_state is state,
                        result.executable,
                    )
                )
                return result

        class RecordingExecutor:
            def apply_effects(self, payload, received_state):
                result = DEFAULT_TOYCRAFT_EXECUTOR.apply_effects(
                    payload,
                    received_state,
                )
                calls.append(
                    (
                        "execute",
                        payload.intent,
                        received_state is state,
                        result.executed,
                    )
                )
                return result

            def advance_time(self, received_state, seconds):
                raise AssertionError("pipeline must not advance time implicitly")

        class RecordingNarrator(CommandPipelineNarrator):
            def narrate_execution_result(self, result, *, command_text=""):
                calls.append(("narrate_execution", result.intent, command_text))
                return super().narrate_execution_result(
                    result,
                    command_text=command_text,
                )

            def narrate_rejected_validation(
                self,
                validation,
                state,
                *,
                command_text="",
                intent="UNKNOWN",
            ):
                raise AssertionError("successful command must not use rejection narration")

            def narrate_interpretation_failure(self, interpretation, state):
                raise AssertionError("successful command must not use parser narration")

        pipeline = CommandProcessingPipeline(
            interpreter=RecordingInterpreter(),
            validator=RecordingValidator(),
            executor=RecordingExecutor(),
            narrator=RecordingNarrator(),
        )

        response = pipeline.process_command(
            CommandProcessingRequest("미네랄에 일꾼 두 기 붙여", state),
        )

        self.assertEqual(CommandProcessingStatus.EXECUTED, response.status)
        self.assertTrue(response.executed)
        self.assertTrue(response.state_changed)
        self.assertEqual("GATHER_RESOURCE", response.intent)
        self.assertEqual(66, response.after_state.resources.minerals)
        self.assertEqual(
            [
                ("interpret", "미네랄에 일꾼 두 기 붙여", "GATHER_RESOURCE"),
                ("validate", "GATHER_RESOURCE", True, True),
                ("execute", "GATHER_RESOURCE", True, True),
                (
                    "narrate_execution",
                    "GATHER_RESOURCE",
                    "미네랄에 일꾼 두 기 붙여",
                ),
            ],
            calls,
        )

    def test_validation_failure_flow_calls_no_executor_and_narrates_reason(
        self,
    ) -> None:
        state = pipeline_state()
        calls = []

        class RecordingInterpreter:
            def interpret(self, command_text):
                result = DEFAULT_COMMAND_INTERPRETER.interpret(command_text)
                calls.append(("interpret", command_text, result.payload.intent))
                return result

        class RecordingValidator:
            def validate_intent(self, payload, received_state):
                result = DEFAULT_FEASIBILITY_VALIDATOR.validate_intent(
                    payload,
                    received_state,
                )
                calls.append(
                    (
                        "validate",
                        payload.intent,
                        received_state is state,
                        result.executable,
                    )
                )
                return result

        class ForbiddenExecutor:
            def apply_effects(self, payload, received_state):
                raise AssertionError("validation-blocked commands must not execute")

            def advance_time(self, received_state, seconds):
                raise AssertionError("pipeline must not advance time implicitly")

        class RecordingNarrator(CommandPipelineNarrator):
            def narrate_rejected_validation(
                self,
                validation,
                received_state,
                *,
                command_text="",
                intent="UNKNOWN",
            ):
                calls.append(
                    (
                        "narrate_rejection",
                        intent,
                        received_state is state,
                        validation.executable,
                    )
                )
                return super().narrate_rejected_validation(
                    validation,
                    received_state,
                    command_text=command_text,
                    intent=intent,
                )

            def narrate_execution_result(self, result, *, command_text=""):
                raise AssertionError("rejected command must not use success narration")

            def narrate_interpretation_failure(self, interpretation, state):
                raise AssertionError("validated command must not use parser narration")

        pipeline = CommandProcessingPipeline(
            interpreter=RecordingInterpreter(),
            validator=RecordingValidator(),
            executor=ForbiddenExecutor(),
            narrator=RecordingNarrator(),
        )

        response = pipeline.process_command(
            CommandProcessingRequest("SCV로 가스 채취해", state),
        )

        self.assertEqual(CommandProcessingStatus.BLOCKED_BY_VALIDATION, response.status)
        self.assertFalse(response.executed)
        self.assertFalse(response.state_changed)
        self.assertEqual("GATHER_RESOURCE", response.intent)
        self.assertIsNone(response.execution)
        self.assertEqual(state, response.after_state)
        self.assertEqual("validation", response.failure.stage.value)
        self.assertIn("Refinery", response.narration)
        self.assertEqual(
            [
                ("interpret", "SCV로 가스 채취해", "GATHER_RESOURCE"),
                ("validate", "GATHER_RESOURCE", True, False),
                ("narrate_rejection", "GATHER_RESOURCE", True, False),
            ],
            calls,
        )

    def test_pipeline_passes_typed_stage_artifacts_without_collapsing_boundaries(
        self,
    ) -> None:
        state = pipeline_state()
        observed = {}
        test_case = self

        class BoundaryInterpreter:
            def interpret(self, command_text):
                test_case.assertIsInstance(command_text, str)
                result = DEFAULT_COMMAND_INTERPRETER.interpret(command_text)
                observed["payload"] = result.payload
                return result

        class BoundaryValidator:
            def validate_intent(self, payload, received_state):
                test_case.assertIs(payload, observed["payload"])
                test_case.assertIs(received_state, state)
                test_case.assertFalse(isinstance(payload, str))
                validation = DEFAULT_FEASIBILITY_VALIDATOR.validate_intent(
                    payload,
                    received_state,
                )
                observed["validation"] = validation
                return validation

        class BoundaryExecutor:
            def apply_effects(self, payload, received_state):
                test_case.assertIs(payload, observed["payload"])
                test_case.assertIs(received_state, state)
                execution = DEFAULT_TOYCRAFT_EXECUTOR.apply_effects(
                    payload,
                    received_state,
                )
                observed["execution"] = execution
                return execution

            def advance_time(self, received_state, seconds):
                raise AssertionError("pipeline must not advance time implicitly")

        class BoundaryNarrator(CommandPipelineNarrator):
            def narrate_execution_result(self, result, *, command_text=""):
                test_case.assertIs(result, observed["execution"])
                test_case.assertEqual("미네랄에 일꾼 붙여", command_text)
                return super().narrate_execution_result(
                    result,
                    command_text=command_text,
                )

        pipeline = CommandProcessingPipeline(
            interpreter=BoundaryInterpreter(),
            validator=BoundaryValidator(),
            executor=BoundaryExecutor(),
            narrator=BoundaryNarrator(),
        )

        response = pipeline.process_command(
            CommandProcessingRequest("미네랄에 일꾼 붙여", state),
        )

        self.assertIs(response.payload, observed["payload"])
        self.assertIs(response.validation, observed["validation"])
        self.assertIs(response.execution, observed["execution"])
        self.assertEqual(CommandProcessingStatus.EXECUTED, response.status)


class CommandProcessingPipelineDelegationTest(unittest.TestCase):
    def test_pipeline_runs_with_fake_interpreter_and_real_remaining_components(
        self,
    ) -> None:
        state = pipeline_state()
        payload = GatherResourceIntent(
            resource="minerals",
            worker_count=1,
            base="main",
        )
        fake_calls = []

        class FakeInterpreter:
            def interpret(self, command_text):
                fake_calls.append(command_text)
                return CommandInterpretationResult(
                    command_text=command_text,
                    payload=payload,
                )

        pipeline = CommandProcessingPipeline(
            interpreter=FakeInterpreter(),
            validator=DEFAULT_FEASIBILITY_VALIDATOR,
            executor=DEFAULT_TOYCRAFT_EXECUTOR,
            narrator=CommandPipelineNarrator(),
        )

        response = pipeline.process_command(
            CommandProcessingRequest("테스트용 스텁 명령", state),
        )

        self.assertEqual(["테스트용 스텁 명령"], fake_calls)
        self.assertEqual(CommandProcessingStatus.EXECUTED, response.status)
        self.assertTrue(response.executed)
        self.assertTrue(response.state_changed)
        self.assertEqual("GATHER_RESOURCE", response.intent)
        self.assertEqual(58, response.after_state.resources.minerals)
        self.assertIsNotNone(response.validation)
        self.assertTrue(response.validation.executable)
        self.assertIsNotNone(response.execution)
        self.assertTrue(response.execution.executed)
        self.assertIsNotNone(response.narrator_response)
        self.assertIn("자원 채취", response.narration)

    def test_pipeline_delegates_successful_command_without_parsing_or_rule_logic(
        self,
    ) -> None:
        state = pipeline_state()
        payload = GatherResourceIntent(
            resource="minerals",
            worker_count=1,
            base="main",
        )
        calls = []

        class RecordingInterpreter:
            def interpret(self, command_text):
                calls.append(("interpret", command_text))
                return CommandInterpretationResult(
                    command_text=command_text,
                    payload=payload,
                )

        class RecordingValidator:
            def validate_intent(self, received_payload, received_state):
                calls.append(("validate", received_payload, received_state))
                return IntentValidationResult(
                    executable=True,
                    payload=received_payload,
                )

        class RecordingExecutor:
            def apply_effects(self, received_payload, received_state):
                calls.append(("execute", received_payload, received_state))
                return execute_toycraft_intent(received_payload, received_state)

            def advance_time(self, received_state, seconds):
                raise AssertionError("pipeline must not advance time implicitly")

        class RecordingNarrator(CommandPipelineNarrator):
            def narrate_execution_result(self, result, *, command_text=""):
                calls.append(("narrate_execution", result.intent, command_text))
                return super().narrate_execution_result(
                    result,
                    command_text=command_text,
                )

        pipeline = CommandProcessingPipeline(
            interpreter=RecordingInterpreter(),
            validator=RecordingValidator(),
            executor=RecordingExecutor(),
            narrator=RecordingNarrator(),
        )

        response = pipeline.process_command(
            CommandProcessingRequest("미네랄 캐", state),
        )

        self.assertEqual(CommandProcessingStatus.EXECUTED, response.status)
        self.assertEqual(
            [
                ("interpret", "미네랄 캐"),
                ("validate", payload, state),
                ("execute", payload, state),
                ("narrate_execution", "GATHER_RESOURCE", "미네랄 캐"),
            ],
            calls,
        )

    def test_parser_block_stops_before_validation_and_execution(self) -> None:
        state = pipeline_state()
        failure = build_parsing_failure_report(
            command_text="핵 쏴",
            code="unsupported_command_text",
            message="Phase 0 범위 밖입니다.",
            alternatives=("상태 알려줘",),
        )
        interpretation = CommandInterpretationResult(
            command_text="핵 쏴",
            payload=None,
            clarification_required=True,
            clarification_prompt="지원하지 않는 명령입니다. 상태 알려줘처럼 말해 주세요.",
            reason="Phase 0 범위 밖입니다.",
            alternatives=("상태 알려줘",),
            failure=failure,
        )

        class BlockedInterpreter:
            def interpret(self, command_text):
                return interpretation

        class ForbiddenValidator:
            def validate_intent(self, received_payload, received_state):
                raise AssertionError("parser-blocked commands must not validate")

        class ForbiddenExecutor:
            def apply_effects(self, received_payload, received_state):
                raise AssertionError("parser-blocked commands must not execute")

            def advance_time(self, received_state, seconds):
                raise AssertionError("pipeline must not advance time implicitly")

        pipeline = CommandProcessingPipeline(
            interpreter=BlockedInterpreter(),
            validator=ForbiddenValidator(),
            executor=ForbiddenExecutor(),
            narrator=CommandPipelineNarrator(),
        )

        response = pipeline.process_command(CommandProcessingRequest("핵 쏴", state))

        self.assertEqual(CommandProcessingStatus.BLOCKED_BEFORE_VALIDATION, response.status)
        self.assertFalse(response.executed)
        self.assertFalse(response.state_changed)
        self.assertIs(response.failure, failure)
        self.assertIsNone(response.validation)
        self.assertIsNone(response.execution)
        self.assertEqual(state, response.after_state)
        self.assertIn("지원하지 않는 명령", response.narration)

    def test_parse_error_returns_failure_response_without_crossing_boundaries(
        self,
    ) -> None:
        state = pipeline_state()

        class BrokenInterpreter:
            def interpret(self, command_text):
                raise ValueError("lexicon unavailable")

        class ForbiddenValidator:
            def validate_intent(self, received_payload, received_state):
                raise AssertionError("parse errors must not validate")

        class ForbiddenExecutor:
            def apply_effects(self, received_payload, received_state):
                raise AssertionError("parse errors must not execute")

            def advance_time(self, received_state, seconds):
                raise AssertionError("pipeline must not advance time implicitly")

        pipeline = CommandProcessingPipeline(
            interpreter=BrokenInterpreter(),
            validator=ForbiddenValidator(),
            executor=ForbiddenExecutor(),
            narrator=CommandPipelineNarrator(),
        )

        response = pipeline.process_command(CommandProcessingRequest("상태 알려줘", state))

        self.assertEqual(CommandProcessingStatus.BLOCKED_BEFORE_VALIDATION, response.status)
        self.assertFalse(response.executed)
        self.assertFalse(response.state_changed)
        self.assertIsNone(response.validation)
        self.assertIsNone(response.execution)
        self.assertEqual(state, response.after_state)
        self.assertIsNotNone(response.failure)
        self.assertEqual("parsing", response.failure.stage.value)
        self.assertEqual(("parse_error",), response.failure.reason_codes)
        self.assertEqual(
            "ValueError",
            response.failure.primary_reason.metadata["exception_type"],
        )
        self.assertIn("명령 해석 중 오류", response.narration)

    def test_validation_block_stops_before_execution_and_narrates_alternative(
        self,
    ) -> None:
        state = pipeline_state()
        payload = GatherResourceIntent(
            resource="gas",
            worker_count=1,
            base="main",
        )
        issue = FeasibilityIssue(
            reason=FeasibilityErrorReason.MISSING_PREREQUISITE,
            message="가스를 캐려면 Refinery가 필요합니다.",
            alternative="Refinery를 먼저 건설하세요.",
        )
        validation = IntentValidationResult(
            executable=False,
            payload=payload,
            reason=issue.message,
            alternative=issue.alternative,
            issues=(issue,),
        )

        class PayloadInterpreter:
            def interpret(self, command_text):
                return CommandInterpretationResult(
                    command_text=command_text,
                    payload=payload,
                )

        class RejectingValidator:
            def validate_intent(self, received_payload, received_state):
                return validation

        class ForbiddenExecutor:
            def apply_effects(self, received_payload, received_state):
                raise AssertionError("validation-blocked commands must not execute")

            def advance_time(self, received_state, seconds):
                raise AssertionError("pipeline must not advance time implicitly")

        pipeline = CommandProcessingPipeline(
            interpreter=PayloadInterpreter(),
            validator=RejectingValidator(),
            executor=ForbiddenExecutor(),
            narrator=CommandPipelineNarrator(),
        )

        response = pipeline.process_command(CommandProcessingRequest("가스 캐", state))

        self.assertEqual(CommandProcessingStatus.BLOCKED_BY_VALIDATION, response.status)
        self.assertFalse(response.executed)
        self.assertFalse(response.state_changed)
        self.assertIs(response.validation, validation)
        self.assertIsNone(response.execution)
        self.assertIsNotNone(response.narrator_response)
        self.assertEqual(state, response.after_state)
        self.assertEqual("validation", response.failure.stage.value)
        self.assertIn("추천 행동", response.narration)
        self.assertIn("Refinery", response.narration)

    def test_execution_error_returns_rule_failure_without_state_change(
        self,
    ) -> None:
        state = pipeline_state()
        payload = GatherResourceIntent(
            resource="minerals",
            worker_count=1,
            base="main",
        )
        validation = IntentValidationResult(
            executable=True,
            payload=payload,
        )

        class PayloadInterpreter:
            def interpret(self, command_text):
                return CommandInterpretationResult(
                    command_text=command_text,
                    payload=payload,
                )

        class AcceptingValidator:
            def validate_intent(self, received_payload, received_state):
                return validation

        class BrokenExecutor:
            def apply_effects(self, received_payload, received_state):
                raise RuntimeError("rule table unavailable")

            def advance_time(self, received_state, seconds):
                raise AssertionError("pipeline must not advance time implicitly")

        class ForbiddenSuccessNarrator(CommandPipelineNarrator):
            def narrate_execution_result(self, result, *, command_text=""):
                raise AssertionError("failed execution must not use success narration")

        pipeline = CommandProcessingPipeline(
            interpreter=PayloadInterpreter(),
            validator=AcceptingValidator(),
            executor=BrokenExecutor(),
            narrator=ForbiddenSuccessNarrator(),
        )

        response = pipeline.process_command(
            CommandProcessingRequest("미네랄에 일꾼 붙여", state),
        )

        self.assertEqual(CommandProcessingStatus.BLOCKED_BY_EXECUTOR, response.status)
        self.assertFalse(response.executed)
        self.assertFalse(response.state_changed)
        self.assertIs(response.validation, validation)
        self.assertIsNotNone(response.execution)
        self.assertFalse(response.execution.executed)
        self.assertEqual(state, response.execution.before_state)
        self.assertEqual(state, response.execution.after_state)
        self.assertEqual(state, response.after_state)
        self.assertEqual((), response.execution.executed_actions)
        self.assertIsNotNone(response.failure)
        self.assertEqual("rule_execution", response.failure.stage.value)
        self.assertEqual(("executor_error",), response.failure.reason_codes)
        self.assertEqual(
            "RuntimeError",
            response.failure.primary_reason.metadata["exception_type"],
        )
        self.assertIn("실행하지 않았습니다", response.narration)
        self.assertIn("대안", response.narration)


if __name__ == "__main__":
    unittest.main()
