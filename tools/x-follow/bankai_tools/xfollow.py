"""Bounded, read-only API collection and offline relationship analysis."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from .twitterapi import ApiError, HANDLE, KEY_ENV, TwitterApiClient


def collect(
    client: TwitterApiClient,
    handle: str,
    relation: str,
    *,
    max_pages: int = 20,
    max_items: int = 10000,
) -> dict[str, Any]:
    if relation not in {"following", "followers"} or max_pages < 1 or max_items < 1:
        raise ValueError("Invalid collection bounds")
    users: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    cursor = None
    pages = 0
    reason = "collection_limit"
    for _ in range(max_pages):
        try:
            page = client.page(relation, handle, cursor)
        except ApiError:
            reason = "api_error"
            break
        pages += 1
        for row in page["rows"]:
            if row["id"] not in users and len(users) >= max_items:
                return {
                    "complete": False,
                    "reason": "item_limit",
                    "pages": pages,
                    "users": list(users.values()),
                }
            users[row["id"]] = row
        if page["terminal"]:
            return {
                "complete": True,
                "reason": "api_terminal",
                "pages": pages,
                "users": list(users.values()),
            }
        cursor = page["cursor"]
        if not cursor or cursor in seen:
            reason = "invalid_cursor"
            break
        seen.add(cursor)
        if len(users) >= max_items:
            reason = "item_limit"
            break
    return {"complete": False, "reason": reason, "pages": pages, "users": list(users.values())}


def analyze(snapshot: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Absence-based results require both lists to have explicit API termination."""
    lists = []
    for relation in ("following", "followers"):
        section = snapshot.get(relation)
        if not isinstance(section, dict) or section.get("complete") is not True:
            raise ValueError("Both lists must be complete before relationship analysis")
        rows = section.get("users")
        if not isinstance(rows, list):
            raise ValueError("Invalid user list")
        indexed = {}
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not row["id"].isdigit()
                or not isinstance(row.get("username"), str)
                or not HANDLE.fullmatch(row["username"])
            ):
                raise ValueError("Invalid user record")
            indexed[row["id"]] = {k: str(row.get(k, "")) for k in ("id", "username", "name", "bio")}
        lists.append(indexed)
    following, followers = lists

    def order(row: dict[str, str]) -> tuple[str, str]:
        return row["username"].lower(), row["id"]

    return {
        "mutual": sorted((following[i] for i in following.keys() & followers.keys()), key=order),
        "not_following_back": sorted(
            (following[i] for i in following.keys() - followers.keys()), key=order
        ),
        "fans_only": sorted((followers[i] for i in followers.keys() - following.keys()), key=order),
    }


def write_output(path: Path, value: dict[str, Any], fmt: str = "json") -> None:
    """Exclusive creation avoids silently replacing existing exports."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig" if fmt == "csv" else "utf-8", newline="") as file:
        if fmt == "json":
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
        else:
            writer = csv.DictWriter(file, fieldnames=["relation", "id", "username", "name", "bio"])
            writer.writeheader()
            for relation, rows in value.items():
                for row in rows:
                    safe = {
                        key: (
                            "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
                        )
                        for key, text in row.items()
                    }
                    writer.writerow({"relation": relation, **safe})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only X relationship tool")
    subs = parser.add_subparsers(dest="command", required=True)
    fetch = subs.add_parser(
        "fetch", help="Fetch both lists using twitterapi.io (billed by provider)"
    )
    fetch.add_argument("username")
    fetch.add_argument("--max-pages", type=int, default=20, help="Per list; default 20")
    fetch.add_argument("--max-items", type=int, default=10000, help="Per list; default 10000")
    fetch.add_argument("--interval", type=float, default=5.2)
    fetch.add_argument("--output", type=Path, required=True)
    offline = subs.add_parser("analyze", help="Analyze a complete saved snapshot offline")
    offline.add_argument("snapshot", type=Path)
    offline.add_argument("--format", choices=["json", "csv"], default="json")
    offline.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        # Check before any paid requests, as output never overwrites existing data.
        if args.output.exists():
            raise FileExistsError()
        if args.command == "analyze":
            result = analyze(json.loads(args.snapshot.read_text(encoding="utf-8")))
            write_output(args.output, result, args.format)
            print(json.dumps({"complete": True, "counts": {k: len(v) for k, v in result.items()}}))
            return 0
        handle = args.username.strip().removeprefix("@")
        if (
            not HANDLE.fullmatch(handle)
            or not 1 <= args.max_pages <= 1500
            or not 1 <= args.max_items <= 100000
            or not 0 <= args.interval <= 60
        ):
            raise ValueError()
        client = TwitterApiClient(os.environ.get(KEY_ENV, ""), interval=args.interval)
        info = client.user_info(handle)
        snapshot: dict[str, Any] = {
            "schema_version": 1,
            "account": info,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        for relation in ("following", "followers"):
            snapshot[relation] = collect(
                client, handle, relation, max_pages=args.max_pages, max_items=args.max_items
            )
            if not snapshot[relation]["complete"]:
                # Stop after an error or limit; avoid spending on the second list.
                break
        complete = all(
            snapshot.get(r, {}).get("complete") is True for r in ("following", "followers")
        )
        snapshot["complete"] = complete
        if complete:
            snapshot["analysis"] = analyze(snapshot)
        write_output(args.output, snapshot)
        print(json.dumps({"complete": complete, "saved": True}))
        return 0 if complete else 2
    except FileExistsError:
        print(json.dumps({"complete": False, "error": "Output already exists; choose a new file"}))
    except ApiError, OSError, ValueError, TypeError, KeyError:
        print(
            json.dumps(
                {
                    "complete": False,
                    "error": "Request or input failed; check key, network and input format",
                }
            )
        )
    return 1
