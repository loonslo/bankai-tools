"""Self-contained entry point; only loads the engine shipped alongside this skill."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

from privacy_guard.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
