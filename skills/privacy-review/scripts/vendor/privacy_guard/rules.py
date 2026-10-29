from datetime import date
from pathlib import PurePosixPath
import re

from .model import Blob, Finding, finding
from .paths import SAFE_DOT_DIRS, credential_name

PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
PHONE_CONTEXT = re.compile(r"手机|电话|微信|联系电话|telephone|phone|mobile", re.I)
ID_CARD = re.compile(r"(?<!\d)[1-9]\d{16}[\dXx](?!\w)")
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w.-])")
LOCAL_PATH = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\[^\s\\/]+[\\/]|"
    r"/(?:home|Users|root|mnt|media|Volumes|workspace|tmp|var|opt|etc)/|~/)"
    r"[^\s\"'<>|`]+"
)
MEMORY_NAME = re.compile(r"(?:^|[_.-])(?:memory|memories)(?:[_.-]|$)", re.I)
CHAT_NAME = re.compile(
    r"(?:^|[_.-])(?:chat|conversation|conversations|history|session|sessions)(?:[_.-]|$)", re.I
)
CHAT_ROLE = re.compile(r'"role"\s*:\s*"(?:user|assistant|system|tool)"')


def valid_id(value: str) -> bool:
    try:
        birth = date(int(value[6:10]), int(value[10:12]), int(value[12:14]))
    except ValueError:
        return False
    if not date(1900, 1, 1) <= birth <= date.today():
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    return (
        "10X98765432"[sum(int(x) * w for x, w in zip(value[:17], weights)) % 11]
        == value[-1].upper()
    )


def inspect(blob: Blob) -> list[Finding]:
    hits = []
    path = PurePosixPath(blob.path)
    if any(p.startswith(".") and p not in SAFE_DOT_DIRS for p in path.parts[:-1]):
        hits.append(finding(blob, "hidden-directory", "high", 0, blob.path.encode()))
    if credential_name(blob.path):
        hits.append(finding(blob, "credential-path", "critical", 0, blob.path.encode()))
    if blob.source == "memory" or MEMORY_NAME.search(path.name):
        hits.append(finding(blob, "agent-memory", "high", 0, blob.path.encode()))
    if blob.source == "chat_history" or CHAT_NAME.search(path.name):
        hits.append(finding(blob, "chat-history", "high", 0, blob.path.encode()))
    if blob.mode == "120000":
        hits.append(finding(blob, "symlink", "high", 0, blob.content))
    if b"\0" in blob.content or blob.content.startswith(
        b"version https://git-lfs.github.com/spec/v1"
    ):
        hits.append(finding(blob, "unsupported-content", "high", 0, blob.content))
        return hits
    try:
        text = blob.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        hits.append(finding(blob, "unsupported-encoding", "high", 0, blob.content))
        return hits
    if CHAT_ROLE.search(text) and not any(hit.rule == "chat-history" for hit in hits):
        hits.append(finding(blob, "chat-history", "high", 0, blob.content))
    for number, line in enumerate(text.splitlines(), 1):
        for match in LOCAL_PATH.finditer(line):
            hits.append(finding(blob, "local-path", "high", number, match[0].encode()))
        for match in PHONE.finditer(line):
            if PHONE_CONTEXT.search(line):
                hits.append(finding(blob, "phone-context", "high", number, match[0].encode()))
        for match in ID_CARD.finditer(line):
            if valid_id(match[0]):
                hits.append(finding(blob, "id-checksum", "high", number, match[0].encode()))
        for match in EMAIL.finditer(line):
            value = match[0]
            domain = value.rsplit("@", 1)[1].lower()
            if domain in {"example.com", "example.org", "example.net"} or value.lower().endswith(
                ".png"
            ):
                continue
            if value == "git@github.com" or domain.endswith(".noreply.github.com"):
                continue
            hits.append(finding(blob, "email", "medium", number, value.encode()))
    return hits
