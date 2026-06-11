"""Tactical controller interfaces for ToyCraft command execution.

This module is the Phase 0 seam between commander UX and concrete ToyCraft
simulation rules. Future SC2 integration should replace the implementations
behind these protocols, not the interpreter, validator, or narrator contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from toycraft_commander.executor import (
    DEFAULT_TOYCRAFT_RULE_ENGINE,
    ToyCraftExecutionResult,
    ToyCraftRuleEngineInterface,
)
from toycraft_commander.feasibility import (
    DEFAULT_FEASIBILITY_VALIDATOR,
    IntentFeasibilityValidator,
    ToyCraftState,
)
from toycraft_commander.intents import IntentPayload, IntentValidationResult


@runtime_checkable
class TacticalControllerInterface(Protocol):
    """Commander-facing boundary for validation and rule-engine execution."""

    def validate(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> IntentValidationResult:
        """Check command feasibility without mutating ToyCraft state."""

    def execute(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> ToyCraftExecutionResult:
        """Execute a command through validation and rule-based state transition."""

    def advance_time(self, state: ToyCraftState, seconds: int) -> ToyCraftExecutionResult:
        """Advance deterministic simulator time through the rule engine."""


@dataclass(frozen=True)
class ToyCraftTacticalController:
    """Default controller that composes the validator and rule engine modules."""

    validator: IntentFeasibilityValidator = DEFAULT_FEASIBILITY_VALIDATOR
    rule_engine: ToyCraftRuleEngineInterface = DEFAULT_TOYCRAFT_RULE_ENGINE

    def validate(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> IntentValidationResult:
        """Check command feasibility without mutating ToyCraft state."""

        return self.validator.validate_intent(payload, state)

    def execute(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> ToyCraftExecutionResult:
        """Execute a command through the configured rule engine."""

        return self.rule_engine.execute_intent(payload, state)

    def advance_time(self, state: ToyCraftState, seconds: int) -> ToyCraftExecutionResult:
        """Advance deterministic simulator time through the configured rule engine."""

        return self.rule_engine.advance_time(state, seconds)


DEFAULT_TACTICAL_CONTROLLER: Final[TacticalControllerInterface] = (
    ToyCraftTacticalController()
)
