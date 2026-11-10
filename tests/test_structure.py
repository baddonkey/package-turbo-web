import pytest

from ph_backend import files, package_factory, structure, xsd_info
from ph_backend.package_factory import PackageCreationError
from ph_backend.sip import SipPackage
from ph_backend.validation import Validator


def _schema_errors(pkg):
    return [i["description"] for i in Validator(pkg).run(full=False) if i["checkId"] == "schema"]


@pytest.fixture
def new_pkg(tmp_path):
    return package_factory.create_package(tmp_path, "SIP_20260104_Testamt_Ref1", "Testamt")


def test_new_package_is_schema_valid(new_pkg):
    assert new_pkg.delivery_type == "GEVER"
    assert len(list(new_pkg.iter_kind("ordnungssystemposition"))) == 1
    assert _schema_errors(new_pkg) == []
    full = {i["checkId"] for i in Validator(new_pkg).run(full=True)}
    assert not full & {"checksum", "surplus_file", "missing_file", "header_content", "sip_name"}


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_gever_classification_scheme_is_created_persisted_and_required(tmp_path, version):
    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_GEVER_Scheme_{version}", "Test",
                                         version=version, delivery_type="GEVER")
    scheme = pkg.classification_system
    assert scheme is not None
    assert scheme.getparent() is pkg.delivery
    assert len(list(pkg.iter_kind("ordnungssystem"))) == 1
    assert _schema_errors(pkg) == []

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    assert reopened.classification_system is not None
    assert reopened.classification_system.getparent() is reopened.delivery
    assert _schema_errors(reopened) == []

    reopened.delivery.remove(reopened.classification_system)
    issues = Validator(reopened).run(full=True)
    assert any(issue["checkId"] == "schema" for issue in issues), version


@pytest.mark.parametrize("version", ["1.1", "1.2", "1.3"])
def test_structured_process_activity_creation_and_editing(tmp_path, version):
    pkg = package_factory.create_package(tmp_path, "SIP_20261105_Test_Process", "Test", version=version)
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    process = structure.add_objects(pkg, dossier, "vorgang")[0]
    activity = structure.add_objects(pkg, process, "aktivitaet")[0]
    assert process.get("id") is None and activity.get("id") is None
    pkg.set_text(process, "titel", "Process title")
    pkg.set_text(activity, "vorschreibung", "Activity instruction")
    pkg.set_repeated_texts(process, "verweis", ["Reference one", "Reference two"])
    pkg.set_repeated_texts(activity, "bemerkung", ["First remark", "Second remark"])
    assert pkg.text(process, "titel") == "Process title"
    assert pkg.text(activity, "vorschreibung") == "Activity instruction"
    assert _schema_errors(pkg) == []
    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    assert reopened.repeated_texts(next(reopened.iter_kind("aktivitaet")), "bemerkung") == ["First remark", "Second remark"]


def test_v10_legacy_process_is_preserved_without_structured_conversion(tmp_path):
    pkg = package_factory.create_package(tmp_path, "SIP_20261105_Test_Legacy", "Test", version="1.0")
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    process = pkg.create_child(dossier, "vorgang")
    process.text = "Legacy process description"
    assert "vorgang" not in structure.allowed_children(pkg, dossier)
    assert "aktivitaet" not in structure.allowed_children(pkg, process)
    with pytest.raises(structure.StructureError, match="structured"):
        structure.add_objects(pkg, dossier, "vorgang")
    with pytest.raises(structure.StructureError, match="structured"):
        structure.add_objects(pkg, process, "aktivitaet")
    assert process.text == "Legacy process description" and not len(process)
    assert _schema_errors(pkg) == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_files_schema_does_not_offer_gever_processes(tmp_path, version):
    pkg = package_factory.create_package(tmp_path, "SIP_20261105_Test_FilesProcess", "Test",
                                         version=version, delivery_type="FILES")
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    assert "vorgang" not in structure.allowed_children(pkg, dossier)
    with pytest.raises(structure.StructureError):
        structure.add_objects(pkg, dossier, "vorgang")
    assert _schema_errors(pkg) == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_files_classification_relationships_follow_release_schema(tmp_path, version):
    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_FILES_Classification_{version}", "Test",
                                         version=version, delivery_type="FILES")
    top_position = next(pkg.iter_kind("ordnungssystemposition"))
    leaf_position = structure.add_objects(pkg, top_position, "ordnungssystemposition")[0]

    assert "dossier" not in structure.allowed_children(pkg, top_position)
    with pytest.raises(structure.StructureError):
        structure.add_objects(pkg, top_position, "dossier")
    direct_dossier = structure.add_objects(pkg, leaf_position, "dossier")[0]

    supports_mappe = version in ("1.2", "1.3")
    assert ("mappe" in structure.allowed_children(pkg, leaf_position)) is supports_mappe
    if supports_mappe:
        mappe = structure.add_objects(pkg, leaf_position, "mappe")[0]
        mapped_dossier = structure.add_objects(pkg, mappe, "dossier")[0]
        assert mapped_dossier.getparent() is mappe

    assert direct_dossier.getparent() is leaf_position
    assert _schema_errors(pkg) == []
    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    assert _schema_errors(reopened) == []
    assert len(list(reopened.iter_kind("dossier"))) == (2 if supports_mappe else 1)
    assert len(list(reopened.iter_kind("mappe"))) == (1 if supports_mappe else 0)


def test_new_files_package(tmp_path):
    pkg = package_factory.create_package(tmp_path, "SIP_20260104_Amt_X", "Amt", delivery_type="FILES")
    assert pkg.delivery_type == "FILES" and _schema_errors(pkg) == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_package_creation_transfers_source_metadata_values(tmp_path, version, delivery_type):
    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_SourceMetadata_{version}_{delivery_type}", "Receiving office",
        delivery_type=delivery_type, creator="Source creator", system_name="Source system", version=version)

    assert pkg.text(pkg.delivery, "ablieferndeStelle") == "Receiving office"
    assert pkg.text(pkg.provenance, "aktenbildnerName") == "Source creator"
    assert pkg.text(pkg.classification_system, "name") == "Source system"
    assert _schema_errors(pkg) == []

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)

    assert reopened.text(reopened.delivery, "ablieferndeStelle") == "Receiving office"
    assert reopened.text(reopened.provenance, "aktenbildnerName") == "Source creator"
    assert reopened.text(reopened.classification_system, "name") == "Source system"
    assert _schema_errors(reopened) == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize(("delivery_type", "matching_type", "mismatching_type"), [
    ("FILES", "ablieferungFilesSIP", "ablieferungGeverSIP"),
    ("GEVER", "ablieferungGeverSIP", "ablieferungFilesSIP"),
])
def test_delivery_type_uses_matching_metadata_entity(
        tmp_path, version, delivery_type, matching_type, mismatching_type):
    xsi_type = "{http://www.w3.org/2001/XMLSchema-instance}type"
    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261107_Amt_{delivery_type}_Type_{version}", "Amt",
        version=version, delivery_type=delivery_type)

    assert pkg.text(pkg.delivery, "ablieferungstyp") == delivery_type
    assert pkg.delivery.get(xsi_type) == matching_type
    assert _schema_errors(pkg) == []

    pkg.delivery.set(xsi_type, mismatching_type)
    schema_issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "schema"]

    assert any(issue["severity"] == "error" for issue in schema_issues), version


@pytest.mark.parametrize("version,schema_version", [("1.0", "4.0"), ("1.1", "4.1"), ("1.2", "5.0"), ("1.3", "5.1")])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_creation_preserves_selected_schema_version(tmp_path, version, schema_version, delivery_type):
    pkg = package_factory.create_package(tmp_path, "SIP_20260104_Amt_Version", "Amt",
                                         delivery_type=delivery_type, version=version)
    schema_location = pkg.root.get("{http://www.w3.org/2001/XMLSchema-instance}schemaLocation", "").split()
    assert schema_location == [pkg.ns, "xsd/arelda.xsd"]
    assert (pkg.metadata_path.parent / schema_location[1]).resolve() == pkg.xsd_path().resolve()
    assert pkg.xsd_path().is_file()
    assert pkg.root.get("schemaVersion") == schema_version
    bundled_xsds = xsd_info.schema_directory(schema_version)
    source_files = {path.name: path.read_bytes() for path in bundled_xsds.glob("*.xsd")}
    package_files = {path.name: path.read_bytes() for path in (pkg.file_root / "header" / "xsd").glob("*.xsd")}
    assert package_files == source_files
    pkg.set_text(pkg.delivery, "ablieferungsnummer", "42")
    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_schema_location = reopened.root.get(
        "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation", "").split()
    assert reopened_schema_location == [reopened.ns, "xsd/arelda.xsd"]
    assert (reopened.metadata_path.parent / reopened_schema_location[1]).resolve() == reopened.xsd_path().resolve()
    assert reopened.root.get("schemaVersion") == schema_version
    reopened_files = {path.name: path.read_bytes() for path in (reopened.file_root / "header" / "xsd").glob("*.xsd")}
    assert reopened_files == source_files
    assert _schema_errors(reopened) == []


@pytest.mark.parametrize("version,schema_version", [
    ("1.0", "4.0"), ("1.1", "4.1"), ("1.2", "5.0"), ("1.3", "5.1")])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_core_metadata_graph_roundtrips_by_release(tmp_path, version, schema_version, delivery_type):
    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_Model_{version}_{delivery_type}", "Test",
        version=version, delivery_type=delivery_type)
    expected_type = "ablieferungGeverSIP" if delivery_type == "GEVER" else "ablieferungFilesSIP"
    xsi_type = "{http://www.w3.org/2001/XMLSchema-instance}type"
    position = next(pkg.iter_kind("ordnungssystemposition"))
    dossier = structure.add_objects(pkg, position, "dossier", title="Model dossier")[0]
    document = structure.add_objects(pkg, dossier, "dokument", title="Model document")[0]
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    source = tmp_path / "model.txt"
    source.write_text("model graph", encoding="utf-8")
    files.import_paths(pkg, content, [source])
    file_element = pkg.children(content, "datei")[0]
    assert structure.assign_files(pkg, document, [file_element]) == 1
    file_id = file_element.get("id")
    document_id = document.get("id")

    assert pkg.kind(pkg.root) == "paket"
    assert pkg.delivery is not None and pkg.delivery.get(xsi_type) == expected_type
    assert pkg.provenance.getparent() is pkg.delivery
    assert pkg.classification_system.getparent() is pkg.delivery
    assert len(list(pkg.iter_kind("ordnungssystemposition"))) == 1
    assert dossier.getparent() is position
    assert document.getparent() is dossier
    assert pkg.referenced_files(document) == [file_element]
    toc_roots = {pkg.text(entry, "name") for entry in pkg.children(pkg.table_of_contents, "ordner")}
    assert toc_roots == {"header", "content"}
    assert pkg.root.get("schemaVersion") == schema_version
    assert _schema_errors(pkg) == []

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)

    assert reopened.kind(reopened.root) == "paket"
    assert reopened.delivery_type == delivery_type
    assert reopened.delivery.get(xsi_type) == expected_type
    assert reopened.provenance.getparent() is reopened.delivery
    assert reopened.classification_system.getparent() is reopened.delivery
    assert len(list(reopened.iter_kind("ordnungssystemposition"))) == 1
    reopened_position = next(reopened.iter_kind("ordnungssystemposition"))
    reopened_dossier = next(reopened.iter_kind("dossier"))
    reopened_document = next(document for document in reopened.iter_kind("dokument")
                             if reopened.text(document, "titel") == "Model document")
    reopened_file = reopened.file_by_id(file_id)
    assert reopened_dossier.getparent() is reopened_position
    assert reopened_document.getparent() is reopened_dossier
    assert reopened.referenced_files(reopened_document) == [reopened_file]
    assert reopened.relpath(reopened_file).as_posix() == "content/model.txt"
    assert {reopened.text(entry, "name") for entry in reopened.children(reopened.table_of_contents, "ordner")} == {
        "header", "content"}
    assert reopened.root.get("schemaVersion") == schema_version
    assert _schema_errors(reopened) == []


@pytest.mark.parametrize("version,schema_version", [("1.2", "5.0"), ("1.3", "5.1")])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_arelda_elements_use_default_namespace_after_reopen(tmp_path, version, schema_version, delivery_type):
    from lxml import etree

    expected_namespace = "http://bar.admin.ch/arelda/v4"
    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Amt_Unprefixed_{version}_{delivery_type}", "Amt",
                                         delivery_type=delivery_type, version=version)
    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    arelda_elements = [element for element in reopened.root.iter()
                       if etree.QName(element).namespace == expected_namespace]

    assert arelda_elements
    assert reopened.root.nsmap[None] == expected_namespace
    assert all(element.prefix is None for element in arelda_elements)
    assert reopened.root.get("schemaVersion") == schema_version
    assert _schema_errors(reopened) == []


def test_unknown_creation_version_does_not_create_package(tmp_path):
    with pytest.raises(PackageCreationError, match="Unsupported creation version"):
        package_factory.create_package(tmp_path, "SIP_20260104_Amt_Future", "Amt", version="9.0")
    assert not (tmp_path / "SIP_20260104_Amt_Future").exists()


@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_recursive_mappen_use_contextual_schema_type(tmp_path, delivery_type):
    pkg = package_factory.create_package(tmp_path, "SIP_20260104_Amt_Mappe", "Amt", delivery_type=delivery_type)
    position = next(pkg.iter_kind("ordnungssystemposition"))
    outer = structure.add_objects(pkg, position, "mappe")[0]
    inner = structure.add_objects(pkg, outer, "mappe")[0]
    structure.add_objects(pkg, inner, "dossier")
    assert "dossier" in structure.allowed_children(pkg, outer)
    assert "dokument" not in structure.allowed_children(pkg, outer)
    with pytest.raises(structure.StructureError, match="package schema"):
        structure.add_objects(pkg, outer, "dokument")
    info = xsd_info.for_package(pkg)
    assert xsd_info.type_name(pkg, outer, info) == xsd_info.type_name(pkg, inner, info)
    assert _schema_errors(pkg) == []
    pkg.save()
    assert _schema_errors(SipPackage.open_folder(pkg.root_dir)) == []


@pytest.mark.parametrize("version", ["1.2", "1.3"])
def test_gever_dossier_mappe_document_relationships_roundtrip(tmp_path, version):
    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_GEVERe2e_Relationships_{version}", "Test",
                                         version=version, delivery_type="GEVER")
    position = next(pkg.iter_kind("ordnungssystemposition"))
    dossier = structure.add_objects(pkg, position, "dossier")[0]
    mappe = structure.add_objects(pkg, dossier, "mappe")[0]
    direct_document = structure.add_objects(pkg, dossier, "dokument")[0]
    mapped_document = structure.add_objects(pkg, mappe, "dokument")[0]

    with pytest.raises(structure.StructureError):
        structure.add_objects(pkg, position, "dokument")
    if version == "1.2":
        assert "mappe" not in structure.allowed_children(pkg, mappe)
        with pytest.raises(structure.StructureError):
            structure.add_objects(pkg, mappe, "mappe")
    else:
        nested_mappe = structure.add_objects(pkg, mappe, "mappe")[0]
        assert nested_mappe.getparent() is mappe

    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    sources = []
    for filename in ("direct.txt", "mapped.txt"):
        source = tmp_path / filename
        source.write_text(filename, encoding="utf-8")
        sources.append(source)
    files.import_paths(pkg, content, sources)
    file_elements = list(pkg.children(content, "datei"))
    structure.assign_files(pkg, direct_document, [file_elements[0]])
    structure.assign_files(pkg, mapped_document, [file_elements[1]])

    assert _schema_errors(pkg) == []
    assert pkg.referenced_files(direct_document) == [file_elements[0]]
    assert pkg.referenced_files(mapped_document) == [file_elements[1]]
    pkg.save()

    reopened = SipPackage.open_folder(pkg.root_dir, read_only=True)
    reopened_dossier = next(reopened.iter_kind("dossier"))
    reopened_mappe = reopened.children(reopened_dossier, "mappe")[0]
    assert len(reopened.children(reopened_dossier, "dokument")) == 1
    assert len(reopened.children(reopened_mappe, "dokument")) == 1
    assert [reopened.display_name(file_element) for file_element in
            reopened.referenced_files(reopened.children(reopened_dossier, "dokument")[0])] == ["direct.txt"]
    assert [reopened.display_name(file_element) for file_element in
            reopened.referenced_files(reopened.children(reopened_mappe, "dokument")[0])] == ["mapped.txt"]
    assert _schema_errors(reopened) == []


@pytest.mark.parametrize("version", ["1.2", "1.3"])
def test_sequential_mappe_names_and_content_folders_roundtrip(tmp_path, version):
    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_SequentialMappen_{version}", "Test",
        version=version, delivery_type="GEVER")
    position = next(pkg.iter_kind("ordnungssystemposition"))
    dossier = structure.add_objects(pkg, position, "dossier", title="Case")[0]
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    mappe_folders = {}
    if version == "1.2":
        for name in ("m0001", "m0002"):
            mappe = structure.add_objects(pkg, dossier, "mappe", title=name)[0]
            folder = files.create_folder(pkg, content, name)
            mappe_folders[name] = (mappe, folder)
    else:
        outer_mappe = structure.add_objects(pkg, dossier, "mappe", title="m0001")[0]
        outer_folder = files.create_folder(pkg, content, "m0001")
        inner_mappe = structure.add_objects(pkg, outer_mappe, "mappe", title="m0002")[0]
        inner_folder = files.create_folder(pkg, outer_folder, "m0002")
        mappe_folders = {"m0001": (outer_mappe, outer_folder), "m0002": (inner_mappe, inner_folder)}

    expected_paths = {}
    for name, (mappe, folder) in mappe_folders.items():
        source = tmp_path / f"{name}.txt"
        source.write_text(name, encoding="utf-8")
        files.import_paths(pkg, folder, [source])
        file_element = next(element for element in pkg.children(folder, "datei"))
        document = structure.add_objects(pkg, mappe, "dokument", title=f"Document {name}")[0]
        assert structure.assign_files(pkg, document, [file_element]) == 1
        expected_paths[name] = pkg.relpath(file_element).as_posix()

    assert _schema_errors(pkg) == []
    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_mappen = {pkg.text(mappe, "titel"): mappe for mappe in reopened.iter_kind("mappe")}

    assert list(reopened_mappen) == ["m0001", "m0002"]
    for name, expected_path in expected_paths.items():
        mappe = reopened_mappen[name]
        document = next(element for element in reopened.children(mappe, "dokument"))
        file_element = reopened.referenced_files(document)[0]
        assert reopened.relpath(file_element).as_posix() == expected_path
    assert _schema_errors(reopened) == []


def test_v12_mappen_reject_v13_only_recursion(tmp_path):
    pkg = package_factory.create_package(tmp_path, "SIP_20260104_Amt_Legacy", "Amt", version="1.2")
    position = next(pkg.iter_kind("ordnungssystemposition"))
    outer = structure.add_objects(pkg, position, "mappe")[0]
    assert "mappe" not in structure.allowed_children(pkg, outer)
    with pytest.raises(structure.StructureError, match="package schema"):
        structure.add_objects(pkg, outer, "mappe")
    assert _schema_errors(pkg) == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_schema_restore_matches_original_version(tmp_path, version):
    from lxml import etree

    pkg = package_factory.create_package(tmp_path, "SIP_20260104_Amt_Restore", "Amt", version=version)
    pkg.xsd_path().unlink()
    assert files.restore_header_xsd(pkg) == 1
    restored = etree.parse(str(pkg.xsd_path())).getroot()
    assert restored.get("targetNamespace") == pkg.ns
    assert _schema_errors(pkg) == []


def test_new_package_name_rules(tmp_path):
    with pytest.raises(PackageCreationError):
        package_factory.create_package(tmp_path, "SIP 2026", "Amt")
    with pytest.raises(PackageCreationError):
        package_factory.create_package(tmp_path, "Paket_ohne_Muster", "Amt")
    package_factory.create_package(tmp_path, "Paket_ohne_Muster", "Amt", validate_sip_name=False)
    assert package_factory.suggested_name("Bundesamt für Test", "Ref 1").startswith("SIP_")


def test_csv_import(tmp_path):
    csv_path = tmp_path / "plan.csv"
    csv_path.write_text("nummer|type|titel|federfuehrendeOrganisationseinheit\n1||Führung|\n10||Planung|x\n"
                        "10.1|dossier|Jahresplanung|\n2||Ressourcen|\n", encoding="utf-8")
    pkg = package_factory.create_package(tmp_path, "SIP_20260104_Amt_CSV", "Amt", csv_path=csv_path)
    positions = {pkg.text(p, "nummer"): p for p in pkg.iter_kind("ordnungssystemposition")}
    assert set(positions) == {"1", "10", "2"}
    assert positions["10"].getparent() is positions["1"]
    dossier = next(pkg.iter_kind("dossier"))
    assert dossier.getparent() is positions["10"] and pkg.text(dossier, "aktenzeichen") == "10.1"
    assert pkg.text(positions["10"], "federfuehrendeOrganisationseinheit") == "x"
    assert _schema_errors(pkg) == []

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_positions = {reopened.text(position, "nummer"): position
                          for position in reopened.iter_kind("ordnungssystemposition")}
    assert reopened.text(reopened_positions["10"], "federfuehrendeOrganisationseinheit") == "x"
    assert _schema_errors(reopened) == []


def test_csv_import_requires_nummer(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("titel\nx\n", encoding="utf-8")
    with pytest.raises(structure.StructureError):
        package_factory.create_package(tmp_path, "SIP_20260104_Amt_Bad", "Amt", csv_path=csv_path)
    assert not (tmp_path / "SIP_20260104_Amt_Bad").exists()


def test_build_import_assign_and_validate(new_pkg, tmp_path):
    pkg = new_pkg
    source = tmp_path / "ablage"
    (source / "Gruppe Ä" / "Dossier 1").mkdir(parents=True)
    (source / "Gruppe Ä" / "Dossier 1" / "brief&final.pdf").write_bytes(b"%PDF-1.4 a")
    (source / "Gruppe Ä" / "Dossier 1" / "notiz.txt").write_bytes(b"b")
    (source / "Gruppe Ä" / "lose.pdf").write_bytes(b"%PDF-1.4 c")
    content = next(o for o in pkg.iter_kind("ordner") if pkg.text(o, "name") == "content")
    stats = files.import_paths(pkg, content, [source])
    assert stats["files"] == 3 and stats["folders"] == 3

    structure.delete(pkg, list(pkg.iter_kind("ordnungssystemposition")))
    top = next(o for o in pkg.children(content, "ordner") if pkg.text(o, "name") == "ablage")
    created, unassigned = structure.mass_assign(pkg, [top], pkg.classification_system, 1, 1)
    assert created == 2 and unassigned == 1
    dossier = next(pkg.iter_kind("dossier"))
    assert pkg.text(dossier, "titel") == "Dossier 1"
    # GEVER dossiers reference files through documents.
    assert len(list(dossier.iter(pkg.q("dokument")))) == 2
    pkg.save()
    assert _schema_errors(pkg) == []
    full = {i["checkId"] for i in Validator(pkg).run(full=True)}
    assert not full & {"checksum", "surplus_file", "missing_file", "surplus_folder"}
    assert "charset" in full

    assert files.normalize_names(pkg) == 2
    assert "charset" not in {i["checkId"] for i in Validator(pkg).run(full=False)}
    assert files.denormalize(pkg) == 2
    assert files.shorten_paths(pkg) > 0
    names = {pkg.text(f, "name") for f in pkg.iter_kind("datei") if pkg.relpath(f).parts[0] == "content"}
    assert names == {"P1.pdf", "P2.txt", "P3.pdf"} or all(n.startswith("P") for n in names)
    pkg.save()


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_mass_assign_maps_content_folders_to_dossiers_roundtrip_by_release(tmp_path, version):
    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_FolderDossier_{version}", "Test", version=version)
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    source_folder = tmp_path / "akten" / "fall"
    source_folder.mkdir(parents=True)
    (source_folder / "dokument.txt").write_text("classified file", encoding="utf-8")
    files.import_paths(pkg, content, [source_folder.parent])
    top = next(folder for folder in pkg.children(content, "ordner") if pkg.text(folder, "name") == "akten")
    structure.delete(pkg, list(pkg.iter_kind("ordnungssystemposition")))

    created, unassigned = structure.mass_assign(
        pkg, [top], pkg.classification_system, osp_levels=1, dossier_levels=1, include_top=True)

    assert created == 2 and unassigned == 0
    position = next(pkg.iter_kind("ordnungssystemposition"))
    dossier = next(pkg.iter_kind("dossier"))
    file_element = next(element for element in pkg.iter_kind("datei")
                        if pkg.relpath(element).as_posix() == "content/akten/fall/dokument.txt")
    assert pkg.text(position, "titel") == "akten"
    assert pkg.text(dossier, "titel") == "fall"
    document = next(dossier.iter(pkg.q("dokument")))
    assert pkg.referenced_files(document) == [file_element]

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_dossier = next(reopened.iter_kind("dossier"))
    reopened_file = reopened.file_by_id(file_element.get("id"))

    assert reopened.text(next(reopened.iter_kind("ordnungssystemposition")), "titel") == "akten"
    assert reopened.text(reopened_dossier, "titel") == "fall"
    assert reopened.relpath(reopened_file).as_posix() == "content/akten/fall/dokument.txt"
    reopened_document = next(reopened_dossier.iter(reopened.q("dokument")))
    assert reopened.referenced_files(reopened_document) == [reopened_file]
    assert _schema_errors(reopened) == []
    full = {i["checkId"] for i in Validator(pkg).run(full=True)}
    assert not full & {"checksum", "surplus_file", "missing_file", "schema"}


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_mass_assign_preserves_sequential_dossier_folder_names(tmp_path, version):
    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_SequentialDossiers_{version}", "Test", version=version)
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    source_folders = []
    for folder_name in ("d0001", "d0002"):
        source_folder = tmp_path / folder_name
        source_folder.mkdir()
        (source_folder / "document.txt").write_text(folder_name, encoding="utf-8")
        source_folders.append(source_folder)
    files.import_paths(pkg, content, source_folders)
    dossier_folders = sorted(pkg.children(content, "ordner"), key=lambda folder: pkg.text(folder, "name"))
    position = next(pkg.iter_kind("ordnungssystemposition"))

    created, unassigned = structure.mass_assign(
        pkg, dossier_folders, position, osp_levels=0, dossier_levels=1, include_top=True)

    assert created == 2 and unassigned == 0
    dossiers = {pkg.text(dossier, "titel"): dossier for dossier in pkg.iter_kind("dossier")}
    assert list(dossiers) == ["d0001", "d0002"]
    expected_paths = {name: f"content/{name}/document.txt" for name in dossiers}
    for dossier_name, dossier in dossiers.items():
        document = next(dossier.iter(pkg.q("dokument")))
        assert [pkg.relpath(file_element).as_posix() for file_element in pkg.referenced_files(document)] == [
            expected_paths[dossier_name]]

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_dossiers = {pkg.text(dossier, "titel"): dossier for dossier in reopened.iter_kind("dossier")}

    assert list(reopened_dossiers) == ["d0001", "d0002"]
    for dossier_name, dossier in reopened_dossiers.items():
        document = next(dossier.iter(reopened.q("dokument")))
        assert [reopened.relpath(file_element).as_posix() for file_element in
                reopened.referenced_files(document)] == [expected_paths[dossier_name]]
    assert _schema_errors(reopened) == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_shorten_paths_uses_sequential_primary_file_names_and_preserves_sources(tmp_path, version):
    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_SequentialPrimaryFiles_{version}", "Test", version=version)
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    sources = []
    for name in ("invoice.pdf", "scan.tif"):
        source = tmp_path / name
        source.write_bytes(name.encode("ascii"))
        sources.append(source)
    files.import_paths(pkg, content, sources)
    file_elements = list(pkg.children(content, "datei"))
    original_by_id = {element.get("id"): pkg.text(element, "name") for element in file_elements}
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    assert structure.assign_files(pkg, dossier, file_elements) == len(file_elements)
    referenced_ids = pkg.referenced_ids()

    assert files.shorten_paths(pkg) == 2
    renamed_by_id = {element.get("id"): (pkg.text(element, "name"), pkg.text(element, "originalName"))
                     for element in file_elements}
    assert renamed_by_id == {
        file_elements[0].get("id"): ("P1.pdf", "invoice.pdf"),
        file_elements[1].get("id"): ("P2.tif", "scan.tif"),
    }

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_dossier = next(reopened.iter_kind("dossier"))
    reopened_documents = list(reopened_dossier.iter(reopened.q("dokument")))
    reopened_files = {element.get("id"): element for element in reopened.iter_kind("datei")
                      if element.get("id") in original_by_id}

    assert reopened.referenced_ids() == referenced_ids
    assert {ident: (reopened.text(element, "name"), reopened.text(element, "originalName"))
            for ident, element in reopened_files.items()} == renamed_by_id
    assert {file_element.get("id") for document in reopened_documents
            for file_element in reopened.referenced_files(document)} == referenced_ids
    assert _schema_errors(reopened) == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_gever_dossier_folder_can_stay_top_level_under_deep_classification(tmp_path, version):
    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_TopLevelDossierFolder_{version}", "Test", version=version)
    root_position = next(pkg.iter_kind("ordnungssystemposition"))
    middle_position = structure.add_objects(pkg, root_position, "ordnungssystemposition", title="Middle")[0]
    leaf_position = structure.add_objects(pkg, middle_position, "ordnungssystemposition", title="Leaf")[0]
    dossier = structure.add_objects(pkg, leaf_position, "dossier", title="Case")[0]
    content = next(folder for folder in pkg.iter_kind("ordner") if pkg.text(folder, "name") == "content")
    top_level_dossier_folder = files.create_folder(pkg, content, "d0001")
    source = tmp_path / "primary.txt"
    source.write_text("primary content", encoding="utf-8")
    files.import_paths(pkg, top_level_dossier_folder, [source])
    file_element = next(element for element in pkg.children(top_level_dossier_folder, "datei"))
    assert structure.assign_files(pkg, dossier, [file_element]) == 1
    file_id = file_element.get("id")

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_positions = list(reopened.iter_kind("ordnungssystemposition"))
    reopened_dossier = next(reopened.iter_kind("dossier"))
    reopened_file = reopened.file_by_id(file_id)
    document = next(reopened_dossier.iter(reopened.q("dokument")))

    assert len(reopened_positions) == 3
    assert reopened_dossier.getparent() is reopened_positions[-1]
    assert reopened.relpath(reopened_file).as_posix() == "content/d0001/primary.txt"
    assert reopened.referenced_files(document) == [reopened_file]
    assert _schema_errors(reopened) == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_normalization_resolves_collisions_and_restores_original_names(tmp_path, version, delivery_type):
    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_Normalize_{version}_{delivery_type}", "Test",
                                         version=version, delivery_type=delivery_type)
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    payloads = {"a中b.txt": b"first payload", "a文b.txt": b"second payload"}
    sources = []
    for name, data in payloads.items():
        source = tmp_path / name
        source.write_bytes(data)
        sources.append(source)
    files.import_paths(pkg, content, sources)

    assert files.normalize_names(pkg) == 2
    entries = list(pkg.children(content, "datei"))
    assert {pkg.text(element, "name") for element in entries} == {"a_b.txt", "a_b_1.txt"}
    assert {pkg.text(element, "originalName") for element in entries} == set(payloads)
    assert {pkg.text(element, "originalName"): pkg.fs_path(element).read_bytes() for element in entries} == payloads

    pkg.save()
    pkg = SipPackage.open_folder(pkg.root_dir)
    assert files.denormalize(pkg) == 2
    entries = list(pkg.children(content := next(element for element in pkg.iter_kind("ordner")
                                                 if pkg.text(element, "name") == "content"), "datei"))
    assert {pkg.text(element, "name"): pkg.fs_path(element).read_bytes() for element in entries} == payloads
    assert not {issue["checkId"] for issue in Validator(pkg).run(full=True)} & {
        "schema", "checksum", "missing_file", "surplus_file", "surplus_folder"}


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_normalization_roundtrips_invalid_folder_and_file_names(tmp_path, version, delivery_type):
    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_NormalizeNames_{version}_{delivery_type}", "Test",
                                         version=version, delivery_type=delivery_type)
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    source = tmp_path / "source"
    invalid_folder = source / "Gruppe & Ä"
    invalid_folder.mkdir(parents=True)
    payload = b"normalization round-trip payload"
    (invalid_folder / "report & Ä.txt").write_bytes(payload)
    files.import_paths(pkg, content, [source])

    assert files.normalize_names(pkg) == 2
    assert not [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "charset"]
    normalized_folder = next(element for element in pkg.iter_kind("ordner")
                             if pkg.text(element, "originalName") == "Gruppe & Ä")
    normalized_file = next(element for element in pkg.iter_kind("datei")
                            if pkg.text(element, "originalName") == "report & Ä.txt")
    assert files.is_valid_name(pkg.text(normalized_folder, "name"))
    assert files.is_valid_name(pkg.text(normalized_file, "name"))
    assert pkg.fs_path(normalized_file).read_bytes() == payload
    pkg.save()

    pkg = SipPackage.open_folder(pkg.root_dir)
    assert files.denormalize(pkg) == 2
    restored_folder = next(element for element in pkg.iter_kind("ordner")
                           if pkg.text(element, "name") == "Gruppe & Ä")
    restored_file = next(element for element in pkg.iter_kind("datei")
                         if pkg.text(element, "name") == "report & Ä.txt")
    assert pkg.fs_path(restored_file).read_bytes() == payload
    pkg.save()

    reopened = SipPackage.open_folder(pkg.root_dir)
    assert any(pkg.text(element, "name") == "Gruppe & Ä" for element in reopened.iter_kind("ordner"))
    restored_file = next(element for element in reopened.iter_kind("datei")
                         if reopened.text(element, "name") == "report & Ä.txt")
    assert reopened.fs_path(restored_file).read_bytes() == payload
    assert not {issue["checkId"] for issue in Validator(reopened).run(full=True)} & {
        "schema", "checksum", "missing_file", "surplus_file"}


def test_mass_assign_rules(new_pkg):
    with pytest.raises(structure.StructureError):
        structure.mass_assign(new_pkg, [], new_pkg.classification_system, 0, 1)


def test_rename_delete_and_references(new_pkg, tmp_path):
    pkg = new_pkg
    content = next(o for o in pkg.iter_kind("ordner") if pkg.text(o, "name") == "content")
    folder = files.create_folder(pkg, content, "akten")
    with pytest.raises(files.FileOperationError):
        files.create_folder(pkg, content, "nicht&erlaubt")
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4")
    files.import_paths(pkg, folder, [src])
    file_el = pkg.children(folder, "datei")[0]
    position = next(pkg.iter_kind("ordnungssystemposition"))
    dossier = structure.add_objects(pkg, position, "dossier")[0]
    assert structure.assign_files(pkg, dossier, [file_el]) == 1

    files.rename(pkg, file_el, "b.pdf")
    assert (pkg.root_dir / "content" / "akten" / "b.pdf").is_file()
    assert pkg.text(file_el, "originalName") == "a.pdf"
    with pytest.raises(files.FileOperationError):
        files.rename(pkg, file_el, "ä.pdf")

    src.write_bytes(b"%PDF-1.4 changed")
    old = pkg.text(file_el, "pruefsumme")
    files.replace_file(pkg, file_el, src)
    assert pkg.text(file_el, "pruefsumme") != old

    files.delete_entries(pkg, [folder])
    assert not (pkg.root_dir / "content" / "akten").exists()
    assert pkg.referenced_ids() == set()
    with pytest.raises(files.FileOperationError):
        files.delete_entries(pkg, [content])


def test_fix_actions(new_pkg):
    pkg = new_pkg
    (pkg.root_dir / "stray.txt").write_text("x")
    (pkg.root_dir / "content" / "neu").mkdir()
    (pkg.root_dir / "content" / "neu" / "x.pdf").write_bytes(b"%PDF")
    issues = Validator(pkg).run(full=True)
    assert {"illegal_entries", "surplus_file", "surplus_folder"} <= {i["checkId"] for i in issues}
    files.delete_from_file_system(pkg, "stray.txt")
    assert files.add_to_table_of_contents(pkg, "content/neu") == 1
    with pytest.raises(files.FileOperationError):
        files.delete_from_file_system(pkg, "../outside")
    found = {i["checkId"] for i in Validator(pkg).run(full=True)}
    assert not found & {"illegal_entries", "surplus_file", "surplus_folder"}

    file_el = next(f for f in pkg.iter_kind("datei") if pkg.text(f, "name") == "x.pdf")
    (pkg.root_dir / "content" / "neu" / "x.pdf").unlink()
    files.remove_from_metadata(pkg, file_el)
    assert "missing_file" not in {i["checkId"] for i in Validator(pkg).run(full=True)}

    pkg.set_text(pkg.delivery, "ablieferungstyp", "")
    assert files.set_delivery_type(pkg) == "GEVER"


def test_objects_move_renumber_and_time_spans(new_pkg):
    pkg = new_pkg
    system = pkg.classification_system
    first = next(pkg.iter_kind("ordnungssystemposition"))
    a, b = structure.add_objects(pkg, system, "ordnungssystemposition", 2)
    child = structure.add_objects(pkg, first, "ordnungssystemposition")[0]
    dossier = structure.add_objects(pkg, child, "dossier")[0]
    with pytest.raises(structure.StructureError):
        structure.add_objects(pkg, child, "ordnungssystemposition")
    with pytest.raises(structure.StructureError):
        structure.move(pkg, first, child)
    structure.move(pkg, child, b)
    assert child.getparent() is b
    assert structure.move_relative(pkg, b, -1)
    assert structure.renumber(pkg, "decimal") == 4
    assert sorted(pkg.text(p, "nummer") for p in pkg.iter_kind("ordnungssystemposition")) == ["0", "1", "10", "2"]
    assert structure.renumber(pkg, "hundred") == 4
    assert pkg.text(child, "nummer") == "0100"

    doc = structure.add_objects(pkg, dossier, "dokument")[0]
    pkg.set_time_span(doc, "entstehungszeitraum", "01.02.2001", "ca. 2003")
    assert pkg.time_span_display(doc, "entstehungszeitraum") == ("01.02.2001", "ca. 2003")
    assert structure.accumulate_time_spans(pkg, dossier) == ("2001-02-01", "2003")
    structure.propagate_time_span_up(pkg, dossier)
    assert pkg.time_span(pkg.delivery, "entstehungszeitraum") == ("2001-02-01", "2003")
    assert _schema_errors(pkg) == []

    assert structure.mass_edit(pkg, system, "dossier", "inhalt", "Gleich") == 1
    assert pkg.text(dossier, "inhalt") == "Gleich"
    assert "dokument" in structure.mass_edit_kinds(pkg, system)
    structure.delete(pkg, [dossier])
    with pytest.raises(structure.StructureError):
        structure.delete(pkg, [system])


def test_xsd_choices(new_pkg):
    info = xsd_info.for_package(new_pkg)
    dossier_type = "dossierGever"
    assert info.choices(dossier_type, "erscheinungsform") == ["keine Angabe", "digital", "nicht digital", "gemischt"]
    assert info.choices(dossier_type, "datenschutz") == ["true", "false"]
    assert info.is_required(dossier_type, "titel")
    assert info.choices("datei", "pruefalgorithmus") == ["MD5", "SHA-1", "SHA-256", "SHA-512"]
