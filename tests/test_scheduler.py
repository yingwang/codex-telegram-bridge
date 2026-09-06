from __future__ import annotations

import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import bridge
import scheduler
from test_bridge import make_config


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        artifacts = Path(__file__).resolve().parents[1] / ".test-artifacts"
        artifacts.mkdir(exist_ok=True)
        self.temp_dir = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        bridge_config = make_config(root)
        bridge_config.inbox_enabled = True
        self.config = scheduler.SchedulerConfig(
            bridge_config=bridge_config,
            target_chat_id="123",
            timezone=ZoneInfo("Europe/Stockholm"),
            runtime_dir=root / "scheduler",
            state_path=root / "scheduler" / "state.json",
            lock_path=root / "scheduler" / "scheduler.lock",
            events_path=root / "scheduler" / "events.jsonl",
            codex_bin="codex",
            codex_workdir=root,
            codex_timeout_seconds=30,
            max_rp_per_day=5,
        )
        self.now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        self.clock = mock.patch.object(scheduler, "utc_now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        state = scheduler.default_state(self.now, self.config.timezone, random.Random(1))
        state["news"]["enabled"] = False
        state["rp"].update({
            "status": "error",
            "last_error": "previous generation failed",
            "next_due_at": scheduler.iso_utc(self.now - timedelta(minutes=1)),
        })
        scheduler.save_state(self.config, state, self.now)

    def state(self) -> dict:
        return scheduler.load_state(self.config, self.now, random.Random(1))

    def tick(self) -> int:
        return scheduler.run_session_tick(
            self.config,
            thread_id="test-thread",
            session_started_at=self.now - timedelta(minutes=10),
            now=self.now,
            rng=random.Random(1),
        )

    def test_ping_schema_is_bundled_and_matches_decision_fields(self) -> None:
        schema = json.loads(scheduler.PING_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["type"], "object")
        self.assertIs(schema["additionalProperties"], False)
        fields = {"send": "boolean", "message": "string", "next_delay_minutes": "integer"}
        self.assertEqual(set(schema["required"]), set(fields))
        self.assertEqual({k: v["type"] for k, v in schema["properties"].items()}, fields)
        for engagement in ("hot", "engaged", "normal", "quiet", "cold", "new"):
            minimum, maximum = scheduler.delay_band(engagement)
            self.assertGreaterEqual(minimum, schema["properties"]["next_delay_minutes"]["minimum"])
            self.assertLessEqual(maximum, schema["properties"]["next_delay_minutes"]["maximum"])

    def test_due_ping_recovers_from_error_and_reschedules_after_send(self) -> None:
        decision = json.dumps({"send": True, "message": "Hello.", "next_delay_minutes": 360})

        def fake_codex(args: list[str], **kwargs: object) -> SimpleNamespace:
            # Unlike a plain mock, check the on-disk dependency passed to Codex.
            schema_path = Path(args[args.index("--output-schema") + 1])
            self.assertEqual(json.loads(schema_path.read_text())["type"], "object")
            self.assertEqual(args[-2:], ["test-thread", "-"])
            self.assertIn("fork", args)
            self.assertIn("--ephemeral", args)
            self.assertIn("--ignore-user-config", args)
            self.assertIn("read-only", args)
            self.assertNotIn("resume", args)
            self.assertNotIn("--last", args)
            self.assertNotIn("TELEGRAM_BOT_TOKEN", kwargs["env"])
            self.assertNotIn("UNRELATED_PRIVATE_KEY", kwargs["env"])
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(decision, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.dict(scheduler.os.environ, {
            "TELEGRAM_BOT_TOKEN": "test-secret",
            "UNRELATED_PRIVATE_KEY": "test-secret",
        }), mock.patch.object(scheduler, "ensure_codex_available"), mock.patch.object(
            scheduler.subprocess, "run", side_effect=fake_codex
        ) as generate, mock.patch.object(bridge, "api_call", return_value={"message_id": 42}) as send:
            self.assertEqual(self.tick(), 0)
            self.assertEqual(self.tick(), 0)
        generate.assert_called_once()
        send.assert_called_once_with(
            self.config.bridge_config, "sendMessage",
            {"chat_id": "123", "text": "Hello.", "disable_web_page_preview": True},
            timeout=30,
        )
        state = self.state()
        self.assertEqual(state["rp"]["status"], "sent")
        self.assertIsNone(state["rp"]["last_error"])
        self.assertEqual(state["rp"]["sent_today"], 1)
        self.assertGreater(scheduler.parse_timestamp(state["rp"]["next_due_at"]), self.now)
        self.assertFalse(state["news"]["enabled"])
        event = json.loads(self.config.bridge_config.inbox_jsonl_path.read_text())
        self.assertEqual(event["source"], "scheduled_rp")
        self.assertEqual(event["chat_id"], "123")
        self.assertEqual(event["message_id"], "42")

    def test_skipped_ping_clears_error_and_still_reschedules(self) -> None:
        decision = json.dumps({"send": False, "message": "", "next_delay_minutes": 360})
        with mock.patch.object(scheduler, "run_codex_session_turn", return_value=decision), mock.patch.object(
            bridge, "send_message"
        ) as send:
            self.tick()
        send.assert_not_called()
        state = self.state()["rp"]
        self.assertEqual(state["status"], "skipped")
        self.assertIsNone(state["last_error"])
        self.assertGreater(scheduler.parse_timestamp(state["next_due_at"]), self.now)

    def test_generation_error_retries_without_sending(self) -> None:
        with mock.patch.object(scheduler, "run_codex_session_turn", side_effect=scheduler.SchedulerError("failed")), mock.patch.object(
            bridge, "send_message"
        ) as send:
            with self.assertRaises(scheduler.SchedulerError):
                self.tick()
        send.assert_not_called()
        state = self.state()["rp"]
        self.assertEqual(state["status"], "error")
        self.assertGreater(scheduler.parse_timestamp(state["next_due_at"]), self.now)

    def test_unanswered_pings_continue_with_long_delay(self) -> None:
        for engagement in ("quiet", "cold"):
            with self.subTest(engagement=engagement):
                state = self.state()
                state["rp"].update({
                    "status": "cooldown",
                    "sent_local_date": self.now.date().isoformat(),
                    "sent_today": 2,
                    "consecutive_unanswered": 8,
                    "next_due_at": scheduler.iso_utc(self.now - timedelta(minutes=1)),
                })
                scheduler.save_state(self.config, state, self.now)
                decision = json.dumps({"send": True, "message": "Hello.", "next_delay_minutes": 1})
                with mock.patch.object(scheduler, "classify_engagement", return_value=engagement), mock.patch.object(
                    scheduler, "run_codex_session_turn", return_value=decision
                ), mock.patch.object(bridge, "send_message", return_value=[42]) as send:
                    self.tick()
                send.assert_called_once()
                rp = self.state()["rp"]
                self.assertEqual(rp["status"], "sent")
                self.assertEqual(rp["sent_today"], 3)
                self.assertEqual(rp["consecutive_unanswered"], 9)
                minimum, _ = scheduler.delay_band(engagement)
                self.assertGreaterEqual(
                    scheduler.parse_timestamp(rp["next_due_at"]), self.now + timedelta(minutes=minimum)
                )

    def test_daily_cap_still_prevents_sending(self) -> None:
        state = self.state()
        state["rp"].update({"sent_local_date": self.now.date().isoformat(), "sent_today": 5})
        scheduler.save_state(self.config, state, self.now)
        with mock.patch.object(scheduler, "run_codex_session_turn") as generate:
            self.tick()
        generate.assert_not_called()
        self.assertEqual(self.state()["rp"]["status"], "cooldown")

    def test_night_tick_does_not_generate_or_send(self) -> None:
        self.now = datetime(2026, 9, 5, 22, 0, tzinfo=timezone.utc)
        with mock.patch.object(scheduler, "run_codex_session_turn") as generate, mock.patch.object(
            bridge, "send_message"
        ) as send:
            self.tick()
        generate.assert_not_called()
        send.assert_not_called()
        rp = self.state()["rp"]
        self.assertEqual(rp["status"], "waiting_window")
        self.assertTrue(scheduler.in_rp_window(scheduler.parse_timestamp(rp["next_due_at"]).astimezone(self.config.timezone)))

    def test_interrupted_send_is_not_automatically_duplicated(self) -> None:
        state = self.state()
        state["rp"]["status"] = "sending"
        scheduler.save_state(self.config, state, self.now)
        with mock.patch.object(scheduler, "run_codex_session_turn") as generate, mock.patch.object(
            bridge, "send_message"
        ) as send:
            self.tick()
        generate.assert_not_called()
        send.assert_not_called()
        self.assertEqual(self.state()["rp"]["status"], "uncertain")

    def test_recent_inbound_defers_ping(self) -> None:
        bridge.append_inbox_event(
            self.config.bridge_config,
            direction="in",
            text="Hello.",
            sender="User",
            chat_id="123",
        )
        with mock.patch.object(scheduler, "run_codex_session_turn") as generate:
            self.tick()
        generate.assert_not_called()
        self.assertGreater(scheduler.parse_timestamp(self.state()["rp"]["next_due_at"]), self.now)

    def test_private_persona_is_loaded_at_runtime(self) -> None:
        config = self.config.bridge_config
        config.persona_enabled = True
        config.persona_path.write_text("Private test persona.", encoding="utf-8")
        prompt = scheduler.ping_prompt(self.now, self.config, [], "normal", 120, 240)
        self.assertIn("Private test persona.", prompt)

    def test_night_time_is_moved_into_daytime_window(self) -> None:
        night = datetime(2026, 9, 5, 22, 0, tzinfo=timezone.utc)
        due = scheduler.schedule_after(night, 60, self.config.timezone, random.Random(1))
        local = due.astimezone(self.config.timezone)
        self.assertEqual(local.date().isoformat(), "2026-09-06")
        self.assertTrue(scheduler.in_rp_window(local))
        self.assertNotIn(local.minute, (0, 30))

    def test_bridge_tick_passes_exact_thread_and_session_start(self) -> None:
        config = self.config.bridge_config
        config.session_scheduler_enabled = True
        config.session_started_at = scheduler.iso_utc(self.now)
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(bridge.subprocess, "run", return_value=result) as run:
            bridge.run_session_tick(config)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--thread-id") + 1], config.codex_resume_session)
        self.assertEqual(args[args.index("--session-started-at") + 1], config.session_started_at)
        self.assertEqual(args[args.index("--env") + 1], str(config.env_path))

    def test_bridge_tick_is_disabled_by_default(self) -> None:
        with mock.patch.object(bridge.subprocess, "run") as run:
            bridge.run_session_tick(self.config.bridge_config)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
