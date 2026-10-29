import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from privacy_guard.advisory import review_files, review_push_plan, review_staged
from privacy_guard.cli import main
from privacy_guard.model import Blob
from privacy_guard.rules import inspect


@pytest.fixture
def scanner():
    if not (os.environ.get("PRIVACY_GUARD_GITLEAKS") or shutil.which("gitleaks")):
        pytest.skip("Gitleaks not installed; secret integration not verified")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_local_path_and_email_are_redacted_rules():
    content = b"Contact: synthetic@privacy-test.invalid\nFile: C:\\Users\\Synthetic\\private.txt"
    hits = inspect(Blob("note.md", content))
    assert {h.rule for h in hits} >= {"email", "local-path"}
    assert all(h.masked == "[REDACTED]" for h in hits)


def test_missing_scanner_and_invalid_arguments_are_incomplete(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PRIVACY_GUARD_GITLEAKS", "missing-synthetic-binary")
    assert main(["doctor", "--repo", str(tmp_path)]) == 1
    assert not json.loads(capsys.readouterr().out)["review_complete"]
    assert main(["push", "--repo", str(tmp_path)]) == 1
    assert not json.loads(capsys.readouterr().out)["review_complete"]
    assert main(["private-sensitive-argument"]) == 1
    assert "private-sensitive-argument" not in capsys.readouterr().out


def test_files_clean_risk_credential_and_escape(tmp_path, scanner, monkeypatch):
    (tmp_path / "clean.txt").write_text("hello world", encoding="utf-8")
    (tmp_path / "note.md").write_text("Contact: synthetic@privacy-test.invalid", encoding="utf-8")
    before = snapshot(tmp_path)
    assert review_files(str(tmp_path), ["clean.txt"])["risk_level"] == "none"
    result = review_files(str(tmp_path), ["note.md"])
    assert result["review_complete"] and result["findings"]
    assert "synthetic@privacy-test.invalid" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)
    assert not review_files(str(tmp_path), ["../outside.txt"])["review_complete"]
    assert snapshot(tmp_path) == before
    # Credential paths are classified without opening their content.
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("credential opened"))
    assert not review_files(str(tmp_path), [".env"])["review_complete"]


def test_staged_history_and_skill_are_read_only(tmp_path, scanner):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Synthetic Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "core.hooksPath", str(tmp_path / "no-hooks"))
    secret = "ghp_" + "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5"
    (tmp_path / "sample.py").write_text(f'token = "{secret}"', encoding="utf-8")
    git(tmp_path, "add", "sample.py")
    git(tmp_path, "commit", "-m", "synthetic fixture")
    (tmp_path / "note.md").write_text("hello", encoding="utf-8")
    git(tmp_path, "add", "note.md")
    before = snapshot(tmp_path)
    assert review_staged(str(tmp_path))["review_complete"]
    result = review_push_plan(str(tmp_path), [{"tip": "HEAD", "base": None}])
    assert result["review_complete"] and result["risk_level"] == "critical"
    assert result["scope"][0]["coverage"] == "all-tip-history"
    script = Path(__file__).resolve().parents[1] / "skills/privacy-review/scripts/review.py"
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            str(script),
            "files",
            "--repo",
            str(tmp_path),
            "--paths",
            "sample.py",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    assert json.loads(proc.stdout)["risk_level"] == "critical"
    assert secret.encode() not in proc.stdout + proc.stderr
    assert snapshot(tmp_path) == before


def test_skill_is_portable(tmp_path, scanner):
    original = Path(__file__).resolve().parents[1] / "skills/privacy-review"
    copied = tmp_path / "copied-skill"
    shutil.copytree(original, copied, ignore=shutil.ignore_patterns("__pycache__"))
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(copied / "scripts/review.py"),
            "doctor",
            "--repo",
            str(tmp_path),
        ],
        capture_output=True,
        check=True,
    )
    assert json.loads(proc.stdout)["review_complete"]
