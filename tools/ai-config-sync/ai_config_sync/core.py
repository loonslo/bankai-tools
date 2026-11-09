"""Small planners over explicit rule files and allowlisted scalar settings."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tomllib
from typing import Any

from .streaming import no_links
from .transaction import PlannedChanges, transaction
from .utils import SECRET, digest, json_bytes

TOPICS = ("instructions", "principles", "engineering", "python", "langgraph", "rag", "security")
KEYS = {
    "codex": {
        "approval_policy": str,
        "sandbox_mode": str,
        "web_search": str,
        "model_reasoning_effort": str,
        "project_doc_max_bytes": int,
    },
    "claude": {"autoMemoryEnabled": bool},
}
RULES = {"codex": "AGENTS.md", "claude": "CLAUDE.md"}
SETTINGS = {"codex": "config.toml", "claude": "settings.json"}
BEGIN = b"<!-- ai-config:begin -->"
END = b"<!-- ai-config:end -->"
MAX_BYTES = 2 * 1024 * 1024


class ConfigError(ValueError):
    """Static error safe for display without source content."""


def path(value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigError("Invalid path")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise ConfigError("Use absolute paths or ~/ paths")
    expanded = expanded.absolute()
    no_links(expanded)
    return expanded.resolve()


def read(file: Path) -> bytes | None:
    no_links(file)
    if not file.exists():
        return None
    if not file.is_file() or file.stat().st_size > MAX_BYTES:
        raise ConfigError("Unsupported file or size limit exceeded")
    return file.read_bytes()


def check_text(data: bytes) -> None:
    text = data.decode("utf-8-sig")
    if "\x00" in text or SECRET.search(text):
        raise ConfigError("Unsupported text or suspected credential; content suppressed")


@dataclass(frozen=True)
class Device:
    state: Path
    roots: dict[str, Path]
    keys: dict[str, tuple[str, ...]]
    topics: tuple[str, ...] = TOPICS

    @property
    def baseline(self) -> Path:
        return self.state / "baseline.json"

    def allowed_targets(self) -> set[Path]:
        return {self.baseline} | {
            root / name
            for agent, root in self.roots.items()
            for name in (RULES[agent], SETTINGS[agent])
        }


def load_device(file: Path) -> Device:
    raw = json.loads(read(file) or b"null")
    allowed = {"state_dir", "codex", "claude", "codex_keys", "claude_keys", "topics"}
    if not isinstance(raw, dict) or set(raw) - allowed or "state_dir" not in raw:
        raise ConfigError("Invalid device file; legacy migration configuration is unsupported")
    roots = {agent: path(raw[agent]) for agent in RULES if agent in raw}
    if not roots or any(not root.is_dir() for root in roots.values()):
        raise ConfigError("Explicit existing assistant roots are required")
    state = path(raw["state_dir"])
    boundaries = [state, *roots.values()]
    if any(
        a.is_relative_to(b) or b.is_relative_to(a)
        for i, a in enumerate(boundaries)
        for b in boundaries[i + 1 :]
    ):
        raise ConfigError("State and assistant roots must not overlap")
    selected = {}
    for agent in RULES:
        names = raw.get(f"{agent}_keys", [])
        if (
            not isinstance(names, list)
            or not all(isinstance(k, str) for k in names)
            or len(names) != len(set(names))
            or set(names) - KEYS[agent].keys()
            or (names and agent not in roots)
        ):
            raise ConfigError("Only declared shared settings may be selected")
        selected[agent] = tuple(names)
    topics = raw.get("topics", list(TOPICS))
    if (
        not isinstance(topics, list)
        or not topics
        or not all(isinstance(t, str) for t in topics)
        or len(topics) != len(set(topics))
        or set(topics) - set(TOPICS)
    ):
        raise ConfigError("Invalid rule topics")
    return Device(state, roots, selected, tuple(topics))


def shared_values(agent: str, values: Any) -> dict[str, Any]:
    if not isinstance(values, dict) or set(values) - KEYS[agent].keys():
        raise ConfigError("Unsupported shared settings")
    if any(type(v) is not KEYS[agent][k] for k, v in values.items()):
        raise ConfigError("Invalid shared setting value")
    check_text(json_bytes(values))
    return values


def parse_settings(agent: str, data: bytes | None) -> dict[str, Any]:
    result = (
        tomllib.loads((data or b"").decode("utf-8-sig"))
        if agent == "codex"
        else json.loads(data or b"{}")
    )
    if not isinstance(result, dict):
        raise ConfigError("Settings must be an object")
    return result


def settings_result(agent: str, data: bytes | None, desired: dict[str, Any]) -> bytes:
    shared_values(agent, desired)
    if agent == "codex":
        try:
            import tomlkit
        except ImportError:
            raise ConfigError(
                "Selected Codex settings require tomlkit; install requirements.txt"
            ) from None
        document = tomlkit.parse((data or b"").decode("utf-8-sig"))
        for key, value in desired.items():
            document[key] = value
        return tomlkit.dumps(document).encode("utf-8")
    document = parse_settings(agent, data)
    document.update(desired)
    return json_bytes(document)


def block(data: bytes) -> bytes | None:
    if BEGIN not in data and END not in data:
        return None
    if data.count(BEGIN) != 1 or data.count(END) != 1 or data.index(BEGIN) > data.index(END):
        raise ConfigError("Malformed managed rules markers; manual review required")
    return data[data.index(BEGIN) : data.index(END) + len(END)]


def render(source: Path, topics: tuple[str, ...]) -> tuple[bytes, dict[Path, str | None]]:
    content = []
    expected = {}
    for topic in topics:
        file = source / "common" / f"{topic}.md"
        data = read(file)
        if data is None:
            raise ConfigError("Missing selected rule topic")
        check_text(data)
        expected[file] = digest(data)
        content.append(data.decode("utf-8-sig").rstrip())
    text = "\n\n".join(content)
    version = digest(text.encode())[:8]
    heading = f"Generated by ai-config-sync; edit common/ in the source.\nai-config 规则版本：{version}\n\n"
    return BEGIN + b"\n" + (heading + text + "\n").encode("utf-8") + END, expected


def baseline(device: Device) -> dict[str, Any]:
    result = json.loads(read(device.baseline) or b"{}")
    if not isinstance(result, dict):
        raise ConfigError("Invalid baseline")
    return result


def sync_plan(device: Device, source: Path, *, overwrite: bool = False) -> PlannedChanges:
    body, expected = render(source, device.topics)
    baseline_data = read(device.baseline)
    prior = json.loads(baseline_data or b"{}")
    if not isinstance(prior, dict):
        raise ConfigError("Invalid baseline")
    expected[device.baseline] = digest(baseline_data)
    updated = dict(prior)
    changes = {}
    for agent, root in device.roots.items():
        target = root / RULES[agent]
        current = read(target)
        existing = current or b""
        old_block = block(existing)
        label = f"rules:{agent}"
        if (
            old_block is not None
            and old_block != body
            and digest(old_block) != prior.get(label)
            and not overwrite
        ):
            raise ConfigError(
                "Managed rules changed locally; review or explicitly overwrite conflicts"
            )
        desired = (
            existing.replace(old_block, body, 1)
            if old_block is not None
            else existing + (b"\n\n" if existing else b"") + body + b"\n"
        )
        expected[target] = digest(current)
        if desired != current:
            changes[target] = desired
        updated[label] = digest(body)
        names = device.keys[agent]
        if not names:
            continue
        template = source / agent / ("config.toml" if agent == "codex" else "settings.shared.json")
        raw = read(template)
        if raw is None:
            raise ConfigError("Missing settings template")
        defaults = parse_settings(agent, raw)
        if set(names) - defaults.keys():
            raise ConfigError("Selected setting missing from template")
        desired_values = shared_values(agent, {key: defaults[key] for key in names})
        expected[template] = digest(raw)
        target = root / SETTINGS[agent]
        current = read(target)
        values = parse_settings(agent, current)
        label = f"settings:{agent}"
        known = prior.get(label, {})
        for key, value in desired_values.items():
            if (
                key in values
                and values[key] != value
                and values[key] != known.get(key)
                and not overwrite
            ):
                raise ConfigError(
                    "Shared setting conflict; review or explicitly overwrite conflicts"
                )
        expected[target] = digest(current)
        if any(values.get(k) != v for k, v in desired_values.items()):
            changes[target] = settings_result(agent, current, desired_values)
        updated[label] = desired_values
    if json_bytes(updated) != baseline_data:
        changes[device.baseline] = json_bytes(updated)
    return PlannedChanges(
        changes, expected=expected, state_root=device.state, metadata={"operation": "sync"}
    )


def apply_plan(device: Device, plan: PlannedChanges) -> Path | None:
    if not set(plan) <= device.allowed_targets():
        raise ConfigError("Plan contains undeclared targets")
    for target in [device.state, *plan, *plan.expected]:
        no_links(target)
    return transaction(plan, device.state / "backups") if plan else None
