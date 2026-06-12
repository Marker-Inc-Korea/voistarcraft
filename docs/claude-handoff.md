# Claude Code Handoff: TextCraft Commander

This document is the continuation brief for Claude Code or another coding agent.
It captures the original product intent, the current repository state, what is
done, what is not done, and the next implementation sequence.

## TLDR

The product is **real StarCraft II natural-language control**, not ToyCraft.
ToyCraft exists only as an offline deterministic harness for parser, validator,
rule-engine, and narration tests.

Current code can parse Korean commander commands into typed Intent DSL and can
translate those intents into semantic StarCraft II command plans. It does **not**
yet launch StarCraft II or execute commands against a real `python-sc2` BotAI in
an end-to-end game.

## Original Product Positioning

TextCraft Commander should let a human command StarCraft by natural language:

```text
User text or voice
  -> command interpreter
  -> typed Intent DSL
  -> game-state resolver
  -> validator
  -> tactical controller
  -> StarCraft II API executor
  -> game
  -> narrator / event memory
```

Core rule:

```text
Do not emulate mouse clicks.
Translate natural-language commands into semantic game actions and execute them
through StarCraft APIs.
```

The user-facing message is:

```text
말하면 스타가 움직인다.
```

## Current Repository State

Latest pushed commits at the time of this handoff:

```text
6a9d2d9 feat: harden StarCraft II executor contracts
a6e5d32 feat: add real StarCraft II executor boundary
279b2dd feat: complete ToyCraft commander pipeline
107d3c9 feat: add ToyCraft commander MVP
706995f chore: initialize project
```

Validation status:

```bash
python3 -m pytest -q
```

Expected current result:

```text
359 passed, 850 subtests passed
```

## Implemented Components

### Intent and Korean Command Layer

Location:

```text
toycraft_commander/intents.py
toycraft_commander/interpreter.py
```

Implemented:

- 10 canonical intents.
- Korean free-form phrase mapping to typed Intent DSL.
- Reject/clarification behavior for unsupported or ambiguous text.
- Stable JSON DSL serialization.

Current canonical intents:

```text
GATHER_RESOURCE
BUILD_STRUCTURE
TRAIN_WORKER
TRAIN_ARMY
SCOUT
SUMMARIZE_STATE
DEFEND
REPAIR
EXPAND
HARASS
```

Important naming note:

The original planning language used phrases such as `keep_worker_production`,
`prevent_supply_block`, `defend_ramp`, and `harass_mineral_line`. The current
code normalized these into the uppercase canonical intent names above. Preserve
backward-compatible tests if changing the names.

### ToyCraft Offline Harness

Location:

```text
toycraft_commander/
```

Implemented:

- Toy resources, supply, units, structures, map aliases, feasibility validation,
  rule execution, tactical controller, pipeline, and Korean narration.
- Deterministic tests for parser, validator, execution, narration, and demo flow.

Important boundary:

ToyCraft is not the product runtime. Do not spend major effort making ToyCraft
more game-realistic unless it directly supports SC2 integration tests.

### Real StarCraft II Boundary

Location:

```text
starcraft_commander/contracts.py
starcraft_commander/sc2_executor.py
```

Implemented:

- Stable semantic SC2 action type contract.
- Intent DSL to SC2 command-plan mapping.
- JSON-ready plan and result contracts.
- Async `SC2RuntimeExecutor` with lifecycle methods.
- Fake BotAI-style execution tests.
- Lazy package imports so tests do not require StarCraft II or `python-sc2`.

Stable public SC2 action types:

```text
assign_workers
build_structure
train_unit
move_group
attack_move
repair
observe
```

Current contract:

- `SC2ActionPlanner.build_plan(payload)` creates an `SC2ExecutionPlan`.
- `SC2RuntimeExecutor.start(bot)` binds a BotAI-like runtime.
- `SC2RuntimeExecutor.execute(plan)` applies ordered actions.
- `SC2RuntimeExecutor.close()` ends lifecycle.
- Missing runtime capability skips the action and returns `success == false`.
- Runtime exceptions are captured as structured errors.

## What Is Not Done Yet

This is the most important section.

The project is **not yet a playable StarCraft II commander**.

Missing:

- Real `python-sc2` dependency setup.
- Real SC2 BotAI adapter that translates semantic actions into actual `python-sc2`
  calls such as unit selection, train, build, move, attack, repair, and expansion.
- Real SC2 game-state resolver from BotAI observations into commander semantic
  state.
- Real map resolver for main, ramp, natural, enemy main, enemy natural, and enemy
  mineral line.
- Real validator against live resources, supply, tech prerequisites, visible
  units, available producers, and pathable build positions.
- End-to-end local custom game demo.
- Text CLI loop that connects user command -> interpreter -> SC2 planner ->
  live BotAI execution -> narration.
- Voice input.
- Brood War / BWAPI executor.

## Next Implementation Plan

### Step 1. Add Real SC2 Runtime Dependency Surface

Goal:

Make the repo able to optionally run with `python-sc2` while keeping CI tests
working without it.

Suggested files:

```text
pyproject.toml
requirements-dev.txt or optional extras
starcraft_commander/python_sc2_adapter.py
tests/test_python_sc2_adapter_contract.py
```

Acceptance:

- Importing the package without `python-sc2` still works.
- A clear error explains how to install and configure SC2 runtime.
- Tests can use fakes/mocks and do not require real SC2.

### Step 2. Implement BotAI Adapter Methods

Goal:

Translate semantic actions into real or mockable BotAI operations.

Implement methods matching the semantic executor contract:

```text
assign_workers(action)
build_structure(action)
train_unit(action)
move_group(action)
attack_move(action)
repair(action)
observe(action)
```

Acceptance:

- Unit tests verify each method calls the correct BotAI-like fake operations.
- Missing units/producers/targets return structured skipped results.
- No mouse or screen automation is introduced.

### Step 3. Implement SC2 State Resolver

Goal:

Convert BotAI raw state into commander semantic state.

Suggested model:

```text
SC2CommanderState:
  resources
  supply
  own_units
  own_structures
  visible_enemy_units
  visible_enemy_structures
  known_locations
  threats
  production
```

Acceptance:

- Tests use fake BotAI observations.
- Resolver can identify basic Terran state: workers, marines, command centers,
  barracks, factories, supply depots, minerals, gas, supply.

### Step 4. Implement Map Resolver for One Map

Goal:

Support one fixed SC2 map first.

Minimum semantic targets:

```text
self_main
self_ramp
self_natural
enemy_main
enemy_ramp
enemy_natural
enemy_mineral_line
```

Acceptance:

- Named targets resolve to Point2-like coordinates in tests.
- Unknown targets are rejected with a clear alternative.

### Step 5. Wire Live Command Pipeline

Goal:

Create the first actual StarCraft II demo path.

Suggested command:

```bash
python -m starcraft_commander.demo_sc2
```

Minimum demo command:

```text
마린 6기 입구로 보내고 SCV 계속 찍어
```

Expected behavior:

- Interpreter produces DSL.
- SC2 validator checks live state or fake state.
- Planner creates semantic SC2 plan.
- BotAI adapter executes train/move/attack actions.
- Narrator reports what happened.

### Step 6. Add Real Local Smoke Test Instructions

Goal:

Document how a developer with StarCraft II installed can run the demo.

Document:

- Required StarCraft II installation.
- Required maps.
- Required Python version.
- `python-sc2` install command.
- How to run local custom game.
- Known limitations.

## MVP Completion Definition

The MVP is complete only when this real-game demo works locally:

```text
1. Start local SC2 custom game or python-sc2 bot match.
2. System reads current state.
3. User enters Korean command.
4. Command becomes Intent DSL.
5. Validator checks live state.
6. Executor issues real SC2 API commands.
7. In-game units/buildings actually change behavior.
8. Narrator explains the result.
```

The first real-game success criterion should be small:

```text
Terran
one fixed map
local AI opponent
text input
SCV production
Marine production
Supply Depot or Barracks build
Marine defend ramp / attack_move
state summary narration
```

## Known Risks

- `python-sc2` API details may differ from the semantic fake interface.
- SC2 local install and map paths are environment-sensitive.
- Map semantic resolution is the hard part after basic command planning.
- Live validators must be conservative: reject if state is unknown.
- LLM should not be called per-frame; keep fast micro in code policies.
- Do not bind the public DSL to python-sc2 method names.

## Commands For Next Agent

Start with:

```bash
git status --short --branch
python3 -m pytest -q
sed -n '1,220p' README.md
sed -n '1,260p' starcraft_commander/contracts.py
sed -n '1,320p' starcraft_commander/sc2_executor.py
```

Then implement Step 1 and Step 2 above.

## Do Not Do

- Do not present ToyCraft as the final product.
- Do not replace SC2 API execution with mouse-click automation.
- Do not require StarCraft II installation for normal unit tests.
- Do not hide unsupported live commands as successful results.
- Do not make `success == true` if any planned action was skipped.
