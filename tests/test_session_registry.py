from __future__ import annotations

import json
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts import session_registry


class FakeOwnerLiveness:
    def __init__(self) -> None:
        self._owners: dict[tuple[int, str], bool] = {}

    def set(self, pid: int, start_token: str, alive: bool) -> None:
        self._owners[(pid, start_token)] = alive

    def __call__(self, pid: int, start_token: str) -> bool:
        return self._owners.get((pid, start_token), False)


class SessionRegistryTests(unittest.TestCase):
    @staticmethod
    def session_ids(snapshot: session_registry.Snapshot) -> set[str]:
        return {lease.session_id for lease in snapshot.sessions.values()}

    def make_registry(
        self,
        root: Path,
        liveness: FakeOwnerLiveness,
    ) -> session_registry.SessionRegistry:
        return session_registry.SessionRegistry(
            root / "live-sessions.json",
            is_owner_alive=liveness,
        )

    def test_first_registration_becomes_sticky_leader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "start-a", True)
            liveness.set(202, "start-b", True)
            registry = self.make_registry(root, liveness)

            first = registry.register(
                "session-a",
                owner_pid=101,
                owner_start="start-a",
                started_at="2026-08-12T18:00:00Z",
            )
            second = registry.register(
                "session-b",
                owner_pid=202,
                owner_start="start-b",
                started_at="2026-08-12T18:01:00Z",
            )

            self.assertEqual(first.leader_id, "session-a")
            self.assertEqual(second.leader_id, "session-a")
            self.assertEqual(self.session_ids(second), {"session-a", "session-b"})

    def test_registration_is_idempotent_and_refreshes_the_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "old-start", True)
            liveness.set(303, "new-start", True)
            registry = self.make_registry(root, liveness)

            registry.register(
                "session-a",
                owner_pid=101,
                owner_start="old-start",
                started_at="2026-08-12T18:00:00Z",
            )
            refreshed = registry.register(
                "session-a",
                owner_pid=101,
                owner_start="old-start",
                started_at="2026-08-12T19:00:00Z",
            )

            self.assertEqual(refreshed.leader_id, "session-a")
            self.assertEqual(self.session_ids(refreshed), {"session-a"})
            self.assertEqual(len(refreshed.sessions), 1)
            lease = refreshed.leader
            self.assertIsNotNone(lease)
            assert lease is not None
            self.assertEqual(lease.owner_pid, 101)
            self.assertEqual(lease.owner_start, "old-start")
            self.assertEqual(lease.started_at, "2026-08-12T19:00:00Z")

    def test_unregistering_a_follower_keeps_the_leader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "start-a", True)
            liveness.set(202, "start-b", True)
            registry = self.make_registry(root, liveness)
            registry.register("session-a", 101, "start-a", "2026-08-12T18:00:00Z")
            registry.register("session-b", 202, "start-b", "2026-08-12T18:01:00Z")

            snapshot = registry.unregister("session-b", 202, "start-b")

            self.assertEqual(snapshot.leader_id, "session-a")
            self.assertEqual(self.session_ids(snapshot), {"session-a"})

    def test_unregistering_the_leader_fails_over_to_a_live_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "start-a", True)
            liveness.set(202, "start-b", True)
            registry = self.make_registry(root, liveness)
            registry.register("session-a", 101, "start-a", "2026-08-12T18:00:00Z")
            registry.register("session-b", 202, "start-b", "2026-08-12T18:01:00Z")

            snapshot = registry.unregister("session-a", 101, "start-a")

            self.assertEqual(snapshot.leader_id, "session-b")
            self.assertEqual(self.session_ids(snapshot), {"session-b"})

    def test_unregistering_the_last_session_returns_an_empty_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "start-a", True)
            registry = self.make_registry(root, liveness)
            registry.register("session-a", 101, "start-a", "2026-08-12T18:00:00Z")

            empty = registry.unregister("session-a", 101, "start-a")
            still_empty = registry.unregister("session-a", 101, "start-a")

            self.assertIsNone(empty.leader_id)
            self.assertEqual(dict(empty.sessions), {})
            self.assertIsNone(still_empty.leader_id)
            self.assertEqual(dict(still_empty.sessions), {})

    def test_same_owner_switches_sessions_without_leaving_a_stale_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "start-a", True)
            registry = self.make_registry(root, liveness)
            registry.register("session-old", 101, "start-a", "2026-08-12T18:00:00Z")

            switched = registry.register(
                "session-new",
                101,
                "start-a",
                "2026-08-12T18:01:00Z",
            )

            self.assertEqual(switched.leader_id, "session-new")
            self.assertEqual(self.session_ids(switched), {"session-new"})
            self.assertEqual(len(switched.sessions), 1)

    def test_late_session_end_for_old_thread_does_not_remove_new_owner_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "start-a", True)
            registry = self.make_registry(root, liveness)
            registry.register("session-old", 101, "start-a", "2026-08-12T18:00:00Z")
            registry.register("session-new", 101, "start-a", "2026-08-12T18:01:00Z")

            after_late_end = registry.unregister("session-old", 101, "start-a")

            self.assertEqual(after_late_end.leader_id, "session-new")
            self.assertEqual(self.session_ids(after_late_end), {"session-new"})
            self.assertEqual(len(after_late_end.sessions), 1)

    def test_late_session_end_from_reused_pid_does_not_remove_new_process_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "old-process", True)
            registry = self.make_registry(root, liveness)
            registry.register("session-a", 101, "old-process", "2026-08-12T18:00:00Z")
            liveness.set(101, "old-process", False)
            liveness.set(101, "new-process", True)
            registry.register("session-a", 101, "new-process", "2026-08-12T18:01:00Z")

            after_late_end = registry.unregister("session-a", 101, "old-process")

            self.assertEqual(after_late_end.leader_id, "session-a")
            self.assertEqual(len(after_late_end.sessions), 1)
            only_lease = next(iter(after_late_end.sessions.values()))
            self.assertEqual((only_lease.owner_pid, only_lease.owner_start), (101, "new-process"))

    def test_same_session_on_two_owners_keeps_two_independent_leases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "start-a", True)
            liveness.set(202, "start-b", True)
            registry = self.make_registry(root, liveness)
            registry.register("shared-session", 101, "start-a", "2026-08-12T18:00:00Z")

            both = registry.register(
                "shared-session",
                202,
                "start-b",
                "2026-08-12T18:01:00Z",
            )

            self.assertEqual(both.leader_id, "shared-session")
            self.assertEqual(len(both.sessions), 2)
            self.assertEqual(
                {(lease.owner_pid, lease.owner_start) for lease in both.sessions.values()},
                {(101, "start-a"), (202, "start-b")},
            )

            remaining = registry.unregister("shared-session", 101, "start-a")
            self.assertEqual(remaining.leader_id, "shared-session")
            self.assertEqual(len(remaining.sessions), 1)
            only_lease = next(iter(remaining.sessions.values()))
            self.assertEqual((only_lease.owner_pid, only_lease.owner_start), (202, "start-b"))

    def test_prune_removes_a_dead_leader_and_selects_a_live_follower(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "start-a", True)
            liveness.set(202, "start-b", True)
            registry = self.make_registry(root, liveness)
            registry.register("session-a", 101, "start-a", "2026-08-12T18:00:00Z")
            registry.register("session-b", 202, "start-b", "2026-08-12T18:01:00Z")
            liveness.set(101, "start-a", False)

            snapshot = registry.prune()

            self.assertEqual(snapshot.leader_id, "session-b")
            self.assertEqual(self.session_ids(snapshot), {"session-b"})

    def test_pid_reuse_with_a_different_start_token_is_not_alive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            liveness.set(101, "original-start", True)
            registry = self.make_registry(root, liveness)
            registry.register(
                "session-a",
                owner_pid=101,
                owner_start="original-start",
                started_at="2026-08-12T18:00:00Z",
            )

            liveness.set(101, "original-start", False)
            liveness.set(101, "reused-pid-start", True)
            snapshot = registry.prune()

            self.assertIsNone(snapshot.leader_id)
            self.assertEqual(dict(snapshot.sessions), {})

    def test_snapshot_persists_across_instances_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-sessions.json"
            liveness = FakeOwnerLiveness()
            liveness.set(101, "start-a", True)
            first = self.make_registry(root, liveness)
            first.register("session-a", 101, "start-a", "2026-08-12T18:00:00Z")

            persisted = json.loads(path.read_text(encoding="utf-8"))
            reopened = self.make_registry(root, liveness).snapshot()

            self.assertIsInstance(persisted, dict)
            self.assertEqual(reopened.leader_id, "session-a")
            self.assertEqual(self.session_ids(reopened), {"session-a"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_concurrent_registrations_do_not_lose_live_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            liveness = FakeOwnerLiveness()
            session_count = 8
            for index in range(session_count):
                liveness.set(1000 + index, f"start-{index}", True)

            def register(index: int) -> None:
                registry = self.make_registry(root, liveness)
                registry.register(
                    f"session-{index}",
                    owner_pid=1000 + index,
                    owner_start=f"start-{index}",
                    started_at=f"2026-08-12T18:00:{index:02d}Z",
                )

            with ThreadPoolExecutor(max_workers=session_count) as executor:
                list(executor.map(register, range(session_count)))

            snapshot = self.make_registry(root, liveness).snapshot()
            self.assertEqual(
                self.session_ids(snapshot),
                {f"session-{index}" for index in range(session_count)},
            )
            self.assertIn(snapshot.leader_id, self.session_ids(snapshot))
            json.loads((root / "live-sessions.json").read_text(encoding="utf-8"))

    def test_corrupt_registry_fails_closed_instead_of_starting_unbounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "live-sessions.json"
            path.write_text("not-json", encoding="utf-8")
            registry = self.make_registry(root, FakeOwnerLiveness())

            with self.assertRaises(session_registry.RegistryError):
                registry.snapshot()


if __name__ == "__main__":
    unittest.main()
