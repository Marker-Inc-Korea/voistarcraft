"""Tests for in-game-loop standing orders (W8).

These tests run without StarCraft II, python-sc2, anthropic, faster-whisper,
or sounddevice installed. Bots are pure-Python recording fakes; the
cross-module contract with the Korean interpreter is pinned by interpreting
real utterances through ``DEFAULT_COMMAND_INTERPRETER`` and feeding the
genuine payloads into ``register_from_payload``.
"""

import asyncio
import json
import threading
import unittest

from starcraft_commander.standing_orders import (
    CONSTRAINT_TO_STANDING_ORDER,
    KEEP_WORKER_PRODUCTION_CONSTRAINT_TEXT,
    KOREAN_STATUS_NONE,
    PREVENT_SUPPLY_BLOCK_CONSTRAINT_TEXT,
    SKIPPED_BOT_ERROR_PREFIX,
    SKIPPED_DEPOT_IN_PROGRESS,
    SKIPPED_NO_COMMAND_CENTER,
    SKIPPED_SUPPLY_BLOCKED,
    SKIPPED_SUPPLY_COMFORTABLE,
    SKIPPED_SUPPLY_UNREADABLE,
    SKIPPED_UNAFFORDABLE,
    STANDING_ORDER_KINDS,
    STANDING_ORDER_KOREAN_LABELS,
    SUPPLY_BLOCK_THRESHOLD,
    StandingOrderController,
    StandingOrderControllerInterface,
    StandingOrderTick,
)
from toycraft_commander.interpreter import (
    DEFAULT_COMMAND_INTERPRETER,
    KEEP_WORKER_PRODUCTION_CONSTRAINT,
    PREVENT_SUPPLY_BLOCK_CONSTRAINT,
)


def run(coro):
    return asyncio.run(coro)


class FakeCommandCenter:
    """Recording Command Center fake with adapter-style train orders."""

    def __init__(self, name="CommandCenter", *, is_ready=True, is_idle=True, tag=None):
        self.name = name
        self.is_ready = is_ready
        self.is_idle = is_idle
        self.tag = tag
        self.train_calls = []

    def train(self, type_id):
        self.train_calls.append(type_id)
        return ("train", self.name, type_id)


class FakeStructure:
    """Minimal structure fake carrying a type name and readiness flag."""

    def __init__(self, name, *, is_ready=True, is_idle=True):
        self.name = name
        self.is_ready = is_ready
        self.is_idle = is_idle


class FakePoint:
    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)


class FakeRamp:
    def __init__(self, x, y):
        self.top_center = FakePoint(x, y)


class FakeBot:
    """Configurable recording BotAI-like fake for standing-order ticks."""

    def __init__(
        self,
        *,
        structures=(),
        supply_left=10,
        affordable=True,
        with_do=True,
        start_location=None,
        main_base_ramp=None,
        already_pending_value=None,
    ):
        self.structures = list(structures)
        if supply_left is not None:
            self.supply_left = supply_left
        self.affordable = affordable
        self.can_afford_calls = []
        self.issued_commands = []
        self.build_calls = []
        if with_do:
            self.do = self._do
        if start_location is not None:
            self.start_location = start_location
        if main_base_ramp is not None:
            self.main_base_ramp = main_base_ramp
        if already_pending_value is not None:
            self.already_pending = lambda type_id: already_pending_value

    def unit_type_id_resolver(self, type_name):
        return type_name

    def can_afford(self, type_id):
        self.can_afford_calls.append(type_id)
        if callable(self.affordable):
            return self.affordable(type_id)
        return self.affordable

    def _do(self, command):
        self.issued_commands.append(command)
        return None

    async def build(self, type_id, near=None):
        self.build_calls.append((type_id, near))
        return None


class HostileAttributeError(RuntimeError):
    """Marker exception raised by booby-trapped bot attributes."""


class BoobyTrappedBot:
    """Bot whose every attribute access raises (worst-case hostility)."""

    def __getattr__(self, name):
        raise HostileAttributeError(name)


class StandingOrderConstantsTest(unittest.TestCase):
    def test_standing_order_kinds_are_the_two_planned_policies(self) -> None:
        self.assertEqual(
            ("keep_worker_production", "prevent_supply_block"),
            STANDING_ORDER_KINDS,
        )

    def test_mirrored_constraints_match_interpreter_constants_exactly(self) -> None:
        with self.subTest(kind="keep_worker_production"):
            self.assertEqual(
                KEEP_WORKER_PRODUCTION_CONSTRAINT,
                KEEP_WORKER_PRODUCTION_CONSTRAINT_TEXT,
            )
        with self.subTest(kind="prevent_supply_block"):
            self.assertEqual(
                PREVENT_SUPPLY_BLOCK_CONSTRAINT,
                PREVENT_SUPPLY_BLOCK_CONSTRAINT_TEXT,
            )

    def test_constraint_mapping_covers_both_kinds_with_interpreter_text(self) -> None:
        self.assertEqual(
            {
                KEEP_WORKER_PRODUCTION_CONSTRAINT: "keep_worker_production",
                PREVENT_SUPPLY_BLOCK_CONSTRAINT: "prevent_supply_block",
            },
            CONSTRAINT_TO_STANDING_ORDER,
        )
        for constraint, kind in CONSTRAINT_TO_STANDING_ORDER.items():
            with self.subTest(constraint=constraint):
                self.assertIn(kind, STANDING_ORDER_KINDS)

    def test_korean_labels_cover_every_kind(self) -> None:
        self.assertEqual(
            set(STANDING_ORDER_KINDS), set(STANDING_ORDER_KOREAN_LABELS)
        )

    def test_supply_block_threshold_is_three(self) -> None:
        self.assertEqual(3, SUPPLY_BLOCK_THRESHOLD)


class StandingOrderTickContractTest(unittest.TestCase):
    def test_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            StandingOrderTick(kind="patrol_forever", skipped_reason="x")

    def test_rejects_silent_tick_without_reason_or_actions(self) -> None:
        with self.assertRaises(ValueError):
            StandingOrderTick(kind="keep_worker_production")

    def test_rejects_tick_claiming_both_actions_and_skip(self) -> None:
        with self.assertRaises(ValueError):
            StandingOrderTick(
                kind="keep_worker_production",
                actions_issued=("train_scv",),
                skipped_reason="자원 부족",
            )

    def test_rejects_non_string_action_entries(self) -> None:
        for bad_actions in ((1,), ("",), (None,)):
            with self.subTest(actions=bad_actions):
                with self.assertRaises(ValueError):
                    StandingOrderTick(
                        kind="keep_worker_production",
                        actions_issued=bad_actions,
                    )

    def test_to_dict_is_json_ready_and_honest(self) -> None:
        issued = StandingOrderTick(
            kind="keep_worker_production", actions_issued=("train_scv",)
        )
        skipped = StandingOrderTick(
            kind="prevent_supply_block", skipped_reason=SKIPPED_SUPPLY_COMFORTABLE
        )
        with self.subTest(tick="issued"):
            payload = issued.to_dict()
            self.assertEqual(
                {
                    "kind": "keep_worker_production",
                    "actions_issued": ["train_scv"],
                    "skipped_reason": "",
                    "issued": True,
                },
                payload,
            )
            json.dumps(payload)
        with self.subTest(tick="skipped"):
            payload = skipped.to_dict()
            self.assertFalse(payload["issued"])
            self.assertEqual(SKIPPED_SUPPLY_COMFORTABLE, payload["skipped_reason"])
            json.dumps(payload)


class StandingOrderRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = StandingOrderController()

    def test_satisfies_runtime_checkable_interface(self) -> None:
        self.assertIsInstance(self.controller, StandingOrderControllerInterface)

    def test_register_is_idempotent(self) -> None:
        self.assertTrue(self.controller.register("keep_worker_production"))
        self.assertFalse(self.controller.register("keep_worker_production"))
        self.assertEqual(("keep_worker_production",), self.controller.active_kinds())

    def test_cancel_is_idempotent(self) -> None:
        self.controller.register("prevent_supply_block")
        self.assertTrue(self.controller.cancel("prevent_supply_block"))
        self.assertFalse(self.controller.cancel("prevent_supply_block"))
        self.assertEqual((), self.controller.active_kinds())

    def test_register_and_cancel_reject_unknown_kinds(self) -> None:
        for method_name in ("register", "cancel"):
            with self.subTest(method=method_name):
                with self.assertRaises(ValueError):
                    getattr(self.controller, method_name)("attack_forever")

    def test_active_kinds_keep_canonical_order(self) -> None:
        self.controller.register("prevent_supply_block")
        self.controller.register("keep_worker_production")
        self.assertEqual(STANDING_ORDER_KINDS, self.controller.active_kinds())

    def test_concurrent_register_grants_exactly_one_winner(self) -> None:
        thread_count = 16
        barrier = threading.Barrier(thread_count)
        results = []
        results_lock = threading.Lock()

        def attempt() -> None:
            barrier.wait()
            won = self.controller.register("keep_worker_production")
            with results_lock:
                results.append(won)

        threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(thread_count, len(results))
        self.assertEqual(1, sum(1 for won in results if won))
        self.assertEqual(("keep_worker_production",), self.controller.active_kinds())

    def test_korean_status_lines(self) -> None:
        with self.subTest(state="empty"):
            self.assertEqual(KOREAN_STATUS_NONE, self.controller.korean_status())
        with self.subTest(state="worker_production"):
            self.controller.register("keep_worker_production")
            self.assertEqual(
                "상비 명령: 지속 SCV 생산 활성", self.controller.korean_status()
            )
        with self.subTest(state="both"):
            self.controller.register("prevent_supply_block")
            self.assertEqual(
                "상비 명령: 지속 SCV 생산 활성, 보급 차단 방지 활성",
                self.controller.korean_status(),
            )

    def test_to_dict_is_json_ready(self) -> None:
        self.controller.register("keep_worker_production")
        payload = self.controller.to_dict()
        self.assertEqual(["keep_worker_production"], payload["active_kinds"])
        self.assertIn("지속 SCV 생산", payload["korean_status"])
        json.dumps(payload)


class RegisterFromPayloadTest(unittest.TestCase):
    """Pins the cross-module contract against the genuine interpreter."""

    def setUp(self) -> None:
        self.controller = StandingOrderController()

    def test_genuine_train_worker_payload_registers_worker_production(self) -> None:
        payload = DEFAULT_COMMAND_INTERPRETER.interpret_text("SCV 계속 찍어")
        self.assertIsNotNone(payload)
        self.assertIn(KEEP_WORKER_PRODUCTION_CONSTRAINT_TEXT, payload.constraints)
        self.assertEqual(
            ("keep_worker_production",),
            self.controller.register_from_payload(payload),
        )
        with self.subTest(part="second registration is silent"):
            self.assertEqual((), self.controller.register_from_payload(payload))
        self.assertEqual(("keep_worker_production",), self.controller.active_kinds())

    def test_genuine_supply_block_payload_registers_supply_guard(self) -> None:
        payload = DEFAULT_COMMAND_INTERPRETER.interpret_text("서플 막히지 않게 해줘")
        self.assertIsNotNone(payload)
        self.assertIn(PREVENT_SUPPLY_BLOCK_CONSTRAINT_TEXT, payload.constraints)
        self.assertEqual(
            ("prevent_supply_block",),
            self.controller.register_from_payload(payload),
        )

    def test_mapping_payload_with_both_constraints_registers_both(self) -> None:
        payload = {
            "intent": "TRAIN_WORKER",
            "constraints": [
                KEEP_WORKER_PRODUCTION_CONSTRAINT_TEXT,
                PREVENT_SUPPLY_BLOCK_CONSTRAINT_TEXT,
            ],
        }
        self.assertEqual(
            STANDING_ORDER_KINDS,
            self.controller.register_from_payload(payload),
        )

    def test_unrelated_or_hostile_payloads_register_nothing(self) -> None:
        cases = {
            "one_shot_constraint": {"constraints": ["train requested SCV count"]},
            "constraints_is_string": {"constraints": "keep SCV production continuous"},
            "constraints_missing": object(),
            "constraints_none": {"constraints": None},
            "non_string_entries": {"constraints": [42, None]},
        }
        for label, payload in cases.items():
            with self.subTest(payload=label):
                self.assertEqual(
                    (), self.controller.register_from_payload(payload)
                )
        self.assertEqual((), self.controller.active_kinds())


class KeepWorkerProductionTickTest(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = StandingOrderController()
        self.controller.register("keep_worker_production")

    def tick(self, bot):
        return run(self.controller.tick(bot))

    def test_trains_one_scv_per_ready_idle_command_center(self) -> None:
        first = FakeCommandCenter(tag=101)
        second = FakeCommandCenter(tag=202)
        bot = FakeBot(structures=(first, second), supply_left=10)
        (tick,) = self.tick(bot)
        self.assertEqual("keep_worker_production", tick.kind)
        self.assertEqual(("train_scv@101", "train_scv@202"), tick.actions_issued)
        self.assertEqual("", tick.skipped_reason)
        with self.subTest(part="exactly one train order per CC"):
            self.assertEqual(["SCV"], first.train_calls)
            self.assertEqual(["SCV"], second.train_calls)
        with self.subTest(part="orders routed through bot.do"):
            self.assertEqual(2, len(bot.issued_commands))

    def test_tick_is_bounded_one_order_per_cc_even_with_infinite_money(self) -> None:
        command_center = FakeCommandCenter()
        bot = FakeBot(structures=(command_center,), supply_left=100, affordable=True)
        (tick,) = self.tick(bot)
        self.assertEqual(1, len(tick.actions_issued))
        self.assertEqual(1, len(command_center.train_calls))
        with self.subTest(part="next throttled tick trains again"):
            (second_tick,) = self.tick(bot)
            self.assertEqual(1, len(second_tick.actions_issued))
            self.assertEqual(2, len(command_center.train_calls))

    def test_skips_with_reason_when_unaffordable(self) -> None:
        command_center = FakeCommandCenter()
        bot = FakeBot(structures=(command_center,), affordable=False)
        (tick,) = self.tick(bot)
        self.assertEqual(SKIPPED_UNAFFORDABLE, tick.skipped_reason)
        self.assertEqual((), tick.actions_issued)
        self.assertEqual([], command_center.train_calls)

    def test_skips_with_reason_when_no_command_center(self) -> None:
        cases = {
            "empty_structures": FakeBot(structures=()),
            "only_other_structures": FakeBot(
                structures=(FakeStructure("Barracks"),)
            ),
            "cc_busy": FakeBot(
                structures=(FakeCommandCenter(is_idle=False),)
            ),
            "cc_unfinished": FakeBot(
                structures=(FakeCommandCenter(is_ready=False),)
            ),
        }
        for label, bot in cases.items():
            with self.subTest(bot=label):
                (tick,) = self.tick(bot)
                self.assertEqual(SKIPPED_NO_COMMAND_CENTER, tick.skipped_reason)

    def test_skips_with_reason_when_supply_blocked(self) -> None:
        command_center = FakeCommandCenter()
        bot = FakeBot(structures=(command_center,), supply_left=0)
        (tick,) = self.tick(bot)
        self.assertEqual(SKIPPED_SUPPLY_BLOCKED, tick.skipped_reason)
        self.assertEqual([], command_center.train_calls)

    def test_supply_budget_caps_orders_below_command_center_count(self) -> None:
        first = FakeCommandCenter()
        second = FakeCommandCenter()
        bot = FakeBot(structures=(first, second), supply_left=1)
        (tick,) = self.tick(bot)
        self.assertEqual(1, len(tick.actions_issued))
        self.assertEqual(
            1, len(first.train_calls) + len(second.train_calls)
        )

    def test_mid_batch_money_run_out_still_reports_real_orders(self) -> None:
        budget = {"remaining": 1}

        def affordable(type_id):
            if budget["remaining"] <= 0:
                return False
            budget["remaining"] -= 1
            return True

        first = FakeCommandCenter()
        second = FakeCommandCenter()
        bot = FakeBot(structures=(first, second), affordable=affordable)
        (tick,) = self.tick(bot)
        self.assertTrue(tick.issued)
        self.assertEqual(1, len(tick.actions_issued))

    def test_train_orders_work_without_bot_do(self) -> None:
        command_center = FakeCommandCenter()
        bot = FakeBot(structures=(command_center,), with_do=False)
        (tick,) = self.tick(bot)
        self.assertTrue(tick.issued)
        self.assertEqual(["SCV"], command_center.train_calls)


class PreventSupplyBlockTickTest(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = StandingOrderController()
        self.controller.register("prevent_supply_block")

    def tick(self, bot):
        return run(self.controller.tick(bot))

    def test_triggers_exactly_one_depot_then_waits_while_pending(self) -> None:
        bot = FakeBot(
            supply_left=2,
            start_location=FakePoint(30.0, 30.0),
        )
        with self.subTest(stage="first low-supply tick builds one depot"):
            (tick,) = self.tick(bot)
            self.assertEqual(("build_supplydepot",), tick.actions_issued)
            self.assertEqual(1, len(bot.build_calls))
            self.assertEqual("SUPPLYDEPOT", bot.build_calls[0][0])
        with self.subTest(stage="pending depot suppresses the second order"):
            bot.structures.append(FakeStructure("SupplyDepot", is_ready=False))
            (tick,) = self.tick(bot)
            self.assertEqual(SKIPPED_DEPOT_IN_PROGRESS, tick.skipped_reason)
            self.assertEqual(1, len(bot.build_calls))
        with self.subTest(stage="finished depot re-arms the guard"):
            bot.structures[-1].is_ready = True
            (tick,) = self.tick(bot)
            self.assertEqual(("build_supplydepot",), tick.actions_issued)
            self.assertEqual(2, len(bot.build_calls))

    def test_already_pending_hook_suppresses_duplicate_depot(self) -> None:
        bot = FakeBot(
            supply_left=1,
            start_location=FakePoint(30.0, 30.0),
            already_pending_value=1,
        )
        (tick,) = self.tick(bot)
        self.assertEqual(SKIPPED_DEPOT_IN_PROGRESS, tick.skipped_reason)
        self.assertEqual([], bot.build_calls)

    def test_skips_when_supply_is_comfortable(self) -> None:
        bot = FakeBot(
            supply_left=SUPPLY_BLOCK_THRESHOLD + 1,
            start_location=FakePoint(30.0, 30.0),
        )
        (tick,) = self.tick(bot)
        self.assertEqual(SKIPPED_SUPPLY_COMFORTABLE, tick.skipped_reason)
        self.assertEqual([], bot.build_calls)

    def test_threshold_boundary_triggers_at_exactly_three(self) -> None:
        bot = FakeBot(
            supply_left=SUPPLY_BLOCK_THRESHOLD,
            start_location=FakePoint(30.0, 30.0),
        )
        (tick,) = self.tick(bot)
        self.assertEqual(("build_supplydepot",), tick.actions_issued)

    def test_skips_with_reason_when_unaffordable(self) -> None:
        bot = FakeBot(
            supply_left=0,
            affordable=False,
            start_location=FakePoint(30.0, 30.0),
        )
        (tick,) = self.tick(bot)
        self.assertEqual(SKIPPED_UNAFFORDABLE, tick.skipped_reason)
        self.assertEqual([], bot.build_calls)

    def test_skips_honestly_when_supply_left_is_unreadable(self) -> None:
        bot = FakeBot(supply_left=None, start_location=FakePoint(30.0, 30.0))
        (tick,) = self.tick(bot)
        self.assertEqual(SKIPPED_SUPPLY_UNREADABLE, tick.skipped_reason)

    def test_prefers_main_base_ramp_top_center_for_placement(self) -> None:
        ramp = FakeRamp(38.0, 36.0)
        bot = FakeBot(
            supply_left=2,
            start_location=FakePoint(30.0, 30.0),
            main_base_ramp=ramp,
        )
        (tick,) = self.tick(bot)
        self.assertTrue(tick.issued)
        self.assertIs(ramp.top_center, bot.build_calls[0][1])

    def test_falls_back_to_offset_start_location_without_ramp(self) -> None:
        bot = FakeBot(supply_left=2, start_location=FakePoint(30.0, 30.0))
        (tick,) = self.tick(bot)
        self.assertTrue(tick.issued)
        near = bot.build_calls[0][1]
        x, y = (near.x, near.y) if hasattr(near, "x") else near
        with self.subTest(part="offset moves away from the town hall"):
            self.assertNotEqual((30.0, 30.0), (x, y))


class HostileBotTickTest(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = StandingOrderController()
        self.controller.register("keep_worker_production")
        self.controller.register("prevent_supply_block")

    def test_bot_missing_everything_yields_skip_reasons_not_exceptions(self) -> None:
        ticks = run(self.controller.tick(object()))
        self.assertEqual(2, len(ticks))
        by_kind = {tick.kind: tick for tick in ticks}
        with self.subTest(kind="keep_worker_production"):
            self.assertEqual(
                SKIPPED_NO_COMMAND_CENTER,
                by_kind["keep_worker_production"].skipped_reason,
            )
        with self.subTest(kind="prevent_supply_block"):
            self.assertEqual(
                SKIPPED_SUPPLY_UNREADABLE,
                by_kind["prevent_supply_block"].skipped_reason,
            )

    def test_booby_trapped_attributes_are_contained_with_honest_reason(self) -> None:
        ticks = run(self.controller.tick(BoobyTrappedBot()))
        self.assertEqual(2, len(ticks))
        for tick in ticks:
            with self.subTest(kind=tick.kind):
                self.assertFalse(tick.issued)
                self.assertIn(SKIPPED_BOT_ERROR_PREFIX, tick.skipped_reason)
                self.assertIn("HostileAttributeError", tick.skipped_reason)

    def test_tick_without_active_orders_is_a_no_op(self) -> None:
        controller = StandingOrderController()
        self.assertEqual((), run(controller.tick(BoobyTrappedBot())))


if __name__ == "__main__":
    unittest.main()
