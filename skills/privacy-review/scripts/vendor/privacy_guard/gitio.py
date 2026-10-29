from pathlib import Path
import re
import subprocess

from .model import Blob, GuardError
from .paths import check_root, protected_location, protected_path

MAX_BYTES = 4 * 1024 * 1024
MAX_BLOBS = 5000


def git(repo: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GuardError("Git could not run or timed out") from exc
    if proc.returncode:
        raise GuardError("Git operation failed; check repository and revision locally")
    return proc.stdout


def root(repo: Path) -> Path:
    repo = check_root(repo)
    return check_root(Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()))


def resolve(repo: Path, ref: str) -> str:
    return validate_oid(
        git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    )


def validate_oid(value: str) -> str:
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value):
        raise GuardError("Invalid Git object identifier; details suppressed")
    return value


def read_blob(repo: Path, oid: str) -> bytes:
    size = int(git(repo, "cat-file", "-s", oid))
    if size > MAX_BYTES:
        raise GuardError("Blob exceeds scan limit; review required")
    return git(repo, "cat-file", "blob", oid)


def entries(data: bytes) -> dict[str, tuple[str, str]]:
    result = {}
    for entry in data.split(b"\0"):
        if not entry:
            continue
        header, name = entry.split(b"\t", 1)
        mode, oid, stage = header.decode().split()
        if stage != "0":
            raise GuardError("Unmerged index is not supported")
        result[name.decode("utf-8", "surrogateescape")] = (mode, oid)
    return result


def staged(repo: Path, all_tracked: bool = False) -> list[Blob]:
    repo = root(repo)
    index = entries(git(repo, "ls-files", "--stage", "-z"))
    names = {
        p.decode("utf-8", "surrogateescape")
        for p in git(
            repo,
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-textconv",
            "--name-only",
            "--no-renames",
            "-z",
        ).split(b"\0")
        if p
    }
    work_names = set()
    if all_tracked:
        work_names = {
            p.decode("utf-8", "surrogateescape")
            for p in git(
                repo, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "--no-renames", "-z"
            ).split(b"\0")
            if p
        }
        names |= work_names
    if len(names) > MAX_BLOBS:
        raise GuardError("Too many changed files; review required")
    blobs = []
    for name in sorted(names):
        if name not in index:
            continue  # deleted from index
        mode, oid = index[name]
        if mode == "160000":
            raise GuardError("Gitlink changed; scan child repository separately")
        if protected_path(name):
            blobs.append(Blob(name, b"", mode))
            continue  # path-only advice: never open credentials or Agent runtime contents
        if name in work_names:
            path = repo / name
            if not path.resolve().is_relative_to(repo.resolve()):
                raise GuardError("Worktree path escapes repository")
            if path.is_symlink():
                raise GuardError("Symlink in commit -a requires review")
            if protected_location(path.resolve()):
                raise GuardError("Protected worktree target; contents were not read")
            if not path.exists():
                continue  # tracked deletion with commit -a
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                raise GuardError("Worktree file cannot be scanned safely")
            content = path.read_bytes()
        else:
            content = read_blob(repo, oid)
        blobs.append(Blob(name, content, mode))
    return blobs


def history(repo: Path, tip: str = "HEAD", base: str | None = None) -> list[Blob]:
    repo = root(repo)
    tip_oid = resolve(repo, tip)
    args = ["rev-list", tip_oid]
    if base:
        args.append("^" + resolve(repo, base))
    commits = [validate_oid(value) for value in git(repo, *args).decode().splitlines()]
    if len(commits) > MAX_BLOBS:
        raise GuardError("History exceeds scan limit; review required")
    blobs = []
    seen = set()
    for commit in commits:
        message = git(
            repo, "show", "--no-ext-diff", "--no-textconv", "--no-patch", "--format=%B", commit
        )
        if len(message) > MAX_BYTES:
            raise GuardError("Commit message exceeds scan limit")
        blobs.append(Blob("commit-message.txt", message, commit=commit))
        names = set(
            git(
                repo,
                "diff-tree",
                "--root",
                "-m",
                "--no-commit-id",
                "-r",
                "--name-only",
                "--no-renames",
                "-z",
                commit,
            ).split(b"\0")
        )
        for entry in git(repo, "ls-tree", "-r", "-z", commit).split(b"\0"):
            if not entry:
                continue
            header, name = entry.split(b"\t", 1)
            if name not in names:
                continue
            mode, kind, oid = header.decode().split()
            if kind != "blob":
                raise GuardError("Gitlink in push history requires child repository scan")
            if (name, oid) in seen:
                continue
            seen.add((name, oid))
            path = name.decode("utf-8", "surrogateescape")
            content = b"" if protected_path(path) else read_blob(repo, oid)
            blobs.append(Blob(path, content, mode, commit))
            if len(blobs) > MAX_BLOBS:
                raise GuardError("History exceeds blob limit; review required")
    return blobs
