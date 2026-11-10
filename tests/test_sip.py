import pytest

from ph_backend import timespan
from ph_backend.sip import InvalidPackageError, SipPackage, localname


def test_open_folder_and_query(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    assert pkg.delivery_type == "GEVER"
    assert pkg.text(pkg.provenance, "aktenbildnerName") == "Testamt"
    dossiers = list(pkg.iter_kind("dossier"))
    assert [pkg.display_name(d) for d in dossiers] == ["Dossier mit Datei", "Leer"]
    assert pkg.referenced_ids() == {"_file1"}
    file_el = pkg.file_by_id("_file1")
    assert pkg.relpath(file_el).as_posix() == "content/d0001/p000001.pdf"
    assert pkg.fs_path(file_el).is_file()


def test_open_invalid_folder(tmp_path):
    with pytest.raises(InvalidPackageError):
        SipPackage.open_folder(tmp_path)


def test_open_rejects_oversized_metadata(tmp_path, monkeypatch):
    from ph_backend import package_factory, processing

    pkg = package_factory.create_package(tmp_path, "SIP_20261109_Test_LargeMetadata", "Test")
    monkeypatch.setattr(processing, "MAX_METADATA_BYTES", 16)

    with pytest.raises(InvalidPackageError, match="metadata.xml exceeds"):
        SipPackage.open_folder(pkg.root_dir)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_transfer_container_is_not_opened_as_sip_folder(tmp_path, version):
    import zipfile
    from pathlib import Path
    from ph_backend import package_factory

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_ContainerBoundary_{version}", "Test", version=version)
    assert SipPackage.open_folder(pkg.root_dir).name == pkg.root_dir.name

    container_path = tmp_path / "delivery.zip"
    with zipfile.ZipFile(container_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in pkg.root_dir.rglob("*"):
            if path.is_file():
                archive.write(path, (Path(pkg.root_dir.name) / path.relative_to(pkg.root_dir)).as_posix())

    with pytest.raises(InvalidPackageError):
        SipPackage.open_folder(container_path)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("package_count", [1, 2])
def test_one_or_more_sips_open_as_distinct_top_level_packages(tmp_path, version, package_count):
    from ph_backend import package_factory

    delivery_root = tmp_path / "delivery"
    delivery_root.mkdir()
    package_names = [f"SIP_20261108_Test_{index}_{version}" for index in range(1, package_count + 1)]
    packages = [package_factory.create_package(delivery_root, name, "Test", version=version)
                for name in package_names]

    assert {path.name for path in delivery_root.iterdir() if path.is_dir()} == set(package_names)
    assert {SipPackage.open_folder(package.root_dir).name for package in packages} == set(package_names)
    with pytest.raises(InvalidPackageError):
        SipPackage.open_folder(delivery_root)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_metadata_is_xml_stored_under_header_for_each_release(tmp_path, version, delivery_type):
    from xml.etree import ElementTree
    from ph_backend import package_factory

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261107_Test_MetadataLocation_{version}_{delivery_type}", "Test",
        delivery_type=delivery_type, version=version)
    metadata = pkg.file_root / "header" / "metadata.xml"

    assert metadata.is_file()
    assert ElementTree.parse(metadata).getroot().tag == pkg.root.tag
    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    assert reopened.metadata_path.relative_to(reopened.file_root).as_posix() == "header/metadata.xml"

    metadata.rename(pkg.file_root / "content" / "metadata.xml")
    with pytest.raises(InvalidPackageError, match=r"Datei metadata\.xml fehlt"):
        SipPackage.open_folder(pkg.root_dir)


def test_open_standalone_metadata_is_read_only(sip_dir, tmp_path):
    copy = tmp_path / "metadata.xml"
    copy.write_bytes((sip_dir / "header" / "metadata.xml").read_bytes())
    pkg = SipPackage.open_metadata(copy)
    assert pkg.read_only and pkg.root_dir is None
    assert not SipPackage.open_metadata(sip_dir / "header" / "metadata.xml").read_only


def test_set_text_inserts_in_schema_order_and_saves(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    dossier = next(pkg.iter_kind("dossier"))
    pkg.set_text(dossier, "schutzfrist", "50")
    pkg.set_text(dossier, "inhalt", "Beschreibung")
    names = [localname(c) for c in pkg.children(dossier)]
    assert names.index("titel") < names.index("inhalt") < names.index("entstehungszeitraum")
    assert names.index("aktenzeichen") < names.index("schutzfrist") < names.index("dokument")
    pkg.save()
    reopened = SipPackage.open_folder(sip_dir)
    assert reopened.text(next(reopened.iter_kind("dossier")), "schutzfrist") == "50"


def test_set_text_empty_removes_optional_element(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    delivery = pkg.delivery
    pkg.set_text(delivery, "ablieferungsnummer", "")
    assert pkg.child(delivery, "ablieferungsnummer") is None


def test_time_span_roundtrip(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    dossier = next(pkg.iter_kind("dossier"))
    pkg.set_time_span(dossier, "entstehungszeitraum", "2001", "")
    assert pkg.time_span(dossier, "entstehungszeitraum") == ("2001", "keine Angabe")
    with pytest.raises(timespan.DateFormatError):
        pkg.set_time_span(dossier, "entstehungszeitraum", "2001-02", "")
    with pytest.raises(timespan.DateFormatError):
        pkg.set_time_span(dossier, "entstehungszeitraum", "31.02.2001", "")
    pkg.set_time_span(dossier, "entstehungszeitraum", "ca. 2001", "31.12.2002")
    assert pkg.time_span(dossier, "entstehungszeitraum") == ("2001", "2002-12-31")
    pkg.set_time_span(dossier, "entstehungszeitraum", "2001", "2002")
    assert pkg.time_span_display(dossier, "entstehungszeitraum") == ("2001", "2002")


def test_unknown_schema_version_is_read_only(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    pkg.root.set("schemaVersion", "99.0")
    pkg.save()
    unknown = SipPackage.open_folder(sip_dir)
    assert unknown.read_only and not unknown.version_supported
    with pytest.raises(PermissionError):
        unknown.save()


def test_save_rejected_when_read_only(sip_dir):
    pkg = SipPackage.open_folder(sip_dir, read_only=True)
    with pytest.raises(PermissionError):
        pkg.save()


def test_fs_path_refuses_traversal(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    file_el = pkg.file_by_id("_file2")
    pkg.child(file_el, "name").text = "../../../../outside.txt"
    pkg.invalidate_file_index()
    assert pkg.fs_path(file_el) is None


def test_zip(sip_dir, tmp_path):
    import zipfile

    pkg = SipPackage.open_folder(sip_dir)
    target = tmp_path / "out.zip"
    pkg.zip_to(target)
    names = zipfile.ZipFile(target).namelist()
    assert f"{sip_dir.name}/header/metadata.xml" in names


def test_zip_rejects_symlink_entries(sip_dir, tmp_path):
    from ph_backend.sip import InvalidPackageError

    pkg = SipPackage.open_folder(sip_dir)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside package", encoding="utf-8")
    link = pkg.root_dir / "header" / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation unavailable: {exc}")

    with pytest.raises(InvalidPackageError, match="Linked files"):
        pkg.zip_to(tmp_path / "out.zip")

    assert not (tmp_path / "out.zip").exists()


def test_zip_rejects_destination_inside_package(sip_dir):
    from ph_backend.sip import InvalidPackageError

    pkg = SipPackage.open_folder(sip_dir)

    with pytest.raises(InvalidPackageError, match="outside the SIP"):
        pkg.zip_to(pkg.root_dir / "inside.zip")

    assert not (pkg.root_dir / "inside.zip").exists()


@pytest.mark.parametrize("value,lower,upper", [
    ("2006", "2006-01-01", "2006-12-31"),
    ("2008-02", "2008-02-01", "2008-02-29"),
    ("2010-05-17", "2010-05-17", "2010-05-17"),
])
def test_timespan_parsing(value, lower, upper):
    assert timespan.parse_lower(value).isoformat() == lower
    assert timespan.parse_upper(value).isoformat() == upper


def test_timespan_open():
    assert timespan.is_open("keine Angabe") and timespan.is_open("") and not timespan.is_open("2001")
