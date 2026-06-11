# Phase 0 Interface Contracts

This document is the implementation-facing contract for ToyCraft Commander's
Phase 0 command loop. It defines the typed Intent DSL boundary, validation gate,
and rule-engine/executor interface. It does not add SC2, BWAPI, voice control, or
autonomous bot behavior.

## Contract Flow

```text
command_text
  -> CommandInterpreter
  -> IntentPayload
  -> IntentFeasibilityValidator.validate_intent(payload, state)
  -> ToyCraftExecutorInterface.apply_effects(payload, state)
  -> ToyCraftRuleEngine.execute_intent(payload, state)
  -> ToyCraftExecutionResult
  -> StateNarrator
```

Blocked interpretation stops before validation. Rejected validation stops before
execution. Executor failures must return a blocked result with unchanged state.

## Typed Intent DSL

The Intent DSL source of truth is `toycraft_commander/intents.py`.
`INTENT_DSL_FORMAT_VERSION` is `toycraft.intent_dsl.v1`.

Every payload is an intent-specific dataclass that inherits the same common
fields:

| Field | Type | Required | Contract |
| --- | --- | --- | --- |
| `intent` | `IntentName` | yes | One of exactly 10 canonical Phase 0 intent names. |
| `priority` | `Priority` | yes | `low`, `normal`, `high`, or `urgent`; defaults to `normal`. |
| `constraints` | `tuple[str, ...]` | yes | Natural-language or normalized conditions that must hold before execution. Empty is valid. |

The 10 canonical typed payload schemas are:

| Intent | Payload class | Required intent-specific fields |
| --- | --- | --- |
| `GATHER_RESOURCE` | `GatherResourceIntent` | `resource`, `worker_count`, `base` |
| `BUILD_STRUCTURE` | `BuildStructureIntent` | `structure`, `location` |
| `TRAIN_WORKER` | `TrainWorkerIntent` | `count` |
| `TRAIN_ARMY` | `TrainArmyIntent` | `unit_type`, `count` |
| `SCOUT` | `ScoutIntent` | `target`, `unit_group` |
| `SUMMARIZE_STATE` | `SummarizeStateIntent` | none beyond common fields |
| `DEFEND` | `DefendIntent` | `location`, `unit_group` |
| `REPAIR` | `RepairIntent` | `target`, `worker_count` |
| `EXPAND` | `ExpandIntent` | `location` |
| `HARASS` | `HarassIntent` | `target`, `unit_group` |

The stable parsed-command display envelope is:

```json
{
  "format": "toycraft.intent_dsl.v1",
  "command_text": "본진 입구 수비해",
  "intent_dsl": {
    "intent": "DEFEND",
    "priority": "urgent",
    "constraints": ["hold ramp against early pressure"],
    "location": "main ramp",
    "unit_group": "available combat units"
  },
  "entity_references": []
}
```

Payload serialization must use `serialize_intent_payload()` or
`IntentCommandPayload.to_dsl_document()` so field order follows
`INTENT_DSL_FIELD_ORDER_BY_INTENT`: common fields first, then the required
intent-specific fields.

## Validation Contract

The validator boundary is `IntentFeasibilityValidator` in
`toycraft_commander/feasibility.py`:

```python
class IntentFeasibilityValidator(Protocol):
    def validate_intent(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> IntentValidationResult:
        ...
```

Inputs:

| Input | Contract |
| --- | --- |
| `payload` | A typed `IntentPayload` or raw mapping coercible through `validate_intent_payload()`. |
| `state` | Immutable `ToyCraftState` snapshot containing resources, supply, units, structures, queues, map claims, damaged targets, positions, and combat pressure state. |

Outputs:

| Output field | Contract |
| --- | --- |
| `executable` | `True` only when the payload may reach `apply_effects`. |
| `status` | `ValidationStatus.EXECUTABLE` or `ValidationStatus.REJECTED`. |
| `payload` | Typed payload on success; may be absent for malformed or unsupported raw input. |
| `reason` | Human-readable rejection summary for blocked commands. |
| `alternative` | Actionable supported alternative for blocked commands. |
| `missing_fields` | Required DSL fields missing from raw payload validation. |
| `issues` | Ordered `FeasibilityIssue` values with typed reason codes. |
| `reason_code` / `reason_codes` | Machine-readable `FeasibilityErrorReason` values for tests, demos, and UI adapters. |

Validation invariants:

1. The validator must not mutate `ToyCraftState`.
2. Unsupported intents, malformed payloads, missing required fields, invalid
   field values, impossible state requests, and conflicting constraints return
   `executable=False`.
3. Every rejected result must include a reason and an actionable alternative
   through its primary `FeasibilityIssue`.
4. State-aware checks cover resources, gas, supply, prerequisites, producer
   availability, worker availability, unit-group availability, targets, map
   locations, damaged repair targets, and known conflicting constraints.
5. Validation remains a gate only; it does not spend resources, enqueue units,
   move units, repair targets, or apply combat damage.

## Rule Engine and Executor Contract

`ToyCraftExecutorInterface` is the backend seam used by the pipeline and future
adapter experiments:

```python
class ToyCraftExecutorInterface(Protocol):
    def apply_effects(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> ToyCraftExecutionResult:
        ...

    def advance_time(
        self,
        state: ToyCraftState,
        seconds: int,
    ) -> ToyCraftExecutionResult:
        ...
```

The default `ToyCraftExecutor` delegates to `ToyCraftRuleEngineInterface`:

```python
class ToyCraftRuleEngineInterface(Protocol):
    def execute_intent(
        self,
        payload: IntentPayload | Mapping[str, object],
        state: ToyCraftState,
    ) -> ToyCraftExecutionResult:
        ...

    def advance_time(
        self,
        state: ToyCraftState,
        seconds: int,
    ) -> ToyCraftExecutionResult:
        ...
```

`ToyCraftRuleEngine.execute_intent()` validates first through its configured
`IntentFeasibilityValidator`. If validation rejects, it returns a rejected
`ToyCraftExecutionResult` and leaves `before_state == after_state`.

`ToyCraftExecutionResult` fields:

| Field | Contract |
| --- | --- |
| `intent` | Canonical intent name, `PROGRESS_TIME`, or `UNKNOWN` for malformed rejected input. |
| `validation` | Validation result used for the execution decision. |
| `before_state` / `after_state` | Immutable snapshots before and after rule application. |
| `executed` | `True` only for successful mutating or read-only execution. |
| `read_only` | `True` for state summaries, rejected results, and backend failures. |
| `narration` | Non-empty Korean commander-facing explanation or rejection message. |
| `state_changes` | Raw state-change labels for demos and backward-compatible narration. |
| `executed_actions` | Structured actions such as `spend_resources`, `queue_construction`, `queue_production`, `move_units`, `apply_damage`, or `advance_time`. |
| `state_delta` | Structured before/after deltas derived from state snapshots. |
| `summary` | Structured state summary for `SUMMARIZE_STATE` and UI adapters. |
| `failure` | `CommandFailureReport` for rejected or failed executions only. |

Execution invariants:

1. `executed=True` requires executable validation.
2. Rejected execution results must include `failure`, must have no
   `executed_actions`, and must not mutate state.
3. State-changing execution results require at least one `executed_action` and a
   non-empty structured `state_delta`.
4. Read-only results must keep `before_state == after_state`.
5. `advance_time(state, seconds)` is the only contract for deterministic timer
   progression; normal command execution must not advance time implicitly.
6. The default Phase 0 rule table handles `GATHER_RESOURCE`, `BUILD_STRUCTURE`,
   `TRAIN_WORKER`, `TRAIN_ARMY`, `SUMMARIZE_STATE`, `DEFEND`, `EXPAND`, and
   `HARASS` through ToyCraft-only rules.
7. `REPAIR` is still part of the canonical typed DSL and feasibility contract;
   until a ToyCraft repair effect handler is registered, a validated repair
   payload must be blocked at the executor boundary rather than mutating state.

## Pipeline Stop Conditions

The command pipeline in `toycraft_commander/pipeline.py` coordinates the stages
without owning stage-specific logic:

| Pipeline status | Stop point | State mutation |
| --- | --- | --- |
| `blocked_before_validation` | Interpreter returned no payload or parser error. | Not allowed. |
| `blocked_by_validation` | Validator rejected a typed payload. | Not allowed. |
| `blocked_by_executor` | Executor returned or raised a backend failure. | Not allowed. |
| `executed` | Executor accepted the payload and narrator rendered the result. | Allowed only when execution result is state-changing. |

The safety contract is simple: unsupported, impossible, or conflicting commands
must not execute, must include a reason plus alternative, and must preserve
`before_state == after_state`.

## SC2 Readiness Boundary

Phase 0 readiness for SC2 is limited to the visible executor abstraction. A
future backend may implement `ToyCraftExecutorInterface`, but it must preserve
the typed Intent DSL, validator gate, execution result shape, narration inputs,
and no-mutation rejection safety invariant. Phase 0 does not implement SC2 API
calls, BWAPI, voice input, hidden autonomous build-order control, or live
opponent modeling.
