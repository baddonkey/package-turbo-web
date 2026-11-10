import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from lxml import etree

from ph_backend.audit import AuditCheckpoint, AuditError, AuditJournal, PREMIS_NS, ZERO_DIGEST


def _append(journal, **kwargs):
    return journal.append("metadata modification", agent_identifier="operator:test",
                          software_version="0.1.0", **kwargs)


def test_journal_survives_reopen_and_preserves_details(tmp_path):
    path = tmp_path / "audit" / "journal.sqlite3"
    journal = AuditJournal(path)
    detail = {"target": "_dos1", "field": "titel", "old": "Old", "new": "New"}
    first = _append(journal, detail=detail)
    detail["new"] = "Changed caller value"
    checkpoint = journal.verify()
    assert checkpoint.sequence == 1 and checkpoint.digest == first["digest"]
    reopened = AuditJournal(path, package_identifier=journal.package_identifier)
    assert reopened.verify(checkpoint) == checkpoint
    assert reopened.records()[0]["payload"]["detail"]["new"] == "New"
    timestamp = datetime.fromisoformat(first["payload"]["eventDateTime"])
    assert timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)


def test_empty_journal_and_wrong_identity(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = AuditJournal(path)
    assert journal.verify() == AuditCheckpoint(journal.package_identifier, 0, ZERO_DIGEST)
    with pytest.raises(AuditError, match="different package"):
        AuditJournal(path, package_identifier="other-package")
    with pytest.raises(AuditError, match="different package"):
        journal.verify(AuditCheckpoint("other", 0, ZERO_DIGEST))


def test_read_only_journal_never_creates_or_appends(tmp_path):
    path = tmp_path / "missing" / "journal.sqlite3"
    with pytest.raises(AuditError, match="does not exist"):
        AuditJournal(path, read_only=True)
    assert not path.parent.exists()
    writable = AuditJournal(path)
    _append(writable)
    before = path.read_bytes()
    journal = AuditJournal(path, read_only=True)
    assert len(journal.records()) == 1
    with pytest.raises(AuditError, match="read-only"):
        _append(journal)
    assert path.read_bytes() == before


def test_concurrent_appends_form_one_chain(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = AuditJournal(path)

    def append_one(number):
        writer = AuditJournal(path, package_identifier=journal.package_identifier)
        return _append(writer, detail={"number": number})

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(append_one, range(12)))
    assert journal.verify().sequence == 12
    assert sorted(result["payload"]["sequence"] for result in results) == list(range(1, 13))


def test_events_are_immutable_and_tampering_is_detected(tmp_path):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    _append(journal)
    with sqlite3.connect(journal.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM events")
        connection.execute("DROP TRIGGER immutable_event_update")
        connection.execute("UPDATE events SET payload='{}' WHERE sequence=1")
    with pytest.raises(AuditError):
        journal.verify()
    with pytest.raises(AuditError):
        _append(journal)


def test_sql_failure_rolls_back_event_and_head(tmp_path):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    _append(journal)
    before = journal.verify()
    with sqlite3.connect(journal.path) as connection:
        connection.execute("""CREATE TRIGGER fail_head_update BEFORE UPDATE ON journal
                              BEGIN SELECT RAISE(ABORT, 'Simulated journal write failure'); END""")
    with pytest.raises(AuditError, match="Simulated journal write failure"):
        _append(journal)
    assert journal.verify() == before
    assert len(journal.records()) == 1


def test_atomic_event_batch_rolls_back_all_on_failure(tmp_path):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    event = {"event_type": "metadata modification", "agent_identifier": "operator",
             "software_version": "0.1.0", "transaction_identifier": "batch-1"}
    with pytest.raises(AuditError, match="required"):
        journal.append_many([event, {**event, "agent_identifier": ""}])
    assert journal.verify().sequence == 0
    records = journal.append_many([event, event])
    assert len(records) == 2 and journal.verify().sequence == 2
    assert records[1]["previousDigest"] == records[0]["digest"]


def test_truncation_and_trusted_checkpoint_are_checked(tmp_path):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    _append(journal)
    first = journal.verify()
    _append(journal)
    second = journal.verify()
    assert journal.verify(first) == second
    with sqlite3.connect(journal.path) as connection:
        connection.execute("DROP TRIGGER immutable_event_delete")
        connection.execute("DELETE FROM events WHERE sequence=2")
    with pytest.raises(AuditError, match="head"):
        journal.verify()
    with sqlite3.connect(journal.path) as connection:
        connection.execute("UPDATE journal SET sequence=1, digest=?", (first.digest,))
    with pytest.raises(AuditError, match="trusted checkpoint"):
        journal.verify(first)
    with pytest.raises(AuditError, match="checkpoint"):
        journal.verify(second)


def test_external_checkpoint_rejects_consistent_journal_rewrite(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = AuditJournal(path)
    _append(journal)
    package_identifier = journal.package_identifier

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER immutable_event_delete")
        connection.execute("DELETE FROM events")
        connection.execute("UPDATE journal SET sequence=0, digest=?", (ZERO_DIGEST,))

    with pytest.raises(AuditError, match="trusted checkpoint"):
        AuditJournal(path, package_identifier=package_identifier)


def test_external_checkpoint_binds_package_identity_to_journal_path(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = AuditJournal(path)
    _append(journal)

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER immutable_event_delete")
        connection.execute("DELETE FROM events")
        connection.execute("UPDATE journal SET package_identifier='forged', sequence=0, digest=?",
                           (ZERO_DIGEST,))

    with pytest.raises(AuditError, match="different package"):
        AuditJournal(path)


def test_trusted_checkpoint_recovers_after_commit_before_finalize(tmp_path, monkeypatch):
    path = tmp_path / "journal.sqlite3"
    journal = AuditJournal(path)
    package_identifier = journal.package_identifier

    def interrupted_finalize(_checkpoint):
        raise AuditError("simulated interruption")

    monkeypatch.setattr(journal._checkpoint_store, "finalize", interrupted_finalize)
    with pytest.raises(AuditError, match="simulated interruption"):
        _append(journal)

    reopened = AuditJournal(path, package_identifier=package_identifier)
    assert reopened.verify().sequence == 1


@pytest.mark.parametrize("detail", [{"invalid": float("nan")}, {"invalid": object()}])
def test_unencodable_events_do_not_advance_journal(tmp_path, detail):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    with pytest.raises(AuditError, match="canonical JSON"):
        _append(journal, detail=detail)
    assert journal.verify().sequence == 0


def test_failed_and_discarded_events_remain_distinct(tmp_path):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    for outcome in ("failure", "discarded", "success"):
        _append(journal, outcome=outcome, transaction_identifier="transaction-1")
    records = journal.records()
    assert [record["payload"]["outcome"] for record in records] == ["failure", "discarded", "success"]
    assert {record["payload"]["transactionIdentifier"] for record in records} == {"transaction-1"}


@pytest.mark.parametrize("redact_values", [False, True])
def test_premis_export_validates_and_resolves_links(tmp_path, redact_values):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    _append(journal, detail={"old": "Sensitive previous value", "new": "Current value"})
    _append(journal, outcome="failure")
    xml = journal.premis_xml(redact_values=redact_values)
    root = etree.fromstring(xml)
    schema_path = Path(__file__).resolve().parent.parent / "ph_backend/resources/preservation/premis-v3-0.xsd"
    etree.XMLSchema(etree.parse(str(schema_path))).assertValid(root)
    namespaces = {"p": PREMIS_NS}
    assert root.get("version") == "3.0"
    agents = set(root.xpath("p:agent/p:agentIdentifier/p:agentIdentifierValue/text()", namespaces=namespaces))
    assert set(root.xpath("p:event/p:linkingAgentIdentifier/p:linkingAgentIdentifierValue/text()",
                          namespaces=namespaces)) <= agents
    assert set(root.xpath("p:event/p:linkingObjectIdentifier/p:linkingObjectIdentifierValue/text()",
                          namespaces=namespaces)) == {journal.package_identifier}
    assert (b"Sensitive previous value" in xml) is not redact_values
    for text in root.xpath("p:event/p:eventDetailInformation/p:eventDetail/text()", namespaces=namespaces):
        exported = json.loads(text)
        assert exported["redacted"] is redact_values
    assert journal.premis_xml(redact_values=redact_values) == xml
    assert journal.records()[0]["payload"]["detail"]["old"] == "Sensitive previous value"


def test_empty_premis_export_is_valid(tmp_path):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    schema_path = Path(__file__).resolve().parent.parent / "ph_backend/resources/preservation/premis-v3-0.xsd"
    etree.XMLSchema(etree.parse(str(schema_path))).assertValid(etree.fromstring(journal.premis_xml()))


@pytest.mark.parametrize("redact_values", [False, True])
def test_premis_export_models_file_fixity_and_metadata_entities(tmp_path, redact_values):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    _append(journal, detail={"objectId": "_document-1", "path": "/paket/ablieferung/dokument[1]",
                             "field": "titel", "oldValue": "Private title", "newValue": "New title"})
    journal.append("file modification", agent_identifier="operator:test", software_version="0.1.0",
                   detail={"path": "content/private-record.pdf",
                           "before": {"type": "file", "size": 3, "sha256": "a" * 64},
                           "after": {"type": "file", "size": 4, "sha256": "b" * 64}})

    xml = journal.premis_xml(redact_values=redact_values)
    root = etree.fromstring(xml)
    schema_path = Path(__file__).resolve().parent.parent / "ph_backend/resources/preservation/premis-v3-0.xsd"
    etree.XMLSchema(etree.parse(str(schema_path))).assertValid(root)
    namespaces = {"p": PREMIS_NS}
    file_objects = root.xpath("p:object[@xsi:type='premis:file']", namespaces={**namespaces, "xsi": "http://www.w3.org/2001/XMLSchema-instance"})
    entity_objects = root.xpath("p:object[@xsi:type='premis:intellectualEntity']",
                                namespaces={**namespaces, "xsi": "http://www.w3.org/2001/XMLSchema-instance"})
    assert len(file_objects) == 2
    assert len(entity_objects) == 1
    digests = {obj.xpath("string(p:objectCharacteristics/p:fixity/p:messageDigest)", namespaces=namespaces)
               for obj in file_objects}
    assert digests == {"a" * 64, "b" * 64}
    linked_objects = set(root.xpath("p:event/p:linkingObjectIdentifier/p:linkingObjectIdentifierValue/text()",
                                    namespaces=namespaces))
    object_ids = set(root.xpath("p:object/p:objectIdentifier/p:objectIdentifierValue/text()",
                                namespaces=namespaces))
    assert object_ids <= linked_objects | {journal.package_identifier}
    assert ("content/private-record.pdf" in xml.decode("utf-8")) is not redact_values
    assert (b"Private title" in xml) is not redact_values


def test_premis_export_relates_metadata_hierarchy_and_referenced_files(tmp_path):
        journal = AuditJournal(tmp_path / "journal.sqlite3")
        metadata = b'''<paket xmlns="http://bar.admin.ch/arelda/v5" schemaVersion="5.1">
            <ablieferung id="_delivery">
                <dossier id="_dossier"><dokument id="_document"><dateiRef>_file-1</dateiRef></dokument></dossier>
            </ablieferung>
            <inhaltsverzeichnis><ordner><name>content</name>
                <datei id="_file-1"><name>report.pdf</name></datei>
            </ordner></inhaltsverzeichnis>
        </paket>'''
        manifest = {"content/report.pdf": {"type": "file", "size": 12, "sha256": "a" * 64}}

        xml = journal.premis_xml(metadata_xml=metadata, sip_manifest=manifest)
        root = etree.fromstring(xml)
        schema_path = Path(__file__).resolve().parent.parent / "ph_backend/resources/preservation/premis-v3-0.xsd"
        etree.XMLSchema(etree.parse(str(schema_path))).assertValid(root)
        namespaces = {"p": PREMIS_NS}
        relationships = {}
        for object_element in root.xpath("p:object", namespaces=namespaces):
                object_id = object_element.xpath("string(p:objectIdentifier/p:objectIdentifierValue)", namespaces=namespaces)
                relationships[object_id] = {
                        (relationship.xpath("string(p:relationshipSubType)", namespaces=namespaces),
                         relationship.xpath("string(p:relatedObjectIdentifier/p:relatedObjectIdentifierValue)",
                                                                namespaces=namespaces))
                        for relationship in object_element.xpath("p:relationship", namespaces=namespaces)
                }

        entity_id = lambda identity: "urn:package-turbo:xml:" + hashlib.sha256(
                (journal.package_identifier + "\0xml\0id:" + identity).encode("utf-8")).hexdigest()
        file_id = "urn:package-turbo:file:" + hashlib.sha256(
                (journal.package_identifier + "\0content/report.pdf").encode("utf-8")).hexdigest() + ":" + "a" * 64
        assert ("has part", entity_id("_dossier")) in relationships[entity_id("_delivery")]
        assert ("has part", entity_id("_document")) in relationships[entity_id("_dossier")]
        assert ("has part", file_id) in relationships[entity_id("_document")]


@pytest.mark.parametrize("redact_values", [False, True])
def test_premis_deleted_entity_uses_stable_xml_id_as_source_object(tmp_path, redact_values):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    journal.append("package operation", agent_identifier="operator:test", software_version="0.1.0",
                   detail={"xmlChanges": [
                       {"path": "/paket/ablieferung/dossier[1]/@id", "oldValue": "_dossier-1", "newValue": None},
                       {"path": "/paket/ablieferung/dossier[1]/titel/text()", "oldValue": "Old title", "newValue": None},
                   ]})

    root = etree.fromstring(journal.premis_xml(redact_values=redact_values))
    schema_path = Path(__file__).resolve().parent.parent / "ph_backend/resources/preservation/premis-v3-0.xsd"
    etree.XMLSchema(etree.parse(str(schema_path))).assertValid(root)
    expected = "urn:package-turbo:xml:" + hashlib.sha256(
        (journal.package_identifier + "\0xml\0id:_dossier-1").encode("utf-8")).hexdigest()
    namespaces = {"p": PREMIS_NS}
    object_ids = root.xpath("p:object/p:objectIdentifier/p:objectIdentifierValue/text()", namespaces=namespaces)
    linked_ids = root.xpath("p:event/p:linkingObjectIdentifier/p:linkingObjectIdentifierValue/text()",
                            namespaces=namespaces)
    assert expected in object_ids and expected in linked_ids
    assert (b"/paket/ablieferung/dossier[1]" in etree.tostring(root)) is not redact_values


@pytest.mark.parametrize("version", ["2", "1.12.1"])
@pytest.mark.parametrize("redact_values", [False, True])
def test_mets_companion_validates_with_fixity_and_resolved_ids(tmp_path, version, redact_values):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    _append(journal, detail={"old": "Private value"})
    digest = hashlib.sha256(b"Example SIP archive bytes").hexdigest()
    xml = journal.mets_xml("SIP Test.zip", digest, version=version, redact_values=redact_values)
    root = etree.fromstring(xml)
    base = Path(__file__).resolve().parent.parent / "ph_backend/resources/preservation"
    schema = base / ("mets2.xsd" if version == "2" else "mets-1.12.1.xsd")
    xlink_url = "http://www.loc.gov/standards/xlink/xlink.xsd"
    resolved_urls = []

    class LocalXLinkResolver(etree.Resolver):
        def resolve(self, url, public_id, context):
            if url == xlink_url:
                resolved_urls.append(url)
                return self.resolve_filename(str(base / "xlink.xsd"), context)
            return None

    parser = etree.XMLParser(no_network=True)
    parser.resolvers.add(LocalXLinkResolver())
    etree.XMLSchema(etree.parse(str(schema), parser)).assertValid(root)
    if version == "1.12.1":
        assert xlink_url in resolved_urls
    assert root.get("OBJID") == journal.package_identifier
    assert root.get("LABEL").endswith("REDACTED" if redact_values else "FULL")
    reference = root.xpath("//*[local-name()='mdRef']")[0]
    assert reference.get("CHECKSUM") == hashlib.sha256(journal.premis_xml(redact_values)).hexdigest()
    locator = root.xpath("//*[local-name()='FLocat']")[0]
    attribute = "LOCREF" if version == "2" else "{http://www.w3.org/1999/xlink}href"
    assert locator.get(attribute) == "SIP%20Test.zip"
    identifiers = set(root.xpath("//@ID"))
    assert set(root.xpath("//@FILEID")) <= identifiers
    assert set(root.xpath("//@MDID | //@ADMID")) <= identifiers
    assert journal.mets_xml("SIP Test.zip", digest, version=version, redact_values=redact_values) == xml


@pytest.mark.parametrize("filename", ["../package.zip", "/package.zip", "C:/package.zip", "a\\package.zip", "./package.zip"])
def test_mets_rejects_unsafe_companion_references(tmp_path, filename):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    with pytest.raises(AuditError, match="safe relative"):
        journal.mets_xml(filename, "a" * 64)


def test_mets_rejects_unsupported_version_and_invalid_digest(tmp_path):
    journal = AuditJournal(tmp_path / "journal.sqlite3")
    with pytest.raises(AuditError, match="Unsupported METS"):
        journal.mets_xml("sip.zip", "a" * 64, version="3")
    with pytest.raises(AuditError, match="SHA-256"):
        journal.mets_xml("sip.zip", "not-a-digest")