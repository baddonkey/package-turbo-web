import hashlib
import stat
import zipfile

import pytest

from tools.prepare_kost_fixture import FIXTURE_NAME, _member_target, provision_fixture


def _archive(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data, mode in members:
            info = zipfile.ZipInfo(name.replace("\\", "/"))
            info.filename = name
            if mode:
                info.external_attr = mode << 16
            archive.writestr(info, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fixture_provisioner_verifies_and_extracts_idempotently(tmp_path):
    archive = tmp_path / "fixture.zip"
    digest = _archive(archive, [("header/metadata.xml", b"<metadata/>", 0), ("content/", b"", 0)])
    samples = tmp_path / "samples"

    target = provision_fixture(samples, archive, digest)

    assert target == samples / FIXTURE_NAME
    assert (target / "header" / "metadata.xml").read_bytes() == b"<metadata/>"
    assert provision_fixture(samples, archive, digest) == target


def test_fixture_provisioner_rejects_hash_mismatch(tmp_path):
    archive = tmp_path / "fixture.zip"
    _archive(archive, [("header/metadata.xml", b"<metadata/>", 0), ("content/", b"", 0)])

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        provision_fixture(tmp_path / "samples", archive, "0" * 64)

    assert not (tmp_path / "samples" / FIXTURE_NAME).exists()


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "C:/drive.txt"])
def test_fixture_provisioner_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / "fixture.zip"
    digest = _archive(archive, [(name, b"bad", 0), ("header/metadata.xml", b"<metadata/>", 0),
                                ("content/", b"", 0)])

    with pytest.raises(ValueError, match="Unsafe ZIP member path"):
        provision_fixture(tmp_path / "samples", archive, digest)

    assert not (tmp_path / "escape.txt").exists()


def test_fixture_provisioner_rejects_raw_backslash_member(tmp_path):
    info = zipfile.ZipInfo("safe-name")
    info.filename = "bad\\path.txt"

    with pytest.raises(ValueError, match="Unsafe ZIP member path"):
        _member_target(tmp_path, info, set())


def test_fixture_provisioner_rejects_symlink_members(tmp_path):
    archive = tmp_path / "fixture.zip"
    digest = _archive(archive, [("header/metadata.xml", b"<metadata/>", 0), ("content/", b"", 0),
                                ("content/link", b"target", stat.S_IFLNK | 0o777)])

    with pytest.raises(ValueError, match="Symbolic links"):
        provision_fixture(tmp_path / "samples", archive, digest)