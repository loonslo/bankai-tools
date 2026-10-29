import sys

from .xfollow import main as xfollow_main


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] != "x-follow":
        print("Usage: bankai x-follow {fetch,analyze} --help")
        return 0 if args in ([], ["--help"], ["-h"]) else 1
    return xfollow_main(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
