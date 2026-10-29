"""Read-only product API: recommendations for the human, no execution decisions."""

from dataclasses import asdict
import json
from pathlib import Path, PurePosixPath
from typing import TypedDict

from .gitio import (
    MAX_BLOBS,
    MAX_BYTES,
    git,
    history,
    resolve,
    root as git_root,
    staged,
    validate_oid,
)
from .model import Blob, GuardError, Result, digest
from .paths import check_root, protected_location, protected_path
from .scanner import scan

GUIDANCE = {
    "agent-memory": "疑似 Agent 记忆导出；建议移出分享范围，或由用户选出必要信息并去除私有事实。",
    "chat-history": "疑似聊天记录导出；建议核对所有消息及会话元数据，删除不必要的历史与个人信息。",
    "local-path": "建议将本机路径改为 <LOCAL_PATH> 或可共享的相对路径；本工具未修改原文。",
    "hidden-directory": "建议检查是否误选了 Agent 或运行目录；由用户决定是否将其移出分享范围。",
    "credential-path": "建议不要分享凭据文件，仅保留变量名或脱敏示例；本次未读取该文件内容。",
    "phone-context": "建议确认联系方式是否适合外发，必要时由用户安排删除或脱敏。",
    "id-checksum": "建议核对身份证信息的外发必要性，由用户决定是否脱敏。",
    "email": "建议核对邮箱是否为个人信息，以及是否需要保留在外发材料中。",
    "symlink": "符号链接未检查指向内容，建议用户明确实际分享对象后另行审查。",
    "unsupported-content": "内容类型暂不支持检查，建议用户使用合适工具补充审查。",
    "unsupported-encoding": "编码暂不支持检查，建议用户提供 UTF-8 副本后复查。",
}


class ReviewReport(TypedDict):
    schema_version: int
    mode: str
    review_complete: bool
    risk_level: str
    scanned: int
    findings: list[dict[str, str | int]]
    errors: list[str]
    next_step: str
    semantic_status: str
    scope: list[dict[str, str]]


def report(result: Result) -> ReviewReport:
    items = []
    for hit in result.findings:
        item = asdict(hit)
        item["suggestion"] = (
            "疑似密钥：建议将代码中的硬编码值改为环境变量读取，文档中的值改为占位符；"
            "若曾泄露，由用户自行决定是否撤销或轮换。本工具不会执行这些修改。"
            if hit.rule.startswith("secret:")
            else GUIDANCE.get(hit.rule, "建议用户核实此风险。")
        )
        items.append(item)
    incomplete = bool(result.errors) or any(
        hit.rule
        in {
            "unsupported-content",
            "unsupported-encoding",
            "symlink",
            "credential-path",
            "hidden-directory",
        }
        for hit in result.findings
    )
    return {
        "schema_version": 3,
        "mode": "advisory",
        "review_complete": not incomplete,
        "risk_level": max(
            (f.severity for f in result.findings),
            default="none",
            key=lambda v: {"none": 0, "medium": 1, "high": 2, "critical": 3}[v],
        ),
        "scanned": result.scanned,
        "findings": items,
        "errors": result.errors,
        "next_step": "请将建议交给用户。用户自行决定是否另行让 AI 修改；本次只检查并返回建议。",
        "semantic_status": "disabled",
        "scope": [],
    }


def reviewed(blobs: list[Blob]) -> ReviewReport:
    return report(scan(blobs))


def review_files(repo: str, paths: list[str], source: str = "auto") -> ReviewReport:
    """Inspect only explicit relative files. No recursion, output files, or target writes."""
    try:
        if not paths or len(paths) > MAX_BLOBS:
            raise GuardError("Provide a bounded, nonempty list of explicit files")
        if source not in {"auto", "memory", "chat_history"}:
            raise GuardError("Invalid source type")
        root = check_root(Path(repo))
        blobs = []
        for name in paths:
            p = PurePosixPath(name)
            if p.is_absolute() or "\\" in name or ":" in name or ".." in p.parts:
                raise GuardError("Files must be relative paths inside the explicit review root")
            if protected_path(name):
                blobs.append(Blob(name, b"", source=source))
                continue
            target = root / name
            if target.is_symlink():
                blobs.append(Blob(name, b"", mode="120000"))
                continue
            resolved = target.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise GuardError("File escapes review root or is not a regular file")
            if protected_location(resolved):
                raise GuardError("Protected review target; contents were not read")
            if resolved.stat().st_size > MAX_BYTES:
                raise GuardError("File exceeds review limit; review incomplete")
            blobs.append(Blob(name, resolved.read_bytes(), source=source))
        return reviewed(blobs)
    except Exception as exc:
        return error_report(exc)


def review_staged(repo: str, include_worktree: bool = False) -> ReviewReport:
    try:
        return reviewed(staged(Path(repo), include_worktree))
    except Exception as exc:
        return error_report(exc)


def review_history(repo: str, base: str | None = None, tip: str = "HEAD") -> ReviewReport:
    try:
        return review_push_plan(repo, [{"tip": tip, "base": base}])
    except Exception as exc:
        return error_report(exc)


class PushRange(TypedDict):
    tip: str
    base: str | None


def review_push_plan(repo: str, ranges: list[PushRange]) -> ReviewReport:
    """Review explicit local commit ranges; no remote access or actual push."""
    try:
        if not ranges or len(ranges) > 64:
            raise GuardError("Provide a bounded nonempty push plan")
        root = git_root(Path(repo))
        scope = []
        blobs = []
        for item in ranges:
            if set(item) != {"tip", "base"} or not isinstance(item["tip"], str):
                raise GuardError("Invalid push range")
            if item["base"] is not None and not isinstance(item["base"], str):
                raise GuardError("Invalid push base")
            object_id = validate_oid(
                git(root, "rev-parse", "--verify", "--end-of-options", item["tip"]).decode().strip()
            )
            if git(root, "cat-file", "-t", object_id).strip() != b"commit":
                raise GuardError("Non-commit push tip unsupported; tag payload was not reviewed")
            tip = resolve(root, item["tip"])
            base = resolve(root, item["base"]) if item["base"] else None
            scope.append(
                {
                    "range_ref": digest(json.dumps(item, sort_keys=True).encode()),
                    "tip_commit": tip,
                    "base_commit": base or "",
                    "coverage": "base-to-tip" if base else "all-tip-history",
                }
            )
            blobs.extend(history(root, tip, base))
            if len(blobs) > MAX_BLOBS:
                raise GuardError("Push plan exceeds blob limit")
        output = reviewed(blobs)
        output["scope"] = scope
        return output
    except Exception as exc:
        return error_report(exc)


def error_report(exc: Exception) -> ReviewReport:
    message = str(exc) if isinstance(exc, GuardError) else "Review incomplete; details suppressed"
    return report(Result(errors=[message]))
