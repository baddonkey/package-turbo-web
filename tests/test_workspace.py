import sqlite3

import pytest

from ph_backend.audit import AuditError
from ph_backend.workspace import MetadataWorkspace, companion_directory

BEFORE = b'<paket><dossier id="_dos1"><titel>Old title</titel></dossier></paket>'
AFTER = BEFORE.replace(b"Old title", b"New title")


def _workspace(tmp_path):
    root = tmp_path / "SIP_Test"
    path = root / "header/metadata.xml"
    path.parent.mkdir(parents=True)
    path.write_bytes(BEFORE)
    return MetadataWorkspace(path, root, operator="confirmed-operator", software_version="0.1.0")


def test_metadata_commit_is_audited_and_reopen_is_stable(tmp_path):
    workspace = _workspace(tmp_path)
    assert workspace.directory == companion_directory(workspace.metadata_path.parent.parent)
    assert workspace.save(AFTER)
    assert workspace.metadata_path.read_bytes() == AFTER
    records = workspace.journal.records()
    assert [record["payload"]["eventType"] for record in records] == ["metadata commit preparation", "metadata commit"]
    assert records[-1]["payload"]["detail"]["changes"][0]["oldValue"] == "Old title"
    assert records[-1]["payload"]["detail"]["changes"][0]["newValue"] == "New title"
    reopened = MetadataWorkspace(workspace.metadata_path, workspace.metadata_path.parent.parent,
                                 operator="confirmed-operator", software_version="0.1.0")
    assert reopened.journal.package_identifier == workspace.journal.package_identifier
    assert not reopened.save(AFTER)
    assert len(reopened.journal.records()) == 2


def test_metadata_save_rejects_oversized_xml(tmp_path, monkeypatch):
    from ph_backend import processing, workspace as workspace_module

    workspace = _workspace(tmp_path)
    monkeypatch.setattr(processing, "MAX_METADATA_BYTES", 8)
    monkeypatch.setattr(workspace_module, "MAX_METADATA_BYTES", 8)

    with pytest.raises(AuditError, match="metadata.xml exceeds"):
        workspace.save(b"x" * 9)

    assert workspace.metadata_path.read_bytes() == BEFORE
    assert workspace.journal.records() == []


def test_save_logs_individual_edits_in_one_transaction(tmp_path):
    workspace = _workspace(tmp_path)
    events = [{"event_type": "metadata modification", "operator": "editor-1",
               "detail": {"oldValue": "Old title", "newValue": "New title"}}]
    workspace.save(AFTER, events)
    records = workspace.journal.records()
    assert len(records) == 3
    assert records[1]["payload"]["agentIdentifier"] == "editor-1"
    assert len({record["payload"]["transactionIdentifier"] for record in records}) == 1


def test_external_changes_are_not_overwritten(tmp_path):
    workspace = _workspace(tmp_path)
    external = BEFORE.replace(b"Old title", b"External edit")
    workspace.metadata_path.write_bytes(external)
    with pytest.raises(AuditError, match="outside"):
        workspace.save(AFTER)
    assert workspace.metadata_path.read_bytes() == external
    assert workspace.journal.records() == []


def test_another_workspace_commit_invalidates_stale_session(tmp_path):
    first = _workspace(tmp_path)
    second = MetadataWorkspace(first.metadata_path, first.metadata_path.parent.parent,
                               operator="other-operator", software_version="0.1.0")
    assert first.save(AFTER)
    with pytest.raises(AuditError, match="outside"):
        second.save(BEFORE.replace(b"Old title", b"Stale session overwrite"))
    assert first.metadata_path.read_bytes() == AFTER
    assert first.journal.verify().sequence == 2


def test_journal_prepare_failure_never_changes_metadata(tmp_path):
    workspace = _workspace(tmp_path)
    with sqlite3.connect(workspace.journal.path) as connection:
        connection.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON events
                              BEGIN SELECT RAISE(ABORT, 'Injected audit failure'); END""")
    with pytest.raises(AuditError, match="Injected audit failure"):
        workspace.save(AFTER)
    assert workspace.metadata_path.read_bytes() == BEFORE
    assert workspace.journal.verify().sequence == 0


def test_write_failure_records_failure_and_retains_original(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)

    def fail_write(data):
        raise PermissionError("Injected metadata write failure")

    monkeypatch.setattr(workspace, "_write", fail_write)
    with pytest.raises(PermissionError):
        workspace.save(AFTER)
    assert workspace.metadata_path.read_bytes() == BEFORE
    assert workspace.journal.records()[-1]["payload"]["outcome"] == "failure"


def test_crash_after_replacement_recovers_exactly_once(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)

    def crash(*args, **kwargs):
        raise SystemExit("Injected process interruption")

    monkeypatch.setattr(workspace, "_finish", crash)
    with pytest.raises(SystemExit):
        workspace.save(AFTER)
    assert workspace.metadata_path.read_bytes() == AFTER
    assert len(workspace.journal.records()) == 1
    recovered = MetadataWorkspace(workspace.metadata_path, workspace.metadata_path.parent.parent,
                                  operator="recovery-operator", software_version="0.1.0")
    records = recovered.journal.records()
    assert len(records) == 2
    assert records[-1]["payload"]["eventType"] == "metadata commit recovery"
    assert records[-1]["payload"]["outcome"] == "success"
    MetadataWorkspace(workspace.metadata_path, workspace.metadata_path.parent.parent,
                      operator="recovery-operator", software_version="0.1.0")
    assert len(recovered.journal.records()) == 2


def test_crash_before_replacement_recovers_as_failure(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)

    def crash(data):
        raise SystemExit("Injected interruption before write")

    monkeypatch.setattr(workspace, "_write", crash)
    with pytest.raises(SystemExit):
        workspace.save(AFTER)
    recovered = MetadataWorkspace(workspace.metadata_path, workspace.metadata_path.parent.parent,
                                  operator="operator", software_version="0.1.0")
    assert recovered.metadata_path.read_bytes() == BEFORE
    assert recovered.journal.records()[-1]["payload"]["outcome"] == "failure"


def test_recovery_refuses_ambiguous_external_file(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.setattr(workspace, "_finish", lambda *args: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):
        workspace.save(AFTER)
    workspace.metadata_path.write_bytes(b"External replacement")
    with pytest.raises(AuditError, match="conflicts"):
        MetadataWorkspace(workspace.metadata_path, workspace.metadata_path.parent.parent,
                          operator="operator", software_version="0.1.0")
    assert workspace.metadata_path.read_bytes() == b"External replacement"


def test_discard_records_outcome_without_saving(tmp_path):
    workspace = _workspace(tmp_path)
    workspace.discard([{"detail": {"oldValue": "Old title", "newValue": "Uncommitted"}}])
    assert workspace.metadata_path.read_bytes() == BEFORE
    assert workspace.journal.records()[0]["payload"]["outcome"] == "discarded"


def test_operator_confirmation_required_before_side_effects(tmp_path):
    with pytest.raises(AuditError, match="Confirm"):
        MetadataWorkspace(tmp_path / "SIP/header/metadata.xml", tmp_path / "SIP",
                          operator="", software_version="0.1.0")
    assert list(tmp_path.iterdir()) == []


def test_invalid_event_is_rejected_before_metadata_replacement(tmp_path):
    workspace = _workspace(tmp_path)
    with pytest.raises(AuditError, match="Pending audit"):
        workspace.save(AFTER, [{"event_type": "", "detail": {}}])
    assert workspace.metadata_path.read_bytes() == BEFORE
    assert workspace.journal.records() == []


def test_audit_completion_failure_requires_recovery_not_discard(tmp_path):
    workspace = _workspace(tmp_path)
    with sqlite3.connect(workspace.journal.path) as connection:
        connection.execute("""CREATE TRIGGER fail_completion BEFORE INSERT ON events WHEN NEW.sequence=2
                              BEGIN SELECT RAISE(ABORT, 'Injected completion failure'); END""")
    with pytest.raises(AuditError, match="Injected completion"):
        workspace.save(AFTER)
    assert workspace.metadata_path.read_bytes() == AFTER
    assert workspace.journal.verify().sequence == 1
    with pytest.raises(AuditError, match="Recover"):
        workspace.discard([{"detail": {}}])
    with sqlite3.connect(workspace.journal.path) as connection:
        connection.execute("DROP TRIGGER fail_completion")
    recovered = MetadataWorkspace(workspace.metadata_path, workspace.metadata_path.parent.parent,
                                  operator="operator", software_version="0.1.0")
    assert recovered.journal.records()[-1]["payload"]["outcome"] == "success"


def test_external_replacement_during_completion_is_not_success(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    write = workspace._write

    def external_after_write(data):
        write(data)
        workspace.metadata_path.write_bytes(b"External conflict")

    monkeypatch.setattr(workspace, "_write", external_after_write)
    with pytest.raises(AuditError, match="during commit"):
        workspace.save(AFTER)
    assert len(workspace.journal.records()) == 1


def test_recovery_intent_tampering_is_detected(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)

    def crash(data):
        raise SystemExit()

    monkeypatch.setattr(workspace, "_write", crash)
    with pytest.raises(SystemExit):
        workspace.save(AFTER)
    with sqlite3.connect(workspace.journal.path) as connection:
        connection.execute("UPDATE metadata_prepared SET operator='forged-operator'")
    with pytest.raises(AuditError, match="intent does not match"):
        MetadataWorkspace(workspace.metadata_path, workspace.metadata_path.parent.parent,
                          operator="operator", software_version="0.1.0")
    assert workspace.metadata_path.read_bytes() == BEFORE


def test_baseline_tampering_cannot_hide_external_metadata(tmp_path):
    import hashlib

    workspace = _workspace(tmp_path)
    workspace.save(AFTER)
    workspace.metadata_path.write_bytes(BEFORE)
    with sqlite3.connect(workspace.journal.path) as connection:
        connection.execute("UPDATE metadata_state SET baseline_digest=?", (hashlib.sha256(BEFORE).hexdigest(),))
    with pytest.raises(AuditError, match="baseline does not match"):
        MetadataWorkspace(workspace.metadata_path, workspace.metadata_path.parent.parent,
                          operator="operator", software_version="0.1.0")
    assert workspace.metadata_path.read_bytes() == BEFORE