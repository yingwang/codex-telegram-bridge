from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("watchdog", ROOT / "scripts/watchdog.py")
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

PERSON = {"thread_source": "user", "source": "vscode", "_mtime": 100}
AUTOMATION = {"thread_source": "automation", "source": "vscode", "_mtime": 300}
SUBAGENT = {"thread_source": "subagent", "source": {"subagent": {"other": "guardian"}}, "_mtime": 300}
EXEC = {"thread_source": "user", "source": "exec", "_mtime": 300}


class DecideTests(unittest.TestCase):
    def test_healthy_bridge_is_left_alone(self):
        target, replace, state = watchdog.decide(
            41, "a", ["a"], {"a": PERSON}, {"problem_since": 1}, now=500
        )
        self.assertIsNone(target)
        self.assertEqual(state, {"last_bound": "a"})

    def test_nothing_to_do_without_a_live_conversation(self):
        target, _, state = watchdog.decide(None, None, [], {}, {}, now=500)
        self.assertIsNone(target)
        self.assertEqual(state, {})

    def test_daemon_unreachable_changes_nothing(self):
        target, _, state = watchdog.decide(None, None, None, {}, {"problem_since": 1}, now=500)
        self.assertIsNone(target)
        self.assertEqual(state, {"problem_since": 1})

    def test_first_sighting_only_waits(self):
        target, _, state = watchdog.decide(None, None, ["a"], {"a": PERSON}, {}, now=500)
        self.assertIsNone(target)
        self.assertEqual(state, {"problem_since": 500})

    def test_within_grace_still_waits(self):
        target, _, state = watchdog.decide(None, None, ["a"], {"a": PERSON}, {"problem_since": 450}, now=500)
        self.assertIsNone(target)
        self.assertEqual(state, {"problem_since": 450})

    def test_missing_bridge_is_started_without_replacing(self):
        target, replace, state = watchdog.decide(
            None, None, ["a"], {"a": PERSON}, {"problem_since": 100}, now=500
        )
        self.assertEqual(target, "a")
        self.assertFalse(replace)
        self.assertEqual(state, {})

    def test_bridge_on_unloaded_conversation_is_moved(self):
        target, replace, _ = watchdog.decide(
            41, "old", ["a"], {"a": PERSON}, {"problem_since": 100}, now=500
        )
        self.assertEqual(target, "a")
        self.assertTrue(replace)

    def test_only_conversations_a_person_started_count(self):
        metas = {"auto": AUTOMATION, "guardian": SUBAGENT, "exec": EXEC}
        target, _, state = watchdog.decide(None, None, list(metas), metas, {"problem_since": 100}, now=500)
        self.assertIsNone(target)
        self.assertEqual(state, {})

    def test_last_served_conversation_is_preferred(self):
        newer = dict(PERSON, _mtime=900)
        target, _, _ = watchdog.decide(
            None, "a", ["a", "b"], {"a": PERSON, "b": newer}, {"problem_since": 100}, now=500
        )
        self.assertEqual(target, "a")

    def test_otherwise_the_most_recently_written_conversation_wins(self):
        newer = dict(PERSON, _mtime=900)
        target, _, _ = watchdog.decide(
            None, "gone", ["a", "b"], {"a": PERSON, "b": newer}, {"problem_since": 100}, now=500
        )
        self.assertEqual(target, "b")

    def test_bridge_stopped_on_purpose_stays_stopped(self):
        target, _, state = watchdog.decide(
            None, None, ["a"], {"a": PERSON}, {"last_bound": "a"}, now=500
        )
        self.assertIsNone(target)
        self.assertEqual(state, {"held": "a"})
        target, _, state = watchdog.decide(None, None, ["a"], {"a": PERSON}, state, now=900)
        self.assertIsNone(target)
        self.assertEqual(state, {"held": "a"})

    def test_new_session_still_gets_a_bridge_while_another_is_held(self):
        metas = {"a": PERSON, "b": dict(PERSON, _mtime=900)}
        target, replace, state = watchdog.decide(
            None, None, ["a", "b"], metas, {"held": "a", "problem_since": 100}, now=500
        )
        self.assertEqual(target, "b")
        self.assertFalse(replace)
        self.assertEqual(state, {"held": "a"})

    def test_hold_ends_when_its_conversation_closes(self):
        _, _, state = watchdog.decide(None, None, [], {}, {"held": "a"}, now=500)
        self.assertEqual(state, {})

    def test_session_that_ended_hands_over_to_the_new_one(self):
        # 2026-10-01: the bridge's conversation exited and a new one opened without a hook.
        target, _, state = watchdog.decide(
            None, None, ["new"], {"new": PERSON}, {"last_bound": "old"}, now=500
        )
        self.assertIsNone(target)
        self.assertEqual(state, {"problem_since": 500})
        target, replace, _ = watchdog.decide(None, None, ["new"], {"new": PERSON}, state, now=620)
        self.assertEqual(target, "new")
        self.assertFalse(replace)

    def test_missing_control_socket_means_unknown(self):
        self.assertIsNone(watchdog.loaded_threads(ROOT / "no-such-socket"))


if __name__ == "__main__":
    unittest.main()
