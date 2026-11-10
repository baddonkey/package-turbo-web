import pytest

from ph_backend import file_formats
from ph_backend.sip import SipPackage
from ph_backend.validation import CHECKS, ERROR, WARNING, Validator, summarize


def _checks(issues):
    return {i["checkId"] for i in issues}


def test_continuous_checks_on_synthetic_sip(sip_dir):
    issues = Validator(SipPackage.open_folder(sip_dir)).run(full=False)
    found = _checks(issues)
    assert {"empty_folders", "unassigned_files", "empty_dossiers", "dossier_title", "open_time_span"} <= found
    assert not any(c.full_only for c in CHECKS if c.id in found)


def test_full_validation_detects_file_system_mismatch(sip_dir):
    baseline = Validator(SipPackage.open_folder(sip_dir)).run(full=True)
    assert not _checks(baseline) & {"checksum", "missing_file", "surplus_file"}

    (sip_dir / "content" / "d0001" / "p000001.pdf").write_bytes(b"changed")
    (sip_dir / "content" / "d0001" / "surplus.pdf").write_bytes(b"x")
    (sip_dir / "content" / "d0001" / "p000002.txt").unlink()
    (sip_dir / "stray.txt").write_text("x")
    issues = Validator(SipPackage.open_folder(sip_dir)).run(full=True)
    found = _checks(issues)
    assert {"checksum", "surplus_file", "missing_file", "illegal_entries", "header_content",
            "validity_failed"} <= found


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_sip_root_contains_only_header_and_content(tmp_path, version):
    from ph_backend import package_factory

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_RootLayout_{version}", "Test",
                                         version=version)
    relevant = {"illegal_entries", "header_content"}

    def layout_issues():
        return [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] in relevant]

    assert not layout_issues()

    extra = pkg.file_root / "unexpected.txt"
    extra.write_text("unexpected root entry", encoding="utf-8")
    assert any(issue["checkId"] == "illegal_entries" and issue["value"] == "unexpected.txt"
               for issue in layout_issues())
    extra.unlink()

    content = pkg.file_root / "content"
    missing_content = pkg.file_root / "content-missing"
    content.rename(missing_content)
    assert any(issue["checkId"] == "header_content" and issue["value"] == "content"
               for issue in layout_issues())
    missing_content.rename(content)

    header = pkg.file_root / "header"
    missing_header = pkg.file_root / "header-missing"
    header.rename(missing_header)
    assert any(issue["checkId"] == "header_content" and issue["value"] == "header"
               for issue in layout_issues())


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_primary_data_under_header_is_rejected(tmp_path, version):
    from ph_backend import files, package_factory

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_HeaderData_{version}", "Test",
                                         version=version)
    content = pkg.file_root / "content" / "allowed.txt"
    content.write_text("primary content", encoding="utf-8")
    files.add_to_table_of_contents(pkg, "content/allowed.txt")
    assert not [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "header_content"]

    xsd_directory = pkg.file_root / "header" / "xsd"
    listed_payload = xsd_directory / "primary.pdf"
    listed_payload.write_bytes(b"primary data in header")
    files.add_to_table_of_contents(pkg, "header/xsd/primary.pdf")
    unlisted_payload = xsd_directory / "unlisted.pdf"
    unlisted_payload.write_bytes(b"unlisted primary data in header")

    issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "header_content"]

    assert {issue["value"] for issue in issues} == {
        "header/xsd/primary.pdf", "header/xsd/unlisted.pdf"}


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_primary_data_must_be_under_content(tmp_path, version, delivery_type):
    from ph_backend import files, package_factory

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261107_Test_PrimaryLocation_{version}_{delivery_type}", "Test",
        version=version, delivery_type=delivery_type)
    relevant = {"illegal_entries", "header_content"}

    def location_issues():
        return [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] in relevant]

    content_payload = pkg.file_root / "content" / "primary.txt"
    content_payload.write_text("primary content", encoding="utf-8")
    files.add_to_table_of_contents(pkg, "content/primary.txt")
    assert not location_issues()

    content_payload.unlink()
    root_payload = pkg.file_root / "primary.txt"
    root_payload.write_text("primary content", encoding="utf-8")
    root_issues = location_issues()
    assert any(issue["checkId"] == "illegal_entries" and issue["value"] == "primary.txt"
               and issue["severity"] == ERROR for issue in root_issues)
    root_payload.unlink()

    header_payload = pkg.file_root / "header" / "xsd" / "primary.pdf"
    header_payload.write_bytes(b"primary content")
    files.add_to_table_of_contents(pkg, "header/xsd/primary.pdf")
    header_issues = location_issues()
    assert any(issue["checkId"] == "header_content" and issue["value"] == "header/xsd/primary.pdf"
               and issue["severity"] == ERROR for issue in header_issues)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_toc_completeness_for_each_release(tmp_path, version):
    from lxml import etree
    from ph_backend import package_factory
    from ph_backend.sip import localname

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_Toc_{version}", "Test", version=version)
    original_xml = etree.tostring(pkg.tree)

    def mismatch_pairs():
        issues = Validator(pkg).run(full=True)
        return {(issue["checkId"], issue["value"]) for issue in issues
                if issue["checkId"] in {"missing_file", "surplus_file", "missing_folder", "surplus_folder"}}

    assert mismatch_pairs() == set()

    file_entry, file_path = next((element, path) for element, path in pkg.walk_table_of_contents()
                                 if localname(element) == "datei")
    old_file_name = pkg.text(file_entry, "name")
    missing_file_path = file_path.parent / ("missing-" + old_file_name)
    pkg.set_text(file_entry, "name", missing_file_path.name, required=True)
    pkg.invalidate_file_index()
    pairs = mismatch_pairs()
    assert ("missing_file", missing_file_path.as_posix()) in pairs
    assert ("surplus_file", file_path.as_posix()) in pairs

    pkg.restore_xml(original_xml)
    file_entry, file_path = next((element, path) for element, path in pkg.walk_table_of_contents()
                                 if localname(element) == "datei")
    file_entry.getparent().remove(file_entry)
    pkg.invalidate_file_index()
    assert ("surplus_file", file_path.as_posix()) in mismatch_pairs()

    pkg.restore_xml(original_xml)
    content_entry = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    pkg.set_text(content_entry, "name", "missing-content", required=True)
    pkg.invalidate_file_index()
    pairs = mismatch_pairs()
    assert ("missing_folder", "missing-content") in pairs
    assert ("surplus_folder", "content") in pairs

    pkg.restore_xml(original_xml)
    content_entry = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    content_entry.getparent().remove(content_entry)
    pkg.invalidate_file_index()
    assert ("surplus_folder", "content") in mismatch_pairs()


def test_sip_file_count_limit_and_folder_recommendation(sip_dir, monkeypatch):
    from ph_backend import validation

    monkeypatch.setattr(validation, "MAX_FILES_IN_PACKAGE", 1)
    monkeypatch.setattr(validation, "RECOMMENDED_FILES_PER_FOLDER", 1)

    issues = Validator(SipPackage.open_folder(sip_dir)).run(full=True)

    package_limit = next(issue for issue in issues if issue["checkId"] == "file_count")
    folder_limit = next(issue for issue in issues if issue["checkId"] == "folder_file_count")
    assert package_limit["severity"] == ERROR and package_limit["value"] == "2"
    assert folder_limit["severity"] == WARNING and folder_limit["value"] == "d0001"


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_assigned_dossier_folders_should_share_content_depth(tmp_path, version, delivery_type):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_FolderDepth_{version}_{delivery_type}", "Test",
        version=version, delivery_type=delivery_type)
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    position = next(pkg.iter_kind("ordnungssystemposition"))

    def assign_file(dossier_title, *folder_names):
        folder = content
        for folder_name in folder_names:
            folder = files.create_folder(pkg, folder, folder_name)
        source = tmp_path / f"{dossier_title}.txt"
        source.write_text(dossier_title, encoding="utf-8")
        files.import_paths(pkg, folder, [source])
        file_element = next(element for element in pkg.children(folder, "datei"))
        dossier = structure.add_objects(pkg, position, "dossier", title=dossier_title)[0]
        assert structure.assign_files(pkg, dossier, [file_element]) == 1

    assign_file("First", "d0001")
    assign_file("Second", "d0002")
    unassigned_folder = files.create_folder(pkg, content, "unassigned")
    nested_unassigned_folder = files.create_folder(pkg, unassigned_folder, "nested")
    orphan = tmp_path / "orphan.txt"
    orphan.write_text("unassigned", encoding="utf-8")
    files.import_paths(pkg, nested_unassigned_folder, [orphan])
    check_id = "folder_level"
    issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == check_id]
    assert not issues

    assign_file("Third", "d0003", "nested")
    issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == check_id]
    assert len(issues) == 1
    assert issues[0]["severity"] == WARNING
    assert "gleicher Ebene" in issues[0]["description"]


@pytest.mark.parametrize("version", ["1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_assigned_mappe_folders_should_share_content_depth(tmp_path, version, delivery_type):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_MappeDepth_{version}_{delivery_type}", "Test",
        version=version, delivery_type=delivery_type)
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]

    def assign_mapped_file(name, folder_names):
        folder = content
        for folder_name in folder_names:
            folder = files.create_folder(pkg, folder, folder_name)
        source = tmp_path / f"{name}.txt"
        source.write_text(name, encoding="utf-8")
        files.import_paths(pkg, folder, [source])
        file_element = next(element for element in pkg.children(folder, "datei"))
        mappe = structure.add_objects(pkg, dossier, "mappe", title=name)[0]
        owner = mappe
        if delivery_type == "GEVER":
            owner = structure.add_objects(pkg, mappe, "dokument", title=f"Document {name}")[0]
        assert structure.assign_files(pkg, owner, [file_element]) == 1

    assign_mapped_file("m0001", ("m0001",))
    assign_mapped_file("m0002", ("m0002",))
    issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "folder_level"]
    assert not issues

    assign_mapped_file("m0003", ("group", "m0003"))
    issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "folder_level"]
    assert len(issues) == 1
    assert issues[0]["severity"] == WARNING
    assert "gleicher Ebene" in issues[0]["description"]


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_recommended_package_size_boundary_is_a_warning(tmp_path, monkeypatch, version, delivery_type):
    from ph_backend import files, package_factory, validation

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_SizeBoundary_{version}_{delivery_type}", "Test",
                                         version=version, delivery_type=delivery_type)
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    payload = tmp_path / "payload.txt"
    payload.write_bytes(b"x")
    files.import_paths(pkg, content, [payload])
    total = sum(path.stat().st_size for path in pkg.file_root.rglob("*") if path.is_file())

    monkeypatch.setattr(validation, "MAX_PACKAGE_SIZE", total)
    assert not [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "package_size"]

    monkeypatch.setattr(validation, "MAX_PACKAGE_SIZE", total - 1)
    findings = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "package_size"]

    assert len(findings) == 1 and findings[0]["value"] == str(total)
    assert findings[0]["severity"] == WARNING
    assert "Kontaktieren Sie vor der Vorbereitung oder Übermittlung" in findings[0]["description"]


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_recommended_path_length_boundary_is_a_warning(tmp_path, version):
    from ph_backend import files, package_factory

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_PathBoundary_{version}", "Test",
                                         version=version)
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    prefix_length = len(f"{pkg.root_dir.name}/content/")
    names = [(target_length, "a" * (target_length - prefix_length - 4) + ".txt")
             for target_length in (179, 180)]
    sources = []
    for _, name in names:
        source = tmp_path / name
        source.write_text("path length boundary", encoding="utf-8")
        sources.append(source)
    files.import_paths(pkg, content, sources)

    findings = {issue["value"]: issue["severity"] for issue in Validator(pkg).run(full=True)
                if issue["checkId"] == "path_length"}

    assert findings == {f"{pkg.root_dir.name}/content/{names[1][1]}": WARNING}


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_files_path_length_boundary_covers_files_and_folders(tmp_path, version):
    from ph_backend import files, package_factory

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_FILES_PathBoundary_{version}", "Test",
        version=version, delivery_type="FILES")
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    prefix = f"{pkg.root_dir.name}/content/"
    targets = (179, 180)
    file_names = {target: "f" * (target - len(prefix) - 4) + ".txt" for target in targets}
    sources = []
    for target, name in file_names.items():
        source = tmp_path / name
        source.write_text("path length boundary", encoding="utf-8")
        sources.append(source)
    files.import_paths(pkg, content, sources)

    folder_names = {target: "d" * (target - len(prefix)) for target in targets}
    folder_paths = set()
    for target, name in folder_names.items():
        folder = files.create_folder(pkg, content, name)
        folder_paths.add(f"{prefix}{name}")
        source = tmp_path / f"folder-child-{target}.txt"
        source.write_text("folder path length boundary", encoding="utf-8")
        files.import_paths(pkg, folder, [source])

    expected_paths = {f"{prefix}{name}" for name in file_names.values()} | folder_paths
    findings = {issue["value"]: issue["severity"] for issue in Validator(pkg).run(full=True)
                if issue["checkId"] == "path_length" and issue["value"] in expected_paths}

    assert findings == {
        f"{prefix}{file_names[180]}": WARNING,
        f"{prefix}{folder_names[180]}": WARNING,
    }


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_files_integrated_documentation_uses_simple_classification(tmp_path, version):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_IntegratedDocs_{version}", "Test",
        version=version, delivery_type="FILES")
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    documentation = files.create_folder(pkg, content, "1_DOK")
    readme = tmp_path / "readme.txt"
    readme.write_text("Integrated documentation", encoding="utf-8")
    files.import_paths(pkg, documentation, [readme])

    issues = Validator(pkg).run(full=True)
    missing = [issue for issue in issues if issue["checkId"] == "files_documentation"]
    assert any("2_DATEN" in issue["description"] for issue in missing)

    data_folder = files.create_folder(pkg, content, "2_DATEN")
    data_file = tmp_path / "database.siard"
    data_file.write_bytes(b"not a format-conformance fixture")
    files.import_paths(pkg, data_folder, [data_file])
    classification_positions = list(pkg.iter_kind("ordnungssystemposition"))
    assert len(classification_positions) == 1
    dossier = structure.add_objects(pkg, classification_positions[0], "dossier")[0]
    assert dossier.getparent() is classification_positions[0]
    issues = Validator(pkg).run(full=True)
    assert any("Dossier" in issue["description"] for issue in issues if issue["checkId"] == "files_documentation")

    structure.assign_files(pkg, dossier, list(pkg.children(data_folder, "datei")))
    issues = Validator(pkg).run(full=True)
    assert not [issue for issue in issues if issue["checkId"] == "files_documentation"]

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_positions = list(reopened.iter_kind("ordnungssystemposition"))
    reopened_dossier = next(reopened.iter_kind("dossier"))
    siard_file = next(element for element in reopened.iter_kind("datei")
                      if reopened.relpath(element).as_posix() == "content/2_DATEN/database.siard")
    assert len(reopened_positions) == 1
    assert reopened_dossier.getparent() is reopened_positions[0]
    assert reopened.referenced_files(reopened_dossier) == [siard_file]
    reopened_issues = Validator(reopened).run(full=True)
    assert not [issue for issue in reopened_issues
                if issue["checkId"] in {"schema", "files_documentation"}]

    reopened_dossier.getparent().remove(reopened_dossier)
    missing_dossier = [issue for issue in Validator(reopened).run(full=True)
                       if issue["checkId"] == "files_documentation"]
    assert any("Dossier" in issue["description"] for issue in missing_dossier), version


def test_missing_checksum_is_reported(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    file_element = pkg.file_by_id("_file1")
    assert not [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "checksum"]
    pkg.set_text(file_element, "pruefsumme", "")

    issues = Validator(pkg).run(full=True)

    assert any(issue["checkId"] == "checksum" and issue["fieldTag"] == "pruefsumme" for issue in issues)


def test_sip_name_check_can_be_disabled(sip_dir):
    renamed = sip_dir.rename(sip_dir.with_name("not_a_sip_name"))
    pkg = SipPackage.open_folder(renamed)
    assert "sip_name" in _checks(Validator(pkg).run(full=True))
    assert "sip_name" not in _checks(Validator(pkg, validate_sip_name=False).run(full=True))


@pytest.mark.parametrize(("name", "invalid"), [
    ("SIP_20261107_Archive", False),
    ("SIP_20261107_Archive_Ref-1", False),
    ("archive_20261107_Archive", True),
    ("SIP_2026110_Archive", True),
    ("SIP_20261107_", True),
])
def test_sip_root_name_format_cases(tmp_path, name, invalid):
    from ph_backend import package_factory

    pkg = package_factory.create_package(tmp_path, "SIP_20261107_Test_SipName", "Test")
    renamed = pkg.root_dir.with_name(name)
    pkg.root_dir.rename(renamed)
    pkg = SipPackage.open_folder(renamed)

    assert ("sip_name" in _checks(Validator(pkg).run(full=True))) is invalid


def test_unsupported_checksum_is_reported_not_silently_md5(sip_dir):
    import pytest
    from ph_backend.sip import UnsupportedChecksumError, file_checksum

    pkg = SipPackage.open_folder(sip_dir)
    file_el = pkg.file_by_id("_file1")
    pkg.set_text(file_el, "pruefalgorithmus", "UNKNOWN", required=True)
    with pytest.raises(UnsupportedChecksumError):
        file_checksum(pkg.fs_path(file_el), "UNKNOWN")
    issues = Validator(pkg).run(full=True)
    assert any(issue["checkId"] == "checksum" and issue["fieldTag"] == "pruefalgorithmus" for issue in issues)


def test_checksum_validation_uses_declared_algorithm_and_reports_mismatch(sip_dir):
    import hashlib

    pkg = SipPackage.open_folder(sip_dir)
    file_element = pkg.file_by_id("_file1")
    content = pkg.fs_path(file_element).read_bytes()
    for label, algorithm in (("MD5", "md5"), ("SHA-1", "sha1"),
                             ("SHA-256", "sha256"), ("SHA-512", "sha512")):
        digest = hashlib.new(algorithm, content).hexdigest()
        pkg.set_text(file_element, "pruefalgorithmus", label, required=True)
        pkg.set_text(file_element, "pruefsumme", digest, required=True)
        issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "checksum"]
        assert not issues, (label, issues)

        pkg.set_text(file_element, "pruefsumme", "0" * len(digest), required=True)
        issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "checksum"]
        assert any(issue["fieldTag"] == "pruefsumme" for issue in issues), label


def test_encrypted_pdf_is_reported_as_primary_file(tmp_path):
    import zipfile
    from pypdf import PdfWriter
    from ph_backend import files, package_factory

    pkg = package_factory.create_package(tmp_path, "SIP_20261107_Test_EncryptedPdf", "Test")
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    encrypted_pdf = tmp_path / "encrypted.pdf"
    plain_pdf = tmp_path / "plain.pdf"
    for path, password in ((encrypted_pdf, "test-password"), (plain_pdf, None)):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        if password:
            writer.encrypt(password)
        with path.open("wb") as stream:
            writer.write(stream)

    encrypted_zip = tmp_path / "encrypted.zip"
    with zipfile.ZipFile(encrypted_zip, "w") as archive:
        archive.writestr("record.txt", "protected payload")
    archive_bytes = bytearray(encrypted_zip.read_bytes())
    local_header = archive_bytes.index(b"PK\x03\x04")
    central_header = archive_bytes.index(b"PK\x01\x02")
    for offset in (local_header + 6, central_header + 8):
        flags = int.from_bytes(archive_bytes[offset:offset + 2], "little") | 1
        archive_bytes[offset:offset + 2] = flags.to_bytes(2, "little")
    encrypted_zip.write_bytes(archive_bytes)
    files.import_paths(pkg, content, [encrypted_pdf, plain_pdf, encrypted_zip])

    issues = Validator(pkg).run(full=True)

    encrypted = {issue["value"].rsplit("/", 1)[-1] for issue in issues if issue["checkId"] == "encryption"}
    assert encrypted == {"encrypted.pdf", "encrypted.zip"}


def test_encrypted_ooxml_container_is_reported_without_flagging_ordinary_office(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    import msoffcrypto
    from msoffcrypto.format.ooxml import OOXMLFile
    from ph_backend import files, package_factory

    pkg = package_factory.create_package(tmp_path, "SIP_20261107_Test_EncryptedOffice", "Test")
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    protected = tmp_path / "protected.docx"
    ordinary = tmp_path / "ordinary.docx"
    plain_package = BytesIO()
    with ZipFile(plain_package, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
    plain_bytes = plain_package.getvalue()
    encrypted_package = BytesIO()
    OOXMLFile(BytesIO(plain_bytes)).encrypt("test-password", encrypted_package)
    protected.write_bytes(encrypted_package.getvalue())
    ordinary.write_bytes(plain_bytes)
    files.import_paths(pkg, content, [protected, ordinary])

    issues = Validator(pkg).run(full=True)

    assert [issue["value"] for issue in issues if issue["checkId"] == "encryption"] == ["content/protected.docx"]


def test_dangling_datei_ref_is_reported(tmp_path):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(tmp_path, "SIP_20261105_Test_DanglingRef", "Test",
                                         version="1.0", delivery_type="FILES")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    source = tmp_path / "reference.txt"
    source.write_text("reference", encoding="utf-8")
    files.import_paths(pkg, content, [source])
    file_element = next(element for element in pkg.children(content, "datei"))
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    document = structure.add_objects(pkg, dossier, "dokument")[0]
    structure.assign_files(pkg, document, [file_element])
    pkg.children(document, "dateiRef")[0].text = "_missing_file"

    issues = Validator(pkg).run(full=True)

    assert any(issue["checkId"] == "invalid_reference" and issue["value"] == "_missing_file"
               for issue in issues)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_files_primary_data_hierarchy_roundtrips_under_content(tmp_path, version):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_FILES_Content_{version}", "Test",
                                         version=version, delivery_type="FILES")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    nested = files.create_folder(pkg, content, "series")
    source = tmp_path / "primary.txt"
    source.write_text("primary content", encoding="utf-8")
    files.import_paths(pkg, nested, [source])
    file_element = next(element for element in pkg.children(nested, "datei"))
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    structure.assign_files(pkg, dossier, [file_element])

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_file = reopened.file_by_id(file_element.get("id"))

    assert reopened.relpath(reopened_file).as_posix() == "content/series/primary.txt"
    assert reopened.fs_path(reopened_file).is_file()
    assert not [issue for issue in Validator(reopened).run(full=True)
                if issue["checkId"] in {"schema", "missing_file", "surplus_file", "missing_folder",
                                        "surplus_folder", "invalid_reference"}]


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_files_primary_data_reference_outside_content_is_rejected(tmp_path, version):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_FILES_Outside_{version}", "Test",
                                         version=version, delivery_type="FILES")
    primary = pkg.file_root / "header" / "xsd" / "primary.pdf"
    primary.write_bytes(b"primary content")
    assert files.add_to_table_of_contents(pkg, "header/xsd/primary.pdf") == 1
    from ph_backend.sip import localname

    file_element = next(element for element, path in pkg.walk_table_of_contents()
                        if localname(element) == "datei" and path.as_posix() == "header/xsd/primary.pdf")
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    structure.assign_files(pkg, dossier, [file_element])

    issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "invalid_reference"]

    assert any(issue["severity"] == ERROR and issue["value"] == file_element.get("id") for issue in issues)


@pytest.mark.parametrize("schema_location", ["../../../outside.xsd", "https://example.invalid/evil.xsd"])
def test_schema_validation_rejects_external_or_escaping_dependencies(tmp_path, schema_location):
    from ph_backend import package_factory

    pkg = package_factory.create_package(tmp_path, "SIP_20261106_Test_UnsafeSchema", "Test")
    xsd_path = pkg.xsd_path()
    source = xsd_path.read_text(encoding="utf-8")
    outside = tmp_path / "outside.xsd"
    outside.write_text('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>', encoding="utf-8")
    xsd_path.write_text(source.replace("</xs:schema>",
                                       f'<xs:include schemaLocation="{schema_location}"/></xs:schema>'),
                         encoding="utf-8")

    issues = Validator(SipPackage.open_folder(pkg.root_dir)).run(full=True)

    assert any(issue["checkId"] == "schema" and "XSD dependenc" in issue["description"] for issue in issues)


def test_schema_validation_reports_missing_arelda_entrypoint(tmp_path):
    from ph_backend import package_factory

    pkg = package_factory.create_package(tmp_path, "SIP_20261106_Test_MissingArelda", "Test")
    pkg.set_text(pkg.delivery, "ablieferungstyp", "INVALID")
    (pkg.file_root / "header" / "xsd" / "arelda.xsd").unlink()

    issues = Validator(pkg).run(full=True)

    assert any(issue["checkId"] == "schema" and "arelda.xsd fehlt" in issue["description"]
               for issue in issues)
    assert any(issue["checkId"] == "schema" and "vertrauenswürdigen eCH-Schema" in issue["description"]
               for issue in issues)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_schema_validation_accepts_and_rejects_metadata_for_each_release(tmp_path, version):
    from ph_backend import package_factory

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_Schema_{version}", "Test",
                                         version=version)
    assert not [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "schema"]

    pkg.delivery.remove(pkg.child(pkg.delivery, "ablieferungstyp"))
    issues = Validator(pkg).run(full=True)

    assert any(issue["checkId"] == "schema" for issue in issues), version


@pytest.mark.parametrize("version,schema_version", [("1.2", "5.0"), ("1.3", "5.1")])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_schema_validation_rejects_arelda_namespace_mismatch(tmp_path, version, schema_version, delivery_type):
    from ph_backend import package_factory

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261107_Test_NamespaceMismatch_{version}_{delivery_type}", "Test",
        version=version, delivery_type=delivery_type)
    assert pkg.root.get("schemaVersion") == schema_version
    assert not [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "schema"]

    pkg.root.tag = "{urn:invalid:arelda}paket"
    issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "schema"]

    assert any(issue["severity"] == ERROR for issue in issues), (version, delivery_type)


def test_schema_validation_rejects_metadata_accepted_only_by_custom_package_schema(tmp_path):
    from ph_backend import package_factory, xsd_info
    from lxml import etree

    pkg = package_factory.create_package(tmp_path, "SIP_20261106_Test_CustomSchema", "Test")
    namespace = pkg.ns
    xsd = pkg.file_root / "header" / "xsd" / "arelda.xsd"
    xsd.write_text(f'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
        targetNamespace="{namespace}" xmlns="{namespace}" elementFormDefault="qualified">
            <xs:element name="paket" type="xs:anyType"/>
    </xs:schema>''', encoding="utf-8")
    for element in pkg.root.iter():
        element.attrib.pop("{http://www.w3.org/2001/XMLSchema-instance}type", None)
    pkg.set_text(pkg.delivery, "ablieferungstyp", "INVALID")
    local_schema = etree.XMLSchema(etree.parse(str(xsd)))
    trusted_schema = etree.XMLSchema(etree.parse(str(xsd_info.schema_directory(
        pkg.root.get("schemaVersion")) / "arelda.xsd")))
    assert local_schema.validate(pkg.tree), local_schema.error_log
    assert not trusted_schema.validate(pkg.tree), trusted_schema.error_log

    issues = Validator(pkg).run(full=True)

    assert any(issue["checkId"] == "schema" and "vertrauenswürdigen eCH-Schema" in issue["description"]
               for issue in issues), issues


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_schema_validation_reports_missing_bundled_dependency(tmp_path, version):
    from ph_backend import package_factory

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_MissingSchemaDependency_{version}", "Test",
                                         version=version)
    required_dependency = pkg.file_root / "header" / "xsd" / "base.xsd"
    assert required_dependency.is_file()
    required_dependency.unlink()

    issues = Validator(pkg).run(full=True)

    assert any(issue["checkId"] == "schema" and "dependency is missing" in issue["description"].lower()
               for issue in issues), version


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_mandatory_metadata_is_reported_after_sip_save_and_reopen(tmp_path, version, delivery_type):
    from ph_backend import package_factory, structure
    from ph_backend.sip import SipPackage

    pkg = package_factory.create_package(tmp_path, f"SIP_20261106_Test_Required_{version}_{delivery_type}", "Test",
                                         version=version, delivery_type=delivery_type)
    position = next(pkg.iter_kind("ordnungssystemposition"))
    structure.add_objects(pkg, position, "dossier")
    required_fields = (("ablieferung", "ablieferndeStelle"), ("dossier", "titel"))

    for kind, field in required_fields:
        owner = pkg.delivery if kind == "ablieferung" else next(pkg.iter_kind("dossier"))
        pkg.set_text(owner, field, "")
        pkg.save()
        pkg = SipPackage.open_folder(pkg.root_dir)
        issues = Validator(pkg).run(full=True)
        assert any(issue["checkId"] == "missing_values" and issue["fieldTag"] == field for issue in issues), (
            version, delivery_type, field)

        reopened_owner = pkg.delivery if kind == "ablieferung" else next(pkg.iter_kind("dossier"))
        pkg.set_text(reopened_owner, field, "Restored required value", required=True)
        pkg.save()
        pkg = SipPackage.open_folder(pkg.root_dir)
        issues = Validator(pkg).run(full=True)
        assert not any(issue["checkId"] == "missing_values" and issue["fieldTag"] == field for issue in issues), (
            version, delivery_type, field)
        assert not any(issue["checkId"] == "schema" for issue in issues), (version, delivery_type, field)


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_legacy_files_provenance_fields_roundtrip(tmp_path, version):
    from ph_backend import package_factory
    from ph_backend.sip import SipPackage

    pkg = package_factory.create_package(tmp_path, f"SIP_20261106_Test_LegacyFiles_{version}", "Test",
                                         version=version, delivery_type="FILES")
    for field, value in (("systemName", "Source system"), ("systemBeschreibung", "Source description")):
        pkg.set_text(pkg.provenance, field, value, required=True)
        pkg.save()
        pkg = SipPackage.open_folder(pkg.root_dir)
        issues = Validator(pkg).run(full=True)
        assert not any(issue["checkId"] == "missing_values" and issue["fieldTag"] == field
                   for issue in issues), (version, field)
        assert not any(issue["checkId"] == "schema" for issue in issues), (version, field)

        pkg.set_text(pkg.provenance, field, "")
        pkg.save()
        pkg = SipPackage.open_folder(pkg.root_dir)
        assert any(issue["checkId"] == "missing_values" and issue["fieldTag"] == field
                   for issue in Validator(pkg).run(full=True)), (version, field)


def test_optional_metadata_fields_may_be_absent(tmp_path):
    from ph_backend import package_factory

    pkg = package_factory.create_package(tmp_path, "SIP_20261105_Test_OptionalFields", "Test",
                                         version="1.3", delivery_type="FILES")
    for tag in ("ablieferungsnummer", "schutzfristenkategorie", "schutzfrist"):
        element = pkg.child(pkg.delivery, tag)
        if element is not None:
            pkg.delivery.remove(element)

    issues = Validator(pkg).run(full=False)

    missing = {issue["fieldTag"] for issue in issues if issue["checkId"] == "missing_values"}
    assert not missing & {"ablieferungsnummer", "schutzfristenkategorie", "schutzfrist"}


def test_time_span_checks(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    dossier = next(pkg.iter_kind("dossier"))
    pkg.set_time_span(dossier, "entstehungszeitraum", "2009", "2001")
    issues = Validator(pkg).run(full=False)
    assert "invalid_time_span" in _checks(issues)
    pkg.set_time_span(dossier, "entstehungszeitraum", "2001", "2003")
    assert "time_span_hierarchy" in _checks(Validator(pkg).run(full=False))


def test_ech_allowed_filename_punctuation_passes_validation(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    file_element = pkg.file_by_id("_file1")
    source = pkg.fs_path(file_element)
    target = source.with_name("report (final)+[1].pdf")
    source.rename(target)
    pkg.set_text(file_element, "name", target.name, required=True)
    pkg.invalidate_file_index()

    issues = Validator(pkg).run(full=True)

    assert not {issue["checkId"] for issue in issues} & {"charset", "missing_file", "wrong_file", "checksum"}


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_charset_validation_checks_imported_folder_and_file_names(tmp_path, version, delivery_type):
    from ph_backend import files, package_factory

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_Charset_{version}_{delivery_type}", "Test",
                                         version=version, delivery_type=delivery_type)
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    source = tmp_path / "batch"
    invalid_folder = source / "Group & Ä"
    invalid_folder.mkdir(parents=True)
    (source / "allowed.txt").write_text("allowed name", encoding="utf-8")
    (invalid_folder / "report&final.txt").write_text("invalid names", encoding="utf-8")
    files.import_paths(pkg, content, [source])

    findings = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "charset"]

    assert {(issue["object"], issue["value"]) for issue in findings} == {
        ("Ordner", "Group & Ä"), ("Datei", "report&final.txt")}


def test_estimated_dossier_period_requires_reason(tmp_path):
    from ph_backend import package_factory, structure
    from ph_backend.sip import SipPackage

    pkg = package_factory.create_package(tmp_path, "SIP_20261106_Test_EstimatedPeriod", "Test",
                                         version="1.3", delivery_type="GEVER")
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    pkg.set_time_span(dossier, "entstehungszeitraum", "ca. 2000", "2001")

    issues = Validator(pkg).run(full=False)
    assert any(issue["checkId"] == "missing_values" and issue["fieldTag"] == "entstehungszeitraumAnmerkung"
               for issue in issues)
    pkg.save()
    pkg = SipPackage.open_folder(pkg.root_dir)
    assert any(issue["checkId"] == "missing_values" and issue["fieldTag"] == "entstehungszeitraumAnmerkung"
               for issue in Validator(pkg).run(full=False))

    dossier = next(pkg.iter_kind("dossier"))
    pkg.set_text(dossier, "entstehungszeitraumAnmerkung", "Date estimated from incomplete records")
    pkg.save()
    pkg = SipPackage.open_folder(pkg.root_dir)
    issues = Validator(pkg).run(full=False)
    assert not any(issue["checkId"] == "missing_values" and issue["fieldTag"] == "entstehungszeitraumAnmerkung"
                   for issue in issues)


def test_protection_period_inconsistency(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    dossier = next(pkg.iter_kind("dossier"))
    pkg.set_text(dossier, "schutzfristenkategorie", "BGA")
    issues = Validator(pkg).run(full=False)
    assert any(i["checkId"] == "protection_period" and i["key"] == pkg.key(dossier) for i in issues)
    pkg.set_text(dossier, "schutzfrist", "50")
    assert any(i["checkId"] == "protection_period" and "einheitlich" in i["description"]
               for i in Validator(pkg).run(full=False))


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_protection_period_scope_is_consistent(tmp_path, version, delivery_type):
    from ph_backend import package_factory, structure

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_Protection_{version}_{delivery_type}", "Test",
                                         version=version, delivery_type=delivery_type)
    position = next(pkg.iter_kind("ordnungssystemposition"))
    dossier = structure.add_objects(pkg, position, "dossier")[0]
    scopes = {"delivery": pkg.delivery, "position": position, "dossier": dossier}

    def clear_scopes():
        for owner in scopes.values():
            for field in ("schutzfristenkategorie", "schutzfrist"):
                element = pkg.child(owner, field)
                if element is not None:
                    owner.remove(element)

    def set_scope(name):
        owner = scopes[name]
        pkg.set_text(owner, "schutzfristenkategorie", "BGA", required=True)
        pkg.set_text(owner, "schutzfrist", "30", required=True)

    for scope in scopes:
        clear_scopes()
        set_scope(scope)
        issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "protection_period"]
        assert not issues, (version, delivery_type, scope, issues)

    for other_scope in ("position", "dossier"):
        clear_scopes()
        set_scope("delivery")
        set_scope(other_scope)
        issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "protection_period"]
        assert any("Stufe" in issue["description"] for issue in issues), (version, delivery_type, other_scope)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
@pytest.mark.parametrize("scope", ["delivery", "position", "dossier"])
def test_protection_period_values_roundtrip_by_release(tmp_path, version, delivery_type, scope):
    from ph_backend import package_factory, structure

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261107_Test_ProtectionRoundtrip_{version}_{delivery_type}_{scope}", "Test",
        version=version, delivery_type=delivery_type)
    position = next(pkg.iter_kind("ordnungssystemposition"))
    dossier = structure.add_objects(pkg, position, "dossier")[0]
    owners = {"delivery": pkg.delivery, "position": position, "dossier": dossier}
    owner = owners[scope]
    pkg.set_text(owner, "schutzfristenkategorie", "BGA", required=True)
    pkg.set_text(owner, "schutzfrist", "30", required=True)

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_owners = {
        "delivery": reopened.delivery,
        "position": next(reopened.iter_kind("ordnungssystemposition")),
        "dossier": next(reopened.iter_kind("dossier")),
    }
    reopened_owner = reopened_owners[scope]

    assert reopened.text(reopened_owner, "schutzfristenkategorie") == "BGA"
    assert reopened.text(reopened_owner, "schutzfrist") == "30"
    issues = [issue for issue in Validator(reopened).run(full=True)
              if issue["checkId"] in {"schema", "protection_period"}]
    assert not issues, (version, delivery_type, scope, issues)


def test_duplicate_position_numbers(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    position = next(pkg.iter_kind("ordnungssystemposition"))
    from copy import deepcopy
    clone = deepcopy(position)
    clone.set("id", "_osp2")
    for el in clone.iter():
        if el.get("id") and el is not clone:
            el.set("id", el.get("id") + "_copy")
    position.addnext(clone)
    assert "position_number" in _checks(Validator(pkg).run(full=False))


def test_summarize_counts_every_check(sip_dir):
    issues = Validator(SipPackage.open_folder(sip_dir)).run(full=True)
    summary = summarize(issues)
    assert len(summary) == len(CHECKS)
    assert sum(s["errors"] + s["warnings"] for s in summary) == len(issues)


def test_kad_classification():
    assert file_formats.classify("a.PDF").rating == file_formats.RECOMMENDED
    assert file_formats.classify("a.docx").rating == file_formats.ACCEPTED
    assert file_formats.classify("a.odt").rating == file_formats.NOT_ACCEPTED
    assert file_formats.classify("a.xyz").rating == file_formats.UNKNOWN
    assert file_formats.classify("noext").rating == file_formats.UNKNOWN
    assert file_formats.classify_puid("fmt/1756").rating == file_formats.NOT_ACCEPTED
    assert file_formats.classify_puid("fmt/412").rating == file_formats.ACCEPTED


def test_file_format_check_follows_kad(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    assert "file_format" not in _checks(Validator(pkg).run(full=True))
    pkg.child(pkg.file_by_id("_file1"), "name").text = "p000001.odt"
    pkg.child(pkg.file_by_id("_file2"), "name").text = "p000002.xyz"
    pkg.invalidate_file_index()
    severities = {i["value"]: i["severity"] for i in Validator(pkg).run(full=True) if i["checkId"] == "file_format"}
    assert severities == {"p000001.odt": ERROR, "p000002.xyz": WARNING}


def test_droid_extension_mismatch_is_reported(sip_dir, monkeypatch):
    import csv
    import subprocess
    from pathlib import Path
    from ph_backend import validation

    pkg = SipPackage.open_folder(sip_dir)
    mismatched_path = pkg.fs_path(pkg.file_by_id("_file1"))
    matching_path = pkg.fs_path(pkg.file_by_id("_file2"))
    monkeypatch.setattr(validation.shutil, "which", lambda command: "droid")

    class FinishedProcess:
        returncode = 0

        def poll(self):
            return self.returncode

    def fake_popen(command, **kwargs):
        report_path = Path(command[command.index("-o") + 1])
        with report_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["FILE_PATH", "NAME", "EXTENSION_MISMATCH", "PUID", "FORMAT_NAME"])
            writer.writerow([str(mismatched_path), mismatched_path.name, "true", "fmt/18", "PDF"])
            writer.writerow([str(matching_path), matching_path.name, "false", "fmt/200", "Plain Text"])
            writer.writerow([str(matching_path), matching_path.name, "false", "", ""])
        return FinishedProcess()

    monkeypatch.setattr(validation.subprocess, "Popen", fake_popen)
    issues = [issue for issue in Validator(pkg, identify_formats=True).run(full=True)
              if issue["checkId"] == "droid_format"]

    assert len(issues) == 1
    assert issues[0]["severity"] == ERROR
    assert issues[0]["value"] == "p000001.pdf"
    assert "fmt/18" in issues[0]["description"]


def test_droid_puid_applies_kad_suitability_to_identified_format(sip_dir, monkeypatch):
    import csv
    from pathlib import Path
    from ph_backend import validation

    pkg = SipPackage.open_folder(sip_dir)
    identified_path = pkg.fs_path(pkg.file_by_id("_file1"))
    monkeypatch.setattr(validation.shutil, "which", lambda command: "droid")

    class FinishedProcess:
        returncode = 0

        def poll(self):
            return self.returncode

    def fake_popen(command, **kwargs):
        report_path = Path(command[command.index("-o") + 1])
        with report_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["FILE_PATH", "NAME", "EXTENSION_MISMATCH", "PUID", "FORMAT_NAME"])
            writer.writerow([str(identified_path), identified_path.name, "true", "fmt/1756", "OpenDocument Text"])
        return FinishedProcess()

    monkeypatch.setattr(validation.subprocess, "Popen", fake_popen)
    issues = [issue for issue in Validator(pkg, identify_formats=True).run(full=True)
              if issue["checkId"] == "file_format"]

    assert len(issues) == 1
    assert issues[0]["severity"] == ERROR
    assert issues[0]["value"] == "p000001.pdf"
    assert "OpenDocument Text" in issues[0]["description"]
    assert "fmt/1756" in issues[0]["description"]


@pytest.mark.parametrize(("platform", "exit_races_with_termination"), [
    ("posix", False), ("posix", True), ("nt", False),
])
def test_droid_scan_stops_and_cleans_temp_report_on_cancel(
        sip_dir, monkeypatch, platform, exit_races_with_termination):
    import os
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace
    from ph_backend import validation

    pkg = SipPackage.open_folder(sip_dir)
    monkeypatch.setattr(validation.shutil, "which", lambda command: "droid")
    monkeypatch.setattr(validation, "os", SimpleNamespace(name=platform, path=os.path, environ=os.environ))
    state = {"cancelled": False, "process": None, "report_path": None}

    class RunningProcess:
        pid = 12345
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            if exit_races_with_termination:
                self.returncode = 0
                raise ProcessLookupError()
            self.returncode = -15

        def kill(self):
            self.terminated = True
            self.returncode = -9

        def wait(self, timeout=None):
            if self.returncode is None:
                self.kill()
            return self.returncode

    def fake_popen(command, **kwargs):
        state["report_path"] = Path(command[command.index("-o") + 1])
        state["process"] = RunningProcess()
        state["cancelled"] = True
        return state["process"]

    def fake_taskkill(command, **kwargs):
        assert command == ["taskkill", "/PID", "12345", "/T", "/F"]
        state["process"].terminate()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(validation.subprocess, "Popen", fake_popen)
    if platform == "nt":
        monkeypatch.setattr(validation.subprocess, "run", fake_taskkill)
    issues = Validator(pkg, identify_formats=True).run(full=True, cancelled=lambda: state["cancelled"])

    assert state["process"].terminated
    assert not state["report_path"].exists()
    assert not [issue for issue in issues if issue["checkId"] == "droid_format"]


def test_droid_timeout_stops_process_and_cleans_temp_report(sip_dir, monkeypatch):
    import os
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace
    from ph_backend import validation

    pkg = SipPackage.open_folder(sip_dir)
    monkeypatch.setattr(validation.shutil, "which", lambda command: "droid")
    times = iter((1000.0, 1300.0))
    monkeypatch.setattr(validation, "time", SimpleNamespace(monotonic=lambda: next(times), sleep=lambda delay: None))
    state = {"process": None, "report_path": None}

    class RunningProcess:
        pid = 12345
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.terminated = True
            self.returncode = -9

        def wait(self, timeout=None):
            if self.returncode is None:
                self.kill()
            return self.returncode

    def fake_popen(command, **kwargs):
        state["report_path"] = Path(command[command.index("-o") + 1])
        state["report_path"].write_text("partial report", encoding="utf-8")
        state["process"] = RunningProcess()
        return state["process"]

    def fake_taskkill(command, **kwargs):
        assert command == ["taskkill", "/PID", "12345", "/T", "/F"]
        state["process"].terminate()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(validation.subprocess, "Popen", fake_popen)
    if os.name == "nt":
        monkeypatch.setattr(validation.subprocess, "run", fake_taskkill)
    issues = [issue for issue in Validator(pkg, identify_formats=True).run(full=True)
              if issue["checkId"] == "droid_format"]

    assert state["process"].terminated
    assert not state["report_path"].exists()
    assert len(issues) == 1
    assert issues[0]["severity"] == WARNING
    assert "nach 300 Sekunden abgebrochen" in issues[0]["description"]


@pytest.mark.parametrize(("failure", "expected_detail"), [
    ("launch", "start failed"),
    ("exit", "Exit-Code 2"),
    ("missing_report", "Exit-Code 0"),
    ("bad_columns", "missing required columns"),
    ("bad_encoding", "DROID konnte die Dateien nicht identifizieren"),
])
def test_droid_cli_failures_warn_without_crashing_validation(sip_dir, monkeypatch, failure, expected_detail):
    from pathlib import Path
    from ph_backend import validation

    pkg = SipPackage.open_folder(sip_dir)
    monkeypatch.setattr(validation.shutil, "which", lambda command: "droid")

    class FinishedProcess:
        returncode = 2 if failure == "exit" else 0

        def poll(self):
            return self.returncode

    def fake_popen(command, **kwargs):
        if failure == "launch":
            raise OSError("start failed")
        report_path = Path(command[command.index("-o") + 1])
        if failure == "bad_columns":
            report_path.write_text('"UNKNOWN"\n"value"\n', encoding="utf-8")
        elif failure == "bad_encoding":
            report_path.write_bytes(b"\xff\xfe\xff")
        elif failure != "missing_report":
            report_path.write_text('"FILE_PATH","EXTENSION_MISMATCH"\n', encoding="utf-8")
        return FinishedProcess()

    monkeypatch.setattr(validation.subprocess, "Popen", fake_popen)
    issues = [issue for issue in Validator(pkg, identify_formats=True).run(full=True)
              if issue["checkId"] == "droid_format"]

    assert len(issues) == 1, (failure, issues)
    assert issues[0]["severity"] == WARNING
    assert expected_detail in issues[0]["description"]


def test_droid_missing_warns_when_identification_is_requested(sip_dir, monkeypatch):
    from ph_backend import validation

    monkeypatch.setattr(validation.shutil, "which", lambda command: None)
    issues = [issue for issue in Validator(SipPackage.open_folder(sip_dir), identify_formats=True).run(full=True)
              if issue["checkId"] == "droid_format"]

    assert len(issues) == 1
    assert issues[0]["severity"] == WARNING
    assert "nicht gefunden" in issues[0]["description"]


def test_droid_detects_pdf_content_with_txt_extension(tmp_path):
    import shutil
    from pathlib import Path
    from ph_backend import files, package_factory

    if not shutil.which("droid"):
        pytest.skip("DROID is not installed")
    sample_pdf = next((Path(__file__).parents[1] / "samples").rglob("*.pdf"), None)
    if sample_pdf is None:
        pytest.skip("No PDF sample is available")

    pkg = package_factory.create_package(tmp_path, "SIP_20261107_Test_DROID_Mismatch", "Test")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    mislabeled_pdf = tmp_path / "misidentified.txt"
    shutil.copyfile(sample_pdf, mislabeled_pdf)
    files.import_paths(pkg, content, [mislabeled_pdf])

    issues = [issue for issue in Validator(pkg, identify_formats=True).run(full=True)
              if issue["checkId"] == "droid_format"]

    assert any(issue["severity"] == ERROR and issue["value"] == "misidentified.txt"
               and "fmt/" in issue["description"] for issue in issues)


def test_droid_puid_rates_odt_mislabeled_as_docx_against_real_docx_control(tmp_path):
    import shutil
    import zipfile
    from ph_backend import files, package_factory

    if not shutil.which("droid"):
        pytest.skip("DROID is not installed")

    odt_path = tmp_path / "odt-content.docx"
    odt_mimetype = "application/vnd.oasis.opendocument.text"
    odt_manifest = f'''<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">
  <manifest:file-entry manifest:full-path="/" manifest:media-type="{odt_mimetype}"/>
  <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>'''
    with zipfile.ZipFile(odt_path, "w") as archive:
        archive.writestr("mimetype", odt_mimetype, compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/manifest.xml", odt_manifest)
        archive.writestr(
            "content.xml",
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'office:version="1.3"><office:body><office:text/></office:body></office:document-content>')
        archive.writestr(
            "styles.xml",
            '<office:document-styles xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'office:version="1.3"/>')

    docx_path = tmp_path / "genuine.docx"
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''
    relationships = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    word_document = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>control</w:t></w:r></w:p><w:sectPr/></w:body></w:document>'''
    with zipfile.ZipFile(docx_path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", word_document)

    pkg = package_factory.create_package(tmp_path, "SIP_20261108_Test_DROID_KaD", "Test")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    files.import_paths(pkg, content, [odt_path, docx_path])
    issues = Validator(pkg, identify_formats=True).run(full=True)
    kad_issues = [issue for issue in issues if issue["checkId"] == "file_format"]
    mismatch_issues = [issue for issue in issues if issue["checkId"] == "droid_format"]

    assert [(issue["value"], issue["severity"]) for issue in kad_issues] == [("odt-content.docx", ERROR)]
    assert "fmt/1756" in kad_issues[0]["description"]
    assert [(issue["value"], issue["severity"]) for issue in mismatch_issues] == [
        ("odt-content.docx", ERROR)]
    assert "genuine.docx" not in {issue["value"] for issue in kad_issues + mismatch_issues}


def test_kost_sample_is_schema_valid(sample_sip):
    issues = Validator(SipPackage.open_folder(sample_sip)).run(full=True)
    found = _checks(issues)
    assert not found & {"schema", "checksum", "missing_file", "surplus_file", "illegal_entries"}
