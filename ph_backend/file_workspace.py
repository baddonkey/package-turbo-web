"""Staged package filesystem operations and recoverable directory replacement."""

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import rfc8785
from lxml import etree

from .audit import AuditError, AuditJournal
from .workspace import MetadataWorkspace, companion_directory
from .processing import (MAX_METADATA_BYTES, copy_tree, critical_section, current_control, digest_bytes,
                        protect_commit, read_bytes, run_background, tree_size)


def manifest(root: Path) -> dict:
    root = Path(root)
    return run_background(lambda: _manifest(root), "Hash package")


def _manifest(root: Path) -> dict:
    if not root.exists():
        return {}
    result = {}
    control = current_control()
    paths = [root, *sorted(root.rglob("*"))]
    if control:
        control.start("Hash package", sum(path.stat().st_size for path in paths if path.is_file()))
    for path in paths:
        if control:
            control.checkpoint()
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or attributes & 0x400:
            raise AuditError("Linked files and junctions cannot be staged safely.")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            result[relative] = {"type": "directory"}
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    if control:
                        control.checkpoint()
                    digest.update(chunk)
                    if control:
                        control.advance(len(chunk))
            result[relative] = {"type": "file", "size": path.stat().st_size, "sha256": digest.hexdigest()}
        else:
            raise AuditError("Special files cannot be staged safely.")
    return result


def file_changes(before: dict, after: dict) -> list[dict]:
    return [{"path": path, "before": before.get(path), "after": after.get(path)}
            for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]


def sync_directories(*directories: Path):
    if os.name == "nt":
        return
    for directory in directories:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def flush_package(root: Path):
    control = current_control()
    paths = list(root.rglob("*"))
    if control:
        control.start("Flush staged files", sum(path.stat().st_size for path in paths if path.is_file()))
    for path in paths:
        if path.is_file():
            mode = path.stat().st_mode & 0o777
            try:
                if os.name == "nt" and not mode & 0o200:
                    path.chmod(mode | 0o200)
                with path.open("r+b" if os.name == "nt" else "rb") as stream:
                    os.fsync(stream.fileno())
            finally:
                path.chmod(mode)
            if control:
                control.advance(path.stat().st_size)
    directories = [path for path in paths if path.is_dir()]
    sync_directories(*sorted(directories, key=lambda path: len(path.parts), reverse=True), root)


def remove_staging_tree(root: Path):
    with critical_section(check_cancel=False):
        return run_background(lambda: _remove_staging_tree(root), "Clean temporary files")


def _remove_staging_tree(root: Path):
    paths = [root, *root.rglob("*")]
    for path in paths:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or attributes & 0x400:
            raise AuditError("Linked working copies cannot be removed automatically.")
    if os.name == "nt":
        for path in paths:
            path.chmod(path.stat().st_mode | 0o200)
    shutil.rmtree(root)


class UnchangedOperation(Exception):
    def __init__(self, result):
        self.result = result


class FileWorkspace:
    def __init__(self, metadata_workspace: MetadataWorkspace, max_bytes: int = 32 * 1024 ** 3):
        self.metadata = metadata_workspace
        self.root = metadata_workspace.metadata_path.parent.parent
        self.directory = metadata_workspace.directory
        self.journal = metadata_workspace.journal
        self.operator = metadata_workspace.operator
        self.software_version = metadata_workspace.software_version
        self.max_bytes = max_bytes
        self.stage = self.directory / "staged-package"
        self.backup = self.directory / "rollback-package"
        self._initialize()

    def _initialize(self):
        with self.metadata._locked():
            with self.journal._connection("rw") as connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS package_draft (
                    id INTEGER PRIMARY KEY CHECK(id=1), baseline TEXT NOT NULL,
                    snapshot TEXT NOT NULL, events TEXT NOT NULL)""")
                connection.execute("""CREATE TABLE IF NOT EXISTS package_prepared (
                    id INTEGER PRIMARY KEY CHECK(id=1), transaction_id TEXT NOT NULL,
                    before_manifest TEXT NOT NULL, after_manifest TEXT NOT NULL,
                    events TEXT NOT NULL, operator TEXT NOT NULL, software_version TEXT NOT NULL)""")
                connection.commit()
            self._recover()

    def _draft(self):
        with self.journal._connection("ro") as connection:
            _, records = self.journal._verify(connection)
            draft = connection.execute("SELECT * FROM package_draft WHERE id=1").fetchone()
        if draft is not None:
            staged = [record["payload"] for record in records if record["payload"]["eventType"] == "package draft staging"]
            expected = {"baselineSha256": self.metadata._hash(draft["baseline"].encode()),
                        "snapshotSha256": self.metadata._hash(draft["snapshot"].encode()),
                        "eventsSha256": self.metadata._hash(draft["events"].encode())}
            if not staged or any(staged[-1]["detail"].get(key) != value for key, value in expected.items()):
                raise AuditError("The staged package record does not match preservation history.")
        return draft

    def check(self):
        with self.metadata._locked():
            self._check(self._draft())

    def _budget(self, snapshot: dict, copies: int = 1):
        size = sum(entry.get("size", 0) for entry in snapshot.values())
        occupied = tree_size(self.directory)
        if occupied + size * copies > self.max_bytes:
            raise AuditError("The package workspace storage limit would be exceeded.")
        if shutil.disk_usage(self.directory).free < size * copies + 1024 * 1024:
            raise AuditError("Not enough free space for a safe staging copy.")

    def _check(self, draft):
        with self.journal._connection("ro") as connection:
            if connection.execute("SELECT id FROM package_prepared").fetchone() is not None:
                raise AuditError("Recover the interrupted package commit before editing its draft.")
        self.metadata._check_baseline()
        if draft is not None and manifest(self.root) != json.loads(draft["baseline"]):
            raise AuditError("Package files changed outside this workspace. No changes were committed.")
        if draft is not None and manifest(self.stage) != json.loads(draft["snapshot"]):
            raise AuditError("The staged package does not match its recorded snapshot.")

    def stage_operation(self, pkg, action, events: list[dict]):
        with self.metadata._locked():
            draft = self._draft()
            self._check(draft)
            baseline = json.loads(draft["baseline"]) if draft is not None else manifest(self.root)
            if draft is None:
                if self.stage.exists():
                    raise AuditError("An unrecorded staging directory needs manual review.")
                self._budget(baseline, copies=2)
                try:
                    copy_tree(self.root, self.stage)
                    if manifest(self.root) != baseline or manifest(self.stage) != baseline:
                        raise AuditError("The source package changed while its staging copy was created.")
                except BaseException:
                    if self.stage.exists():
                        remove_staging_tree(self.stage)
                    raise
            snapshot = manifest(self.stage)
            self._budget(snapshot)
            checkpoint = Path(tempfile.mkdtemp(prefix="operation-", dir=self.directory)) / "package"
            try:
                copy_tree(self.stage, checkpoint)
            except BaseException:
                remove_staging_tree(checkpoint.parent)
                if draft is None:
                    remove_staging_tree(self.stage)
                raise
            before_xml = etree.tostring(pkg.tree, encoding="UTF-8", xml_declaration=True)
            previous_root = pkg.file_root
            pkg.file_root = self.stage
            completed = False
            try:
                result = action(pkg)
                self._persist(pkg, baseline, events)
                completed = True
            except UnchangedOperation as unchanged:
                if draft is None:
                    remove_staging_tree(self.stage)
                pkg.file_root = previous_root
                completed = True
                return unchanged.result
            except BaseException:
                try:
                    remove_staging_tree(self.stage)
                    checkpoint.rename(self.stage)
                    if draft is None:
                        remove_staging_tree(self.stage)
                    pkg.restore_xml(before_xml)
                    pkg.file_root = previous_root
                    completed = True
                except BaseException as exc:
                    raise AuditError(f"Rollback is blocked. The working checkpoint is retained at {checkpoint}.") from exc
                raise
            finally:
                if completed and checkpoint.parent.exists():
                    remove_staging_tree(checkpoint.parent)
            return result

    def _persist(self, pkg, baseline: dict, events: list[dict]):
        for event in events:
            if (not isinstance(event, dict) or not isinstance(event.get("event_type"), str)
                    or not event["event_type"].strip() or not isinstance(event.get("detail"), dict)
                    or not isinstance(event.get("operator", self.operator), str)
                    or not event.get("operator", self.operator).strip()):
                raise AuditError("Package events need a type, operator and structured detail.")
        try:
            events_json = rfc8785.dumps(events).decode("utf-8")
        except (ValueError, TypeError) as exc:
            raise AuditError("Package events cannot be encoded as canonical JSON.") from exc
        xml = etree.tostring(pkg.tree, encoding="UTF-8", xml_declaration=True)
        path = self.stage / "header" / "metadata.xml"
        previous = read_bytes(path)
        try:
            with path.open("wb") as stream:
                stream.write(xml)
                stream.flush()
                os.fsync(stream.fileno())
            snapshot = manifest(self.stage)
            self._budget(snapshot, copies=0)
            baseline_json = rfc8785.dumps(baseline).decode("utf-8")
            snapshot_json = rfc8785.dumps(snapshot).decode("utf-8")
            self._publish_draft(baseline, snapshot, baseline_json, snapshot_json, events_json)
        except BaseException:
            path.write_bytes(previous)
            raise

    def _publish_draft(self, baseline, snapshot, baseline_json, snapshot_json, events_json):
        with critical_section(), self.journal._connection("rw") as connection:
            self.journal._verify(connection)
            connection.execute("INSERT OR REPLACE INTO package_draft VALUES (1, ?, ?, ?)",
                               (baseline_json, snapshot_json, events_json))
            connection.execute("DELETE FROM metadata_draft WHERE id=1")
            self.journal._append(connection, "package draft staging", agent_identifier=self.operator,
                                 software_version=self.software_version,
                                 detail={"scope": "staged-package", "snapshotSha256": self.metadata._hash(snapshot_json.encode()),
                                     "baselineSha256": self.metadata._hash(baseline_json.encode()),
                                     "eventsSha256": self.metadata._hash(events_json.encode()),
                                         "changes": file_changes(baseline, snapshot)})
            connection.commit()

    def persist(self, pkg, events: list[dict]):
        with self.metadata._locked():
            draft = self._draft()
            self._check(draft)
            if draft is None:
                raise AuditError("There is no staged package.")
            self._persist(pkg, json.loads(draft["baseline"]), events)

    def persist_metadata(self, pkg, events: list[dict]):
        if pkg.file_root != self.stage:
            raise AuditError("Metadata-only updates require the active staged package.")
        xml = etree.tostring(pkg.tree, encoding="UTF-8", xml_declaration=True)
        if len(xml) > MAX_METADATA_BYTES:
            raise AuditError(f"metadata.xml exceeds the {MAX_METADATA_BYTES}-byte limit.")
        try:
            events_json = rfc8785.dumps(events).decode("utf-8")
        except (ValueError, TypeError) as exc:
            raise AuditError("Package events cannot be encoded as canonical JSON.") from exc

        with self.metadata._locked():
            self.metadata._check_baseline()
            draft = self._draft()
            if draft is None:
                raise AuditError("There is no staged package.")
            baseline_json = draft["baseline"]
            baseline = json.loads(baseline_json)
            snapshot = json.loads(draft["snapshot"])
            metadata_key = "header/metadata.xml"
            previous_entry = snapshot.get(metadata_key)
            if not isinstance(previous_entry, dict) or previous_entry.get("type") != "file":
                raise AuditError("The staged package has no metadata file entry.")
            metadata_path = self.stage / metadata_key
            previous_xml = read_bytes(metadata_path)
            if (len(previous_xml) != previous_entry.get("size")
                    or digest_bytes(previous_xml) != previous_entry.get("sha256")):
                raise AuditError("Staged metadata changed outside this workspace.")

            updated_snapshot = dict(snapshot)
            updated_snapshot[metadata_key] = {"type": "file", "size": len(xml), "sha256": digest_bytes(xml)}
            if sum(entry.get("size", 0) for entry in updated_snapshot.values()) > self.max_bytes:
                raise AuditError("The package workspace storage limit would be exceeded.")
            snapshot_json = rfc8785.dumps(updated_snapshot).decode("utf-8")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=metadata_path.parent, prefix=".metadata-", suffix=".tmp",
                                                 delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(xml)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, metadata_path)
                self._publish_draft(baseline, updated_snapshot, baseline_json, snapshot_json, events_json)
            except BaseException:
                try:
                    current = self._draft()
                    committed = (current is not None
                                 and json.loads(current["snapshot"]).get(metadata_key)
                                 == updated_snapshot[metadata_key])
                except (AuditError, OSError):
                    committed = False
                if not committed:
                    rollback = metadata_path.with_name(".metadata-rollback-" + uuid.uuid4().hex)
                    try:
                        rollback.write_bytes(previous_xml)
                        os.replace(rollback, metadata_path)
                    finally:
                        rollback.unlink(missing_ok=True)
                raise
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def restore(self, pkg) -> list[dict]:
        with self.metadata._locked():
            draft = self._draft()
            self._check(draft)
            if draft is None:
                return []
            pkg.restore_xml(read_bytes(self.stage / "header" / "metadata.xml"))
            pkg.file_root = self.stage
            return json.loads(draft["events"])

    def _intent_detail(self, pending):
        return {"scope": "package-files", "beforeManifestSha256": self.metadata._hash(pending["before_manifest"].encode()),
                "afterManifestSha256": self.metadata._hash(pending["after_manifest"].encode()),
                "eventsSha256": self.metadata._hash(pending["events"].encode())}

    def _check_intent(self, pending):
        records = [record["payload"] for record in self.journal.records()
                   if record["payload"]["eventType"] == "package commit preparation"
                   and record["payload"]["transactionIdentifier"] == pending["transaction_id"]]
        if (len(records) != 1 or records[0]["detail"] != self._intent_detail(pending)
                or records[0]["agentIdentifier"] != pending["operator"]
                or records[0]["softwareVersion"] != pending["software_version"]):
            raise AuditError("The package recovery intent does not match preservation history.")

    def _finish(self, pending, success: bool, recovered: bool = False):
        self._check_intent(pending)
        before, after = json.loads(pending["before_manifest"]), json.loads(pending["after_manifest"])
        if manifest(self.root) != (after if success else before):
            raise AuditError("Package files conflict with the transaction outcome.")
        if success and manifest(self.backup) != before:
            raise AuditError("Rollback files changed during package commit. The original copy is retained for review.")
        with self.journal._connection("rw") as connection:
            self.journal._verify(connection)
            if success:
                for event in json.loads(pending["events"]):
                    self.journal._append(connection, event["event_type"],
                                         agent_identifier=event.get("operator", pending["operator"]),
                                         software_version=pending["software_version"], detail=event["detail"],
                                         transaction_identifier=pending["transaction_id"])
                for change in file_changes(before, after):
                    if change["path"] == "header/metadata.xml":
                        continue
                    self.journal._append(connection, "file modification", agent_identifier=pending["operator"],
                                         software_version=pending["software_version"], detail=change,
                                         transaction_identifier=pending["transaction_id"])
            self.journal._append(connection, "package commit recovery" if recovered else "package commit",
                                 agent_identifier=self.operator, software_version=self.software_version,
                                 transaction_identifier=pending["transaction_id"], outcome="success" if success else "failure",
                                 detail={"scope": "package-files", "beforeSha256": before["header/metadata.xml"]["sha256"],
                                         "afterSha256": after["header/metadata.xml"]["sha256"], "recovered": recovered})
            if success:
                connection.execute("UPDATE metadata_state SET baseline_digest=? WHERE id=1",
                                   (after["header/metadata.xml"]["sha256"],))
                connection.execute("DELETE FROM package_draft WHERE id=1")
            connection.execute("DELETE FROM package_prepared WHERE id=1")
            connection.commit()
        if success:
            self.metadata._baseline_digest = after["header/metadata.xml"]["sha256"]

    def _recover(self):
        with self.journal._connection("ro") as connection:
            self.journal._verify(connection)
            pending = connection.execute("SELECT * FROM package_prepared WHERE id=1").fetchone()
        if pending is None:
            return
        protect_commit()
        self._check_intent(pending)
        before, after = json.loads(pending["before_manifest"]), json.loads(pending["after_manifest"])
        current = manifest(self.root)
        if current == after:
            self._finish(pending, True, recovered=True)
        elif current == before:
            self._finish(pending, False, recovered=True)
        elif not current and manifest(self.backup) == before:
            self.backup.rename(self.root)
            sync_directories(self.root.parent, self.directory)
            self._finish(pending, False, recovered=True)
        else:
            raise AuditError("Package recovery conflicts with external files. Nothing was replaced.")

    def commit(self, pkg, events: list[dict]):
        self.persist(pkg, events)
        with self.metadata._locked():
            draft = self._draft()
            self._check(draft)
            self._budget(json.loads(draft["baseline"]))
            protect_commit()
            run_background(lambda: flush_package(self.stage), "Flush staged files")
            if manifest(self.stage) != json.loads(draft["snapshot"]):
                raise AuditError("The staging copy changed before commit preparation.")
            if self.backup.exists():
                records = [record["payload"] for record in self.journal.records()]
                commits = [record for record in records if record["eventType"] in ("package commit", "package commit recovery")
                           and record["outcome"] == "success"]
                preparation = [record for record in records if commits and record["eventType"] == "package commit preparation"
                               and record["transactionIdentifier"] == commits[-1]["transactionIdentifier"]]
                digest = self.metadata._hash(rfc8785.dumps(manifest(self.backup)))
                if len(preparation) != 1 or preparation[0]["detail"]["beforeManifestSha256"] != digest:
                    raise AuditError("An unrecognized rollback package needs review.")
                retained = self.directory / ("rollback-" + uuid.uuid4().hex)
                self.backup.rename(retained)
                sync_directories(self.directory)
                self.journal.append("rollback copy retained", agent_identifier=self.operator,
                            software_version=self.software_version,
                            detail={"path": retained.name, "manifestSha256": digest,
                                "bytes": tree_size(retained)})
            with self.journal._connection("rw") as connection:
                self.journal._verify(connection)
                if connection.execute("SELECT id FROM package_prepared").fetchone():
                    raise AuditError("An interrupted package commit needs recovery.")
                transaction = "urn:uuid:" + str(uuid.uuid4())
                connection.execute("INSERT INTO package_prepared VALUES (1, ?, ?, ?, ?, ?, ?)",
                                   (transaction, draft["baseline"], draft["snapshot"], draft["events"],
                                    self.operator, self.software_version))
                pending = connection.execute("SELECT * FROM package_prepared WHERE id=1").fetchone()
                self.journal._append(connection, "package commit preparation", agent_identifier=self.operator,
                                     software_version=self.software_version, transaction_identifier=transaction,
                                     detail=self._intent_detail(pending))
                connection.commit()
            try:
                self.root.rename(self.backup)
                sync_directories(self.root.parent, self.directory)
                if manifest(self.backup) != json.loads(pending["before_manifest"]):
                    if not self.root.exists():
                        self.backup.rename(self.root)
                        sync_directories(self.root.parent, self.directory)
                    raise AuditError("External changes detected during commit. Review the retained recovery intent.")
                self.stage.rename(self.root)
                sync_directories(self.root.parent, self.directory)
            except OSError:
                self._recover()
                raise
            self._finish(pending, True)
            pkg.file_root = self.root

    def discard(self):
        with self.metadata._locked():
            draft = self._draft()
            if draft is not None and manifest(self.stage) != json.loads(draft["snapshot"]):
                raise AuditError("The staged package does not match its recorded snapshot.")
            with self.journal._connection("rw") as connection:
                if connection.execute("SELECT id FROM package_prepared").fetchone():
                    raise AuditError("Recover the package commit before discarding its draft.")
                if draft is None:
                    return
                self.journal._append(connection, "package draft discarded", agent_identifier=self.operator,
                                     software_version=self.software_version, outcome="discarded",
                                     detail={"scope": "staged-package", "edits": json.loads(draft["events"])})
                connection.execute("DELETE FROM package_draft WHERE id=1")
                connection.commit()
            remove_staging_tree(self.stage)


def recover_file_commit(root: Path, operator: str, software_version: str):
    directory = companion_directory(root)
    if not (directory / "journal.sqlite3").exists():
        return
    journal = AuditJournal(directory / "journal.sqlite3", read_only=True)
    with journal._connection("ro") as connection:
        if connection.execute("SELECT name FROM sqlite_master WHERE name='package_prepared'").fetchone() is None:
            return
        if connection.execute("SELECT id FROM package_prepared").fetchone() is None:
            return
    if not operator.strip():
        raise AuditError("Confirm an operator before recovering the package.")
    workspace = FileWorkspace.__new__(FileWorkspace)
    workspace.root = Path(root).resolve()
    workspace.directory = directory
    workspace.stage = directory / "staged-package"
    workspace.backup = directory / "rollback-package"
    workspace.journal = AuditJournal(directory / "journal.sqlite3")
    workspace.operator, workspace.software_version = operator, software_version
    workspace.metadata = MetadataWorkspace.__new__(MetadataWorkspace)
    workspace.metadata.directory = directory
    with workspace.metadata._locked():
        workspace._recover()


def has_file_draft(journal: AuditJournal) -> bool:
    with journal._connection("ro") as connection:
        if connection.execute("SELECT name FROM sqlite_master WHERE name='package_draft'").fetchone() is None:
            return False
        return connection.execute("SELECT id FROM package_draft").fetchone() is not None