#!/usr/bin/env python3
"""Private, locked registry of live interactive Codex session leases."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping


VERSION = 1


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    lease_id: str
    session_id: str
    owner_pid: int
    owner_start: str
    started_at: str


@dataclass(frozen=True)
class Snapshot:
    leader_id: str | None
    sessions: Mapping[str, Lease]
    leader_lease_id: str | None = None

    @property
    def leader(self) -> Lease | None:
        return self.sessions.get(self.leader_lease_id or "")


def lease_id_for(session_id: str, owner_pid: int, owner_start: str) -> str:
    material = f"{session_id}\0{owner_pid}\0{owner_start}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def process_is_alive(pid: int, start_token: str) -> bool:
    if pid <= 1 or not start_token:
        return False
    try:
        os.kill(pid, 0)
        process = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return process.returncode == 0 and process.stdout.strip() == start_token


class SessionRegistry:
    def __init__(
        self,
        path: Path,
        *,
        is_owner_alive: Callable[[int, str], bool] = process_is_alive,
    ) -> None:
        self.path = path
        self.lock_path = path.with_suffix(".lock")
        self.is_owner_alive = is_owner_alive

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _empty(self) -> dict[str, object]:
        return {"version": VERSION, "leader_id": None, "sessions": {}}

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return self._empty()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"Session registry is unreadable: {self.path}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), dict):
            raise RegistryError(f"Session registry has an invalid structure: {self.path}")
        if raw.get("version") != VERSION:
            raise RegistryError(f"Unsupported session registry version: {raw.get('version')!r}")
        return raw

    def _leases(self, raw: dict[str, object]) -> dict[str, Lease]:
        leases: dict[str, Lease] = {}
        supplied = raw.get("sessions")
        assert isinstance(supplied, dict)
        try:
            for key, value in supplied.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    raise ValueError
                lease = Lease(
                    lease_id=key,
                    session_id=str(value["session_id"]),
                    owner_pid=int(value["owner_pid"]),
                    owner_start=str(value["owner_start"]),
                    started_at=str(value["started_at"]),
                )
                if lease.owner_pid <= 1 or not lease.session_id or not lease.owner_start:
                    raise ValueError
                leases[key] = lease
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError(f"Session registry has invalid lease data: {self.path}") from exc
        return leases

    def _choose_leader(self, previous: str | None, leases: Mapping[str, Lease]) -> str | None:
        if previous and previous in leases:
            return previous
        if not leases:
            return None
        return max(leases.values(), key=lambda item: (item.started_at, item.lease_id)).lease_id

    def _pruned(self, raw: dict[str, object]) -> tuple[dict[str, Lease], str | None]:
        leases = {
            key: lease
            for key, lease in self._leases(raw).items()
            if self.is_owner_alive(lease.owner_pid, lease.owner_start)
        }
        previous = raw.get("leader_id")
        leader_id = self._choose_leader(previous if isinstance(previous, str) else None, leases)
        return leases, leader_id

    def _write(self, leases: Mapping[str, Lease], leader_id: str | None) -> Snapshot:
        payload = {
            "version": VERSION,
            "leader_id": leader_id,
            "sessions": {
                key: {field: value for field, value in asdict(lease).items() if field != "lease_id"}
                for key, lease in sorted(leases.items())
            },
        }
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, self.path)
        self.path.chmod(0o600)
        return self._snapshot(leases, leader_id)

    def _snapshot(self, leases: Mapping[str, Lease], leader_id: str | None) -> Snapshot:
        leader = leases.get(leader_id or "")
        return Snapshot(
            leader_id=leader.session_id if leader else None,
            sessions=dict(leases),
            leader_lease_id=leader_id,
        )

    def register(
        self,
        session_id: str,
        owner_pid: int,
        owner_start: str,
        started_at: str,
    ) -> Snapshot:
        session_id = session_id.strip()
        owner_start = owner_start.strip()
        started_at = started_at.strip()
        if not session_id or owner_pid <= 1 or not owner_start or not started_at:
            raise RegistryError("A complete session lease is required")
        with self._locked():
            raw = self._read()
            leases, leader_id = self._pruned(raw)
            # A single interactive Codex process can move from one thread to
            # another. Replace only that owner's previous lease.
            leases = {
                key: lease
                for key, lease in leases.items()
                if not (lease.owner_pid == owner_pid and lease.owner_start == owner_start)
            }
            new_id = lease_id_for(session_id, owner_pid, owner_start)
            leases[new_id] = Lease(new_id, session_id, owner_pid, owner_start, started_at)
            if leader_id not in leases:
                leader_id = new_id
            return self._write(leases, leader_id)

    def unregister(
        self,
        session_id: str,
        owner_pid: int | None = None,
        owner_start: str | None = None,
    ) -> Snapshot:
        with self._locked():
            raw = self._read()
            leases, leader_id = self._pruned(raw)
            matched: list[str] = []
            for key, lease in leases.items():
                if lease.session_id != session_id:
                    continue
                if owner_pid is not None and lease.owner_pid != owner_pid:
                    continue
                if owner_start is not None and lease.owner_start != owner_start:
                    continue
                matched.append(key)
            for key in matched:
                leases.pop(key, None)
            leader_id = self._choose_leader(leader_id, leases)
            return self._write(leases, leader_id)

    def prune(self) -> Snapshot:
        with self._locked():
            raw = self._read()
            before = self._leases(raw)
            leases, leader_id = self._pruned(raw)
            previous = raw.get("leader_id")
            if before == leases and previous == leader_id:
                return self._snapshot(leases, leader_id)
            return self._write(leases, leader_id)

    def snapshot(self) -> Snapshot:
        with self._locked():
            raw = self._read()
            leases = self._leases(raw)
            previous = raw.get("leader_id")
            lease_id = previous if isinstance(previous, str) and previous in leases else None
            return self._snapshot(leases, lease_id)


def snapshot_json(snapshot: Snapshot) -> str:
    leader = snapshot.leader
    return json.dumps(
        {
            "leader_id": snapshot.leader_id,
            "leader_lease_id": snapshot.leader_lease_id,
            "leader": asdict(leader) if leader else None,
            "count": len(snapshot.sessions),
        },
        ensure_ascii=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--session-id", required=True)
    register.add_argument("--owner-pid", type=int, required=True)
    register.add_argument("--owner-start", required=True)
    register.add_argument("--started-at", required=True)
    unregister = subparsers.add_parser("unregister")
    unregister.add_argument("--session-id", required=True)
    unregister.add_argument("--owner-pid", type=int)
    unregister.add_argument("--owner-start")
    subparsers.add_parser("prune")
    subparsers.add_parser("snapshot")
    args = parser.parse_args()
    registry = SessionRegistry(Path(args.path).expanduser())
    try:
        if args.command == "register":
            result = registry.register(args.session_id, args.owner_pid, args.owner_start, args.started_at)
        elif args.command == "unregister":
            result = registry.unregister(args.session_id, args.owner_pid, args.owner_start)
        elif args.command == "prune":
            result = registry.prune()
        else:
            result = registry.snapshot()
        print(snapshot_json(result))
        return 0
    except RegistryError as exc:
        print(f"session registry error: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
