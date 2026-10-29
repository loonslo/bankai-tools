import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from ai_config_sync.bundle import load_bundle, restore_plan, save_bundle
from ai_config_sync.cli import main
from ai_config_sync.core import (
    ConfigError,
    TOPICS,
    apply_plan,
    load_device,
    sync_plan,
)
from ai_config_sync.history import journals, recover, undo_plan
from ai_config_sync.transaction import (
    SyncBusyError,
    SyncLock,
    transaction,
    write_file,
)
from ai_config_sync.utils import digest


@pytest.fixture
def environment(tmp_path):
    source = tmp_path / "source"
    for folder in ("common", "codex", "claude"):
        (source / folder).mkdir(parents=True)
    for topic in TOPICS:
        (source / "common" / f"{topic}.md").write_text(
            f"# {topic}\nShared text\n", encoding="utf-8"
        )
    (source / "codex/config.toml").write_text('model_reasoning_effort = "high"\n', encoding="utf-8")
    (source / "claude/settings.shared.json").write_text(
        '{"autoMemoryEnabled":true}', encoding="utf-8"
    )
    roots = {agent: tmp_path / agent for agent in ("codex", "claude")}
    for root in roots.values():
        root.mkdir()
    raw = {"state_dir": str(tmp_path / "state"), **{k: str(v) for k, v in roots.items()}}
    device_file = tmp_path / "device.json"
    device_file.write_text(json.dumps(raw), encoding="utf-8")
    return source, device_file, raw


def configure(file, raw):
    file.write_text(json.dumps(raw), encoding="utf-8")
    return load_device(file)


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_preview_no_writes_sync_preserves_outside_and_is_idempotent(environment):
    source, file, raw = environment
    device = load_device(file)
    target = device.roots["codex"] / "AGENTS.md"
    target.write_bytes(b"Personal instructions\r\n")
    before = contents(file.parent)
    plan = sync_plan(device, source)
    assert contents(file.parent) == before and not device.state.exists()
    assert b"Personal instructions\r\n" in plan[target]
    backup = apply_plan(device, plan)
    assert backup.is_dir() and journals(device)[0]["status"] == "COMMITTED"
    assert not sync_plan(device, source)
    assert b"Personal instructions\r\n" in target.read_bytes()


def test_source_update_allowed_local_edits_conflict(environment):
    source, file, raw = environment
    device = load_device(file)
    apply_plan(device, sync_plan(device, source))
    (source / "common/instructions.md").write_text("# updated", encoding="utf-8")
    apply_plan(device, sync_plan(device, source))
    target = device.roots["codex"] / "AGENTS.md"
    target.write_bytes(target.read_bytes().replace(b"updated", b"locally edited"))
    with pytest.raises(ConfigError, match="changed locally"):
        sync_plan(device, source)
    assert sync_plan(device, source, overwrite=True)


def test_stale_source_or_target_aborts_before_writes(environment):
    source, file, raw = environment
    device = load_device(file)
    plan = sync_plan(device, source)
    (source / "common/instructions.md").write_text("changed after preview", encoding="utf-8")
    with pytest.raises(ValueError, match="invalidated"):
        apply_plan(device, plan)
    assert not (device.roots["claude"] / "CLAUDE.md").exists()


def test_selected_settings_keep_unknown_and_credentials_outside_exports(environment, monkeypatch):
    source, file, raw = environment
    raw.update(codex_keys=["model_reasoning_effort"], claude_keys=["autoMemoryEnabled"])
    device = configure(file, raw)
    codex = device.roots["codex"] / "config.toml"
    codex.write_text(
        '# user comment\ncustom = "keep"\n[providers.local]\nendpoint = "local"\n', encoding="utf-8"
    )
    claude = device.roots["claude"] / "settings.json"
    claude.write_text('{"custom":42,"env":{"LOCAL_NAME":"not-shared"}}', encoding="utf-8")
    apply_plan(device, sync_plan(device, source))
    assert "# user comment" in codex.read_text(encoding="utf-8")
    assert "[providers.local]" in codex.read_text(encoding="utf-8")
    assert json.loads(claude.read_text(encoding="utf-8"))["custom"] == 42
    output = file.parent / "backup.zip"
    save_bundle(device, output, apply=True)
    data = load_bundle(output)
    assert json.loads(data["settings/claude.json"]) == {"autoMemoryEnabled": True}
    assert b"not-shared" not in b"".join(data.values())


def test_settings_conflict_requires_explicit_overwrite(environment):
    source, file, raw = environment
    raw["codex_keys"] = ["model_reasoning_effort"]
    device = configure(file, raw)
    target = device.roots["codex"] / "config.toml"
    target.write_text('model_reasoning_effort = "low"', encoding="utf-8")
    with pytest.raises(ConfigError, match="conflict"):
        sync_plan(device, source)
    apply_plan(device, sync_plan(device, source, overwrite=True))
    assert '"high"' in target.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "change",
    [
        {"codex_keys": ["api_key"]},
        {"claude_keys": ["env"]},
        {"topics": ["../outside"]},
        {"memory_repo": "~/old"},
        {"codex": "relative"},
        {"topics": []},
    ],
)
def test_device_rejects_unshared_legacy_or_unsafe_inputs(environment, change):
    source, file, raw = environment
    raw.update(change)
    with pytest.raises((ConfigError, ValueError)):
        configure(file, raw)


def test_state_cannot_overlap_assistant_root(environment):
    source, file, raw = environment
    raw["state_dir"] = str(Path(raw["codex"]) / "state")
    with pytest.raises(ConfigError):
        configure(file, raw)


def test_bad_markers_refused_and_secrets_not_echoed(environment, capsys):
    source, file, raw = environment
    device = load_device(file)
    target = device.roots["codex"] / "AGENTS.md"
    target.write_bytes(b"<!-- ai-config:begin -->broken")
    with pytest.raises(ConfigError):
        sync_plan(device, source)
    target.unlink()
    secret = "ghp_" + "Ab" * 20
    (source / "common/instructions.md").write_text(f"token: {secret}", encoding="utf-8")
    assert main(["--device", str(file), "sync", "--source", str(source)]) == 2
    assert secret not in capsys.readouterr().out


def test_portable_backup_and_restore_remap_roots_without_auth_reads(environment, monkeypatch):
    source, file, raw = environment
    device = load_device(file)
    apply_plan(device, sync_plan(device, source))
    (device.roots["codex"] / "auth.json").write_text("never read", encoding="utf-8")
    (device.roots["claude"] / "credentials.json").write_text("never read", encoding="utf-8")
    original_read = Path.read_bytes

    def guarded_read(path):
        if path.name in {"auth.json", "credentials.json"}:
            pytest.fail("Read authentication")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    output = file.parent / "portable.zip"
    assert save_bundle(device, output, apply=False) and not output.exists()
    save_bundle(device, output, apply=True)
    with zipfile.ZipFile(output) as archive:
        assert str(file.parent).encode() not in archive.read("manifest.json")
    roots = {agent: file.parent / f"second-{agent}" for agent in device.roots}
    for root in roots.values():
        root.mkdir()
    next_raw = {
        "state_dir": str(file.parent / "second-state"),
        **{k: str(v) for k, v in roots.items()},
    }
    next_file = file.parent / "second.json"
    other = configure(next_file, next_raw)
    apply_plan(other, restore_plan(other, output))
    assert (other.roots["codex"] / "AGENTS.md").read_bytes() == (
        device.roots["codex"] / "AGENTS.md"
    ).read_bytes()
    assert not (other.roots["codex"] / "auth.json").exists()
    assert not sync_plan(other, source)


def test_restore_conflicts_backups_and_undo_preserve_later_edits(environment):
    source, file, raw = environment
    device = load_device(file)
    apply_plan(device, sync_plan(device, source))
    output = file.parent / "backup.zip"
    save_bundle(device, output, apply=True)
    target = device.roots["codex"] / "AGENTS.md"
    target.write_bytes(b"original local rules")
    with pytest.raises(ConfigError, match="conflict"):
        restore_plan(device, output)
    operation = apply_plan(device, restore_plan(device, output, overwrite=True))
    plan = undo_plan(device, operation.name)
    apply_plan(device, plan)
    assert target.read_bytes() == b"original local rules"
    target.write_bytes(b"newer user edit")
    with pytest.raises(ConfigError, match="changed"):
        undo_plan(device, operation.name)


@pytest.mark.parametrize("damage", ["checksum", "traversal", "unknown", "credential"])
def test_zip_validation_rejects_bad_or_sensitive_payload(environment, damage):
    source, file, raw = environment
    device = load_device(file)
    apply_plan(device, sync_plan(device, source))
    output = file.parent / "good.zip"
    save_bundle(device, output, apply=True)
    with zipfile.ZipFile(output) as archive:
        entries = {n: archive.read(n) for n in archive.namelist()}
    if damage == "checksum":
        entries["rules/codex.md"] = b"tampered"
    elif damage == "traversal":
        entries["../outside.md"] = b"x"
    elif damage == "unknown":
        entries["auth.json"] = b"x"
    else:
        payload = b'{"api_key":"not-allowed"}'
        entries["settings/codex.json"] = payload
        manifest = json.loads(entries["manifest.json"])
        manifest["files"]["settings/codex.json"] = {"sha256": digest(payload), "size": len(payload)}
        entries["manifest.json"] = json.dumps(manifest).encode()
    invalid = file.parent / "bad.zip"
    with zipfile.ZipFile(invalid, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(ConfigError):
        load_bundle(invalid)


def test_backup_no_overwrite_or_export_into_assistant_root(environment):
    source, file, raw = environment
    device = load_device(file)
    apply_plan(device, sync_plan(device, source))
    output = file.parent / "one.zip"
    save_bundle(device, output, apply=True)
    before = output.read_bytes()
    with pytest.raises(ConfigError):
        save_bundle(device, output, apply=True)
    assert output.read_bytes() == before
    with pytest.raises(ConfigError):
        save_bundle(device, device.roots["codex"] / "backup.zip", apply=True)


def test_transaction_failure_rolls_back_originals(environment):
    source, file, raw = environment
    device = load_device(file)
    target = device.roots["codex"] / "AGENTS.md"
    target.write_bytes(b"original")
    plan = sync_plan(device, source)
    calls = 0

    def failing_writer(path, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic write error")
        write_file(path, data)

    with pytest.raises(OSError):
        transaction(plan, device.state / "backups", writer=failing_writer)
    assert target.read_bytes() == b"original"
    assert journals(device)[0]["status"] == "ROLLED_BACK"


def test_interrupted_transaction_recovers_and_retains_later_edits(environment):
    source, file, raw = environment
    device = load_device(file)
    target = device.roots["codex"] / "AGENTS.md"
    target.write_bytes(b"original")
    calls = 0

    def interrupted_writer(path, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        write_file(path, data)

    with pytest.raises(KeyboardInterrupt):
        transaction(sync_plan(device, source), device.state / "backups", writer=interrupted_writer)
    assert recover(device)[0]["status"] == "INTERRUPTED"
    assert recover(device, apply=True)[0]["status"] == "ROLLED_BACK"
    assert target.read_bytes() == b"original"


def test_lock_and_corrupted_backup_refuse_writes(environment):
    source, file, raw = environment
    device = load_device(file)
    with SyncLock(device.state):
        with pytest.raises(SyncBusyError):
            apply_plan(device, sync_plan(device, source))
    target = device.roots["codex"] / "AGENTS.md"
    target.write_bytes(b"original")
    operation = apply_plan(device, sync_plan(device, source))
    (operation / "0").write_bytes(b"corrupted backup")
    with pytest.raises(ConfigError, match="checksum"):
        undo_plan(device, operation.name)


def test_cli_outside_repository_and_default_preview(environment):
    source, file, raw = environment
    script = Path(__file__).resolve().parents[1] / "ai-config-sync/run.py"
    args = [
        sys.executable,
        "-B",
        str(script),
        "--device",
        str(file),
        "sync",
        "--source",
        str(source),
    ]
    before = contents(file.parent)
    proc = subprocess.run(args, cwd=file.parent, capture_output=True, check=True)
    assert json.loads(proc.stdout)["status"] == "preview" and contents(file.parent) == before
    proc = subprocess.run([*args, "--apply"], cwd=file.parent, capture_output=True, check=True)
    assert json.loads(proc.stdout)["status"] == "applied"
    assert (Path(raw["codex"]) / "AGENTS.md").is_file()


def test_bundled_rules_and_templates_work_with_isolated_device(environment):
    source, file, raw = environment
    source = Path(__file__).resolve().parents[1] / "ai-config-sync"
    raw.update(codex_keys=["model_reasoning_effort"], claude_keys=["autoMemoryEnabled"])
    device = configure(file, raw)
    apply_plan(device, sync_plan(device, source))
    assert not sync_plan(device, source)
    assert b"Developer Profile" in (device.roots["codex"] / "AGENTS.md").read_bytes()


def test_recovery_keeps_a_newer_user_edit(environment):
    source, file, raw = environment
    device = load_device(file)
    target = device.roots["codex"] / "AGENTS.md"
    target.write_bytes(b"original")
    calls = 0

    def interrupted_writer(path, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        write_file(path, data)

    with pytest.raises(KeyboardInterrupt):
        transaction(sync_plan(device, source), device.state / "backups", writer=interrupted_writer)
    target.write_bytes(b"newer user edit")
    result = recover(device, apply=True)
    assert result[0]["status"] == "ROLLBACK_REQUIRED"
    assert target.read_bytes() == b"newer user edit"


def test_journal_cannot_redirect_undo_outside_declared_targets(environment):
    source, file, raw = environment
    device = load_device(file)
    operation = apply_plan(device, sync_plan(device, source))
    journal_file = device.state / "transactions" / f"{operation.name}.json"
    value = json.loads(journal_file.read_text(encoding="utf-8"))
    value["changes"][0]["path"] = str(file.parent / "unrelated.txt")
    journal_file.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ConfigError, match="targets"):
        undo_plan(device, operation.name)


def test_rules_only_need_no_tomlkit(environment, monkeypatch):
    import builtins

    source, file, raw = environment
    device = load_device(file)
    original_import = builtins.__import__

    def without_tomlkit(name, *args, **kwargs):
        if name == "tomlkit":
            raise ImportError("synthetic missing optional dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_tomlkit)
    apply_plan(device, sync_plan(device, source))
    raw["codex_keys"] = ["model_reasoning_effort"]
    device = configure(file, raw)
    with pytest.raises(ConfigError, match="tomlkit"):
        sync_plan(device, source)
