"""Coordinated, recoverable file transactions for all local sync operations."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import uuid
from typing import Any, Callable, Iterator, Mapping

from .utils import atomic_write, digest, json_bytes, process_is_alive, read_bytes
from .streaming import StreamContent, content_digest, file_digest, write_stream, no_links


class SyncBusyError(RuntimeError):
    """Another sync operation is currently using this device state."""


class PlannedChanges(dict[Path, bytes | None | StreamContent]):
    """A dict-compatible write plan carrying the read-time consistency boundary."""

    def __init__(
        self,
        changes: Mapping[Path, bytes | None | StreamContent] = (),
        *,
        expected: Mapping[Path, str | None] | None = None,
        expected_trees: Mapping[Path, Mapping[str, str]] | None = None,
        state_root: Path | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(changes)
        self.expected = dict(expected or {})
        self.expected_trees = {
            Path(path): dict(value) for path, value in (expected_trees or {}).items()
        }
        self.state_root = state_root
        self.metadata = dict(metadata or {})


def _stream_paths(plan: PlannedChanges) -> set[Path]:
    return {Path(p) for p in plan.metadata.get("stream_paths", [])} | {
        p for p, v in plan.items() if isinstance(v, StreamContent)
    }


def _current_hash(path: Path, streamed: set[Path]) -> str | None:
    return file_digest(path) if path in streamed else digest(read_bytes(path))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lock_path(state_root: Path) -> Path:
    return state_root / "sync.lock"


class SyncLock:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.path = _lock_path(state_root)
        self.acquired = False
        self.handle = None

    def __enter__(self) -> "SyncLock":
        self.state_root.mkdir(parents=True, exist_ok=True)
        # Keep the guard inode permanently. Deleting a locked file would let
        # another process lock a different inode at the same pathname.
        self.handle = (self.state_root / "sync.guard").open("a+b")
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise SyncBusyError("Sync is already running") from error
        self.acquired = True
        try:
            if self.path.exists():
                try:
                    previous = json.loads(self.path.read_text(encoding="utf-8"))
                except OSError, ValueError:
                    previous = {}
                pid = previous.get("pid") if isinstance(previous, dict) else None
                if isinstance(pid, int) and process_is_alive(pid):
                    raise SyncBusyError(
                        "A legacy sync operation is still running; close it before upgrading"
                    )
            atomic_write(self.path, json_bytes({"pid": os.getpid(), "started_at": _now()}))
        except BaseException:
            self.handle.close()
            self.handle = None
            self.acquired = False
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if self.acquired:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self.handle.close()  # Kernel releases the lock, even on crash.
                self.handle = None
                self.acquired = False


def _tree_state(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    if root.is_symlink():
        raise ValueError(f"Symlink requires manual migration: {root}")
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink requires manual migration: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            result[relative] = digest(path.read_bytes()) or ""
    return result


def _journal_path(state_root: Path, operation_id: str) -> Path:
    return state_root / "transactions" / f"{operation_id}.json"


def _write_journal(path: Path, journal: dict[str, Any]) -> None:
    journal["updated_at"] = _now()
    atomic_write(path, json_bytes(journal))


def _backup_originals(
    originals: Mapping[Path, bytes | None | StreamContent], backup_root: Path, operation_id: str
) -> tuple[Path, list[dict[str, Any]]]:
    backup = backup_root / operation_id
    backup.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    for index, (path, data) in enumerate(originals.items()):
        record = {
            "path": str(path),
            "file": str(index),
            "existed": data is not None,
            "sha256": content_digest(data),
            "size": data.size
            if isinstance(data, StreamContent)
            else len(data)
            if data is not None
            else 0,
        }
        records.append(record)
        if data is not None:
            if isinstance(data, StreamContent):
                write_stream(backup / str(index), data)
            else:
                (backup / str(index)).write_bytes(data)
    atomic_write(backup / "manifest.json", json_bytes(records))
    return backup, records


def _verify_plan(plan: PlannedChanges, expected: Mapping[Path, str | None]) -> None:
    streamed = _stream_paths(plan)
    for path, expected_hash in expected.items():
        if _current_hash(path, streamed) != expected_hash:
            raise ValueError(f"Plan invalidated by a newer change: {path}")
    for root, tree in plan.expected_trees.items():
        if _tree_state(root) != tree:
            raise ValueError(f"Plan invalidated by a newer source change: {root}")


def transaction(
    changes: Mapping[Path, bytes | None | StreamContent],
    backup_root: Path,
    *,
    state_root: Path | None = None,
    operation_id: str | None = None,
    lock: bool = True,
    writer: Callable[[Path, bytes | None | StreamContent], None] | None = None,
) -> Path | None:
    """Apply a planned batch with a journal and recoverable backup.

    A ``PlannedChanges`` instance validates the hashes and source trees captured
    during preview.  A plain dict remains supported for old callers, but its
    consistency boundary starts when this function is called.
    """
    plan = changes if isinstance(changes, PlannedChanges) else PlannedChanges(changes)
    streamed = _stream_paths(plan)
    if streamed:
        plan.metadata["stream_paths"] = sorted(str(p) for p in streamed)
    write = writer or write_file
    state = state_root or plan.state_root or backup_root.parent
    operation = operation_id or uuid.uuid4().hex
    context = SyncLock(state) if lock else null_lock()
    with context:
        pending = recover_transactions(state)
        if any(
            item.get("status")
            in {"PREPARED", "APPLYING", "INTERRUPTED", "ROLLBACK_REQUIRED", "INVALID_JOURNAL"}
            for item in pending
        ):
            raise RuntimeError("Unfinished transaction requires recovery before applying changes")
        originals = {}
        for path, data in plan.items():
            if path not in streamed:
                originals[path] = read_bytes(path)
                continue
            no_links(path)
            if isinstance(data, StreamContent) and data.new_only and path.exists():
                raise ValueError("stream target already exists")
            prior = file_digest(path)
            originals[path] = (
                None if prior is None else StreamContent.from_path(path, prior, path.stat().st_size)
            )
        expected = {path: content_digest(data) for path, data in originals.items()}
        expected.update(plan.expected)
        _verify_plan(plan, expected)
        backup, records = _backup_originals(originals, backup_root, operation)
        journal_path = _journal_path(state, operation)
        journal: dict[str, Any] = {
            "schema_version": 1,
            "operation_id": operation,
            "status": "PREPARED",
            "backup": str(backup),
            "records": records,
            "changes": [
                {
                    "path": str(path),
                    "sha256": content_digest(data),
                    "delete": data is None,
                    **({"streamed": True} if path in streamed else {}),
                }
                for path, data in plan.items()
            ],
            "metadata": plan.metadata,
            "created_at": _now(),
        }
        _write_journal(journal_path, journal)
        completed: list[Path] = []
        current: Path | None = None
        try:
            _verify_plan(plan, expected)
            journal["status"] = "APPLYING"
            _write_journal(journal_path, journal)
            for current, data in plan.items():
                if _current_hash(current, streamed) != expected[current]:
                    raise ValueError(f"Plan invalidated by a newer change: {current}")
                write(current, data)
                if current in streamed and _current_hash(current, streamed) != content_digest(data):
                    raise ValueError("stream write verification failed")
                completed.append(current)
            journal["status"] = "COMMITTED"
            _write_journal(journal_path, journal)
            return backup
        except BaseException as error:
            if isinstance(error, Exception):
                rollback_paths = list(completed)
                partial_conflicts: list[str] = []
                if current is not None and current not in rollback_paths:
                    try:
                        actual = _current_hash(current, streamed)
                        if actual == content_digest(plan[current]):
                            rollback_paths.append(current)
                        elif current in streamed and actual != expected[current]:
                            partial_conflicts.append(str(current))
                    except OSError:
                        pass
                conflicts: list[str] = partial_conflicts
                by_path = {record["path"]: record for record in records}
                for path in reversed(rollback_paths):
                    if _current_hash(path, streamed) != content_digest(plan[path]):
                        conflicts.append(str(path))
                        continue
                    original = by_path[str(path)]
                    data = None
                    if original["existed"]:
                        source = backup / original["file"]
                        data = (
                            StreamContent.from_path(
                                source, original["sha256"], original["size"], new_only=False
                            )
                            if path in streamed
                            else source.read_bytes()
                        )
                    write(path, data)
                journal["status"] = "ROLLBACK_REQUIRED" if conflicts else "ROLLED_BACK"
                if conflicts:
                    journal["rollback_conflicts"] = conflicts
                _write_journal(journal_path, journal)
            else:
                journal["status"] = "INTERRUPTED"
                _write_journal(journal_path, journal)
            raise


def write_file(path: Path, data: bytes | None | StreamContent) -> None:
    no_links(path)
    if isinstance(data, StreamContent):
        write_stream(path, data)
        return
    if data is None:
        if path.is_symlink():
            raise ValueError(f"Symlink requires manual migration: {path}")
        path.unlink(missing_ok=True)
        return
    if path.is_symlink():
        raise ValueError(f"Symlink requires manual migration: {path}")
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    atomic_write(path, data)
    if mode is not None:
        os.chmod(path, mode)


@contextmanager
def null_lock() -> Iterator[None]:
    yield None


def recover_transactions(state_root: Path, *, action: str = "status") -> list[dict[str, Any]]:
    """List or safely roll back interrupted transactions."""
    root = state_root / "transactions"
    if not root.exists():
        return []
    journals = []
    for path in sorted(root.glob("*.json")):
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            journals.append(
                {"operation_id": path.stem, "status": "INVALID_JOURNAL", "path": str(path)}
            )
            continue
        journal["path"] = str(path)
        if (
            journal.get("status") in {"PREPARED", "APPLYING", "INTERRUPTED", "ROLLBACK_REQUIRED"}
            and action == "rollback"
        ):
            with SyncLock(state_root):
                conflicts: list[str] = []
                backup = Path(journal["backup"])
                records = {
                    record["path"]: record
                    for record in json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
                }
                changes = {item["path"]: item for item in journal.get("changes", [])}
                streamed = {Path(name) for name, item in changes.items() if item.get("streamed")}
                for name, item in records.items():
                    target = Path(name)
                    current_hash = _current_hash(target, streamed)
                    if current_hash != item.get("sha256") and current_hash != changes.get(
                        name, {}
                    ).get("sha256"):
                        conflicts.append(name)
                        continue
                    source = backup / item["file"]
                    data = (
                        (
                            StreamContent.from_path(
                                source, item["sha256"], item["size"], new_only=False
                            )
                            if target in streamed
                            else source.read_bytes()
                        )
                        if item["existed"]
                        else None
                    )
                    if (
                        content_digest(data) != item.get("sha256")
                        or isinstance(data, StreamContent)
                        and file_digest(source) != item["sha256"]
                    ):
                        conflicts.append(name)
                        continue
                    write_file(target, data)
                journal["status"] = "ROLLBACK_REQUIRED" if conflicts else "ROLLED_BACK"
                if conflicts:
                    journal["rollback_conflicts"] = conflicts
                _write_journal(path, journal)
        journals.append(journal)
    return journals
