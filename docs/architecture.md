# Phase 0 Component Architecture

ToyCraft Commander is a text-first Phase 0 simulator. Its architecture validates
the commander loop before SC2 or BWAPI integration: Korean command text becomes a
typed Intent DSL, the DSL is validated against a ToyCraft state snapshot,
feasible commands mutate state through deterministic rules, and the result is
narrated back to the commander.

## Runtime Flow

```text
Korean command text
  -> CommandInterpreter
  -> typed Intent DSL payload
  -> IntentFeasibilityValidator
  -> ToyCraftExecutorInterface
  -> ToyCraftRuleEngine
  -> StateNarrator
  -> Korean commander response
```

The pipeline is intentionally one-way. Unsupported interpretation results stop
before validation. Rejected validation results stop before execution. Executor
failures return a blocked execution result without mutating state.

For implementation-facing payload and interface details, see
[contracts.md](contracts.md). That contract document is the stable reference for
the typed Intent DSL, `IntentFeasibilityValidator`, `ToyCraftRuleEngineInterface`,
and `ToyCraftExecutorInterface`.

## End-to-End Command Data Flow

One commander command travels through the system as a typed data handoff, not as
free-form prose shared between layers:

1. Korean input enters the pipeline as `CommandProcessingRequest.command_text`
   with the current immutable `ToyCraftState` snapshot.
2. `CommandInterpreter.interpret(command_text)` preserves the original text and
   maps the Korean utterance to the nearest supported canonical intent. A
   successful result contains an intent-specific `IntentPayload`; unsupported,
   malformed, or ambiguous text returns a clarification result with alternatives
   and no payload.
3. The resolved payload is the typed Intent DSL contract. It always carries the
   common fields `intent`, `priority`, and `constraints`, plus only the fields
   required by that canonical intent, such as `resource`, `structure`,
   `location`, `unit`, `unit_group`, or `target`.
4. `IntentFeasibilityValidator.validate_intent(payload, state)` checks the typed
   DSL against the ToyCraft state snapshot. The validator returns an
   `IntentValidationResult` with either executable status or rejected status,
   reason codes, missing fields, and an actionable alternative.
5. Only executable validation results reach
   `ToyCraftExecutorInterface.apply_effects(payload, state)`. The default
   ToyCraft executor delegates to `ToyCraftRuleEngine`, which applies
   deterministic economy, production, construction, repair, movement, or combat
   effects and returns `ToyCraftExecutionResult` with before/after states,
   executed actions, state-change labels, and structured deltas.
6. `StateNarrator` receives either the rejected validation outcome or the
   execution result. It renders the Korean commander-facing narration from
   structured fields, including the original command, canonical intent,
   validation status, reason/alternative when blocked, and state deltas when
   executed.
7. `CommandProcessingResponse` returns the complete adapter-facing record:
   lifecycle status, original command text, selected Intent DSL when available,
   validation, execution result when reached, before/after state snapshots,
   failure report when blocked, and final narration.

Example successful command:

```text
"본진 입구에 배럭 지어"
  -> BUILD_STRUCTURE Intent DSL
     {intent: BUILD_STRUCTURE, priority: normal, constraints: (...),
      structure: Barracks, location: main ramp}
  -> executable validation
  -> ToyCraftRuleEngine spends minerals, reserves one SCV, queues construction
  -> Korean narration explains the Barracks order and resource/state changes
```

Example blocked command:

```text
"배틀크루저 뽑아"
  -> no supported Phase 0 payload, or a rejected MVP payload if typed directly
  -> no executor call
  -> Korean narration explains the reason and suggests a supported alternative
```

The safety invariant is that every blocked path returns a reason plus an
alternative and leaves `before_state == after_state`.

## Component Boundaries

| Component | Module | Owns | Does not own |
| --- | --- | --- | --- |
| Korean command interpreter | `toycraft_commander/interpreter.py` | Maps Korean or mixed text to the nearest supported MVP intent, preserves original `command_text`, returns typed parser failures and clarification prompts. | Resource feasibility, state mutation, combat math, or narration of state changes. |
| Intent DSL schemas | `toycraft_commander/intents.py` | The exactly 10 canonical intent names, common fields `intent`, `priority`, `constraints`, typed intent-specific payloads, DSL serialization, and payload shape validation. | ToyCraft resource availability, map availability, production queues, or rule execution. |
| Feasibility validator | `toycraft_commander/feasibility.py` | Checks whether a typed payload can execute against an immutable `ToyCraftState`, including resources, supply, prerequisites, producers, workers, targets, locations, and conflicting constraints. | Applying effects, advancing time, changing queues, or rendering final commander prose. |
| ToyCraft state and domain models | `toycraft_commander/resources.py`, `toycraft_commander/units.py`, `toycraft_commander/structures.py`, `toycraft_commander/map.py`, `toycraft_commander/ownership.py`, `toycraft_commander/state_resolver.py` | Minimal Terran-focused simulator vocabulary: minerals, gas, supply, SCV, Marine, Vulture, Zealot, structures, named map locations, ownership, and unit-group resolution. | Natural-language parsing, command lifecycle orchestration, or external game APIs. |
| Rule engine | `toycraft_commander/executor.py` via `ToyCraftRuleEngineInterface` | Deterministic ToyCraft effects for registered feasible commands, including economy ticks, spending, production queues, construction queues, time advancement, defense, expansion, harassment, and state deltas. Feasible commands without a registered effect handler are blocked without mutation. | Free-text interpretation or deciding whether invalid commands should execute. |
| Executor abstraction | `toycraft_commander/executor.py` via `ToyCraftExecutorInterface` | The backend seam used by the pipeline to apply effects and advance time. The default implementation wraps the ToyCraft rule engine. | UI input, voice input, SC2 adapter implementation, or narrator formatting. |
| State narrator | `toycraft_commander/narrator.py` | Converts execution results and rejected validations into Korean commander-facing responses with structured metadata, reason codes, alternatives, and state-change summaries. | Choosing intents, validating feasibility, or mutating state. |
| Command pipeline | `toycraft_commander/pipeline.py` | Coordinates exactly one command through interpreter, validator, executor, and narrator; enforces stop points and response invariants. | Owning stage-specific logic, advancing time implicitly, or bypassing validation. |
| Demo adapter | `toycraft_commander/demo.py` | Provides a 5-7 minute text transcript showing command text, selected Intent DSL, execution status, time advancement, and narration. | Adding new canonical intents, real SC2 control, voice control, or autonomous bot behavior. |

## Responsibility Rules

1. The interpreter may only select or reject an intent. It must not inspect or
   mutate `ToyCraftState`.
2. The Intent DSL must remain the stable command contract. It contains common
   fields plus intent-specific parameters, not validation outcomes or execution
   deltas.
3. The validator is the only normal gate from typed DSL to execution. Commands
   that are unsupported, impossible, or conflicting must return rejection reasons
   and alternatives before the executor is called.
4. The rule engine may assume a command already passed validation, but it still
   returns typed blocked execution results if an execution backend error occurs.
5. The narrator renders outcomes from structured inputs. It must not infer hidden
   game rules from prose-only execution strings.
6. The pipeline coordinates dependencies through interfaces so tests, demos, and
   a future backend adapter can replace individual layers independently.

## SC2 Readiness Boundary

Phase 0 does not implement SC2, BWAPI, voice input, full autonomy, build-order
optimization, or live opponent modeling. SC2 readiness is limited to keeping the
execution seam visible:

- `ToyCraftExecutorInterface.apply_effects(payload, state)` is the future adapter
  slot for applying a validated Intent DSL command to another backend.
- `ToyCraftExecutorInterface.advance_time(state, seconds)` is the backend time
  progression slot.
- Upstream components should continue to depend on typed Intent DSL payloads and
  executor results, not ToyCraft implementation details.
- A future SC2 adapter must preserve the same safety rule: rejected or unclear
  commands do not reach effect application.

## MVP Scope Guard

The Phase 0 architecture supports exactly these 10 canonical intents:
`GATHER_RESOURCE`, `BUILD_STRUCTURE`, `TRAIN_WORKER`, `TRAIN_ARMY`, `SCOUT`,
`SUMMARIZE_STATE`, `DEFEND`, `REPAIR`, `EXPAND`, and `HARASS`.

New aliases may map Korean wording to one of these intents, but they must not add
an eleventh canonical intent. New simulation details are acceptable only when
they improve text-command UX validation without crossing into real game-control
integration.
