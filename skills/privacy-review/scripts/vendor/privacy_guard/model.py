from dataclasses import dataclass, field
import hashlib


class GuardError(Exception):
    """Messages must be static and must never include inspected content or commands."""


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class Blob:
    path: str
    content: bytes
    mode: str = "100644"
    commit: str = ""
    source: str = "auto"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    file_ref: str
    line: int
    fingerprint: str
    commit: str = ""
    masked: str = "[REDACTED]"


def finding(blob: Blob, rule: str, severity: str, line: int, value: bytes) -> Finding:
    return Finding(
        rule,
        severity,
        digest(blob.path.encode("utf-8", "surrogateescape")),
        line,
        digest(rule.encode() + b"\0" + value),
        blob.commit,
    )


@dataclass
class Result:
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scanned: int = 0
