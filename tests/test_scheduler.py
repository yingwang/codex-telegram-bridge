from __future__ import annotations

import json
import random
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import bridge
import scheduler


STOCKHOLM = ZoneInfo("Europe/Stockholm")


def make_bridge_config(root: Path) -> bridge.Config:
    return bridge.Config(
        token="test-token",
        allowed_chat_ids={"123"},
        codex_bin="codex",
        codex_workdir=root,
        codex_sandbox="workspace-write",
        codex_resume_session=None,
        codex_timeout_seconds=30,
        state_path=root / "bridge-state.json",
        runtime_path=None,
        require_codex_prefix=False,
        inbox_enabled=True,
        inbox_path=root / "inbox.md",
        inbox_jsonl_path=root / "inbox.jsonl",
        context_recent_events=12,
        context_max_chars=12000,
        persona_enabled=False,
        persona_path=root / "persona.md",
        memory_enabled=False,
        memory_auto_enabled=False,
        memory_jsonl_path=root / "memory.jsonl",
        memory_recent_events=0,
        memory_max_chars=0,
        ack_message="",
        attachments_enabled=True,
        max_download_bytes=1024,
        max_upload_bytes=1024,
        max_artifact_files=1,
        audio_transcribe_command=None,
        audio_transcribe_timeout_seconds=30,
        audio_transcript_max_chars=1000,
        tts_mode="off",
        tts_command=None,
        tts_timeout_seconds=30,
        tts_max_chars=1000,
        tts_output_extension=".mp3",
        tts_send_as="audio",
        tts_flatten_punctuation=False,
    )


def make_scheduler_config(root: Path) -> scheduler.SchedulerConfig:
    runtime = root / "scheduler"
    return scheduler.SchedulerConfig(
        bridge_config=make_bridge_config(root),
        target_chat_id="123",
        timezone=STOCKHOLM,
        runtime_dir=runtime,
        state_path=runtime / "state.json",
        lock_path=runtime / "scheduler.lock",
        events_path=runtime / "events.jsonl",
        codex_bin="codex",
        codex_workdir=root,
        codex_timeout_seconds=30,
        max_rp_per_day=5,
    )


class SchedulingTests(unittest.TestCase):
    def test_schedule_after_stays_in_window_and_avoids_clock_edges(self) -> None:
        due = scheduler.schedule_after(
            datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc),
            120,
            STOCKHOLM,
            random.Random(7),
        ).astimezone(STOCKHOLM)
        self.assertTrue(scheduler.RP_START <= due.time() <= scheduler.RP_END)
        self.assertNotIn(due.minute, {0, 30})

    def test_schedule_after_rolls_night_to_next_day(self) -> None:
        due = scheduler.schedule_after(
            datetime(2026, 8, 12, 21, 0, tzinfo=timezone.utc),
            90,
            STOCKHOLM,
            random.Random(9),
        ).astimezone(STOCKHOLM)
        self.assertEqual(due.date().isoformat(), "2026-08-13")
        self.assertTrue(scheduler.RP_START <= due.time() <= scheduler.RP_END)

    def test_news_uses_stockholm_dst_and_is_idempotent_per_local_day(self) -> None:
        now = datetime(2026, 3, 29, 7, 0, tzinfo=timezone.utc)
        state = scheduler.default_state(now, STOCKHOLM, random.Random(1))
        self.assertEqual(now.astimezone(STOCKHOLM).hour, 9)
        self.assertTrue(scheduler.news_due(state, now, STOCKHOLM))
        state["news"]["last_sent_local_date"] = "2026-03-29"
        self.assertFalse(scheduler.news_due(state, now, STOCKHOLM))

    def test_news_is_not_due_before_nine_after_dst_fallback(self) -> None:
        now = datetime(2026, 10, 25, 7, 59, tzinfo=timezone.utc)
        state = scheduler.default_state(now, STOCKHOLM, random.Random(2))
        self.assertEqual(now.astimezone(STOCKHOLM).strftime("%H:%M"), "08:59")
        self.assertFalse(scheduler.news_due(state, now, STOCKHOLM))

    def test_paused_news_is_not_due_but_force_still_allows_manual_run(self) -> None:
        now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
        state = scheduler.default_state(now, STOCKHOLM, random.Random(2))
        state["news"]["enabled"] = False
        self.assertFalse(scheduler.news_due(state, now, STOCKHOLM))
        self.assertTrue(scheduler.news_due(state, now, STOCKHOLM, force=True))


class EngagementTests(unittest.TestCase):
    def test_reply_after_last_ping_is_hot(self) -> None:
        now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        rp = {"last_sent_at": scheduler.iso_utc(now - timedelta(hours=1))}
        events = [{"ts": now - timedelta(minutes=10), "direction": "in"}]
        self.assertEqual(scheduler.classify_engagement(events, rp, now), "hot")

    def test_no_reply_after_last_ping_is_quiet(self) -> None:
        now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        rp = {"last_sent_at": scheduler.iso_utc(now - timedelta(hours=1))}
        events = [{"ts": now - timedelta(hours=2), "direction": "in"}]
        self.assertEqual(scheduler.classify_engagement(events, rp, now), "quiet")

    def test_recent_events_ignores_bad_json_and_other_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "inbox.jsonl"
            records = [
                "not-json",
                json.dumps({"ts": "2026-08-12T10:00:00Z", "direction": "in", "chat_id": "999", "text": "other"}),
                json.dumps({"ts": "2026-08-12T10:01:00Z", "direction": "in", "chat_id": "123", "text": "mine"}),
            ]
            path.write_text("\n".join(records) + "\n", encoding="utf-8")
            events = scheduler.recent_events(path, "123")
            self.assertEqual([event["text"] for event in events], ["mine"])

    def test_synchronize_reply_accelerates_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            state = scheduler.default_state(now, STOCKHOLM, random.Random(4))
            state["rp"].update(
                {
                    "last_sent_at": scheduler.iso_utc(now - timedelta(hours=1)),
                    "next_due_at": scheduler.iso_utc(now + timedelta(hours=5)),
                    "consecutive_unanswered": 1,
                }
            )
            events = [{"ts": now - timedelta(minutes=5), "direction": "in"}]
            changed = scheduler.synchronize_reply_state(state, events, now, config, random.Random(4))
            self.assertTrue(changed)
            self.assertEqual(state["rp"]["consecutive_unanswered"], 0)
            self.assertLess(scheduler.parse_timestamp(state["rp"]["next_due_at"]), now + timedelta(hours=5))
            self.assertFalse(scheduler.synchronize_reply_state(state, events, now, config, random.Random(4)))

    def test_fresh_inbound_defers_an_overdue_ping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            state = scheduler.default_state(now, STOCKHOLM, random.Random(8))
            state["rp"]["next_due_at"] = scheduler.iso_utc(now - timedelta(minutes=5))
            events = [{"ts": now - timedelta(minutes=1), "direction": "in"}]
            scheduler.synchronize_reply_state(state, events, now, config, random.Random(8))
            self.assertGreater(scheduler.parse_timestamp(state["rp"]["next_due_at"]), now)

    def test_unanswered_count_decays_on_a_new_day(self) -> None:
        rp = {"sent_local_date": "2026-08-11", "sent_today": 2, "consecutive_unanswered": 2}
        now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        scheduler.reset_rp_day(rp, now, STOCKHOLM)
        self.assertEqual(rp["sent_today"], 0)
        self.assertEqual(rp["consecutive_unanswered"], 1)


class ValidationTests(unittest.TestCase):
    def test_ping_decision_is_clamped_and_directives_are_removed(self) -> None:
        raw = json.dumps(
            {
                "send": True,
                "message": "在看什么？<telegram_memory>{\"remember\":[]}</telegram_memory>",
                "next_delay_minutes": 5,
            }
        )
        parsed = scheduler.parse_ping_decision(raw, 45, 100)
        self.assertEqual(parsed["message"], "在看什么？")
        self.assertEqual(parsed["next_delay_minutes"], 45)

    def test_state_is_private_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            state = scheduler.default_state(now, STOCKHOLM, random.Random(5))
            scheduler.save_state(config, state, now)
            self.assertEqual(stat.S_IMODE(config.state_path.stat().st_mode), 0o600)
            loaded = scheduler.load_state(config, now, random.Random(5))
            self.assertEqual(loaded["timezone"], "Europe/Stockholm")

    def test_corrupt_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            scheduler.ensure_private_dir(config.runtime_dir)
            config.state_path.write_text("not-json", encoding="utf-8")
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            with self.assertRaises(scheduler.SchedulerError):
                scheduler.load_state(config, now, random.Random(5))

    def test_send_message_returns_telegram_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_bridge_config(Path(temp_dir))
            with mock.patch.object(bridge, "api_call", return_value={"message_id": 44}) as api_call:
                results = bridge.send_message(config, "123", "hello")
            self.assertEqual(results, [{"message_id": 44}])
            api_call.assert_called_once()

    def test_inbox_event_records_chat_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_bridge_config(Path(temp_dir))
            bridge.append_inbox_event(
                config,
                direction="out",
                text="hello",
                sender="scheduler",
                message_id=7,
                chat_id="123",
                source="scheduled_rp",
            )
            event = json.loads(config.inbox_jsonl_path.read_text(encoding="utf-8"))
            self.assertEqual(event["chat_id"], "123")
            self.assertEqual(event["source"], "scheduled_rp")

    def test_session_turn_uses_exact_id_disables_hooks_and_excludes_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            captured: dict[str, object] = {}

            def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
                captured["args"] = args
                captured["env"] = kwargs["env"]
                output_index = args.index("--output-last-message") + 1
                Path(args[output_index]).write_text("ok", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch.dict(
                scheduler.os.environ,
                {"TELEGRAM_BOT_TOKEN": "must-not-leak", "UNRELATED_PRIVATE_KEY": "also-must-not-leak"},
            ), mock.patch.object(scheduler.subprocess, "run", side_effect=fake_run):
                result = scheduler.run_codex_session_turn(
                    config,
                    "test",
                    thread_id="thread-123",
                    search=False,
                    schema_path=scheduler.PING_SCHEMA_PATH,
                )
            self.assertEqual(result, "ok")
            args = captured["args"]
            child_env = captured["env"]
            self.assertIn("--disable", args)
            self.assertIn("hooks", args)
            self.assertIn("read-only", args)
            self.assertIn("fork", args)
            self.assertIn("thread-123", args)
            self.assertIn("--output-schema", args)
            self.assertIn("--ephemeral", args)
            self.assertNotIn("resume", args)
            self.assertNotIn("--last", args)
            self.assertNotIn("TELEGRAM_BOT_TOKEN", child_env)
            self.assertNotIn("UNRELATED_PRIVATE_KEY", child_env)

    def test_news_session_turn_enables_search_with_an_ephemeral_fork(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            captured: list[str] = []

            def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
                captured.extend(args)
                output_index = args.index("--output-last-message") + 1
                Path(args[output_index]).write_text("news", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch.object(scheduler.subprocess, "run", side_effect=fake_run):
                result = scheduler.run_codex_session_turn(
                    config,
                    "news",
                    thread_id="thread-123",
                    search=True,
                )
            self.assertEqual(result, "news")
            self.assertIn("--search", captured)
            self.assertIn("fork", captured)
            self.assertIn("thread-123", captured)
            self.assertNotIn("--last", captured)
            self.assertIn("--ephemeral", captured)
            self.assertNotIn("resume", captured)

    def test_missing_exact_session_is_reported_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            failed = SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="thread/resume failed: no rollout found for thread id thread-123",
            )
            with mock.patch.object(scheduler.subprocess, "run", return_value=failed):
                with self.assertRaises(scheduler.ThreadUnavailableError):
                    scheduler.run_codex_session_turn(
                        config,
                        "next",
                        thread_id="thread-123",
                        search=False,
                    )

    def test_prompts_scope_news_and_reassert_full_rp_context(self) -> None:
        ts = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        events = [
            {"ts": ts, "direction": "in", "sender": "Ying", "message_id": "1", "source": "", "text": "hello"},
            {"ts": ts, "direction": "out", "sender": "RP", "message_id": "2", "source": "scheduled_rp", "text": "old ping"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            news = scheduler.news_prompt(ts, config)
            ping = scheduler.ping_prompt(ts, config, events, "hot", 45, 100)
        self.assertIn("SCHEDULED_NEWS_BRIEF / ONE-TURN SCOPE", news)
        self.assertIn("不要延续", news)
        self.assertIn("可信角色设定", ping)
        self.assertIn("hello", ping)
        self.assertIn("old ping", ping)

    def test_interrupted_send_is_not_retried_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            state = scheduler.default_state(now, STOCKHOLM, random.Random(6))
            state["rp"].update({"status": "sending", "next_due_at": scheduler.iso_utc(now - timedelta(minutes=1))})
            scheduler.save_state(config, state, now)
            with mock.patch.object(scheduler, "run_codex_session_turn") as generate, mock.patch.object(
                scheduler, "send_scheduled"
            ) as send:
                scheduler.run_ping(
                    config,
                    thread_id="thread-123",
                    now=now,
                    rng=random.Random(6),
                    force=False,
                    dry_run=False,
                )
            generate.assert_not_called()
            send.assert_not_called()
            loaded = scheduler.load_state(config, now, random.Random(6))
            self.assertEqual(loaded["rp"]["status"], "uncertain")
            self.assertGreater(scheduler.parse_timestamp(loaded["rp"]["next_due_at"]), now)


class NewsIntegrationTests(unittest.TestCase):
    def test_two_news_ticks_send_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
            generated = "今日要闻：" + ("重要进展及其影响。" * 50)
            with mock.patch.object(scheduler, "run_codex_session_turn", return_value=generated) as generate, mock.patch.object(
                scheduler, "send_scheduled", return_value=([{"message_id": 9}], "9")
            ) as send:
                scheduler.run_news(
                    config,
                    thread_id="thread-123",
                    now=now,
                    rng=random.Random(3),
                    force=False,
                    dry_run=False,
                )
                scheduler.run_news(
                    config,
                    thread_id="thread-123",
                    now=now,
                    rng=random.Random(3),
                    force=False,
                    dry_run=False,
                )
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(generate.call_args.kwargs["thread_id"], "thread-123")
            self.assertTrue(generate.call_args.kwargs["search"])
            state = scheduler.load_state(config, now, random.Random(3))
            self.assertEqual(state["news"]["last_sent_local_date"], "2026-08-12")


class SessionTickIntegrationTests(unittest.TestCase):
    def test_session_tick_skips_paused_news_and_keeps_rp_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
            state = scheduler.default_state(now, STOCKHOLM, random.Random(1))
            state["news"]["enabled"] = False
            scheduler.save_state(config, state, now)
            with mock.patch.object(scheduler, "run_news", return_value=0) as news, mock.patch.object(
                scheduler, "run_ping", return_value=0
            ) as ping:
                scheduler.run_session_tick(
                    config,
                    thread_id="thread-123",
                    session_started_at=now - timedelta(minutes=10),
                    now=now,
                    rng=random.Random(1),
                )
            news.assert_not_called()
            ping.assert_called_once()

    def test_session_tick_runs_news_first_and_at_most_one_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
            started_at = now - timedelta(minutes=10)
            with mock.patch.object(scheduler, "run_news", return_value=0) as news, mock.patch.object(
                scheduler, "run_ping", return_value=0
            ) as ping:
                scheduler.run_session_tick(
                    config,
                    thread_id="thread-123",
                    session_started_at=started_at,
                    now=now,
                    rng=random.Random(1),
                )
            news.assert_called_once()
            ping.assert_not_called()
            self.assertEqual(news.call_args.kwargs["thread_id"], "thread-123")
            state = scheduler.load_state(config, now, random.Random(1))
            self.assertEqual(state["session"]["started_at"], scheduler.iso_utc(started_at))
            self.assertEqual(state["session"]["last_tick_at"], scheduler.iso_utc(now))

    def test_session_tick_runs_ping_when_news_is_not_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            state = scheduler.default_state(now, STOCKHOLM, random.Random(2))
            state["news"]["last_sent_local_date"] = "2026-08-12"
            scheduler.save_state(config, state, now)
            with mock.patch.object(scheduler, "run_news", return_value=0) as news, mock.patch.object(
                scheduler, "run_ping", return_value=0
            ) as ping:
                scheduler.run_session_tick(
                    config,
                    thread_id="thread-123",
                    session_started_at=now - timedelta(minutes=10),
                    now=now,
                    rng=random.Random(2),
                )
            news.assert_not_called()
            ping.assert_called_once()
            self.assertEqual(ping.call_args.kwargs["thread_id"], "thread-123")

    def test_older_session_tick_cannot_supersede_a_newer_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            state = scheduler.default_state(now, STOCKHOLM, random.Random(3))
            state["news"]["last_sent_local_date"] = "2026-08-12"
            scheduler.save_state(config, state, now)
            with mock.patch.object(scheduler, "run_ping", return_value=0) as ping:
                scheduler.run_session_tick(
                    config,
                    thread_id="new-thread",
                    session_started_at=now - timedelta(minutes=5),
                    now=now,
                    rng=random.Random(3),
                )
                scheduler.run_session_tick(
                    config,
                    thread_id="old-thread",
                    session_started_at=now - timedelta(hours=1),
                    now=now,
                    rng=random.Random(3),
                )
            self.assertEqual(ping.call_count, 1)

    def test_version_two_state_drops_dedicated_thread_fields_without_losing_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            old = scheduler.default_state(now, STOCKHOLM, random.Random(2))
            old["version"] = 2
            old["rp"].update(
                {
                    "thread_id": "old-dedicated-thread",
                    "thread_status": "ready",
                    "context_cursor": "old-cursor",
                }
            )
            old["rp"]["sent_today"] = 3
            expected_due = old["rp"]["next_due_at"]
            scheduler.ensure_private_dir(config.runtime_dir)
            config.state_path.write_text(json.dumps(old), encoding="utf-8")
            loaded = scheduler.load_state(config, now, random.Random(2))
            self.assertEqual(loaded["version"], 3)
            self.assertEqual(loaded["rp"]["sent_today"], 3)
            self.assertEqual(loaded["rp"]["next_due_at"], expected_due)
            self.assertNotIn("thread_id", loaded["rp"])
            self.assertNotIn("thread_status", loaded["rp"])
            self.assertNotIn("context_cursor", loaded["rp"])


class PingIntegrationTests(unittest.TestCase):
    @staticmethod
    def prepare_state(config: scheduler.SchedulerConfig, now: datetime, seed: int) -> None:
        state = scheduler.default_state(now, STOCKHOLM, random.Random(seed))
        scheduler.save_state(config, state, now)

    def test_ping_send_updates_state_and_next_due(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            self.prepare_state(config, now, 7)
            decision = json.dumps({"send": True, "message": "在忙什么？", "next_delay_minutes": 300})
            with mock.patch.object(scheduler, "run_codex_session_turn", return_value=decision) as generate, mock.patch.object(
                scheduler, "send_scheduled", return_value=([{"message_id": 12}], "12")
            ) as send:
                scheduler.run_ping(
                    config,
                    thread_id="thread-123",
                    now=now,
                    rng=random.Random(7),
                    force=True,
                    dry_run=False,
                )
            send.assert_called_once_with(config, "在忙什么？", "rp")
            self.assertEqual(generate.call_args.kwargs["thread_id"], "thread-123")
            self.assertFalse(generate.call_args.kwargs["search"])
            state = scheduler.load_state(config, now, random.Random(7))
            self.assertEqual(state["rp"]["status"], "sent")
            self.assertEqual(state["rp"]["sent_today"], 1)
            self.assertEqual(state["rp"]["consecutive_unanswered"], 1)
            self.assertGreater(scheduler.parse_timestamp(state["rp"]["next_due_at"]), now)

    def test_ping_skip_schedules_another_check_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            self.prepare_state(config, now, 8)
            decision = json.dumps({"send": False, "message": "", "next_delay_minutes": 360})
            with mock.patch.object(scheduler, "run_codex_session_turn", return_value=decision), mock.patch.object(
                scheduler, "send_scheduled"
            ) as send:
                scheduler.run_ping(
                    config,
                    thread_id="thread-123",
                    now=now,
                    rng=random.Random(8),
                    force=True,
                    dry_run=False,
                )
            send.assert_not_called()
            state = scheduler.load_state(config, now, random.Random(8))
            self.assertEqual(state["rp"]["status"], "skipped")
            self.assertEqual(state["rp"]["sent_today"], 0)
            self.assertGreater(scheduler.parse_timestamp(state["rp"]["next_due_at"]), now)

    def test_ping_delivery_error_enters_uncertain_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            self.prepare_state(config, now, 9)
            decision = json.dumps({"send": True, "message": "在忙什么？", "next_delay_minutes": 300})
            with mock.patch.object(scheduler, "run_codex_session_turn", return_value=decision), mock.patch.object(
                scheduler, "send_scheduled", side_effect=scheduler.SchedulerError("network")
            ):
                with self.assertRaises(scheduler.SchedulerError):
                    scheduler.run_ping(
                        config,
                        thread_id="thread-123",
                        now=now,
                        rng=random.Random(9),
                        force=True,
                        dry_run=False,
                    )
            state = scheduler.load_state(config, now, random.Random(9))
            self.assertEqual(state["rp"]["status"], "uncertain")
            self.assertGreater(scheduler.parse_timestamp(state["rp"]["next_due_at"]), now)

    def test_ping_reasserts_persona_and_full_recent_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            event = {
                "ts": now - timedelta(minutes=2),
                "direction": "in",
                "sender": "Ying",
                "message_id": "1",
                "source": "telegram_bridge",
                "text": "remember this recent line",
            }
            decision = json.dumps({"send": False, "message": "", "next_delay_minutes": 120})
            with mock.patch.object(scheduler, "recent_events", return_value=[event]), mock.patch.object(
                scheduler, "run_codex_session_turn", return_value=decision
            ) as run, mock.patch.object(scheduler, "send_scheduled") as send:
                scheduler.run_ping(
                    config,
                    thread_id="thread-123",
                    now=now,
                    rng=random.Random(10),
                    force=True,
                    dry_run=False,
                )
            send.assert_not_called()
            prompt = run.call_args.args[1]
            self.assertIn("可信角色设定", prompt)
            self.assertIn("remember this recent line", prompt)

    def test_missing_current_session_defers_without_creating_a_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_scheduler_config(Path(temp_dir))
            now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            self.prepare_state(config, now, 11)
            with mock.patch.object(
                scheduler,
                "run_codex_session_turn",
                side_effect=scheduler.ThreadUnavailableError("missing"),
            ) as run, mock.patch.object(scheduler, "send_scheduled") as send:
                with self.assertRaises(scheduler.ThreadUnavailableError):
                    scheduler.run_ping(
                        config,
                        thread_id="thread-123",
                        now=now,
                        rng=random.Random(11),
                        force=True,
                        dry_run=False,
                    )
            self.assertEqual(run.call_count, 1)
            send.assert_not_called()
            state = scheduler.load_state(config, now, random.Random(11))
            self.assertEqual(state["rp"]["status"], "session_unavailable")
            self.assertNotIn("thread_id", state["rp"])
            self.assertGreater(scheduler.parse_timestamp(state["rp"]["next_due_at"]), now)


if __name__ == "__main__":
    unittest.main()
