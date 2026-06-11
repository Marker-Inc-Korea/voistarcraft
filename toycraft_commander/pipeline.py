"""Typed command-processing pipeline contract for ToyCraft Commander."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from toycraft_commander.compat import StrEnum
from toycraft_commander.executor import (
    DEFAULT_TOYCRAFT_EXECUTOR,
    ToyCraftExecutionResult,
    ToyCraftExecutorInterface,
    summarize_toycraft_state,
)
from toycraft_commander.failure import (
    CommandFailureReport,
    CommandFailureStage,
    build_parsing_failure_report,
    build_rule_execution_failure_report,
    build_validation_failure_report,
)
from toycraft_commander.feasibility import (
    DEFAULT_FEASIBILITY_VALIDATOR,
    IntentFeasibilityValidator,
    ToyCraftState,
)
from toycraft_commander.interpreter import (
    CommandInterpretationResult,
    CommandInterpreterInterface,
    DEFAULT_COMMAND_INTERPRETER,
)
from toycraft_commander.intents import IntentPayload, IntentValidationResult
from toycraft_commander.narrator import (
    DEFAULT_STATE_NARRATOR,
    StateNarratorInterface,
    StateNarratorResponse,
    build_rejected_narrator_input,
)


class CommandProcessingStatus(StrEnum):
    """Stable lifecycle status for one text command through the pipeline."""

    BLOCKED_BEFORE_VALIDATION = "blocked_before_validation"
    BLOCKED_BY_VALIDATION = "blocked_by_validation"
    BLOCKED_BY_EXECUTOR = "blocked_by_executor"
    EXECUTED = "executed"


@dataclass(frozen=True)
class CommandProcessingRequest:
    """Typed request entering the text-first commander pipeline."""

    command_text: str
    state: ToyCraftState
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.command_text) is not str:
            raise TypeError("command_text must be a string.")
        if not isinstance(self.state, ToyCraftState):
            raise TypeError("state must be a ToyCraftState.")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready request snapshot for logs and adapters."""

        return {
            "command_text": self.command_text,
            "state": summarize_toycraft_state(self.state),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CommandProcessingResponse:
    """Typed response returned by the command-processing pipeline boundary."""

    request: CommandProcessingRequest
    status: CommandProcessingStatus
    interpretation: CommandInterpretationResult
    before_state: ToyCraftState
    after_state: ToyCraftState
    validation: IntentValidationResult | None = None
    execution: ToyCraftExecutionResult | None = None
    narrator_response: StateNarratorResponse | None = None
    failure: CommandFailureReport | None = None
    narration: str = ""

    def __post_init__(self) -> None:
        status = self.status
        if isinstance(status, str):
            status = CommandProcessingStatus(status)
        object.__setattr__(self, "status", status)
        if not isinstance(self.request, CommandProcessingRequest):
            raise TypeError("request must be a CommandProcessingRequest.")
        if not isinstance(self.interpretation, CommandInterpretationResult):
            raise TypeError("interpretation must be a CommandInterpretationResult.")
        if not isinstance(self.before_state, ToyCraftState):
            raise TypeError("before_state must be a ToyCraftState.")
        if not isinstance(self.after_state, ToyCraftState):
            raise TypeError("after_state must be a ToyCraftState.")
        if not self.narration.strip():
            raise ValueError("narration must be non-empty.")
        _validate_response_invariants(self)

    @property
    def executed(self) -> bool:
        """Return whether this command reached a mutating or read-only executor."""

        return self.status == CommandProcessingStatus.EXECUTED

    @property
    def state_changed(self) -> bool:
        """Return whether the command changed the ToyCraft state snapshot."""

        return self.before_state != self.after_state

    @property
    def payload(self) -> IntentPayload | None:
        """Return the interpreted typed Intent DSL payload, if any."""

        return self.interpretation.payload

    @property
    def intent(self) -> str:
        """Return the selected intent or UNKNOWN before interpretation succeeds."""

        return self.payload.intent if self.payload is not None else "UNKNOWN"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready response contract for UI/demo adapters."""

        return {
            "status": self.status.value,
            "command_text": self.request.command_text,
            "intent": self.intent,
            "executed": self.executed,
            "state_changed": self.state_changed,
            "before_state": summarize_toycraft_state(self.before_state),
            "after_state": summarize_toycraft_state(self.after_state),
            "intent_dsl": None
            if self.payload is None
            else self.payload.to_dict(),
            "validation": _validation_to_dict(self.validation),
            "execution": _execution_to_dict(self.execution),
            "narration": self.narration,
            "narrator_response": None
            if self.narrator_response is None
            else self.narrator_response.to_dict(),
            "failure": None if self.failure is None else self.failure.to_dict(),
            "metadata": dict(self.request.metadata),
        }


@runtime_checkable
class CommandPipelineNarratorInterface(Protocol):
    """Narration seam consumed by the command-processing pipeline."""

    def narrate_execution_result(
        self,
        result: ToyCraftExecutionResult,
        *,
        command_text: str = "",
    ) -> StateNarratorResponse:
        """Render a completed execution result."""

    def narrate_rejected_validation(
        self,
        validation: IntentValidationResult,
        state: ToyCraftState,
        *,
        command_text: str = "",
        intent: str = "UNKNOWN",
    ) -> StateNarratorResponse:
        """Render a validator-blocked command without mutating state."""

    def narrate_interpretation_failure(
        self,
        interpretation: CommandInterpretationResult,
        state: ToyCraftState,
    ) -> str:
        """Render a parser-blocked command without entering validation."""


@dataclass(frozen=True)
class CommandPipelineNarrator:
    """Adapter from pipeline narration needs to the State Narrator boundary."""

    state_narrator: StateNarratorInterface = DEFAULT_STATE_NARRATOR

    def narrate_execution_result(
        self,
        result: ToyCraftExecutionResult,
        *,
        command_text: str = "",
    ) -> StateNarratorResponse:
        """Delegate successful execution rendering to the configured narrator."""

        return self.state_narrator.narrate_execution_result(
            result,
            command_text=command_text,
        )

    def narrate_rejected_validation(
        self,
        validation: IntentValidationResult,
        state: ToyCraftState,
        *,
        command_text: str = "",
        intent: str = "UNKNOWN",
    ) -> StateNarratorResponse:
        """Delegate rejected validation rendering to the configured narrator."""

        narrator_input = build_rejected_narrator_input(
            validation,
            state,
            command_text=command_text,
            intent=intent,
        )
        return self.state_narrator.narrate(narrator_input)

    def narrate_interpretation_failure(
        self,
        interpretation: CommandInterpretationResult,
        state: ToyCraftState,
    ) -> str:
        """Return the interpreter's clarification text for parser-stage blocks."""

        del state
        return interpretation.clarification_prompt


@runtime_checkable
class CommandProcessingPipelineInterface(Protocol):
    """Boundary for processing one text command without owning stage logic."""

    def process_command(
        self,
        request: CommandProcessingRequest,
    ) -> CommandProcessingResponse:
        """Interpret, validate, execute, and narrate one command request."""


@dataclass(frozen=True)
class CommandProcessingPipeline:
    """Thin coordinator that delegates each command stage to injected components."""

    interpreter: CommandInterpreterInterface = DEFAULT_COMMAND_INTERPRETER
    validator: IntentFeasibilityValidator = DEFAULT_FEASIBILITY_VALIDATOR
    executor: ToyCraftExecutorInterface = DEFAULT_TOYCRAFT_EXECUTOR
    narrator: CommandPipelineNarratorInterface = field(
        default_factory=lambda: DEFAULT_COMMAND_PIPELINE_NARRATOR
    )

    def process_command(
        self,
        request: CommandProcessingRequest,
    ) -> CommandProcessingResponse:
        """Process one request through injected parser, validator, executor, narrator."""

        if not isinstance(request, CommandProcessingRequest):
            raise TypeError("request must be a CommandProcessingRequest.")

        try:
            interpretation = self.interpreter.interpret(request.command_text)
        except Exception as error:
            interpretation = _build_parse_error_interpretation(request, error)

        if interpretation.payload is None:
            narration = self.narrator.narrate_interpretation_failure(
                interpretation,
                request.state,
            )
            return CommandProcessingResponse(
                request=request,
                status=CommandProcessingStatus.BLOCKED_BEFORE_VALIDATION,
                interpretation=interpretation,
                before_state=request.state,
                after_state=request.state,
                failure=interpretation.failure,
                narration=narration,
            )

        validation = self.validator.validate_intent(
            interpretation.payload,
            request.state,
        )
        if not validation.executable:
            narrator_response = self.narrator.narrate_rejected_validation(
                validation,
                request.state,
                command_text=request.command_text,
                intent=interpretation.payload.intent,
            )
            failure = build_validation_failure_report(
                validation,
                command_text=request.command_text,
                intent=interpretation.payload.intent,
            )
            return CommandProcessingResponse(
                request=request,
                status=CommandProcessingStatus.BLOCKED_BY_VALIDATION,
                interpretation=interpretation,
                validation=validation,
                before_state=request.state,
                after_state=request.state,
                narrator_response=narrator_response,
                failure=failure,
                narration=narrator_response.response_text,
            )

        try:
            execution = self.executor.apply_effects(
                interpretation.payload,
                request.state,
            )
        except Exception as error:
            execution = _build_execution_error_result(
                request,
                interpretation,
                validation,
                error,
            )

        if not execution.executed:
            return CommandProcessingResponse(
                request=request,
                status=CommandProcessingStatus.BLOCKED_BY_EXECUTOR,
                interpretation=interpretation,
                validation=validation,
                execution=execution,
                before_state=request.state,
                after_state=execution.after_state,
                failure=execution.failure,
                narration=execution.narration,
            )

        narrator_response = self.narrator.narrate_execution_result(
            execution,
            command_text=request.command_text,
        )
        return CommandProcessingResponse(
            request=request,
            status=CommandProcessingStatus.EXECUTED,
            interpretation=interpretation,
            validation=validation,
            execution=execution,
            before_state=request.state,
            after_state=execution.after_state,
            narrator_response=narrator_response,
            narration=narrator_response.response_text,
        )


def process_command(
    command_text: str,
    state: ToyCraftState,
    *,
    metadata: Mapping[str, object] | None = None,
) -> CommandProcessingResponse:
    """Convenience wrapper around the default command-processing pipeline."""

    request = CommandProcessingRequest(
        command_text=command_text,
        state=state,
        metadata={} if metadata is None else metadata,
    )
    return DEFAULT_COMMAND_PROCESSING_PIPELINE.process_command(request)


DEFAULT_COMMAND_PIPELINE_NARRATOR: Final[CommandPipelineNarratorInterface] = (
    CommandPipelineNarrator()
)
DEFAULT_COMMAND_PROCESSING_PIPELINE: Final[CommandProcessingPipelineInterface] = (
    CommandProcessingPipeline()
)


def _validate_response_invariants(response: CommandProcessingResponse) -> None:
    if response.interpretation.command_text != response.request.command_text:
        raise ValueError("interpretation must match request command_text.")
    if response.before_state != response.request.state:
        raise ValueError("before_state must match request state.")
    if response.status == CommandProcessingStatus.BLOCKED_BEFORE_VALIDATION:
        if response.interpretation.payload is not None:
            raise ValueError("pre-validation blocks cannot include a payload.")
        if response.validation is not None or response.execution is not None:
            raise ValueError("pre-validation blocks cannot include validation/execution.")
        if response.after_state != response.before_state:
            raise ValueError("pre-validation blocks cannot mutate state.")
    elif response.status == CommandProcessingStatus.BLOCKED_BY_VALIDATION:
        if response.validation is None or response.validation.executable:
            raise ValueError("validation blocks require rejected validation.")
        if response.execution is not None:
            raise ValueError("validation blocks cannot include execution.")
        if response.after_state != response.before_state:
            raise ValueError("validation blocks cannot mutate state.")
        if response.failure is None:
            raise ValueError("validation blocks require a failure report.")
        if response.failure.stage != CommandFailureStage.VALIDATION:
            raise ValueError("validation blocks require validation failure stage.")
    elif response.status == CommandProcessingStatus.BLOCKED_BY_EXECUTOR:
        if response.execution is None or response.execution.executed:
            raise ValueError("executor blocks require rejected execution.")
        if response.failure is None:
            raise ValueError("executor blocks require a failure report.")
        if response.failure.stage != CommandFailureStage.RULE_EXECUTION:
            raise ValueError("executor blocks require rule execution failure stage.")
        if response.after_state != response.before_state:
            raise ValueError("executor blocks cannot mutate state.")
    elif response.status == CommandProcessingStatus.EXECUTED:
        if response.interpretation.payload is None:
            raise ValueError("executed responses require a payload.")
        if response.validation is None or not response.validation.executable:
            raise ValueError("executed responses require executable validation.")
        if response.execution is None or not response.execution.executed:
            raise ValueError("executed responses require executed results.")
        if response.narrator_response is None:
            raise ValueError("executed responses require narrator output.")


def _validation_to_dict(
    validation: IntentValidationResult | None,
) -> dict[str, object] | None:
    if validation is None:
        return None
    return {
        "executable": validation.executable,
        "status": validation.status.value,
        "reason": validation.reason,
        "alternative": validation.alternative,
        "missing_fields": list(validation.missing_fields),
        "reason_codes": [reason.value for reason in validation.reason_codes],
    }


def _execution_to_dict(
    execution: ToyCraftExecutionResult | None,
) -> dict[str, object] | None:
    if execution is None:
        return None
    return {
        "intent": execution.intent,
        "executed": execution.executed,
        "read_only": execution.read_only,
        "narration": execution.narration,
        "state_changes": list(execution.state_changes),
        "executed_actions": [
            action.to_dict() for action in execution.executed_actions
        ],
        "state_delta": execution.state_delta.to_dict(),
        "summary": dict(execution.summary),
        "failure": None if execution.failure is None else execution.failure.to_dict(),
    }


_PARSE_ERROR_ALTERNATIVES: Final[tuple[str, ...]] = (
    "상태 알려줘",
    "일꾼 계속 찍어",
    "본진에 배럭 지어",
)
_PARSE_ERROR_PROMPT: Final[str] = (
    "명령 해석 중 오류가 발생해 실행하지 않았습니다. "
    "ToyCraft MVP 명령 중 하나로 다시 말해 주세요. "
    "예: 상태 알려줘 / 일꾼 계속 찍어 / 본진에 배럭 지어"
)
_PARSE_ERROR_REASON: Final[str] = "Interpreter failed before returning a parse result."
_EXECUTION_ERROR_ALTERNATIVE: Final[str] = (
    "상태를 확인한 뒤 더 단순한 ToyCraft MVP 명령으로 다시 시도해 주세요. "
    "예: 상태 알려줘 / 본진 방어해."
)


def _build_parse_error_interpretation(
    request: CommandProcessingRequest,
    error: Exception,
) -> CommandInterpretationResult:
    failure = build_parsing_failure_report(
        command_text=request.command_text,
        code="parse_error",
        message=_PARSE_ERROR_REASON,
        alternatives=_PARSE_ERROR_ALTERNATIVES,
        metadata={"exception_type": type(error).__name__},
    )
    return CommandInterpretationResult(
        command_text=request.command_text,
        payload=None,
        clarification_required=True,
        clarification_prompt=_PARSE_ERROR_PROMPT,
        reason=_PARSE_ERROR_REASON,
        alternatives=_PARSE_ERROR_ALTERNATIVES,
        failure=failure,
    )


def _build_execution_error_result(
    request: CommandProcessingRequest,
    interpretation: CommandInterpretationResult,
    validation: IntentValidationResult,
    error: Exception,
) -> ToyCraftExecutionResult:
    if interpretation.payload is None:
        raise ValueError("execution errors require an interpreted payload.")

    message = "Executor failed before returning a ToyCraft execution result."
    failure = build_rule_execution_failure_report(
        command_text=request.command_text,
        intent=interpretation.payload.intent,
        code="executor_error",
        message=message,
        alternative=_EXECUTION_ERROR_ALTERNATIVE,
        metadata={"exception_type": type(error).__name__},
    )
    return ToyCraftExecutionResult(
        intent=interpretation.payload.intent,
        validation=validation,
        before_state=request.state,
        after_state=request.state,
        executed=False,
        read_only=True,
        failure=failure,
        narration=f"실행하지 않았습니다. 이유: {message} 대안: {_EXECUTION_ERROR_ALTERNATIVE}",
    )
