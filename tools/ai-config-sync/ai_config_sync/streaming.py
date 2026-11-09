"""Bounded file descriptors for large, journaled local writes."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Callable, Iterator
import uuid

CHUNK = 1024**2


def no_links(path: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError("linked streaming file boundary")


def file_digest(path: Path) -> str | None:
    no_links(path)
    if not path.exists():
        return None
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("not a regular streaming file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    no_links(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise ValueError("streaming file changed during read")
    return digest.hexdigest()


@dataclass(frozen=True)
class StreamContent:
    chunks: Callable[[], Iterator[bytes]]
    sha256: str
    size: int
    new_only: bool = True

    def __post_init__(self):
        if (
            not callable(self.chunks)
            or not isinstance(self.sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
            or type(self.size) is not int
            or self.size < 0
            or type(self.new_only) is not bool
        ):
            raise ValueError("invalid streaming descriptor")

    @classmethod
    def from_path(cls, path: Path, sha256: str, size: int, *, new_only: bool = True):
        def chunks():
            no_links(path)
            with path.open("rb") as handle:
                while chunk := handle.read(CHUNK):
                    yield chunk

        return cls(chunks, sha256, size, new_only)


def content_digest(data: bytes | None | StreamContent) -> str | None:
    if isinstance(data, StreamContent):
        return data.sha256
    return hashlib.sha256(data).hexdigest() if data is not None else None


def write_stream(path: Path, data: StreamContent) -> None:
    no_links(path)
    if data.new_only and path.exists():
        raise ValueError("stream target already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    no_links(path)
    partial = path.with_name(".stream-" + uuid.uuid4().hex)
    created = False
    target_identity = None
    try:
        with partial.open("xb") as handle:
            created = True
            size = 0
            digest = hashlib.sha256()
            chunks = data.chunks()
            try:
                for chunk in chunks:
                    if not isinstance(chunk, bytes) or len(chunk) > CHUNK:
                        raise ValueError("invalid streaming chunk")
                    size += len(chunk)
                    digest.update(chunk)
                    if size > data.size:
                        raise ValueError("stream exceeds expected size")
                    handle.write(chunk)
            finally:
                if hasattr(chunks, "close"):
                    chunks.close()
            if size != data.size or digest.hexdigest() != data.sha256:
                raise ValueError("stream content changed")
            handle.flush()
            os.fsync(handle.fileno())
        no_links(path)
        if not data.new_only:
            os.replace(partial, path)
        else:
            try:
                os.link(partial, path)
            except OSError as error:
                if error.errno not in {
                    errno.EPERM,
                    errno.EACCES,
                    errno.EXDEV,
                    errno.EINVAL,
                    errno.ENOTSUP,
                }:
                    raise
                with path.open("xb") as output:
                    st = os.fstat(output.fileno())
                    target_identity = (st.st_dev, st.st_ino)
                    with partial.open("rb") as input:
                        while chunk := input.read(CHUNK):
                            output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
    except BaseException:
        if target_identity is not None and path.exists() and not path.is_symlink():
            st = path.stat()
            if (st.st_dev, st.st_ino) == target_identity:
                path.unlink()
        raise
    finally:
        if created:
            partial.unlink(missing_ok=True)
