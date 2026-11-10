import pytest

from ph_backend import files, package_factory
from ph_backend.audit import AuditError
from ph_backend.file_workspace import FileWorkspace, manifest
from ph_backend.retention import RetentionManager
from ph_backend.workspace import MetadataWorkspace


def retained_packages(tmp_path):
    pkg = package_factory.create_package(tmp_path, "SIP_20261105_Test_Retention", "Test")
    metadata = MetadataWorkspace(pkg.metadata_path, pkg.root_dir, operator="operator", software_version="0.1.0")
    workspace = FileWorkspace(metadata)
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    for index in range(3):
        workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, f"folder{index}"), [])
        workspace.commit(pkg, [])
    return pkg, metadata, workspace, RetentionManager(metadata)


def test_preview_is_read_only_and_cleanup_preserves_source_latest_and_audit(tmp_path):
    pkg, metadata, workspace, manager = retained_packages(tmp_path)
    before = manifest(pkg.root_dir)
    latest = manifest(workspace.backup)
    records = metadata.journal.records()
    preview = manager.preview(keep_count=1, minimum_age_days=0)
    assert len(preview["candidates"]) == 2 and preview["reclaimBytes"] > 0
    assert metadata.journal.records() == records
    reclaimed = manager.apply(preview)
    assert reclaimed == preview["reclaimBytes"]
    assert manifest(pkg.root_dir) == before and manifest(workspace.backup) == latest
    assert metadata.journal.records()[:len(records)] == records
    assert metadata.journal.records()[-1]["payload"]["eventType"] == "storage cleanup"


def test_default_policy_keeps_recent_backups_and_unknown_data(tmp_path):
    pkg, metadata, workspace, manager = retained_packages(tmp_path)
    unknown = workspace.directory / ("rollback-" + "a" * 32)
    unknown.mkdir()
    (unknown / "personal.txt").write_bytes(b"not managed")
    preview = manager.preview()
    assert preview["candidates"] == []
    assert any(item["path"] == unknown.name for item in preview["protected"])
    assert (unknown / "personal.txt").read_bytes() == b"not managed"


def test_stale_preview_and_modified_copy_are_rejected(tmp_path):
    pkg, metadata, workspace, manager = retained_packages(tmp_path)
    preview = manager.preview(1, 0)
    metadata.journal.append("test event", agent_identifier="operator", software_version="0.1.0")
    with pytest.raises(AuditError, match="stale"):
        manager.apply(preview)
    preview = manager.preview(1, 0)
    target = workspace.directory / preview["candidates"][0]["path"] / "content/external.txt"
    target.write_bytes(b"external change")
    with pytest.raises(AuditError, match="changed"):
        manager.apply(preview)
    assert target.exists()


def test_active_draft_undo_and_checkpoints_are_never_candidates(tmp_path):
    pkg, metadata, workspace, manager = retained_packages(tmp_path)
    for name in ("undo-active", "operation-recovery"):
        (workspace.directory / name).mkdir()
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content(package), "draft"), [])
    preview = manager.preview(1, 0)
    assert {item["path"] for item in preview["candidates"]}.isdisjoint(
        {"staged-package", "rollback-package", "undo-active", "operation-recovery"})
    manager.apply(preview)
    assert (workspace.stage / "content/draft").is_dir()
    assert workspace.backup.exists()


def content(pkg):
    return next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")


def test_prepared_transaction_blocks_cleanup(tmp_path):
    pkg, metadata, workspace, manager = retained_packages(tmp_path)
    with metadata.journal._connection("rw") as connection:
        connection.execute("INSERT INTO package_prepared VALUES (1, 'pending', '{}', '{}', '[]', 'operator', '0.1.0')")
        connection.commit()
    with pytest.raises(AuditError, match="Recovery"):
        manager.preview(1, 0)


def test_modified_preview_cannot_bypass_retention_policy(tmp_path):
    pkg, metadata, workspace, manager = retained_packages(tmp_path)
    preview = manager.preview()
    preview["keepCount"] = 1
    with pytest.raises(AuditError, match="exact issued preview"):
        manager.apply(preview)


def test_cleanup_failure_records_completed_targets_without_touching_source(tmp_path, monkeypatch):
    import ph_backend.retention as module

    pkg, metadata, workspace, manager = retained_packages(tmp_path)
    before = manifest(pkg.root_dir)
    preview = manager.preview(1, 0)
    remove = module.remove_staging_tree
    calls = []

    def fail_second(path):
        calls.append(path)
        if len(calls) == 2:
            raise PermissionError("Injected cleanup failure")
        return remove(path)

    monkeypatch.setattr(module, "remove_staging_tree", fail_second)
    with pytest.raises(PermissionError):
        manager.apply(preview)
    outcome = metadata.journal.records()[-1]["payload"]
    assert outcome["outcome"] == "failure" and len(outcome["detail"]["completed"]) == 1
    assert manifest(pkg.root_dir) == before


def test_cleanup_preserves_active_undo_and_redo_bytes(tmp_path):
    from ph_backend.undo import UndoHistory

    pkg, metadata, workspace, manager = retained_packages(tmp_path)
    history = UndoHistory(workspace.directory, max_bytes=1024 ** 3)
    before = history.capture(pkg.root_dir, pkg.metadata_path.read_bytes())
    changed = before.xml.replace(b"<ablieferndeStelle>Test", b"<ablieferndeStelle>Updated")
    after = history.capture(pkg.root_dir, changed)
    history.record("Metadata", before, after)
    preview = manager.preview(1, 0)
    manager.apply(preview)
    history.check(history.undo_target().before)
    history.position -= 1
    history.check(history.redo_target().after)