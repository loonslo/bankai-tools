from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/x-follow"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai-config-sync"))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/privacy-review/scripts/vendor")
)
