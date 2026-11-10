import hashlib

import pytest

from ph_backend.processing import (CHUNK_SIZE, TaskCancelled, TaskControl, copy_file, copy_tree,
                                   current_control, digest_file, processing_context)


def test_copy_and_hash_report_bytes(tmp_path):
    source = tmp_path / "source.bin"
    data = b"a" * (CHUNK_SIZE * 2 + 17)
    source.write_bytes(data)
    reports = []
    control = TaskControl(lambda phase, completed, total: reports.append((phase, completed, total)))
    with processing_context(control):
        copy_file(source, tmp_path / "target.bin")
        assert digest_file(source, "sha256") == hashlib.sha256(data).hexdigest()
    assert (tmp_path / "target.bin").read_bytes() == data
    assert reports[-1][1:] == (len(data), len(data))
    assert current_control() is None


def test_copy_cancellation_stops_at_chunk_boundary(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"a" * (CHUNK_SIZE * 4))
    target = tmp_path / "target.bin"
    control = TaskControl()
    control.progress = lambda phase, completed, total: control.cancel() if completed >= CHUNK_SIZE else None
    with processing_context(control), pytest.raises(TaskCancelled):
        copy_file(source, target)
    assert source.stat().st_size == CHUNK_SIZE * 4
    assert target.stat().st_size < source.stat().st_size


def test_protected_commit_refuses_cancellation():
    control = TaskControl()
    control.protect()
    assert not control.cancel() and not control.can_cancel
    control.checkpoint()
    cancelled = TaskControl()
    cancelled.cancel()
    with pytest.raises(TaskCancelled):
        cancelled.protect()
    cancelled.protect(check_cancel=False)
    cancelled.checkpoint()


def test_tree_copy_progress_and_metadata(tmp_path):
    source = tmp_path / "source"
    (source / "folder").mkdir(parents=True)
    (source / "folder/file.txt").write_bytes(b"bytes")
    control = TaskControl()
    with processing_context(control):
        copy_tree(source, tmp_path / "target")
    assert (tmp_path / "target/folder/file.txt").read_bytes() == b"bytes"
    assert control.completed == control.total == 5


def test_context_nesting_and_large_progress_values():
    outer, inner = TaskControl(), TaskControl()
    with processing_context(outer):
        with processing_context(inner):
            assert current_control() is inner
        assert current_control() is outer
        outer.start("Large file", 5 * 1024 ** 3)
        outer.advance(4 * 1024 ** 3)
        assert outer.completed == 4 * 1024 ** 3
    assert current_control() is None


def test_rollback_section_ignores_cancel_then_restores_it():
    control = TaskControl()
    control.cancel()
    with control.critical_section(check_cancel=False):
        control.checkpoint()
        assert not control.cancel()
    with pytest.raises(TaskCancelled):
        control.checkpoint()


def test_metadata_bytes_hash_and_read_are_cancellable(tmp_path):
    from ph_backend.processing import digest_bytes, read_bytes

    data = b"x" * (CHUNK_SIZE * 3)
    path = tmp_path / "metadata.xml"
    path.write_bytes(data)
    assert read_bytes(path) == data
    assert digest_bytes(data) == hashlib.sha256(data).hexdigest()
    control = TaskControl()
    control.progress = lambda phase, completed, total: control.cancel() if completed else None
    with processing_context(control), pytest.raises(TaskCancelled):
        digest_bytes(data)


def test_streamed_io_rejects_non_regular_sources(tmp_path):
    from ph_backend.audit import AuditError
    from ph_backend.processing import read_bytes

    directory = tmp_path / "directory"
    directory.mkdir()
    for operation in (lambda: copy_file(directory, tmp_path / "target"),
                      lambda: digest_file(directory, "sha256"), lambda: read_bytes(directory)):
        with pytest.raises(AuditError, match="regular file"):
            operation()