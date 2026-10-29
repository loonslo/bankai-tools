"""Small, portable ZIPs with an exact file allowlist and no source paths."""

from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

from .core import (
    ConfigError,
    Device,
    MAX_BYTES,
    RULES,
    SETTINGS,
    block,
    check_text,
    parse_settings,
    read,
    settings_result,
    shared_values,
)
from .streaming import file_digest, no_links
from .transaction import PlannedChanges
from .utils import digest, json_bytes

NAMES = {
    f"{kind}/{agent}.{extension}"
    for agent in RULES
    for kind, extension in (("rules", "md"), ("settings", "json"))
}


def capture(device: Device) -> tuple[dict[str, bytes], dict[Path, str | None]]:
    files = {}
    expected = {}
    for agent, root in device.roots.items():
        target = root / RULES[agent]
        data = read(target)
        expected[target] = digest(data)
        if data is not None:
            check_text(data)
            block(data)
            files[f"rules/{agent}.md"] = data
        if not device.keys[agent]:
            continue
        target = root / SETTINGS[agent]
        data = read(target)
        expected[target] = digest(data)
        values = parse_settings(agent, data)
        selected = {k: values[k] for k in device.keys[agent] if k in values}
        files[f"settings/{agent}.json"] = json_bytes(shared_values(agent, selected))
    if not files:
        raise ConfigError("No selected configuration files to back up")
    return files, expected


def save_bundle(device: Device, output: Path, *, apply: bool) -> list[str]:
    no_links(output)
    resolved = output.absolute().resolve()
    if any(resolved.is_relative_to(root) for root in (device.state, *device.roots.values())):
        raise ConfigError("Store portable backups outside assistant and local state directories")
    if output.exists():
        raise ConfigError("Output already exists; choose a new backup file")
    files, expected = capture(device)
    manifest = {
        "schema_version": 1,
        "files": {
            name: {"sha256": digest(data), "size": len(data)} for name, data in files.items()
        },
    }
    if not apply:
        return sorted(files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json_bytes(manifest))
        for name, data in files.items():
            archive.writestr(name, data)
    # Never publish a backup whose selected sources changed during collection.
    if any(digest(read(p)) != expected_hash for p, expected_hash in expected.items()):
        raise ConfigError("Backup sources changed; retry collection")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(buffer.getvalue())
    return sorted(files)


def load_bundle(file: Path) -> dict[str, bytes]:
    no_links(file)
    if file.stat().st_size > 5 * MAX_BYTES:
        raise ConfigError("Backup exceeds size limit")
    with zipfile.ZipFile(file) as archive:
        infos = archive.infolist()
        names = [entry.filename for entry in infos]
        if (
            len(names) != len(set(names))
            or "manifest.json" not in names
            or set(names) - (NAMES | {"manifest.json"})
            or any(entry.file_size > MAX_BYTES or entry.flag_bits & 1 for entry in infos)
        ):
            raise ConfigError("Unsupported or unsafe backup layout")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"schema_version", "files"}
            or manifest["schema_version"] != 1
            or not isinstance(manifest["files"], dict)
            or set(manifest["files"]) != set(names) - {"manifest.json"}
            or len(names) == 1
        ):
            raise ConfigError("Invalid backup manifest")
        result = {}
        for name, record in manifest["files"].items():
            data = archive.read(name)
            if record != {"sha256": digest(data), "size": len(data)}:
                raise ConfigError("Backup checksum mismatch")
            check_text(data)
            if name.startswith("settings/"):
                agent = Path(name).stem
                shared_values(agent, json.loads(data))
            else:
                block(data)
            result[name] = data
        return result


def restore_plan(device: Device, file: Path, *, overwrite: bool = False) -> PlannedChanges:
    expected = {file: file_digest(file)}
    files = load_bundle(file)
    baseline_data = read(device.baseline)
    updated = json.loads(baseline_data or b"{}")
    if not isinstance(updated, dict):
        raise ConfigError("Invalid baseline")
    expected[device.baseline] = digest(baseline_data)
    changes = {}
    for agent, root in device.roots.items():
        data = files.get(f"rules/{agent}.md")
        if data is not None:
            target = root / RULES[agent]
            current = read(target)
            if current is not None and current != data and not overwrite:
                raise ConfigError("Rule restore conflict; explicitly choose overwrite after review")
            expected[target] = digest(current)
            if current != data:
                changes[target] = data
            updated[f"rules:{agent}"] = digest(block(data))
        data = files.get(f"settings/{agent}.json")
        if data is None:
            continue
        desired = {k: v for k, v in json.loads(data).items() if k in device.keys[agent]}
        if not desired:
            continue
        target = root / SETTINGS[agent]
        current = read(target)
        values = parse_settings(agent, current)
        if any(k in values and values[k] != v for k, v in desired.items()) and not overwrite:
            raise ConfigError("Setting restore conflict; explicitly choose overwrite after review")
        expected[target] = digest(current)
        if any(values.get(k) != v for k, v in desired.items()):
            changes[target] = settings_result(agent, current, desired)
        updated[f"settings:{agent}"] = desired
    if json_bytes(updated) != baseline_data:
        changes[device.baseline] = json_bytes(updated)
    return PlannedChanges(
        changes, expected=expected, state_root=device.state, metadata={"operation": "restore"}
    )
