"""Read-before-open policy shared by file and Git entry points."""

from pathlib import Path, PurePosixPath

SAFE_DOT_DIRS = {".github"}
CREDENTIAL_NAMES = {
    "settings.local.json",
    "credentials.json",
    "auth.json",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "cookies.json",
    "cookies.sqlite",
    "login.json",
}


def credential_name(name: str) -> bool:
    path = PurePosixPath(name.lower())
    return (
        path.name == ".env"
        or path.name.startswith(".env.")
        or path.name in CREDENTIAL_NAMES
        or path.suffix in {".pem", ".p12", ".pfx", ".key"}
    )


def protected_path(name: str) -> bool:
    path = PurePosixPath(name.lower())
    return credential_name(name) or any(
        part.startswith(".") and part not in SAFE_DOT_DIRS for part in path.parts[:-1]
    )


def protected_location(path: Path, directory: bool = False) -> bool:
    parts = path.parts if directory else path.parts[:-1]
    return (not directory and credential_name(path.name)) or any(
        part.startswith(".") and part.lower() not in SAFE_DOT_DIRS for part in parts
    )


def check_root(path: Path) -> Path:
    from .model import GuardError

    if protected_location(path.absolute(), directory=True):
        raise GuardError("Protected review root; contents were not read")
    resolved = path.resolve(strict=True)
    if protected_location(resolved, directory=True):
        raise GuardError("Protected review root; contents were not read")
    if not resolved.is_dir():
        raise GuardError("Review root must be a directory")
    return resolved
