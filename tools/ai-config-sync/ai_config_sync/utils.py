"""Small cross-platform helpers shared by the synchronization layers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Any


SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})|"
    r"AKIA[A-Z0-9]{16}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:Authorization)\s*:\s*(?i:Bearer)\s+[A-Za-z0-9._~+/-]{12,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"(?i:api[_ -]?key|access[_ -]?token|password|passwd|secret|密码|口令|密钥|令牌)"
    r"\s*[:=：]\s*(?:[\"'][^\"']{8,}[\"']|[A-Za-z0-9_~+/-]{8,})|"
    r"(?im:^[A-Z0-9_]*(?:PASSWORD|PASSWD|TOKEN|API_KEY)\s*=\s*\S+)"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(data: bytes | None) -> str | None:
    return sha256(data) if data is not None else None


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    """Write a file atomically on the same filesystem."""
    from .streaming import no_links

    no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def canonical_path(value: str) -> str:
    """Canonical comparison form for Windows/macOS file name checks."""
    return "/".join(nfc(part).casefold() for part in value.replace("\\", "/").split("/"))


def safe_relative(value: str) -> str:
    # Keep the source spelling; NFC is used for collision comparison, not for
    # an implicit rename on filesystems that preserve decomposed names.
    normalized = value.replace("\\", "/")
    path = Path(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or "\x00" in normalized:
        raise ValueError(f"Unsafe relative path: {value}")
    if any(part in {"", "."} for part in path.parts):
        raise ValueError(f"Unsafe relative path: {value}")
    return "/".join(path.parts)


def read_bytes(path: Path) -> bytes | None:
    from .streaming import no_links

    no_links(path)
    if path.is_symlink():
        raise ValueError(f"Symlink requires manual migration: {path}")
    return path.read_bytes() if path.exists() else None


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is not a POSIX-style probe on Windows: avoid any
        # signal/termination API when inspecting the owner of a sync lock.
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied or an unknown failure must not permit lock theft.
            return ctypes.get_last_error() != 87
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
