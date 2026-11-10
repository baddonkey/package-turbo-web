from pathlib import Path

import pytest

from ph_backend.audit import AuditError
from ph_backend.undo import UndoHistory


def setup_history(tmp_path, max_steps=20):
    root = tmp_path / "SIP"
    (root / "header").mkdir(parents=True)
    (root / "content").mkdir()
    (root / "header/metadata.xml").write_bytes(b"<paket/>")
    companion = tmp_path / "SIP.package-turbo"
    companion.mkdir()
    history = UndoHistory(companion, max_bytes=1024 * 1024, max_steps=max_steps)
    return root, history


def test_snapshot_history_tracks_undo_redo_and_branches(tmp_path):
    root, history = setup_history(tmp_path)
    before = history.capture(root, b"<paket/>")
    after = history.capture(root, b"<paket><titel>First</titel></paket>")
    assert history.record("Edit", before, after)
    entry = history.undo_target()
    assert entry.before.xml == b"<paket/>"
    history.position -= 1
    assert history.can_redo and not history.can_undo
    assert history.redo_target().identifier == entry.identifier
    branch = history.capture(root, b"<paket><titel>Branch</titel></paket>")
    baseline = history.capture(root, b"<paket/>")
    history.record("Branch", baseline, branch)
    assert not history.can_redo
    assert not after.directory.exists()


def test_snapshots_include_file_bytes_and_are_verified(tmp_path):
    root, history = setup_history(tmp_path)
    (root / "content/file.txt").write_bytes(b"original bytes")
    snapshot = history.capture(root, b"<paket/>")
    assert (snapshot.directory / "content/file.txt").read_bytes() == b"original bytes"
    (snapshot.directory / "content/file.txt").chmod(0o600)
    (snapshot.directory / "content/file.txt").write_bytes(b"changed bytes")
    with pytest.raises(AuditError, match="snapshot has changed"):
        history.check(snapshot)


def test_undo_limit_and_clear_do_not_delete_audit(tmp_path):
    root, history = setup_history(tmp_path, max_steps=1)
    audit = history.companion / "journal.sqlite3"
    audit.write_bytes(b"preservation journal placeholder")
    first = history.capture(root, b"<paket/>")
    second = history.capture(root, b"<paket><titel>Two</titel></paket>")
    history.record("First", first, second)
    third = history.capture(root, second.xml)
    fourth = history.capture(root, b"<paket><titel>Three</titel></paket>")
    history.record("Second", third, fourth)
    assert len(history.entries) == 1 and not first.directory.exists()
    history.clear()
    assert not history.can_undo and not history.can_redo
    assert audit.read_bytes() == b"preservation journal placeholder"


def test_undo_storage_limit_has_no_source_side_effects(tmp_path):
    root, history = setup_history(tmp_path)
    history.max_bytes = 1
    with pytest.raises(AuditError, match="workspace limit"):
        history.capture(root, b"<paket/>")
    assert (root / "header/metadata.xml").read_bytes() == b"<paket/>"
    assert not history.directory.exists()


def test_noop_does_not_enter_undo_history(tmp_path):
    root, history = setup_history(tmp_path)
    before = history.capture(root, b"<paket/>")
    after = history.capture(root, b"<paket/>")
    assert not history.record("No change", before, after)
    assert not history.can_undo


def test_unchanged_content_is_reused_but_source_is_not_linked(tmp_path):
    import os
    from ph_backend.processing import tree_size

    root, history = setup_history(tmp_path)
    data = b"content" * 10000
    (root / "content/large.bin").write_bytes(data)
    first = history.capture(root, b"<paket/>")
    before = tree_size(history.companion)
    second = history.capture(root, b"<paket><titel>Only metadata</titel></paket>")
    assert tree_size(history.companion) - before < 1024
    assert os.path.samefile(first.directory / "content/large.bin", second.directory / "content/large.bin")
    assert not os.path.samefile(root / "content/large.bin", first.directory / "content/large.bin")
    (root / "content/large.bin").write_bytes(b"new source bytes")
    assert (first.directory / "content/large.bin").read_bytes() == data


def test_releasing_shared_snapshot_preserves_surviving_objects(tmp_path):
    root, history = setup_history(tmp_path)
    (root / "content/file.txt").write_bytes(b"shared")
    first = history.capture(root, b"<paket/>")
    second = history.capture(root, b"<paket><titel>New</titel></paket>")
    history.release(first)
    history.check(second)
    assert (second.directory / "content/file.txt").read_bytes() == b"shared"
    assert not (second.directory / "content/file.txt").stat().st_mode & 0o200


def test_metadata_only_snapshot_does_not_copy_unchanged_content(tmp_path, monkeypatch):
    import ph_backend.processing as processing

    root, history = setup_history(tmp_path)
    (root / "content/file.bin").write_bytes(b"reused bytes")
    history.capture(root, b"<paket/>")
    copied = []
    copy = processing._copy_bytes

    def observe(*args, **kwargs):
        copied.append(args[0])
        return copy(*args, **kwargs)

    monkeypatch.setattr(processing, "_copy_bytes", observe)
    history.capture(root, b"<paket><titel>Only XML</titel></paket>")
    assert copied == []


def test_unexpected_object_is_not_silently_removed(tmp_path):
    root, history = setup_history(tmp_path)
    snapshot = history.capture(root, b"<paket/>")
    unexpected = history.objects / "personal.txt"
    unexpected.write_bytes(b"unknown data")
    with pytest.raises(AuditError, match="Unexpected object"):
        history.clear()
    assert unexpected.read_bytes() == b"unknown data"