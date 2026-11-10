from pathlib import Path

import pytest

from ph_backend import files, package_factory
from ph_backend.audit import AuditError
from ph_backend.file_workspace import FileWorkspace, manifest, recover_file_commit
from ph_backend.workspace import MetadataWorkspace


def setup_workspace(tmp_path):
    pkg = package_factory.create_package(tmp_path, "SIP_20261104_Test_Files", "Test")
    metadata = MetadataWorkspace(pkg.metadata_path, pkg.root_dir, operator="operator", software_version="0.1.0")
    workspace = FileWorkspace(metadata)
    content = next(pkg.children(pkg.table_of_contents, "ordner")[index] for index in range(2)
                   if pkg.text(pkg.children(pkg.table_of_contents, "ordner")[index], "name") == "content")
    return pkg, workspace, content


def test_import_is_staged_then_committed_with_fixity(tmp_path):
    pkg, workspace, content = setup_workspace(tmp_path)
    source = tmp_path / "source.txt"
    source.write_bytes(b"staged content")
    before = manifest(pkg.root_dir)
    workspace.stage_operation(pkg, lambda package: files.import_paths(package, content, [source]), [])
    assert manifest(pkg.root_dir) == before
    assert not (pkg.root_dir / "content/source.txt").exists()
    assert (pkg.file_root / "content/source.txt").read_bytes() == b"staged content"
    workspace.commit(pkg, [])
    assert (pkg.root_dir / "content/source.txt").read_bytes() == b"staged content"
    assert manifest(workspace.backup) == before
    records = workspace.journal.records()
    assert any(record["payload"]["eventType"] == "file modification"
               and record["payload"]["detail"]["path"] == "content/source.txt" for record in records)
    assert records[-1]["payload"]["eventType"] == "package commit"


def test_discard_never_modifies_source_package(tmp_path):
    pkg, workspace, content = setup_workspace(tmp_path)
    before = manifest(pkg.root_dir)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "staged"), [])
    workspace.discard()
    assert manifest(pkg.root_dir) == before
    assert not workspace.stage.exists()
    assert workspace.journal.records()[-1]["payload"]["outcome"] == "discarded"


def test_failed_operation_restores_stage_and_metadata(tmp_path):
    pkg, workspace, content = setup_workspace(tmp_path)
    before = manifest(pkg.root_dir)

    def fail(package):
        files.create_folder(package, content, "partial")
        raise PermissionError("injected operation error")

    with pytest.raises(PermissionError):
        workspace.stage_operation(pkg, fail, [])
    assert manifest(pkg.root_dir) == before
    assert not workspace.stage.exists()
    assert "partial" not in [pkg.text(element, "name") for element in pkg.iter_kind("ordner")]


def test_interruption_between_directory_renames_recovers_original(tmp_path, monkeypatch):
    pkg, workspace, content = setup_workspace(tmp_path)
    before = manifest(pkg.root_dir)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    rename = Path.rename

    def interrupt(path, target):
        if path == workspace.stage:
            raise SystemExit("injected crash between renames")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", interrupt)
    with pytest.raises(SystemExit):
        workspace.commit(pkg, [])
    assert not pkg.root_dir.exists()
    monkeypatch.setattr(Path, "rename", rename)
    recover_file_commit(pkg.root_dir, "recovery-operator", "0.1.0")
    assert manifest(pkg.root_dir) == before
    assert workspace.journal.records()[-1]["payload"]["outcome"] == "failure"


def test_post_swap_interruption_recovers_success_once(tmp_path, monkeypatch):
    pkg, workspace, content = setup_workspace(tmp_path)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    monkeypatch.setattr(workspace, "_finish", lambda *args: (_ for _ in ()).throw(SystemExit()))
    with pytest.raises(SystemExit):
        workspace.commit(pkg, [])
    assert (pkg.root_dir / "content/new").is_dir()
    recover_file_commit(pkg.root_dir, "recovery-operator", "0.1.0")
    count = workspace.journal.verify().sequence
    recover_file_commit(pkg.root_dir, "recovery-operator", "0.1.0")
    assert workspace.journal.verify().sequence == count
    assert workspace.journal.records()[-1]["payload"]["outcome"] == "success"


def test_external_file_change_prevents_commit(tmp_path):
    pkg, workspace, content = setup_workspace(tmp_path)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    (pkg.root_dir / "content/external.txt").write_bytes(b"external change")
    with pytest.raises(AuditError, match="outside"):
        workspace.commit(pkg, [])
    assert (pkg.root_dir / "content/external.txt").read_bytes() == b"external change"
    assert not (pkg.root_dir / "content/new").exists()


def test_storage_limit_blocks_staging_before_mutation(tmp_path):
    pkg, workspace, content = setup_workspace(tmp_path)
    workspace.max_bytes = 1
    with pytest.raises(AuditError, match="storage limit"):
        workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    assert not workspace.stage.exists()


def test_multiple_commits_retain_prior_rollback_packages(tmp_path):
    pkg, workspace, content = setup_workspace(tmp_path)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "first"), [])
    workspace.commit(pkg, [])
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "second"), [])
    workspace.commit(pkg, [])
    assert (pkg.root_dir / "content/first").is_dir()
    assert (pkg.root_dir / "content/second").is_dir()
    assert (workspace.backup / "content/first").is_dir()
    assert len(list(workspace.directory.glob("rollback-*"))) == 2


def test_forged_draft_record_is_rejected(tmp_path):
    import sqlite3

    pkg, workspace, content = setup_workspace(tmp_path)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    with sqlite3.connect(workspace.journal.path) as connection:
        connection.execute("UPDATE package_draft SET events='[{}]'")
    with pytest.raises(AuditError, match="preservation history"):
        workspace.commit(pkg, [])
    assert not (pkg.root_dir / "content/new").exists()


def test_copy_failure_cleans_partial_stage_for_retry(tmp_path, monkeypatch):
    import shutil

    pkg, workspace, content = setup_workspace(tmp_path)
    copytree = shutil.copytree

    def fail_copy(source, destination, *args, **kwargs):
        Path(destination).mkdir(parents=True, exist_ok=True)
        raise OSError("injected disk failure")

    monkeypatch.setattr(shutil, "copytree", fail_copy)
    with pytest.raises(OSError):
        workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    assert not workspace.stage.exists()
    monkeypatch.setattr(shutil, "copytree", copytree)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    assert (workspace.stage / "content/new").is_dir()


def test_discard_does_not_overwrite_external_source_changes(tmp_path):
    pkg, workspace, content = setup_workspace(tmp_path)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    (pkg.root_dir / "content/external.txt").write_bytes(b"external change")
    workspace.discard()
    assert (pkg.root_dir / "content/external.txt").read_bytes() == b"external change"
    assert not (pkg.root_dir / "content/new").exists()


def test_external_edit_during_swap_is_not_overwritten(tmp_path, monkeypatch):
    pkg, workspace, content = setup_workspace(tmp_path)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    rename = Path.rename

    def modify_backup(path, target):
        result = rename(path, target)
        if path == pkg.root_dir:
            (workspace.backup / "content/external.txt").write_bytes(b"external bytes")
        return result

    monkeypatch.setattr(Path, "rename", modify_backup)
    with pytest.raises(AuditError, match="External changes"):
        workspace.commit(pkg, [])
    assert (pkg.root_dir / "content/external.txt").read_bytes() == b"external bytes"
    assert not (pkg.root_dir / "content/new").exists()


def test_draft_publication_failure_restores_recorded_stage(tmp_path, monkeypatch):
    pkg, workspace, content = setup_workspace(tmp_path)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
    before = manifest(workspace.stage)
    pkg.set_text(pkg.delivery, "ablieferungsnummer", "later edit")
    publish = workspace._publish_draft

    def fail(*args):
        raise AuditError("injected publication failure")

    monkeypatch.setattr(workspace, "_publish_draft", fail)
    with pytest.raises(AuditError):
        workspace.persist(pkg, [])
    assert manifest(workspace.stage) == before
    monkeypatch.setattr(workspace, "_publish_draft", publish)
    workspace.persist(pkg, [])
    workspace.commit(pkg, [])
    assert b"later edit" in pkg.metadata_path.read_bytes()


def test_commit_preserves_read_only_staged_file_mode(tmp_path):
    pkg, workspace, content = setup_workspace(tmp_path)
    source = pkg.root_dir / "content/readonly.txt"
    source.write_bytes(b"read-only bytes")
    source.chmod(0o444)
    try:
        workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "new"), [])
        workspace.commit(pkg, [])
        assert source.read_bytes() == b"read-only bytes"
        assert not source.stat().st_mode & 0o200
    finally:
        if source.exists():
            source.chmod(0o666)
        retained = workspace.backup / "content/readonly.txt"
        if retained.exists():
            retained.chmod(0o666)


def test_rename_preserves_unrelated_intermediate_name_file(tmp_path):
    pkg, workspace, content = setup_workspace(tmp_path)
    source = tmp_path / "input.txt"
    source.write_bytes(b"rename this file")
    files.import_paths(pkg, content, [source])
    unrelated = pkg.root_dir / "content/input.txt.renaming"
    unrelated.write_bytes(b"preserve unrelated bytes")
    file_element = pkg.children(content, "datei")[0]
    workspace.stage_operation(pkg, lambda package: files.rename(package, file_element, "renamed.txt"), [])
    assert (workspace.stage / "content/input.txt.renaming").read_bytes() == b"preserve unrelated bytes"
    workspace.commit(pkg, [])
    assert unrelated.read_bytes() == b"preserve unrelated bytes"
    assert (pkg.root_dir / "content/renamed.txt").read_bytes() == source.read_bytes()


def test_blocked_rollback_retains_prior_working_checkpoint(tmp_path, monkeypatch):
    import ph_backend.file_workspace as module

    pkg, workspace, content = setup_workspace(tmp_path)
    workspace.stage_operation(pkg, lambda package: files.create_folder(package, content, "earlier"), [])
    before = manifest(workspace.stage)
    remove = module.remove_staging_tree

    def blocked(path):
        if path == workspace.stage:
            raise PermissionError("Injected locked working file")
        return remove(path)

    def fail(package):
        files.create_folder(package, content, "partial")
        raise OSError("Injected operation failure")

    monkeypatch.setattr(module, "remove_staging_tree", blocked)
    with pytest.raises(AuditError, match="checkpoint is retained"):
        workspace.stage_operation(pkg, fail, [])
    checkpoints = list(workspace.directory.glob("operation-*/package"))
    assert len(checkpoints) == 1 and manifest(checkpoints[0]) == before
    assert not (pkg.root_dir / "content/earlier").exists()