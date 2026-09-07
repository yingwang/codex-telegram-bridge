from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("session_end_guard", ROOT / "scripts/session_end_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class SessionEndGuardTests(unittest.TestCase):
    def setUp(self):
        artifacts = ROOT / ".test-artifacts"
        artifacts.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "runtime.json"
        self.runtime.write_text(json.dumps({"thread_id": "owner-thread", "pid": 0}))
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()

    def payload(self, session="owner-thread", event="SessionEnd"):
        return json.dumps({"hook_event_name": event, "session_id": session})

    def test_owning_session_can_stop(self):
        self.assertTrue(guard.may_stop(self.payload(), self.runtime, self.sessions))

    def test_other_session_cannot_stop(self):
        self.assertFalse(guard.may_stop(self.payload("automation-thread"), self.runtime, self.sessions))

    def test_manual_stop_remains_available(self):
        self.assertTrue(guard.may_stop("", self.runtime, self.sessions))

    def test_malformed_or_unidentified_hook_cannot_stop(self):
        for payload in ("{", "[]", "null", "{}", self.payload(event="SessionStart"), self.payload("../owner-thread")):
            with self.subTest(payload=payload):
                self.assertFalse(guard.may_stop(payload, self.runtime, self.sessions))

    def test_bound_automation_cannot_stop(self):
        rollout = self.sessions / "rollout-test-owner-thread.jsonl"
        rollout.write_text(json.dumps({"text": "Automation ID: scheduled-test"}) + "\n")
        self.assertFalse(guard.may_stop(self.payload(), self.runtime, self.sessions))

    def test_unreadable_runtime_fails_closed(self):
        self.runtime.write_text("invalid")
        self.assertFalse(guard.may_stop(self.payload(), self.runtime, self.sessions))

    def test_shell_hook_keeps_runtime_for_unrelated_session(self):
        before = self.runtime.read_bytes()
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/deactivate.sh")],
            input=self.payload("other-thread"), text=True, capture_output=True,
            env={**os.environ, "TELEGRAM_RUNTIME_PATH": str(self.runtime)}, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Leaving", result.stdout)
        self.assertEqual(self.runtime.read_bytes(), before)

    def test_shell_malformed_hook_keeps_runtime(self):
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/deactivate.sh")],
            input="{invalid", text=True, capture_output=True,
            env={**os.environ, "TELEGRAM_RUNTIME_PATH": str(self.runtime)}, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.runtime.exists())

    def test_shell_owner_cleans_only_stale_fixture(self):
        # PID zero is rejected before kill/ps, so no real process is touched.
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/deactivate.sh")],
            input=self.payload(), text=True, capture_output=True,
            env={**os.environ, "TELEGRAM_RUNTIME_PATH": str(self.runtime)}, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.runtime.exists())


if __name__ == "__main__":
    unittest.main()
