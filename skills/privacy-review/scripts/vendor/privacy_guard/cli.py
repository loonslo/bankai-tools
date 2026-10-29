import argparse
import json

from .advisory import error_report, review_files, review_push_plan, review_staged
from .model import GuardError
from .scanner import scan


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise GuardError("Invalid review arguments; input suppressed")


def main(argv: list[str] | None = None) -> int:
    parser = SafeParser(description="Read-only privacy review; never edits inspected material")
    parser.add_argument("mode", choices=["files", "staged", "push", "doctor"])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--paths", nargs="+")
    parser.add_argument("--source", choices=["auto", "memory", "chat_history"], default="auto")
    parser.add_argument("--push-range", nargs=2, action="append", metavar=("TIP", "BASE"))
    try:
        args = parser.parse_args(argv)
        if (args.paths or args.source != "auto") and args.mode != "files":
            raise GuardError("File options require files mode")
        if args.push_range and args.mode != "push":
            raise GuardError("Push ranges require push mode")
        if args.mode == "files":
            result = review_files(args.repo, args.paths or [], args.source)
        elif args.mode == "staged":
            result = review_staged(args.repo)
        elif args.mode == "push":
            if not args.push_range:
                raise GuardError("Provide explicit push ranges; use '-' for all tip history")
            result = review_push_plan(
                args.repo,
                [
                    {"tip": tip, "base": None if base == "-" else base}
                    for tip, base in args.push_range
                ],
            )
        else:
            from .advisory import report

            result = report(scan([]))
    except Exception as exc:
        result = error_report(exc)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["review_complete"] else 1
