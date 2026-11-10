"""Cooperative filesystem work with thread-local execution and cancellation."""

import hashlib
import os
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .audit import AuditError

CHUNK_SIZE = 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024 * 1024
_local = threading.local()


class TaskCancelled(AuditError):
    pass


class MetadataTooLargeError(AuditError):
    pass


class TaskControl:
    def __init__(self, progress=None, state_changed=None):
        self.progress = progress
        self.state_changed = state_changed
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._protected = False
        self._critical_depth = 0
        self.phase = ""
        self.completed = 0
        self.total = 0
        self._last_report = 0.0

    @property
    def can_cancel(self):
        with self._lock:
            return not self._protected and self._critical_depth == 0 and not self._cancelled.is_set()

    @property
    def cancel_requested(self):
        return self._cancelled.is_set()

    def cancel(self) -> bool:
        with self._lock:
            if self._protected or self._critical_depth:
                return False
            self._cancelled.set()
        if self.state_changed:
            self.state_changed()
        return True

    def checkpoint(self):
        with self._lock:
            if self._cancelled.is_set() and not self._protected and not self._critical_depth:
                raise TaskCancelled("Operation cancelled before commit.")

    def protect(self, check_cancel: bool = True):
        with self._lock:
            if check_cancel and self._cancelled.is_set():
                raise TaskCancelled("Operation cancelled before commit.")
            self._protected = True
        if self.state_changed:
            self.state_changed()

    def start(self, phase: str, total: int = 0):
        self.checkpoint()
        self.phase, self.completed, self.total = phase, 0, total
        self._report(True)

    @contextmanager
    def critical_section(self, check_cancel: bool = True):
        with self._lock:
            if check_cancel and self._cancelled.is_set() and not self._protected and not self._critical_depth:
                raise TaskCancelled("Operation cancelled before commit.")
            self._critical_depth += 1
        if self.state_changed:
            self.state_changed()
        try:
            yield
        finally:
            with self._lock:
                self._critical_depth -= 1
            if self.state_changed:
                self.state_changed()

    def advance(self, amount: int):
        first_chunk = self.completed == 0
        self.completed += amount
        self._report(first_chunk or self.total > 0 and self.completed >= self.total)

    def _report(self, force: bool = False):
        now = time.monotonic()
        if self.progress and (force or now - self._last_report >= 0.05):
            self._last_report = now
            self.progress(self.phase, self.completed, self.total)


@contextmanager
def processing_context(control: TaskControl, executor=None):
    previous = getattr(_local, "context", None)
    _local.context = (control, executor)
    try:
        yield control
    finally:
        _local.context = previous


def current_control():
    context = getattr(_local, "context", None)
    return context[0] if context else None


def run_background(operation, phase: str):
    context = getattr(_local, "context", None)
    if context is None:
        return operation()
    control, executor = context
    control.checkpoint()
    if executor is not None:
        return executor(operation, phase)
    control.start(phase)
    return operation()


def protect_commit(check_cancel: bool = True):
    control = current_control()
    if control is not None:
        control.protect(check_cancel)


@contextmanager
def critical_section(check_cancel: bool = True):
    control = current_control()
    if control is None:
        yield
    else:
        with control.critical_section(check_cancel):
            yield


def tree_size(root: Path) -> int:
    root = Path(root)

    def count():
        total = 0
        seen = set()
        control = current_control()
        for path in root.rglob("*"):
            if control:
                control.checkpoint()
            _check_path(path)
            if path.is_file():
                info = path.stat()
                identity = (info.st_dev, info.st_ino) if info.st_ino else ("path", str(path))
                if identity not in seen:
                    total += info.st_size
                    seen.add(identity)
        return total

    return run_background(count, "Measure workspace")


def _check_path(path: Path, require_file: bool = False):
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    if path.is_symlink() or attributes & 0x400:
        raise AuditError("Linked sources and junctions cannot be copied safely.")
    if require_file and not path.is_file():
        raise AuditError("A regular file is required for streamed IO.")


def _copy_bytes(source: Path, target: Path, preserve: bool, manage_progress: bool = True):
    _check_path(source, require_file=True)
    if target.exists():
        _check_path(target, require_file=True)
        if os.path.samefile(source, target):
            raise shutil.SameFileError("Source and destination are the same file.")
    control = current_control()
    if control and manage_progress:
        control.start("Copy file", source.stat().st_size)
    with source.open("rb") as reader, target.open("wb") as writer:
        while True:
            if control:
                control.checkpoint()
            chunk = reader.read(CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)
            if control:
                control.advance(len(chunk))
        writer.flush()
        os.fsync(writer.fileno())
    if preserve:
        shutil.copystat(source, target)
    return str(target)


def copy_file(source, target, preserve: bool = True):
    source, target = Path(source), Path(target)
    return run_background(lambda: _copy_bytes(source, target, preserve), "Copy file")


def copy_tree(source, target):
    source, target = Path(source), Path(target)

    def copy():
        control = current_control()
        _check_path(source)
        total = 0
        for path in source.rglob("*"):
            if control:
                control.checkpoint()
            _check_path(path)
            if path.is_file():
                total += path.stat().st_size
            elif not path.is_dir():
                raise AuditError("Special files cannot be copied safely.")
        if control:
            control.start("Copy package", total)
        source_root = source.resolve()
        target_root = target.resolve()

        def copy_one(original, destination):
            original, destination = Path(original), Path(destination)
            if not original.resolve().is_relative_to(source_root) or not destination.resolve().is_relative_to(target_root):
                raise AuditError("A copy path escaped the package directory.")
            return _copy_bytes(original, destination, True, manage_progress=False)

        return shutil.copytree(source, target, copy_function=copy_one)

    return run_background(copy, "Copy package")


def digest_file(path: Path, algorithm: str) -> str:
    path = Path(path)

    def digest():
        control = current_control()
        _check_path(path, require_file=True)
        if control:
            control.start("Hash file", path.stat().st_size)
        value = hashlib.new(algorithm)
        with path.open("rb") as stream:
            while True:
                if control:
                    control.checkpoint()
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                value.update(chunk)
                if control:
                    control.advance(len(chunk))
        return value.hexdigest()

    return run_background(digest, "Hash file")


def digest_bytes(data: bytes, algorithm: str = "sha256") -> str:
    def digest():
        control = current_control()
        if control:
            control.start("Hash metadata", len(data))
        value = hashlib.new(algorithm)
        view = memoryview(data)
        for offset in range(0, len(data), CHUNK_SIZE):
            if control:
                control.checkpoint()
            chunk = view[offset:offset + CHUNK_SIZE]
            value.update(chunk)
            if control:
                control.advance(len(chunk))
        return value.hexdigest()

    return run_background(digest, "Hash metadata")


def read_bytes(path: Path) -> bytes:
    path = Path(path)
    limit = MAX_METADATA_BYTES if path.name.casefold() == "metadata.xml" else None

    def read():
        control = current_control()
        _check_path(path, require_file=True)
        if limit is not None and path.stat().st_size > limit:
            raise MetadataTooLargeError(f"metadata.xml exceeds the {limit}-byte limit.")
        if control:
            control.start("Read metadata", path.stat().st_size)
        chunks = []
        total = 0
        with path.open("rb") as stream:
            while True:
                if control:
                    control.checkpoint()
                read_size = CHUNK_SIZE if limit is None else min(CHUNK_SIZE, limit - total + 1)
                chunk = stream.read(read_size)
                if not chunk:
                    break
                total += len(chunk)
                if limit is not None and total > limit:
                    raise MetadataTooLargeError(f"metadata.xml exceeds the {limit}-byte limit.")
                chunks.append(chunk)
                if control:
                    control.advance(len(chunk))
        return b"".join(chunks)

    return run_background(read, "Read metadata")