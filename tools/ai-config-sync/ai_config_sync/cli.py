"""Explicit commands: default preview, --apply for every filesystem mutation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .bundle import restore_plan, save_bundle
from .core import ConfigError, apply_plan, load_device, path, sync_plan
from .history import journals, recover, undo_plan
from .transaction import SyncBusyError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local AI rules and selected settings; preview by default"
    )
    parser.add_argument("--device", required=True, type=Path, help="Local device JSON (not shared)")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="Deploy rule topics and opted-in scalar settings")
    sync.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    sync.add_argument("--overwrite-conflicts", action="store_true")
    sync.add_argument("--apply", action="store_true")
    backup = commands.add_parser("backup", help="Export only selected rules/settings to a new ZIP")
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--apply", action="store_true")
    restore = commands.add_parser(
        "restore", help="Restore a new-format ZIP into this device's explicit roots"
    )
    restore.add_argument("bundle", type=Path)
    restore.add_argument("--overwrite-conflicts", action="store_true")
    restore.add_argument("--apply", action="store_true")
    commands.add_parser("history", help="List this device's local operations")
    undo = commands.add_parser("undo", help="Restore originals if there have been no later edits")
    undo.add_argument("operation_id")
    undo.add_argument("--apply", action="store_true")
    recovery = commands.add_parser(
        "recover", help="Inspect interrupted operations or roll them back"
    )
    recovery.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        device = load_device(args.device.absolute())
        if args.command in {"history", "recover"}:
            records = (
                journals(device) if args.command == "history" else recover(device, apply=args.apply)
            )
            result = {
                "operations": [
                    {
                        "id": r["operation_id"],
                        "status": r["status"],
                        "changes": len(r.get("changes", [])),
                    }
                    for r in records
                ]
            }
        elif args.command == "backup":
            files = save_bundle(device, args.output, apply=args.apply)
            result = {"status": "saved" if args.apply else "preview", "files": files}
        else:
            if args.command == "sync":
                plan = sync_plan(
                    device, path(str(args.source.absolute())), overwrite=args.overwrite_conflicts
                )
            elif args.command == "restore":
                plan = restore_plan(
                    device, args.bundle.absolute(), overwrite=args.overwrite_conflicts
                )
            else:
                plan = undo_plan(device, args.operation_id)
            operation = apply_plan(device, plan) if args.apply else None
            result = {
                "status": "applied" if args.apply else "preview",
                "changes": len(plan),
                "paths": [str(p) for p in plan],
                "operation_id": operation.name if operation else None,
                "note": "File writes do not prove assistant loading; start a new session to check.",
            }
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ConfigError, SyncBusyError) as exc:
        print(json.dumps({"status": "incomplete", "error": str(exc)}, ensure_ascii=True))
        return 2
    except Exception:
        print(json.dumps({"status": "incomplete", "error": "Operation failed; details suppressed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
