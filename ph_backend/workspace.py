"""Recoverable metadata commits with an external preservation companion."""

import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

import rfc8785
from lxml import etree

from .audit import AuditError, AuditJournal
from .processing import (MAX_METADATA_BYTES, current_control, digest_bytes, protect_commit,
                         read_bytes, run_background)


class ExternalMetadataChangeError(AuditError):
    """The metadata no longer matches the workspace's recorded baseline."""


def companion_directory(package_root: Path) -> Path:
    root = Path(package_root).resolve()
    return root.with_name(root.name + ".package-turbo")


def metadata_changes(before: bytes, after: bytes) -> list[dict]:
    return run_background(lambda: _metadata_changes(before, after), "Compare metadata")


def _metadata_changes(before: bytes, after: bytes) -> list[dict]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)

    def values(document):
        tree = etree.ElementTree(etree.fromstring(document, parser))
        result = {}
        for element in tree.iter():
            control = current_control()
            if control:
                control.checkpoint()
            if not isinstance(element.tag, str):
                continue
            path = tree.getpath(element)
            result[path + "/text()"] = element.text or ""
            for name, value in element.attrib.items():
                result[path + "/@" + name] = value
        return result

    old, new = values(before), values(after)
    return [{"path": path, "oldValue": old.get(path), "newValue": new.get(path)}
            for path in sorted(old.keys() | new.keys()) if old.get(path) != new.get(path)]


class MetadataWorkspace:
    def __init__(self, metadata_path: Path, package_root: Path, *, operator: str, software_version: str):
        if not operator.strip():
            raise AuditError("Confirm an audit operator before editing a package.")
        if not software_version.strip():
            raise AuditError("A software version is required.")
        self.metadata_path = Path(metadata_path).resolve()
        self.directory = companion_directory(package_root)
        if not self.metadata_path.is_relative_to(Path(package_root).resolve()):
            raise AuditError("The metadata file is outside the package.")
        self.operator = operator
        self.software_version = software_version
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._locked():
            self.journal = AuditJournal(self.directory / "journal.sqlite3")
            with self.journal._connection("rw") as connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS metadata_state (
                    id INTEGER PRIMARY KEY CHECK (id=1), baseline_digest TEXT NOT NULL)""")
                connection.execute("""CREATE TABLE IF NOT EXISTS metadata_prepared (
                    id INTEGER PRIMARY KEY CHECK (id=1), transaction_id TEXT NOT NULL,
                    before_xml BLOB NOT NULL, after_xml BLOB NOT NULL,
                    events TEXT NOT NULL, operator TEXT NOT NULL, software_version TEXT NOT NULL)""")
                connection.execute("""CREATE TABLE IF NOT EXISTS metadata_draft (
                    id INTEGER PRIMARY KEY CHECK (id=1), baseline_digest TEXT NOT NULL,
                    snapshot BLOB NOT NULL, events TEXT NOT NULL)""")
                connection.execute("INSERT OR IGNORE INTO metadata_state VALUES (1, ?)",
                                   (self._hash(read_bytes(self.metadata_path)),))
                connection.commit()
            self._recover()
            self._check_baseline()
            self._baseline_digest = self._hash(read_bytes(self.metadata_path))

    @staticmethod
    def _hash(data: bytes) -> str:
        return digest_bytes(data)

    @contextmanager
    def _locked(self):
        with open(self.directory / "workspace.lock", "a+b") as lock:
            if os.name == "nt":
                import msvcrt

                if lock.tell() == 0:
                    lock.write(b"\x00")
                    lock.flush()
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise AuditError("Another process is using this package workspace.") from exc
                try:
                    yield
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise AuditError("Another process is using this package workspace.") from exc
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _check_baseline(self) -> bytes:
        current = read_bytes(self.metadata_path)
        with self.journal._connection("ro") as connection:
            _, records = self.journal._verify(connection)
            expected = connection.execute("SELECT baseline_digest FROM metadata_state WHERE id=1").fetchone()[0]
        committed = [record["payload"] for record in records
                     if record["payload"]["eventType"] in ("metadata commit", "metadata commit recovery", "package commit", "package commit recovery")
                     and record["payload"]["outcome"] == "success"]
        if committed and committed[-1]["detail"]["afterSha256"] != expected:
            raise AuditError("Workspace baseline does not match preservation history.")
        if (self._hash(current) != expected
            or getattr(self, "_baseline_digest", expected) != expected):
            raise ExternalMetadataChangeError(
                "Die Metadaten wurden ausserhalb dieses Arbeitsbereichs geändert und stimmen nicht mehr "
                "mit der SIP-Erhaltungshistorie überein."
            )
        return current

    def check(self) -> None:
        with self._locked():
            with self.journal._connection("ro") as connection:
                pending = connection.execute("SELECT id FROM metadata_prepared").fetchone()
            if pending is not None:
                raise AuditError("A metadata transaction needs recovery. Reopen the package before editing.")
            self._check_baseline()

    def has_draft(self) -> bool:
        with self.journal._connection("ro") as connection:
            return connection.execute("SELECT id FROM metadata_draft WHERE id=1").fetchone() is not None

    def persist_draft(self, snapshot: bytes, events: list[dict]) -> None:
        if len(snapshot) > MAX_METADATA_BYTES:
            raise AuditError(f"metadata.xml exceeds the {MAX_METADATA_BYTES}-byte limit.")
        if not isinstance(events, list) or not events:
            raise AuditError("A metadata draft requires pending edits.")
        for event in events:
            if (not isinstance(event, dict) or not isinstance(event.get("event_type"), str)
                    or not event["event_type"].strip() or not isinstance(event.get("detail"), dict)
                    or not isinstance(event.get("operator", self.operator), str)
                    or not event.get("operator", self.operator).strip()):
                raise AuditError("Metadata draft events need a type, operator and structured detail.")
        try:
            events_json = rfc8785.dumps(events).decode("utf-8")
        except (ValueError, TypeError) as exc:
            raise AuditError("Metadata draft events cannot be encoded as canonical JSON.") from exc
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        try:
            root = etree.fromstring(snapshot, parser)
        except etree.XMLSyntaxError as exc:
            raise AuditError("The metadata draft is not valid XML.") from exc
        if etree.QName(root).localname != "paket":
            raise AuditError("The metadata draft has no package root element.")

        with self._locked():
            baseline = self._check_baseline()
            baseline_digest = self._hash(baseline)
            snapshot_digest = self._hash(snapshot)
            events_digest = self._hash(events_json.encode("utf-8"))
            detail = {"scope": "metadata-only", "baselineSha256": baseline_digest,
                      "snapshotSha256": snapshot_digest, "eventsSha256": events_digest}
            with self.journal._connection("rw") as connection:
                self.journal._verify(connection)
                if connection.execute("SELECT id FROM metadata_prepared WHERE id=1").fetchone() is not None:
                    raise AuditError("A metadata transaction needs recovery before editing.")
                connection.execute("INSERT OR REPLACE INTO metadata_draft VALUES (1, ?, ?, ?)",
                                   (baseline_digest, snapshot, events_json))
                self.journal._append(connection, "metadata draft staging", agent_identifier=self.operator,
                                     software_version=self.software_version, detail=detail)
                connection.commit()

    def load_draft(self) -> tuple[bytes, list[dict]] | None:
        with self._locked():
            baseline = self._check_baseline()
            with self.journal._connection("ro") as connection:
                _, records = self.journal._verify(connection)
                draft = connection.execute("SELECT * FROM metadata_draft WHERE id=1").fetchone()
            if draft is None:
                return None
            snapshot = bytes(draft["snapshot"])
            events = json.loads(draft["events"])
            if draft["baseline_digest"] != self._hash(baseline) or not isinstance(events, list) or not events:
                raise AuditError("The metadata draft does not match the current package baseline.")
            expected = {"scope": "metadata-only", "baselineSha256": self._hash(baseline),
                        "snapshotSha256": self._hash(snapshot),
                        "eventsSha256": self._hash(draft["events"].encode("utf-8"))}
            staging_records = [record["payload"] for record in records
                               if record["payload"]["eventType"] == "metadata draft staging"]
            if not staging_records or staging_records[-1]["detail"] != expected:
                raise AuditError("The metadata draft does not match preservation history.")
            parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
            try:
                root = etree.fromstring(snapshot, parser)
            except etree.XMLSyntaxError as exc:
                raise AuditError("The metadata draft is not valid XML.") from exc
            if etree.QName(root).localname != "paket":
                raise AuditError("The metadata draft has no package root element.")
            return snapshot, events

    def _write(self, data: bytes) -> None:
        return run_background(lambda: self._write_atomic(data), "Write committed metadata")

    def _write_atomic(self, data: bytes) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.metadata_path.parent, prefix=".package-turbo-",
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(self.metadata_path.stat().st_mode & 0o777)
            os.replace(temporary, self.metadata_path)
            if os.name != "nt":
                descriptor = os.open(self.metadata_path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _finish(self, pending, outcome: str, recovered: bool = False) -> None:
        self._check_intent(pending)
        expected = pending["after_xml"] if outcome == "success" else pending["before_xml"]
        if self._hash(read_bytes(self.metadata_path)) != self._hash(expected):
            raise AuditError("Metadata changed during commit completion. Recovery requires conflict resolution.")
        with self.journal._connection("rw") as connection:
            self.journal._verify(connection)
            events = json.loads(pending["events"])
            if outcome == "success":
                for event in events:
                    self.journal._append(connection, event["event_type"],
                                         agent_identifier=event.get("operator", pending["operator"]),
                                         software_version=pending["software_version"],
                                         transaction_identifier=pending["transaction_id"],
                                         detail=event["detail"])
            self.journal._append(connection, "metadata commit recovery" if recovered else "metadata commit",
                                 agent_identifier=self.operator if recovered else pending["operator"],
                                 software_version=self.software_version,
                                 transaction_identifier=pending["transaction_id"], outcome=outcome,
                                 detail={"scope": "metadata-only", "recovered": recovered,
                                         "beforeSha256": self._hash(pending["before_xml"]),
                                         "afterSha256": self._hash(pending["after_xml"]),
                                         "changes": metadata_changes(pending["before_xml"], pending["after_xml"])})
            if outcome == "success":
                connection.execute("UPDATE metadata_state SET baseline_digest=? WHERE id=1",
                                   (self._hash(pending["after_xml"]),))
                connection.execute("DELETE FROM metadata_draft WHERE id=1")
            connection.execute("DELETE FROM metadata_prepared WHERE id=1")
            connection.commit()
        if outcome == "success":
            self._baseline_digest = self._hash(pending["after_xml"])

    def _check_intent(self, pending) -> None:
        records = self.journal.records()
        preparations = [record["payload"] for record in records
                        if record["payload"]["eventType"] == "metadata commit preparation"
                        and record["payload"]["transactionIdentifier"] == pending["transaction_id"]]
        if len(preparations) != 1:
            raise AuditError("The recovery intent has no unique preservation record.")
        event = preparations[0]
        expected = {"scope": "metadata-only", "beforeSha256": self._hash(pending["before_xml"]),
                    "afterSha256": self._hash(pending["after_xml"]),
                    "eventsSha256": self._hash(pending["events"].encode("utf-8"))}
        if (event["detail"] != expected or event["agentIdentifier"] != pending["operator"]
                or event["softwareVersion"] != pending["software_version"]):
            raise AuditError("The recovery intent does not match its preservation record.")

    def _recover(self) -> None:
        with self.journal._connection("ro") as connection:
            self.journal._verify(connection)
            pending = connection.execute("SELECT * FROM metadata_prepared WHERE id=1").fetchone()
        if pending is None:
            return
        protect_commit()
        self._check_intent(pending)
        digest = self._hash(read_bytes(self.metadata_path))
        if digest == self._hash(pending["after_xml"]):
            self._finish(pending, "success", recovered=True)
        elif digest == self._hash(pending["before_xml"]):
            self._finish(pending, "failure", recovered=True)
        else:
            raise AuditError("Interrupted metadata commit conflicts with external changes. No files were replaced.")

    def save(self, after: bytes, events: list[dict] | None = None) -> bool:
        if len(after) > MAX_METADATA_BYTES:
            raise AuditError(f"metadata.xml exceeds the {MAX_METADATA_BYTES}-byte limit.")
        if not self.operator.strip():
            raise AuditError("Confirm an audit operator before saving.")
        for event in events or []:
            if (not isinstance(event, dict) or not isinstance(event.get("event_type"), str)
                    or not event["event_type"].strip() or not isinstance(event.get("detail"), dict)
                    or not isinstance(event.get("operator", self.operator), str)
                    or not event.get("operator", self.operator).strip()):
                raise AuditError("Pending audit events need a type, operator and structured detail.")
        try:
            canonical = rfc8785.dumps(events or []).decode("utf-8")
        except (ValueError, TypeError) as exc:
            raise AuditError("Pending events cannot be encoded as canonical JSON.") from exc
        with self._locked():
            before = self._check_baseline()
            changes = metadata_changes(before, after)
            if not changes and not events:
                return False
            with self.journal._connection("rw") as connection:
                self.journal._verify(connection)
                if connection.execute("SELECT id FROM metadata_prepared").fetchone() is not None:
                    raise AuditError("A metadata transaction needs recovery before saving.")
                transaction = "urn:uuid:" + str(uuid.uuid4())
                protect_commit()
                connection.execute("INSERT INTO metadata_prepared VALUES (1, ?, ?, ?, ?, ?, ?)",
                                   (transaction, before, after, canonical, self.operator, self.software_version))
                self.journal._append(connection, "metadata commit preparation", agent_identifier=self.operator,
                                     software_version=self.software_version, transaction_identifier=transaction,
                                     detail={"scope": "metadata-only", "beforeSha256": self._hash(before),
                                             "afterSha256": self._hash(after),
                                             "eventsSha256": self._hash(canonical.encode("utf-8"))})
                pending = connection.execute("SELECT * FROM metadata_prepared WHERE id=1").fetchone()
                connection.commit()
            try:
                self._check_baseline()
                self._write(after)
            except OSError:
                self._recover()
                raise
            self._finish(pending, "success")
            return True

    def discard(self, events: list[dict]) -> None:
        with self._locked():
                with self.journal._connection("ro") as connection:
                    pending = connection.execute("SELECT id FROM metadata_prepared WHERE id=1").fetchone()
                if pending is not None:
                    raise AuditError("Recover the pending metadata commit before discarding edits.")
                if events:
                    self._check_baseline()
                with self.journal._connection("rw") as connection:
                    self.journal._verify(connection)
                    if connection.execute("SELECT id FROM metadata_prepared WHERE id=1").fetchone() is not None:
                        raise AuditError("Recover the pending metadata commit before discarding edits.")
                    if events:
                        self.journal._append(connection, "metadata edits discarded", agent_identifier=self.operator,
                                             software_version=self.software_version, outcome="discarded",
                                             detail={"scope": "metadata-only", "edits": events})
                    connection.execute("DELETE FROM metadata_draft WHERE id=1")
                    connection.commit()
