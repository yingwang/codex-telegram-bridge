from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pick_inbox.py"
SPEC = importlib.util.spec_from_file_location("pick_inbox", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
pick_inbox = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pick_inbox)


class CursorPathTests(unittest.TestCase):
    def test_uses_fallback_when_private_cursor_is_not_writable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preferred = root / "private" / "inbox.cursor"
            inbox = root / "inbox.jsonl"

            with mock.patch.object(
                pick_inbox,
                "cursor_path_is_writable",
                side_effect=lambda path: path != preferred,
            ):
                resolved = pick_inbox.resolve_cursor_path(preferred, inbox)

            self.assertNotEqual(resolved, preferred)
            self.assertEqual(resolved.parent, Path(tempfile.gettempdir()))
            self.assertTrue(resolved.name.startswith("codex-telegram-inbox-"))


if __name__ == "__main__":
    unittest.main()
