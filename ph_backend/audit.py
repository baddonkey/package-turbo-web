"""Durable package-level preservation events and PREMIS 3.0 export."""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import rfc8785
from lxml import etree

PREMIS_NS = "http://www.loc.gov/premis/v3"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
ZERO_DIGEST = "0" * 64
FORMAT_VERSION = 1


class AuditError(Exception):
    pass


@dataclass(frozen=True)
class AuditCheckpoint:
    package_identifier: str
    sequence: int
    digest: str


def _digest(previous: str, payload: bytes) -> str:
    return hashlib.sha256(bytes.fromhex(previous) + payload).hexdigest()


def _trusted_state_directory() -> Path:
    configured = os.environ.get("PACKAGE_TURBO_TRUSTED_STATE_DIR")
    if configured:
        root = Path(configured)
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return root / "PackageTurbo" / "audit-checkpoints"


class _TrustedCheckpointStore:
    def __init__(self, package_identifier: str, journal_path: Path):
        self.package_identifier = package_identifier
        self.directory = _trusted_state_directory()
        location = os.path.normcase(str(Path(journal_path).resolve()))
        name = hashlib.sha256(location.encode("utf-8")).hexdigest()
        self.path = self.directory / f"{name}.json"
        self.lock_path = self.directory / f"{name}.lock"

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.directory.chmod(0o700)
        with self.lock_path.open("a+b") as lock:
            if os.name == "nt":
                import msvcrt

                if lock.tell() == 0:
                    lock.write(b"\x00")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _entry(value: dict) -> dict:
        sequence = value.get("sequence")
        digest = value.get("digest")
        if (type(sequence) is not int or sequence < 0 or not isinstance(digest, str)
                or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)):
            raise AuditError("The trusted audit checkpoint is malformed.")
        return {"sequence": sequence, "digest": digest}

    def _read(self) -> dict | None:
        if not self.path.exists():
            return None
        attributes = getattr(self.path.lstat(), "st_file_attributes", 0)
        if self.path.is_symlink() or attributes & 0x400 or not self.path.is_file():
            raise AuditError("The trusted audit checkpoint is not a regular file.")
        if self.path.stat().st_size > 64 * 1024:
            raise AuditError("The trusted audit checkpoint exceeds its size limit.")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("version") != 1 or value.get("packageIdentifier") != self.package_identifier:
                raise AuditError("The trusted audit checkpoint belongs to a different package.")
            checkpoint = self._entry(value["checkpoint"])
            pending_value = value.get("pending")
            pending = None
            if pending_value is not None:
                pending = {"before": self._entry(pending_value["before"]),
                           "after": self._entry(pending_value["after"])}
                if pending["before"] != checkpoint or pending["before"]["sequence"] >= pending["after"]["sequence"]:
                    raise AuditError("The pending trusted checkpoint is inconsistent.")
            return {"version": 1, "packageIdentifier": self.package_identifier,
                    "checkpoint": checkpoint, "pending": pending}
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            raise AuditError("The trusted audit checkpoint cannot be read.") from exc

    def _write(self, checkpoint: dict, pending: dict | None = None) -> None:
        state = {"version": 1, "packageIdentifier": self.package_identifier,
                 "checkpoint": self._entry(checkpoint), "pending": pending}
        data = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".checkpoint-", suffix=".tmp",
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, self.path)
            if os.name != "nt":
                descriptor = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError as exc:
            raise AuditError("The trusted audit checkpoint could not be saved.") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def reconcile(self, current: AuditCheckpoint) -> None:
        actual = {"sequence": current.sequence, "digest": current.digest}
        state = self._read()
        if state is None:
            self._write(actual)
            return
        pending = state["pending"]
        if pending is not None:
            if actual == pending["before"]:
                self._write(pending["before"])
                return
            if actual == pending["after"]:
                self._write(pending["after"])
                return
            raise AuditError("The audit journal conflicts with its pending trusted checkpoint.")
        if actual != state["checkpoint"]:
            raise AuditError("The audit journal does not match its trusted checkpoint.")

    def prepare(self, before: AuditCheckpoint, after: AuditCheckpoint) -> None:
        state = self._read()
        previous = {"sequence": before.sequence, "digest": before.digest}
        following = {"sequence": after.sequence, "digest": after.digest}
        if (before.package_identifier != self.package_identifier
                or after.package_identifier != self.package_identifier
                or state is None or state["pending"] is not None or state["checkpoint"] != previous):
            raise AuditError("The audit journal changed before its trusted checkpoint could advance.")
        self._write(previous, {"before": previous, "after": following})

    def current(self) -> AuditCheckpoint:
        state = self._read()
        if state is None or state["pending"] is not None:
            raise AuditError("The trusted audit checkpoint needs reconciliation before writing.")
        checkpoint = state["checkpoint"]
        return AuditCheckpoint(self.package_identifier, checkpoint["sequence"], checkpoint["digest"])

    def finalize(self, checkpoint: AuditCheckpoint) -> None:
        state = self._read()
        following = {"sequence": checkpoint.sequence, "digest": checkpoint.digest}
        if state is None or state["pending"] is None or state["pending"]["after"] != following:
            raise AuditError("The pending trusted checkpoint cannot be finalized.")
        self._write(following)


class _CheckpointedConnection(sqlite3.Connection):
    def commit(self):
        store = getattr(self, "checkpoint_store", None)
        journal = getattr(self, "journal", None)
        if store is None or journal is None:
            return super().commit()
        header, _ = journal._verify(self, enforce_trusted=False)
        after = AuditCheckpoint(header["package_identifier"], header["sequence"], header["digest"])
        before = store.current()
        if before == after:
            return super().commit()
        if after.sequence <= before.sequence:
            raise AuditError("The audit journal cannot move behind its trusted checkpoint.")
        store.prepare(before, after)
        super().commit()
        store.finalize(after)


class AuditJournal:
    def __init__(self, path: Path, package_identifier: str | None = None, read_only: bool = False):
        self.path = Path(path).resolve()
        self.read_only = read_only
        self._checkpoint_store = None
        if package_identifier is not None and not package_identifier.strip():
            raise AuditError("A package identifier must not be empty.")
        if not self.path.exists():
            if read_only:
                raise AuditError("The audit journal does not exist.")
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self._connection("rwc") as connection:
                statements = ("""CREATE TABLE journal (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        format_version INTEGER NOT NULL,
                        package_identifier TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        digest TEXT NOT NULL
                    )""", """CREATE TABLE events (
                        sequence INTEGER PRIMARY KEY,
                        payload TEXT NOT NULL,
                        previous_digest TEXT NOT NULL,
                        digest TEXT NOT NULL
                    )""", """CREATE TRIGGER immutable_event_update BEFORE UPDATE ON events
                        BEGIN SELECT RAISE(ABORT, 'Audit events are immutable'); END""",
                    """CREATE TRIGGER immutable_event_delete BEFORE DELETE ON events
                        BEGIN SELECT RAISE(ABORT, 'Audit events are immutable'); END""")
                existing = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='journal'").fetchone()
                if existing is None:
                    for statement in statements:
                        connection.execute(statement)
                    identifier = package_identifier or "urn:uuid:" + str(uuid.uuid4())
                    connection.execute("INSERT INTO journal VALUES (1, ?, ?, 0, ?)",
                                       (FORMAT_VERSION, identifier, ZERO_DIGEST))
                connection.commit()
            if os.name != "nt":
                self.path.chmod(0o600)
        with self._connection("ro") as connection:
            header, _ = self._verify(connection)
        self.package_identifier = header["package_identifier"]
        if package_identifier is not None and self.package_identifier != package_identifier:
            raise AuditError("The journal belongs to a different package.")
        self._checkpoint_store = _TrustedCheckpointStore(self.package_identifier, self.path)
        with self._connection("ro") as connection:
            self._verify(connection)

    @contextmanager
    def _connection(self, mode: str):
        connection = None
        lock = self._checkpoint_store.locked() if self._checkpoint_store is not None else nullcontext()
        with lock:
            try:
                connection = sqlite3.connect(self.path.as_uri() + "?mode=" + mode, uri=True, timeout=10,
                                             factory=_CheckpointedConnection)
                connection.checkpoint_store = self._checkpoint_store
                connection.journal = self
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN" if mode == "ro" else "BEGIN IMMEDIATE")
                yield connection
            except sqlite3.Error as exc:
                raise AuditError(f"Audit journal operation failed: {exc}") from exc
            finally:
                if connection is not None:
                    connection.close()

    def _verify(self, connection, checkpoint: AuditCheckpoint | None = None, enforce_trusted: bool = True):
        header = connection.execute("SELECT * FROM journal WHERE id=1").fetchone()
        if header is None or header["format_version"] != FORMAT_VERSION:
            raise AuditError("Unsupported or missing audit journal header.")
        previous = ZERO_DIGEST
        records = []
        checkpoint_found = checkpoint is None or checkpoint.sequence == 0 and checkpoint.digest == ZERO_DIGEST
        if checkpoint is not None and checkpoint.package_identifier != header["package_identifier"]:
            raise AuditError("The checkpoint belongs to a different package.")
        for expected, row in enumerate(connection.execute("SELECT * FROM events ORDER BY sequence"), start=1):
            try:
                payload = json.loads(row["payload"])
                canonical = rfc8785.dumps(payload)
                valid = (row["sequence"] == expected and row["previous_digest"] == previous
                         and row["digest"] == _digest(previous, canonical)
                         and payload["sequence"] == expected
                         and payload["packageIdentifier"] == header["package_identifier"])
            except (ValueError, TypeError, KeyError) as exc:
                raise AuditError(f"Invalid audit event at sequence {expected}.") from exc
            if not valid:
                raise AuditError(f"Audit chain verification failed at sequence {expected}.")
            previous = row["digest"]
            records.append({"payload": payload, "digest": previous, "previousDigest": row["previous_digest"]})
            if checkpoint is not None and checkpoint.sequence == expected:
                checkpoint_found = checkpoint.digest == previous
        if len(records) != header["sequence"] or previous != header["digest"]:
            raise AuditError("The audit journal head does not match its events.")
        if not checkpoint_found:
            raise AuditError("The audit journal does not match the trusted checkpoint.")
        if enforce_trusted and self._checkpoint_store is not None:
            self._checkpoint_store.reconcile(AuditCheckpoint(
                header["package_identifier"], header["sequence"], header["digest"]))
        return header, records

    def verify(self, checkpoint: AuditCheckpoint | None = None) -> AuditCheckpoint:
        with self._connection("ro") as connection:
            header, _ = self._verify(connection, checkpoint)
            return AuditCheckpoint(header["package_identifier"], header["sequence"], header["digest"])

    def records(self) -> list[dict]:
        with self._connection("ro") as connection:
            _, records = self._verify(connection)
            return records

    def append(self, event_type: str, *, agent_identifier: str, software_version: str,
               detail: dict | None = None, outcome: str = "success",
               transaction_identifier: str | None = None) -> dict:
        return self.append_many([{
            "event_type": event_type, "agent_identifier": agent_identifier,
            "software_version": software_version, "detail": detail, "outcome": outcome,
            "transaction_identifier": transaction_identifier,
        }])[0]

    def append_many(self, events: list[dict]) -> list[dict]:
        if self.read_only:
            raise AuditError("The audit journal is read-only.")
        if not events:
            return []
        with self._connection("rw") as connection:
            self._verify(connection)
            records = [self._append(connection, **event) for event in events]
            connection.commit()
            return records

    def _append(self, connection, event_type: str, *, agent_identifier: str, software_version: str,
                detail: dict | None = None, outcome: str = "success",
                transaction_identifier: str | None = None) -> dict:
        for value in (event_type, agent_identifier, software_version):
            if not isinstance(value, str) or not value.strip():
                raise AuditError("Event type, operator and software version are required.")
        if outcome not in ("success", "failure", "discarded"):
            raise AuditError("Unsupported audit event outcome.")
        if detail is not None and not isinstance(detail, dict):
            raise AuditError("Event detail must be a JSON object.")
        if transaction_identifier is not None and not transaction_identifier.strip():
            raise AuditError("A transaction identifier must not be empty.")
        header = connection.execute("SELECT * FROM journal WHERE id=1").fetchone()
        if header["package_identifier"] != self.package_identifier:
            raise AuditError("The journal package identity has changed.")
        payload = {
            "sequence": header["sequence"] + 1,
            "packageIdentifier": self.package_identifier,
            "eventIdentifier": "urn:uuid:" + str(uuid.uuid4()),
            "eventType": event_type,
            "eventDateTime": datetime.now(timezone.utc).isoformat(),
            "agentIdentifier": agent_identifier,
            "softwareVersion": software_version,
            "transactionIdentifier": transaction_identifier or "urn:uuid:" + str(uuid.uuid4()),
            "outcome": outcome,
            "detail": {} if detail is None else detail,
        }
        try:
            canonical = rfc8785.dumps(payload)
        except (ValueError, TypeError) as exc:
            raise AuditError("Event detail cannot be encoded as canonical JSON.") from exc
        digest = _digest(header["digest"], canonical)
        connection.execute("INSERT INTO events VALUES (?, ?, ?, ?)",
                           (payload["sequence"], canonical.decode("utf-8"), header["digest"], digest))
        connection.execute("UPDATE journal SET sequence=?, digest=? WHERE id=1",
                           (payload["sequence"], digest))
        return {"payload": json.loads(canonical), "digest": digest, "previousDigest": header["digest"]}

    def premis_xml(self, redact_values: bool = False, *, metadata_xml: bytes | None = None,
                   sip_manifest: dict | None = None) -> bytes:
        records = self.records()
        root = etree.Element(f"{{{PREMIS_NS}}}premis", nsmap={"premis": PREMIS_NS, "xsi": XSI_NS}, version="3.0")

        def child(parent, name: str, text: str | None = None):
            element = etree.SubElement(parent, f"{{{PREMIS_NS}}}{name}")
            if text is not None:
                element.text = text
            return element

        def identifier(parent, name: str, value: str, kind: str = "local"):
            container = child(parent, name)
            child(container, name + "Type", kind)
            child(container, name + "Value", value)
            return container

        def part_of_package(parent):
            relationship = child(parent, "relationship")
            child(relationship, "relationshipType", "structural")
            child(relationship, "relationshipSubType", "is part of")
            related_object = child(relationship, "relatedObjectIdentifier")
            child(related_object, "relatedObjectIdentifierType", "local")
            child(related_object, "relatedObjectIdentifierValue", self.package_identifier)

        object_definitions = {}
        event_object_links = []

        def file_object(path: str, state: dict | None) -> str | None:
            if not isinstance(state, dict) or state.get("type") != "file":
                return None
            digest = state.get("sha256", "")
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                return None
            path_digest = hashlib.sha256((self.package_identifier + "\0" + path).encode("utf-8")).hexdigest()
            object_id = f"urn:package-turbo:file:{path_digest}:{digest}"
            object_definitions.setdefault(object_id, {"type": "file", "digest": digest, "size": state.get("size")})
            return object_id

        def xml_object(identity: str) -> str:
            identity_digest = hashlib.sha256((self.package_identifier + "\0xml\0" + identity).encode("utf-8"))
            object_id = "urn:package-turbo:xml:" + identity_digest.hexdigest()
            object_definitions.setdefault(object_id, {"type": "intellectualEntity"})
            return object_id

        def gather_event_objects(record: dict) -> dict[str, set[str]]:
            payload = record["payload"]
            detail = payload["detail"]
            links = {self.package_identifier: set()}

            def link(object_id: str | None, role: str = ""):
                if object_id:
                    links.setdefault(object_id, set())
                    if role:
                        links[object_id].add(role)

            def gather_change(change: dict, include_xml: bool = True):
                if not isinstance(change, dict):
                    return
                path = change.get("path", "")
                before, after = change.get("before"), change.get("after")
                if isinstance(before, dict) or isinstance(after, dict):
                    link(file_object(path, before), "source")
                    link(file_object(path, after), "outcome")
                elif include_xml and path and ("oldValue" in change or "newValue" in change):
                    link(xml_object(path), "outcome")

            xml_changes = detail.get("xmlChanges", []) + detail.get("changes", [])
            xml_ids = {}
            for change in xml_changes:
                path = change.get("path", "") if isinstance(change, dict) else ""
                if path.endswith("/@id"):
                    xml_ids[path[:-4]] = (change.get("oldValue"), change.get("newValue"))

            def owning_xml_ids(path: str) -> tuple[str | None, str | None]:
                if "/@" in path:
                    element_path = path.rsplit("/@", 1)[0]
                elif path.endswith("/text()"):
                    element_path = path[:-7]
                else:
                    element_path = path
                while element_path:
                    if element_path in xml_ids:
                        old_id, new_id = xml_ids[element_path]
                        return ((str(old_id) if old_id else None), (str(new_id) if new_id else None))
                    if "/" not in element_path:
                        break
                    element_path = element_path.rsplit("/", 1)[0]
                return None, None

            for change in xml_changes:
                if not isinstance(change, dict) or not change.get("path"):
                    continue
                path = change["path"]
                if path.endswith("/@id"):
                    old_id, new_id = change.get("oldValue"), change.get("newValue")
                else:
                    old_id, new_id = owning_xml_ids(path)
                if old_id:
                    link(xml_object("id:" + old_id), "source")
                if new_id:
                    link(xml_object("id:" + new_id), "outcome")
                if not old_id and not new_id:
                    if change.get("oldValue") is not None:
                        link(xml_object(path), "source")
                    if change.get("newValue") is not None:
                        link(xml_object(path), "outcome")

            for path, state in detail.get("manifest", {}).items():
                link(file_object(path, state), "outcome")
            gather_change(detail, include_xml=False)
            for key in ("changes", "fileChanges"):
                for change in detail.get(key, []):
                    gather_change(change)
            if detail.get("objectId") or (payload["eventType"] in ("metadata modification", "structural modification")
                                            and detail.get("path")):
                identity = ("id:" + str(detail["objectId"]) if detail.get("objectId")
                            else str(detail["path"]))
                link(xml_object(identity), "outcome")
            if detail.get("beforeSha256") or detail.get("afterSha256"):
                path = "header/metadata.xml"
                for key, role in (("beforeSha256", "source"), ("afterSha256", "outcome")):
                    digest = detail.get(key, "")
                    if len(digest) == 64 and all(character in "0123456789abcdef" for character in digest):
                        link(file_object(path, {"type": "file", "size": None, "sha256": digest}), role)
            return links

        for record in records:
            event_object_links.append(gather_event_objects(record))

        if metadata_xml is not None and sip_manifest is not None:
            try:
                metadata_root = etree.fromstring(
                    metadata_xml, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
            except etree.XMLSyntaxError as exc:
                raise AuditError("The SIP metadata snapshot is not valid XML.") from exc

            entity_names = {"ablieferung", "archivischeNotiz", "archivischerVorgang", "dossier",
                            "mappe", "dokument", "ordnungssystem", "ordnungssystemposition"}
            entity_objects = {}

            def local_name(element) -> str:
                return etree.QName(element).localname if isinstance(element.tag, str) else ""

            def element_text(element, name: str) -> str:
                return next(((item.text or "").strip() for item in element
                             if isinstance(item.tag, str) and local_name(item) == name), "")

            for element in metadata_root.iter():
                if local_name(element) in entity_names and element.get("id"):
                    entity_objects[element] = xml_object("id:" + element.get("id"))

            def relate(source_id: str | None, target_id: str | None, subtype: str) -> None:
                if source_id and target_id and source_id in object_definitions and target_id in object_definitions:
                    object_definitions[source_id].setdefault("relationships", set()).add((subtype, target_id))

            for element, object_id in entity_objects.items():
                ancestor = element.getparent()
                while ancestor is not None and ancestor not in entity_objects:
                    ancestor = ancestor.getparent()
                if ancestor is not None:
                    relate(entity_objects[ancestor], object_id, "has part")

            file_paths_by_id = {}
            toc = next((element for element in metadata_root.iter() if local_name(element) == "inhaltsverzeichnis"), None)

            def walk_toc(element, base: PurePosixPath) -> None:
                for item in element:
                    item_name = local_name(item)
                    if item_name not in ("ordner", "datei"):
                        continue
                    name = element_text(item, "name")
                    if not name or name in (".", "..") or "/" in name or "\\" in name:
                        continue
                    path = base / name
                    if item_name == "datei" and item.get("id"):
                        file_paths_by_id.setdefault(item.get("id"), []).append(path.as_posix())
                    elif item_name == "ordner":
                        walk_toc(item, path)

            if toc is not None:
                walk_toc(toc, PurePosixPath())

            for element, source_id in entity_objects.items():
                for reference in element.iter():
                    if local_name(reference) != "dateiRef":
                        continue
                    owner = reference.getparent()
                    while owner is not None and owner not in entity_objects:
                        owner = owner.getparent()
                    if owner is None or entity_objects[owner] != source_id:
                        continue
                    for file_identifier in (reference.text or "").split():
                        paths = file_paths_by_id.get(file_identifier, [])
                        if len(paths) != 1:
                            continue
                        path = paths[0]
                        file_id = file_object(path, sip_manifest.get(path))
                        relate(source_id, file_id, "has part")

        package = child(root, "object")
        package.set(f"{{{XSI_NS}}}type", "premis:representation")
        identifier(package, "objectIdentifier", self.package_identifier)
        for object_id, definition in sorted(object_definitions.items()):
            object_element = child(root, "object")
            object_element.set(f"{{{XSI_NS}}}type", "premis:" + definition["type"])
            identifier(object_element, "objectIdentifier", object_id)
            if definition["type"] == "file":
                characteristics = child(object_element, "objectCharacteristics")
                fixity = child(characteristics, "fixity")
                child(fixity, "messageDigestAlgorithm", "SHA-256")
                child(fixity, "messageDigest", definition["digest"])
                if definition["size"] is not None:
                    child(characteristics, "size", str(definition["size"]))
                format_element = child(characteristics, "format")
                designation = child(format_element, "formatDesignation")
                child(designation, "formatName", "Unknown")
                child(format_element, "formatNote", "Format identification is not asserted by this export.")
            part_of_package(object_element)
            for subtype, related_id in sorted(definition.get("relationships", ())):
                relationship = child(object_element, "relationship")
                child(relationship, "relationshipType", "structural")
                child(relationship, "relationshipSubType", subtype)
                related_object = child(relationship, "relatedObjectIdentifier")
                child(related_object, "relatedObjectIdentifierType", "local")
                child(related_object, "relatedObjectIdentifierValue", related_id)
        operators = set()
        software_versions = set()
        for record, object_links in zip(records, event_object_links):
            payload = record["payload"]
            event = child(root, "event")
            identifier(event, "eventIdentifier", payload["eventIdentifier"], "URN")
            child(event, "eventType", payload["eventType"])
            child(event, "eventDateTime", payload["eventDateTime"])
            information = child(event, "eventDetailInformation")
            exported_detail = {
                "profile": "package-turbo-audit-v1",
                "sequence": payload["sequence"],
                "transactionIdentifier": payload["transactionIdentifier"],
                "sourceEventDigest": record["digest"],
                "previousDigest": record["previousDigest"],
                "redacted": redact_values,
            }
            if not redact_values:
                exported_detail["detail"] = payload["detail"]
            child(information, "eventDetail", rfc8785.dumps(exported_detail).decode("utf-8"))
            outcome = child(event, "eventOutcomeInformation")
            child(outcome, "eventOutcome", payload["outcome"])
            operator = identifier(event, "linkingAgentIdentifier", payload["agentIdentifier"])
            child(operator, "linkingAgentRole", "implementer")
            software_id = "package-turbo:" + payload["softwareVersion"]
            software = identifier(event, "linkingAgentIdentifier", software_id)
            child(software, "linkingAgentRole", "executing program")
            for object_id, roles in sorted(object_links.items()):
                object_link = identifier(event, "linkingObjectIdentifier", object_id)
                for role in sorted(roles):
                    child(object_link, "linkingObjectRole", role)
            operators.add(payload["agentIdentifier"])
            software_versions.add(payload["softwareVersion"])
        for operator_id in sorted(operators):
            operator = child(root, "agent")
            identifier(operator, "agentIdentifier", operator_id)
            child(operator, "agentType", "person")
        for version in sorted(software_versions):
            software = child(root, "agent")
            identifier(software, "agentIdentifier", "package-turbo:" + version)
            child(software, "agentName", "Package Turbo")
            child(software, "agentType", "software")
            child(software, "agentVersion", version)
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)

    def mets_xml(self, package_filename: str, package_sha256: str, *, premis_filename: str = "premis.xml",
                 version: str = "2", redact_values: bool = False, metadata_xml: bytes | None = None,
                 sip_manifest: dict | None = None) -> bytes:
        if version not in ("2", "1.12.1"):
            raise AuditError("Unsupported METS export version.")
        for filename in (package_filename, premis_filename):
            path = PurePosixPath(filename)
            if (not filename or path.is_absolute() or any(part in (".", "..") for part in filename.split("/"))
                    or ":" in filename or "\\" in filename or "\x00" in filename or filename.endswith("/")):
                raise AuditError("Companion paths must be safe relative paths.")
        if package_filename == premis_filename:
            raise AuditError("The package and PREMIS references must be different files.")
        if len(package_sha256) != 64 or any(character not in "0123456789abcdefABCDEF" for character in package_sha256):
            raise AuditError("The package needs a SHA-256 digest.")
        premis_bytes = self.premis_xml(redact_values=redact_values, metadata_xml=metadata_xml,
                          sip_manifest=sip_manifest)
        namespace = "http://www.loc.gov/METS/v2" if version == "2" else "http://www.loc.gov/METS/"
        xlink = "http://www.w3.org/1999/xlink"
        namespaces = {"mets": namespace}
        if version != "2":
            namespaces["xlink"] = xlink
        root = etree.Element(f"{{{namespace}}}mets", nsmap=namespaces, OBJID=self.package_identifier,
                             PROFILE="urn:package-turbo:mets-companion:1",
                             LABEL="Package Turbo companion: " + ("REDACTED" if redact_values else "FULL"))

        def child(parent, name: str, **attributes):
            return etree.SubElement(parent, f"{{{namespace}}}{name}", **attributes)

        def location(parent, name: str, filename: str, **attributes):
            element = child(parent, name, LOCTYPE="URL", **attributes)
            element.set("LOCREF" if version == "2" else f"{{{xlink}}}href", quote(filename, safe="/"))
            return element

        if version == "2":
            section = child(root, "mdSec")
            provenance = child(section, "md", ID="preservation", USE="DIGIPROV")
        else:
            section = child(root, "amdSec")
            provenance = child(section, "digiprovMD", ID="preservation")
        location(provenance, "mdRef", premis_filename, MDTYPE="PREMIS", MIMETYPE="application/xml",
                 CHECKSUM=hashlib.sha256(premis_bytes).hexdigest(), CHECKSUMTYPE="SHA-256")
        files = child(root, "fileSec")
        group = child(files, "fileGrp", USE="SIP")
        metadata_reference = {"MDID" if version == "2" else "ADMID": "preservation"}
        package = child(group, "file", ID="sip", MIMETYPE="application/zip", CHECKSUM=package_sha256.lower(),
                        CHECKSUMTYPE="SHA-256", **metadata_reference)
        location(package, "FLocat", package_filename)
        structures = child(root, "structSec") if version == "2" else root
        structure = child(structures, "structMap", TYPE="physical")
        division = child(structure, "div", TYPE="SIP", **metadata_reference)
        child(division, "fptr", FILEID="sip")
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)