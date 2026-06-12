"""Tests for the thread-safe commander event memory (W2)."""

from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace

from starcraft_commander.event_memory import (
    DEFAULT_MAX_EVENTS,
    KOREAN_EMPTY_MEMORY_SUMMARY,
    CommanderEvent,
    CommanderEventMemory,
    CommanderEventRecorderInterface,
)
from starcraft_commander.live_pipeline import SC2CommandOutcome


def _mapping_outcome(text: str = "마린 뽑아", status: str = "executed") -> dict[str, object]:
    return {
        "command_text": text,
        "status": status,
        "narration": f"{text} 명령을 실행했습니다.",
        "intent_name": "TRAIN_ARMY",
    }


class CommanderEventTest(unittest.TestCase):
    def test_event_validation_and_defaults(self) -> None:
        event = CommanderEvent(
            seq=1,
            command_text="배럭 지어",
            status="executed",
            narration="배럭 건설을 시작했습니다.",
        )
        self.assertEqual(event.intent_name, "")
        self.assertIsNone(event.game_time_seconds)
        self.assertEqual(event.detail, {})

    def test_event_rejects_invalid_fields(self) -> None:
        base = {
            "command_text": "배럭 지어",
            "status": "executed",
            "narration": "배럭 건설을 시작했습니다.",
        }
        cases: tuple[tuple[str, dict[str, object], type[Exception]], ...] = (
            ("zero seq", {"seq": 0}, ValueError),
            ("bool seq", {"seq": True}, TypeError),
            ("blank status", {"status": "   "}, ValueError),
            ("blank narration", {"narration": ""}, ValueError),
            ("negative game time", {"game_time_seconds": -1.0}, ValueError),
            ("nan game time", {"game_time_seconds": float("nan")}, ValueError),
            ("bool game time", {"game_time_seconds": True}, TypeError),
            ("non-mapping detail", {"detail": ["x"]}, TypeError),
        )
        for label, overrides, exc in cases:
            with self.subTest(label=label):
                kwargs: dict[str, object] = {"seq": 1, **base, **overrides}
                with self.assertRaises(exc):
                    CommanderEvent(**kwargs)  # type: ignore[arg-type]

    def test_event_detail_is_defensively_copied(self) -> None:
        detail = {"intent_dsl": {"intent": "DEFEND"}}
        event = CommanderEvent(
            seq=1,
            command_text="입구 막아",
            status="executed",
            narration="입구 방어를 시작했습니다.",
            detail=detail,
        )
        detail["intent_dsl"] = {"intent": "HARASS"}
        self.assertEqual(event.detail, {"intent_dsl": {"intent": "DEFEND"}})

    def test_event_to_dict_json_round_trip(self) -> None:
        event = CommanderEvent(
            seq=7,
            command_text="SCV 3기 미네랄로",
            status="executed",
            narration="SCV 3기를 미네랄에 배정했습니다.",
            intent_name="GATHER_RESOURCE",
            game_time_seconds=83.5,
            detail={"intent_dsl": {"intent": "GATHER_RESOURCE", "worker_count": 3}},
        )
        payload = event.to_dict()
        restored = json.loads(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(restored, payload)
        self.assertEqual(restored["seq"], 7)
        self.assertEqual(restored["intent_name"], "GATHER_RESOURCE")
        self.assertEqual(restored["game_time_seconds"], 83.5)
        self.assertEqual(
            restored["detail"]["intent_dsl"]["intent"], "GATHER_RESOURCE"
        )


class CommanderEventMemoryRecordTest(unittest.TestCase):
    def test_seq_starts_at_one_and_is_monotonic(self) -> None:
        memory = CommanderEventMemory()
        seqs = [memory.record(_mapping_outcome(f"명령 {i}")).seq for i in range(1, 6)]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])
        self.assertEqual(memory.latest_seq(), 5)

    def test_record_accepts_plain_mapping(self) -> None:
        memory = CommanderEventMemory()
        event = memory.record(
            {
                "command_text": "벙커 수리해",
                "status": "blocked",
                "narration": "SCV가 없어 수리할 수 없습니다.",
                "intent_dsl": {"intent": "REPAIR", "structure": "벙커"},
                "game_time_seconds": 42.0,
            }
        )
        self.assertEqual(event.command_text, "벙커 수리해")
        self.assertEqual(event.status, "blocked")
        self.assertEqual(event.intent_name, "REPAIR")
        self.assertEqual(event.game_time_seconds, 42.0)
        self.assertEqual(event.detail, {"intent_dsl": {"intent": "REPAIR", "structure": "벙커"}})

    def test_explicit_game_time_wins_over_mapping_value(self) -> None:
        memory = CommanderEventMemory()
        event = memory.record(
            {**_mapping_outcome(), "game_time_seconds": 10.0},
            game_time_seconds=99.0,
        )
        self.assertEqual(event.game_time_seconds, 99.0)

    def test_record_accepts_real_sc2_command_outcome(self) -> None:
        memory = CommanderEventMemory()
        outcome = SC2CommandOutcome(
            command_text="앞마당 정찰 보내",
            status="blocked",
            narration="정찰 가능한 유닛이 없어 거부합니다. 대안: SCV를 먼저 생산하세요.",
            intent_dsl={"intent": "SCOUT", "target": "enemy_natural"},
        )
        event = memory.record(outcome, game_time_seconds=12.25)
        self.assertEqual(event.seq, 1)
        self.assertEqual(event.command_text, "앞마당 정찰 보내")
        self.assertEqual(event.status, "blocked")
        self.assertEqual(event.narration, outcome.narration)
        self.assertEqual(event.intent_name, "SCOUT")
        self.assertEqual(event.game_time_seconds, 12.25)
        self.assertEqual(
            event.detail,
            {"intent_dsl": {"intent": "SCOUT", "target": "enemy_natural"}},
        )

    def test_record_prefers_plan_intent_name_on_duck_typed_outcomes(self) -> None:
        memory = CommanderEventMemory()
        outcome = SimpleNamespace(
            command_text="입구 막아",
            status="executed",
            narration="입구 방어 배치를 완료했습니다.",
            plan=SimpleNamespace(intent_name="DEFEND"),
            intent_dsl={"intent": "SHOULD_NOT_WIN"},
        )
        event = memory.record(outcome)
        self.assertEqual(event.intent_name, "DEFEND")
        self.assertEqual(event.detail, {"intent_dsl": {"intent": "SHOULD_NOT_WIN"}})

    def test_record_rejects_outcomes_missing_required_fields(self) -> None:
        memory = CommanderEventMemory()
        cases: tuple[tuple[str, object], ...] = (
            ("mapping without status", {"command_text": "x", "narration": "y"}),
            ("mapping blank narration", {"status": "executed", "narration": " "}),
            ("object without narration", SimpleNamespace(command_text="x", status="executed")),
            ("bare object", object()),
        )
        for label, outcome in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    memory.record(outcome)
        self.assertEqual(memory.latest_seq(), 0)
        self.assertEqual(len(memory), 0)

    def test_record_rejects_non_mapping_detail(self) -> None:
        memory = CommanderEventMemory()
        with self.assertRaises(TypeError):
            memory.record({**_mapping_outcome(), "detail": ["not", "a", "mapping"]})

    def test_constructor_validates_max_events(self) -> None:
        for label, value, exc in (
            ("zero", 0, ValueError),
            ("negative", -3, ValueError),
            ("bool", True, TypeError),
            ("float", 2.5, TypeError),
        ):
            with self.subTest(label=label):
                with self.assertRaises(exc):
                    CommanderEventMemory(max_events=value)  # type: ignore[arg-type]
        self.assertEqual(CommanderEventMemory().max_events, DEFAULT_MAX_EVENTS)

    def test_memory_satisfies_recorder_protocol(self) -> None:
        self.assertIsInstance(CommanderEventMemory(), CommanderEventRecorderInterface)


class CommanderEventMemoryBufferTest(unittest.TestCase):
    def test_ring_buffer_trims_oldest_but_keeps_seq(self) -> None:
        memory = CommanderEventMemory(max_events=3)
        for i in range(1, 6):
            memory.record(_mapping_outcome(f"명령 {i}"))
        events = memory.recent(10)
        self.assertEqual(len(memory), 3)
        self.assertEqual([event.seq for event in events], [3, 4, 5])
        self.assertEqual(events[0].command_text, "명령 3")
        self.assertEqual(memory.latest_seq(), 5)

    def test_recent_returns_oldest_first_and_validates_n(self) -> None:
        memory = CommanderEventMemory()
        for i in range(1, 5):
            memory.record(_mapping_outcome(f"명령 {i}"))
        self.assertEqual([e.seq for e in memory.recent(2)], [3, 4])
        self.assertEqual(memory.recent(0), ())
        self.assertEqual(len(memory.recent(99)), 4)
        with self.assertRaises(ValueError):
            memory.recent(-1)
        with self.assertRaises(TypeError):
            memory.recent(2.0)  # type: ignore[arg-type]

    def test_since_boundary_semantics(self) -> None:
        memory = CommanderEventMemory()
        for i in range(1, 4):
            memory.record(_mapping_outcome(f"명령 {i}"))
        latest = memory.latest_seq()
        with self.subTest(boundary="since(latest) is empty"):
            self.assertEqual(memory.since(latest), ())
        with self.subTest(boundary="since(latest - 1) returns only the newest"):
            self.assertEqual([e.seq for e in memory.since(latest - 1)], [latest])
        with self.subTest(boundary="since(0) returns everything stored"):
            self.assertEqual([e.seq for e in memory.since(0)], [1, 2, 3])
        with self.subTest(boundary="non-int cursor rejected"):
            with self.assertRaises(TypeError):
                memory.since("3")  # type: ignore[arg-type]

    def test_since_after_trim_only_sees_surviving_events(self) -> None:
        memory = CommanderEventMemory(max_events=2)
        for i in range(1, 5):
            memory.record(_mapping_outcome(f"명령 {i}"))
        self.assertEqual([e.seq for e in memory.since(0)], [3, 4])

    def test_clear_empties_buffer_but_keeps_counter(self) -> None:
        memory = CommanderEventMemory()
        memory.record(_mapping_outcome("명령 1"))
        memory.record(_mapping_outcome("명령 2"))
        memory.clear()
        self.assertEqual(len(memory), 0)
        self.assertEqual(memory.recent(5), ())
        self.assertEqual(memory.latest_seq(), 2)
        event = memory.record(_mapping_outcome("명령 3"))
        self.assertEqual(event.seq, 3)

    def test_to_dicts_is_json_ready_and_ordered(self) -> None:
        memory = CommanderEventMemory()
        memory.record(_mapping_outcome("명령 1"), game_time_seconds=1.0)
        memory.record(_mapping_outcome("명령 2", status="blocked"))
        payloads = memory.to_dicts()
        restored = json.loads(json.dumps(payloads, ensure_ascii=False))
        self.assertEqual(restored, [dict(p) for p in payloads])
        self.assertEqual([p["seq"] for p in payloads], [1, 2])
        self.assertEqual(payloads[0]["game_time_seconds"], 1.0)
        self.assertIsNone(payloads[1]["game_time_seconds"])
        self.assertEqual(payloads[1]["status"], "blocked")


class CommanderEventMemoryConcurrencyTest(unittest.TestCase):
    def test_concurrent_record_keeps_count_and_seq_integrity(self) -> None:
        memory = CommanderEventMemory(max_events=1000)
        threads_count, per_thread = 8, 50
        total = threads_count * per_thread
        start = threading.Barrier(threads_count)
        recorded: list[list[int]] = [[] for _ in range(threads_count)]
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                start.wait(timeout=10)
                for i in range(per_thread):
                    event = memory.record(_mapping_outcome(f"스레드 {index} 명령 {i}"))
                    recorded[index].append(event.seq)
            except BaseException as exc:  # pragma: no cover - failure reporting
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(index,))
            for index in range(threads_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(len(memory), total)
        self.assertEqual(memory.latest_seq(), total)
        all_seqs = sorted(seq for seqs in recorded for seq in seqs)
        self.assertEqual(all_seqs, list(range(1, total + 1)))
        with self.subTest(check="per-thread seqs strictly increasing"):
            for index, seqs in enumerate(recorded):
                self.assertEqual(seqs, sorted(seqs), msg=f"thread {index}")
        with self.subTest(check="stored buffer ordered by seq"):
            stored = [event.seq for event in memory.since(0)]
            self.assertEqual(stored, list(range(1, total + 1)))

    def test_concurrent_record_with_trim_keeps_newest_window(self) -> None:
        memory = CommanderEventMemory(max_events=100)
        threads_count, per_thread = 8, 50

        def worker() -> None:
            for i in range(per_thread):
                memory.record(_mapping_outcome(f"명령 {i}"))

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        total = threads_count * per_thread
        self.assertEqual(len(memory), 100)
        self.assertEqual(memory.latest_seq(), total)
        stored = [event.seq for event in memory.since(0)]
        self.assertEqual(stored, list(range(total - 99, total + 1)))


class KoreanSummaryTest(unittest.TestCase):
    def test_empty_memory_summary(self) -> None:
        memory = CommanderEventMemory()
        self.assertEqual(memory.korean_summary(), KOREAN_EMPTY_MEMORY_SUMMARY)
        self.assertIn("최근 명령 0건", memory.korean_summary())

    def test_summary_lists_status_and_narration_head(self) -> None:
        memory = CommanderEventMemory()
        memory.record(
            {
                "command_text": "배럭 지어",
                "status": "executed",
                "narration": "배럭 건설을 시작했습니다.",
            }
        )
        memory.record(
            {
                "command_text": "사령부 날려",
                "status": "blocked",
                "narration": "사령부 이동은 지원하지 않습니다. 대안: 앞마당 확장을 명령하세요.",
            }
        )
        summary = memory.korean_summary(n=5)
        lines = summary.splitlines()
        self.assertEqual(lines[0], "최근 명령 2건:")
        self.assertEqual(len(lines), 3)
        self.assertIn("#1", lines[1])
        self.assertIn("[executed]", lines[1])
        self.assertIn("배럭 건설을 시작했습니다.", lines[1])
        self.assertIn("#2", lines[2])
        self.assertIn("[blocked]", lines[2])
        self.assertIn("사령부 이동은 지원하지 않습니다", lines[2])

    def test_summary_limits_to_n_and_truncates_long_narration(self) -> None:
        memory = CommanderEventMemory()
        long_narration = "아주 " * 40 + "긴 서술입니다."
        for i in range(1, 8):
            memory.record(
                {
                    "command_text": f"명령 {i}",
                    "status": "executed",
                    "narration": long_narration if i == 7 else f"{i}번 완료했습니다.",
                }
            )
        summary = memory.korean_summary(n=3)
        lines = summary.splitlines()
        self.assertEqual(lines[0], "최근 명령 3건:")
        self.assertEqual(len(lines), 4)
        self.assertIn("#5", lines[1])
        self.assertIn("#7", lines[3])
        self.assertNotIn("#4", summary)
        self.assertLess(len(lines[3]), len(long_narration))
        self.assertIn("…", lines[3])
        with self.assertRaises(ValueError):
            memory.korean_summary(n=0)

    def test_summary_uses_first_narration_line_only(self) -> None:
        memory = CommanderEventMemory()
        memory.record(
            {
                "command_text": "상황 보고",
                "status": "read_only",
                "narration": "현재 미네랄 350.\n보급 12/15.\n병력: 마린 4기.",
            }
        )
        summary = memory.korean_summary()
        lines = summary.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("현재 미네랄 350.", lines[1])
        self.assertNotIn("보급 12/15", summary)


if __name__ == "__main__":
    unittest.main()
