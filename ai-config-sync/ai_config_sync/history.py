"""Validate local transaction records before recovery or undo."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .core import ConfigError, Device, read
from .streaming import no_links
from .transaction import PlannedChanges, recover_transactions
from .utils import digest


def journals(device: Device) -> list[dict[str, Any]]:
    no_links(device.state)
    directory = device.state / "transactions"
    no_links(directory)
    result = []
    for file in sorted(directory.glob("*.json")):
        journal = json.loads(read(file) or b"null")
        if not isinstance(journal, dict) or not re.fullmatch(r"[a-f0-9]{32}", file.stem):
            raise ConfigError("Invalid local transaction record")
        if journal.get("operation_id") != file.stem:
            raise ConfigError("Invalid local operation identity")
        backup = device.state / "backups" / file.stem
        if journal.get("backup") != str(backup):
            raise ConfigError("Backup escaped local state directory")
        manifest = backup / "manifest.json"
        records = json.loads(read(manifest) or b"null")
        changes = journal.get("changes")
        if not isinstance(records, list) or not isinstance(changes, list):
            raise ConfigError("Invalid local backup records")
        allowed = device.allowed_targets()
        for record in [*records, *changes]:
            if not isinstance(record, dict) or Path(record.get("path", "")) not in allowed:
                raise ConfigError("Operation targets do not match this device")
            no_links(Path(record["path"]))
        if len({r["path"] for r in records}) != len(records):
            raise ConfigError("Duplicate local backup record")
        if {r["path"] for r in records} != {r["path"] for r in changes}:
            raise ConfigError("Incomplete local transaction record")
        for i, record in enumerate(records):
            if record.get("file") != str(i) or type(record.get("existed")) is not bool:
                raise ConfigError("Invalid local backup location")
            if record["existed"] and digest(read(backup / str(i))) != record.get("sha256"):
                raise ConfigError("Local backup checksum mismatch")
        result.append(journal)
    return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)


def undo_plan(device: Device, operation_id: str) -> PlannedChanges:
    operation = next(
        (
            item
            for item in journals(device)
            if item["operation_id"] == operation_id and item["status"] == "COMMITTED"
        ),
        None,
    )
    if operation is None:
        raise ConfigError("No committed operation with that ID")
    backup = Path(operation["backup"])
    manifest = backup / "manifest.json"
    manifest_data = read(manifest)
    expected = {manifest: digest(manifest_data)}
    changes = {}
    wrote = {record["path"]: record["sha256"] for record in operation["changes"]}
    for record in json.loads(manifest_data):
        target = Path(record["path"])
        original_file = backup / record["file"]
        original = read(original_file) if record["existed"] else None
        if digest(original) != record["sha256"]:
            raise ConfigError("Local backup checksum mismatch")
        if record["existed"]:
            expected[original_file] = digest(original)
        current = read(target)
        if digest(current) not in {wrote[str(target)], record["sha256"]}:
            raise ConfigError("Target changed since the operation; undo refused")
        expected[target] = digest(current)
        if current != original:
            changes[target] = original
    return PlannedChanges(
        changes,
        expected=expected,
        state_root=device.state,
        metadata={"operation": "undo", "restored_operation": operation_id},
    )


def recover(device: Device, *, apply: bool = False) -> list[dict[str, Any]]:
    journals(device)  # Validate paths and backup hashes before the inherited recovery routine.
    return recover_transactions(device.state, action="rollback" if apply else "status")
