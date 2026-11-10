"""Bounded session undo/redo snapshots, independent of preservation events."""

import shutil
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .audit import AuditError
from .file_workspace import manifest, remove_staging_tree
from .processing import copy_file, critical_section, current_control, digest_bytes, digest_file, read_bytes, run_background, tree_size


@dataclass(frozen=True)
class PackageSnapshot:
    identifier: str
    directory: Path | None
    xml: bytes
    files: dict | None
    modes: dict = field(default_factory=dict)

    def restore_modes(self, target: Path):
        for relative, mode in self.modes.items():
            path = target / relative
            if path.is_file():
                path.chmod(mode)


@dataclass(frozen=True)
class UndoEntry:
    identifier: str
    label: str
    before: PackageSnapshot
    after: PackageSnapshot


class UndoHistory:
    def __init__(self, companion: Path, max_bytes: int, max_steps: int = 20):
        self.companion = Path(companion)
        self.directory = self.companion / ("undo-" + uuid.uuid4().hex)
        self.objects = self.directory / "objects"
        self.max_bytes = max_bytes
        self.max_steps = max_steps
        self.entries: list[UndoEntry] = []
        self.position = 0

    def capture(self, file_root: Path, xml: bytes) -> PackageSnapshot:
        files = manifest(file_root)
        expected = {name: dict(value) for name, value in files.items()}
        expected["header/metadata.xml"] = {"type": "file", "size": len(xml), "sha256": digest_bytes(xml)}
        modes = {name: (file_root / name).stat().st_mode & 0o777 for name, value in files.items() if value["type"] == "file"}
        missing = {value["sha256"]: value["size"] for value in expected.values()
                   if value["type"] == "file" and not (self.objects / value["sha256"]).exists()}
        size = sum(missing.values())
        occupied = tree_size(self.companion)
        if occupied + size > self.max_bytes:
            raise AuditError("Undo storage would exceed the workspace limit. Clear undo history or raise the limit.")
        if shutil.disk_usage(self.companion).free < size + 1024 * 1024:
            raise AuditError("Not enough free space for a safe undo snapshot.")
        identifier = uuid.uuid4().hex
        target = self.directory / identifier / "package"
        target.parent.mkdir(parents=True, mode=0o700)
        try:
            self.objects.mkdir(parents=True, exist_ok=True, mode=0o700)

            def reuse():
                control = current_control()
                for relative, value in expected.items():
                    if control:
                        control.checkpoint()
                    destination = target / relative
                    if value["type"] == "directory":
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    blob = self.objects / value["sha256"]
                    if blob.exists():
                        if blob.is_symlink() or digest_file(blob, "sha256") != value["sha256"]:
                            raise AuditError("The private undo object has changed.")
                    else:
                        temporary = self.objects / ("new-" + uuid.uuid4().hex)
                        try:
                            if relative == "header/metadata.xml":
                                temporary.write_bytes(xml)
                            else:
                                copy_file(file_root / relative, temporary)
                            if digest_file(temporary, "sha256") != value["sha256"]:
                                raise AuditError("Source bytes changed during undo capture.")
                            temporary.chmod(0o444)
                            os.replace(temporary, blob)
                        finally:
                            if temporary.exists():
                                temporary.chmod(0o600)
                                temporary.unlink()
                    with critical_section():
                        os.link(blob, destination)

            run_background(reuse, "Reuse undo content")
            if manifest(file_root) != files or manifest(target) != expected:
                raise AuditError("Package files changed while the undo snapshot was copied.")
            return PackageSnapshot(identifier, target, xml, expected, modes)
        except BaseException:
            self._remove_references(target.parent)
            raise

    def capture_metadata(self, xml: bytes) -> PackageSnapshot:
        return PackageSnapshot(uuid.uuid4().hex, None, xml, None)

    def check(self, snapshot: PackageSnapshot) -> None:
        if snapshot.files is None:
            if snapshot.directory is not None:
                raise AuditError("The metadata undo snapshot has an unexpected file directory.")
            return
        if manifest(snapshot.directory) != snapshot.files:
            raise AuditError("The undo snapshot has changed. No reversal was applied.")
        if digest_bytes(read_bytes(snapshot.directory / "header/metadata.xml")) != digest_bytes(snapshot.xml):
            raise AuditError("The undo metadata snapshot has changed.")

    def record(self, label: str, before: PackageSnapshot, after: PackageSnapshot,
               operation_identifier: str | None = None) -> bool:
        if before.files == after.files and before.xml == after.xml:
            self.release(before)
            self.release(after)
            return False
        abandoned = self.entries[self.position:]
        self.entries = self.entries[:self.position]
        self.entries.append(UndoEntry(operation_identifier or "urn:uuid:" + str(uuid.uuid4()), label, before, after))
        self.position = len(self.entries)
        for entry in abandoned:
            self.release(entry.before)
            self.release(entry.after)
        while len(self.entries) > self.max_steps:
            entry = self.entries.pop(0)
            self.release(entry.before)
            self.release(entry.after)
            self.position -= 1
        return True

    def release(self, snapshot: PackageSnapshot):
        if snapshot.directory is not None and snapshot.directory.parent.exists():
            self._remove_references(snapshot.directory.parent)

    def _remove_references(self, directory: Path):
        with critical_section(check_cancel=False):
            remove_staging_tree(directory)

            def prune():
                if self.objects.exists():
                    for blob in self.objects.iterdir():
                        if blob.is_symlink() or not blob.is_file() or not re.fullmatch(r"[0-9a-f]{64}", blob.name):
                            raise AuditError("Unexpected object in private undo storage.")
                        if blob.stat().st_nlink == 1:
                            if digest_file(blob, "sha256") != blob.name:
                                raise AuditError("An unreferenced undo object has changed; review is required.")
                            blob.chmod(0o600)
                            blob.unlink()
                        else:
                            blob.chmod(0o444)

            run_background(prune, "Release undo content")

    @property
    def can_undo(self) -> bool:
        return self.position > 0

    @property
    def can_redo(self) -> bool:
        return self.position < len(self.entries)

    def undo_target(self) -> UndoEntry:
        if not self.can_undo:
            raise AuditError("There is no package operation to undo.")
        entry = self.entries[self.position - 1]
        self.check(entry.before)
        return entry

    def redo_target(self) -> UndoEntry:
        if not self.can_redo:
            raise AuditError("There is no package operation to redo.")
        entry = self.entries[self.position]
        self.check(entry.after)
        return entry

    def clear(self):
        if self.directory.exists():
            for path in self.directory.iterdir():
                if path.name != "objects" and not re.fullmatch(r"[0-9a-f]{32}", path.name):
                    raise AuditError("Unexpected data in undo storage requires review.")
            if self.objects.exists():
                for blob in self.objects.iterdir():
                    if blob.is_symlink() or not blob.is_file() or not re.fullmatch(r"[0-9a-f]{64}", blob.name):
                        raise AuditError("Unexpected object in private undo storage.")
            remove_staging_tree(self.directory)
        self.entries = []
        self.position = 0