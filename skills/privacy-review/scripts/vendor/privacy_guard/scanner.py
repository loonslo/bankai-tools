import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .model import Blob, GuardError, Result, finding
from .rules import inspect


def executable() -> str:
    local = Path(__file__).resolve().parents[1] / ".tools" / "gitleaks.exe"
    if getattr(sys, "frozen", False):
        if not local.is_file():
            raise GuardError("Bundled scanner unavailable; review incomplete")
        return str(local)
    configured = os.environ.get("PRIVACY_GUARD_GITLEAKS")
    if configured:
        return configured
    candidate = str(local) if local.is_file() else shutil.which("gitleaks")
    if not candidate:
        raise GuardError("Gitleaks unavailable; install or set PRIVACY_GUARD_GITLEAKS")
    return candidate


def scan(blobs: list[Blob]) -> Result:
    binary = executable()  # dependency health is mandatory even for an empty scan
    result = Result()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITLEAKS_")}
    with tempfile.TemporaryDirectory(prefix="privacy-guard-") as tmp:
        config = Path(tmp) / "rules.toml"
        config.write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
        report = Path(tmp) / "redacted.json"
        for blob in blobs or [Blob("", b"")]:
            if blob.path:
                result.scanned += 1
                result.findings.extend(inspect(blob))
            proc = subprocess.run(
                [
                    binary,
                    "stdin",
                    "--no-banner",
                    "--no-color",
                    "--redact=100",
                    "--config",
                    str(config),
                    "--gitleaks-ignore-path",
                    tmp,
                    "--ignore-gitleaks-allow",
                    "--max-decode-depth",
                    "2",
                    "--report-format",
                    "json",
                    "--report-path",
                    str(report),
                    "--exit-code",
                    "10",
                ],
                input=blob.content,
                capture_output=True,
                cwd=tmp,
                env=env,
                timeout=45,
                check=False,
            )
            if proc.returncode not in {0, 10}:
                raise GuardError("Gitleaks failed; raw diagnostics suppressed")
            if not report.is_file():
                raise GuardError("Gitleaks did not produce a report")
            payload = json.loads(report.read_text(encoding="utf-8"))
            if not isinstance(payload, list) or (proc.returncode == 10 and not payload):
                raise GuardError("Invalid Gitleaks report")
            for item in payload:
                rule = item["RuleID"]
                line = item["StartLine"]
                if not isinstance(rule, str) or not re.fullmatch(r"[a-z0-9_-]{1,100}", rule):
                    raise GuardError("Invalid Gitleaks rule identifier")
                if not isinstance(line, int) or line < 1:
                    raise GuardError("Invalid Gitleaks line number")
                # No Match, Secret, raw filename, message, or Gitleaks Fingerprint leaves this module.
                result.findings.append(
                    finding(
                        blob, "secret:" + rule, "critical", line, blob.content + str(line).encode()
                    )
                )
            report.unlink()
    return result
