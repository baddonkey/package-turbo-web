import random

import pytest

from ph_backend import notes, reports, sampling, search, statistics, xsd_info
from ph_backend.sip import SipPackage
from ph_backend.validation import Validator


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_repeated_authors_preserve_order_and_roundtrip(tmp_path, version, delivery_type):
    from ph_backend import package_factory, structure

    pkg = package_factory.create_package(tmp_path, "SIP_20261105_Test_Repeated", "Test",
                                         version=version, delivery_type=delivery_type)
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    document = structure.add_objects(pkg, dossier, "dokument")[0]
    pkg.set_repeated_texts(document, "autor", ["First author", "Second author"])
    assert pkg.repeated_texts(document, "autor") == ["First author", "Second author"]
    pkg.set_repeated_texts(document, "autor", ["Changed author", "Second author", "Third author"])
    assert pkg.repeated_texts(document, "autor") == ["Changed author", "Second author", "Third author"]
    assert not [issue for issue in Validator(pkg).run(full=False) if issue["checkId"] == "schema"]
    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    assert reopened.repeated_texts(next(reopened.iter_kind("dokument")), "autor") == ["Changed author", "Second author", "Third author"]


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_reference_attributes_follow_version_without_changing_assignments(tmp_path, version, delivery_type):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(tmp_path, "SIP_20261105_Test_References", "Test",
                                         version=version, delivery_type=delivery_type)
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    source = tmp_path / "reference.txt"
    source.write_bytes(b"reference bytes")
    files.import_paths(pkg, content, [source])
    file_element = next(element for element in pkg.children(content, "datei"))
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    document = structure.add_objects(pkg, dossier, "dokument")[0]
    structure.assign_files(pkg, document, [file_element])
    before = pkg.referenced_ids()
    details = pkg.reference_attributes(document)
    if version in ("1.0", "1.1"):
        assert details[0]["attributes"] == []
        with pytest.raises(ValueError, match="schema does not allow"):
            pkg.set_reference_attribute(document, 0, "version", "1")
        assert not pkg.children(document, "dateiRef")[0].attrib
    else:
        assert {attribute["name"] for attribute in details[0]["attributes"]} == {
            "version", "repraesentation", "information", "reihung"}
        for name, value in (("version", "2"), ("repraesentation", "master"), ("information", "original"), ("reihung", "1")):
            pkg.set_reference_attribute(document, 0, name, value)
        reference = pkg.children(document, "dateiRef")[0]
        assert reference.get("version") == "2" and reference.text == file_element.get("id")
        pkg.set_reference_attribute(document, 0, "information", "")
        assert "information" not in reference.attrib and reference.get("version") == "2"
        pkg.set_reference_attribute(document, 0, "information", "original")
    assert pkg.referenced_ids() == before
    assert not [issue for issue in Validator(pkg).run(full=False) if issue["checkId"] == "schema"]
    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    assert reopened.referenced_ids() == before
    assert not [issue for issue in Validator(reopened).run(full=True) if issue["checkId"] == "invalid_reference"]
    reopened_reference = next(reopened.iter_kind("dateiRef"))
    if version in ("1.0", "1.1"):
        assert not reopened_reference.attrib
    else:
        assert {name: reopened_reference.get(name) for name in
                ("version", "repraesentation", "information", "reihung")} == {
                    "version": "2", "repraesentation": "master", "information": "original", "reihung": "1"}
    assert not [issue for issue in Validator(reopened).run(full=True) if issue["checkId"] == "schema"]
    if version in ("1.2", "1.3"):
        assert next(reopened.iter_kind("dateiRef")).get("repraesentation") == "master"


def test_incoming_file_references_preserve_owner_and_shared_targets(tmp_path):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(tmp_path, "SIP_20261109_Test_IncomingReferences", "Test",
                                         version="1.3", delivery_type="FILES")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    nested_folder = files.create_folder(pkg, content, "Nested")
    sources = [tmp_path / "first.txt", tmp_path / "second.txt", tmp_path / "unused.txt"]
    for source in sources:
        source.write_text(source.stem, encoding="utf-8")
    files.import_paths(pkg, nested_folder, sources)
    first_file, second_file, unused_file = pkg.children(nested_folder, "datei")

    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    subdossier = structure.add_objects(pkg, dossier, "dossier")[0]
    document = structure.add_objects(pkg, subdossier, "dokument")[0]
    structure.assign_files(pkg, dossier, [first_file])
    structure.assign_files(pkg, subdossier, [first_file])
    structure.assign_files(pkg, document, [first_file])
    shared_reference = pkg.children(document, "dateiRef")[0]
    shared_reference.text = f"{first_file.get('id')} {second_file.get('id')}"
    shared_reference.set("version", "Edition 2")

    incoming = pkg.incoming_reference_attributes(first_file)
    references_by_kind = {reference["ownerKind"]: reference for reference in incoming}
    assert set(references_by_kind) == {"dossier", "subdossier", "dokument"}
    assert references_by_kind["dossier"]["ownerKey"] == pkg.key(dossier)
    assert references_by_kind["subdossier"]["ownerKey"] == pkg.key(subdossier)
    shared = references_by_kind["dokument"]
    assert shared["index"] == 0
    assert shared["fileIds"] == [first_file.get("id"), second_file.get("id")]
    assert "first" in shared["name"] and "second" in shared["name"]
    assert {attribute["name"] for attribute in shared["attributes"]} == {
        "version", "repraesentation", "information", "reihung"}
    assert next(attribute for attribute in shared["attributes"] if attribute["name"] == "version")["value"] == "Edition 2"
    assert [reference["ownerKey"] for reference in pkg.incoming_reference_attributes(second_file)] == [
        pkg.key(document)]

    assert pkg.incoming_reference_attributes(dossier) == []
    target_options = pkg.reference_targets(unused_file)
    assert {target["ownerKey"] for target in target_options} == {
        pkg.key(dossier), pkg.key(subdossier), pkg.key(document)}
    pkg.set_reference_attribute(document, shared["index"], "information", "Shared scan")
    assert shared_reference.get("information") == "Shared scan"
    updated_shared = next(reference for reference in pkg.incoming_reference_attributes(first_file)
                          if reference["ownerKind"] == "dokument")
    assert next(attribute for attribute in updated_shared["attributes"]
                if attribute["name"] == "information")["value"] == "Shared scan"
    assert pkg.incoming_reference_attributes(unused_file) == []


@pytest.mark.parametrize("version", ["1.1", "1.2", "1.3"])
def test_incoming_file_reference_attributes_follow_schema_release(tmp_path, version):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(tmp_path, f"SIP_20261109_Test_Incoming_{version}", "Test",
                                         version=version, delivery_type="FILES")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    source = tmp_path / f"reference-{version}.txt"
    source.write_text("reference", encoding="utf-8")
    files.import_paths(pkg, content, [source])
    file_element = pkg.children(content, "datei")[0]
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    document = structure.add_objects(pkg, dossier, "dokument")[0]
    structure.assign_files(pkg, document, [file_element])

    incoming = pkg.incoming_reference_attributes(file_element)
    assert len(incoming) == 1
    assert incoming[0]["ownerKind"] == "dokument"
    attribute_names = {attribute["name"] for attribute in incoming[0]["attributes"]}
    if version == "1.1":
        assert attribute_names == set()
    else:
        assert attribute_names == {"version", "repraesentation", "information", "reihung"}


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_reference_target_choices_follow_schema_and_exclude_existing_links(tmp_path, version, delivery_type):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(tmp_path, f"SIP_20261109_Test_Targets_{version}_{delivery_type}", "Test",
                                         version=version, delivery_type=delivery_type)
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    source = tmp_path / f"candidate-{version}.txt"
    source.write_text("candidate", encoding="utf-8")
    files.import_paths(pkg, content, [source])
    file_element = pkg.children(content, "datei")[0]
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    document = structure.add_objects(pkg, dossier, "dokument")[0]

    targets = pkg.reference_targets(file_element)
    target_keys = {target["ownerKey"] for target in targets}
    expected_keys = {pkg.key(document)} if delivery_type == "GEVER" else {pkg.key(dossier), pkg.key(document)}
    assert target_keys == expected_keys
    assert all(target["label"] and target["ownerKind"] in ("dossier", "dokument") for target in targets)

    initial_target = document if delivery_type == "GEVER" else dossier
    assert structure.assign_files(pkg, initial_target, [file_element]) == 1
    remaining = pkg.reference_targets(file_element)
    if delivery_type == "GEVER":
        assert len(remaining) == 1 and remaining[0]["ownerKey"] == pkg.key(document)
        assert remaining[0]["hasReference"] is True
        assert structure.add_file_reference(pkg, document, file_element)
        references = pkg.children(document, "dateiRef")
        assert len(references) == 2
        if version in ("1.2", "1.3"):
            references[1].set("version", "Second version")
        assert not [issue for issue in Validator(pkg).run(full=False) if issue["checkId"] == "schema"]
    else:
        assert pkg.key(initial_target) not in {target["ownerKey"] for target in remaining}
        assert [target["ownerKey"] for target in remaining] == [pkg.key(document)]
        assert structure.assign_files(pkg, document, [file_element]) == 1
        assert pkg.reference_targets(file_element) == []


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_files_relationship_targets_roundtrip_by_release(tmp_path, version):
    from ph_backend import files, package_factory, structure
    from ph_backend.sip import localname

    pkg = package_factory.create_package(tmp_path, f"SIP_20261107_Test_FILES_Relations_{version}", "Test",
                                         version=version, delivery_type="FILES")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    position = next(pkg.iter_kind("ordnungssystemposition"))
    dossier = structure.add_objects(pkg, position, "dossier")[0]
    document = structure.add_objects(pkg, dossier, "dokument")[0]
    targets = [("dossier", dossier), ("dokument", document)]
    if "mappe" in structure.allowed_children(pkg, dossier):
        targets.append(("mappe", structure.add_objects(pkg, dossier, "mappe")[0]))

    sources = []
    for index, (kind, _) in enumerate(targets):
        source = tmp_path / f"{kind}-{index}.txt"
        source.write_text(f"{kind} reference", encoding="utf-8")
        sources.append(source)
    files.import_paths(pkg, content, sources)
    file_elements = list(pkg.children(content, "datei"))

    expected = []
    for (kind, owner), file_element in zip(targets, file_elements):
        assert structure.assign_files(pkg, owner, [file_element]) == 1
        assert pkg.referenced_files(owner) == [file_element]
        expected.append((kind, file_element.get("id")))

    schema_issues = [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "schema"]
    assert not schema_issues, [(issue["description"], issue["value"]) for issue in schema_issues]
    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    actual = [(localname(reference.getparent()), identifier)
              for reference in reopened.root.iter()
              if localname(reference) == "dateiRef"
              for identifier in (reference.text or "").split()]

    assert set(actual) == set(expected)
    issues = Validator(reopened).run(full=True)
    assert not [issue for issue in issues if issue["checkId"] in {"schema", "invalid_reference"}]


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_files_layout_and_classification_roundtrip_by_release(tmp_path, version):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261107_Test_FILES_LayoutClassification_{version}", "Test",
        version=version, delivery_type="FILES")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    group = files.create_folder(pkg, content, "series")
    source = tmp_path / "primary.txt"
    source.write_text("primary content", encoding="utf-8")
    files.import_paths(pkg, group, [source])
    file_element = next(element for element in pkg.children(group, "datei"))
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    structure.assign_files(pkg, dossier, [file_element])
    file_id = file_element.get("id")

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_file = reopened.file_by_id(file_id)
    reopened_dossier = next(reopened.iter_kind("dossier"))

    assert reopened.relpath(reopened_file).as_posix() == "content/series/primary.txt"
    assert reopened.fs_path(reopened_file).is_file()
    assert reopened.referenced_files(reopened_dossier) == [reopened_file]
    valid_issues = Validator(reopened).run(full=True)
    assert not [issue for issue in valid_issues
                if issue["checkId"] in {"schema", "invalid_reference", "unassigned_files",
                                        "missing_file", "surplus_file", "missing_folder", "surplus_folder"}]

    reference = reopened.children(reopened_dossier, "dateiRef")[0]
    reopened_dossier.remove(reference)
    unassigned = [issue for issue in Validator(reopened).run(full=True)
                  if issue["checkId"] == "unassigned_files"]
    assert any(issue["value"] == "content/series/primary.txt" for issue in unassigned), version


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_files_grouping_assignment_roundtrip_by_release(tmp_path, version):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(
        tmp_path, f"SIP_20261108_Test_FILES_Grouping_{version}", "Test",
        version=version, delivery_type="FILES")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    group = files.create_folder(pkg, content, "series")
    sources = []
    for name in ("assigned.txt", "unassigned.txt"):
        source = tmp_path / name
        source.write_text(name, encoding="utf-8")
        sources.append(source)
    files.import_paths(pkg, group, sources)
    file_elements = {pkg.text(element, "name"): element for element in pkg.children(group, "datei")}
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    if "mappe" in structure.allowed_children(pkg, dossier):
        assignment_target = structure.add_objects(pkg, dossier, "mappe")[0]
        target_kind = "mappe"
    else:
        assignment_target = dossier
        target_kind = "dossier"
    assert structure.assign_files(pkg, assignment_target, [file_elements["assigned.txt"]]) == 1

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir)
    reopened_dossier = next(reopened.iter_kind("dossier"))
    reopened_target = (next(reopened_dossier.iter(reopened.q("mappe")))
                       if target_kind == "mappe" else reopened_dossier)
    reopened_assigned = reopened.file_by_id(file_elements["assigned.txt"].get("id"))
    reopened_unassigned = reopened.file_by_id(file_elements["unassigned.txt"].get("id"))

    assert reopened.relpath(reopened_assigned).as_posix() == "content/series/assigned.txt"
    assert reopened.fs_path(reopened_assigned).is_file()
    assert reopened.referenced_files(reopened_target) == [reopened_assigned]
    assert reopened.relpath(reopened_unassigned).as_posix() == "content/series/unassigned.txt"
    issues = Validator(reopened).run(full=True)
    assert not [issue for issue in issues if issue["checkId"] in {"schema", "invalid_reference"}]
    unassigned = [issue for issue in issues if issue["checkId"] == "unassigned_files"]
    assert [issue["value"] for issue in unassigned] == ["content/series/unassigned.txt"], version


def test_v13_datei_ref_supports_multiple_file_ids(tmp_path):
    from ph_backend import files, package_factory, structure

    pkg = package_factory.create_package(tmp_path, "SIP_20261105_Test_MultiRef", "Test",
                                         version="1.3", delivery_type="FILES")
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    sources = []
    for name in ("first.txt", "second.txt"):
        source = tmp_path / name
        source.write_text(name, encoding="utf-8")
        sources.append(source)
    files.import_paths(pkg, content, sources)
    file_elements = list(pkg.children(content, "datei"))
    dossier = structure.add_objects(pkg, next(pkg.iter_kind("ordnungssystemposition")), "dossier")[0]
    document = structure.add_objects(pkg, dossier, "dokument")[0]
    structure.assign_files(pkg, document, file_elements[:1])
    reference = pkg.children(document, "dateiRef")[0]
    reference.text = " ".join(file_element.get("id") for file_element in file_elements)

    assert pkg.referenced_ids() == {file_element.get("id") for file_element in file_elements}
    assert pkg.referenced_files(document) == file_elements
    assert pkg.reference_attributes(document)[0]["name"] == "first.txt, second.txt"
    found = {issue["checkId"] for issue in Validator(pkg).run(full=True)}
    assert not found & {"schema", "unassigned_files"}


def test_repeated_row_reordering_preserves_element_attributes(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    document = next(pkg.iter_kind("dokument"))
    pkg.set_repeated_texts(document, "autor", ["First", "Second"])
    first, second = pkg.children(document, "autor")
    first.set("{urn:vendor}source", "first-source")
    second.set("{urn:vendor}source", "second-source")
    pkg.set_repeated_texts(document, "autor", ["Second", "Changed first"], source_indices=[1, 0])
    assert pkg.children(document, "autor") == [second, first]
    assert second.get("{urn:vendor}source") == "second-source"
    pkg.set_repeated_texts(document, "autor", ["Changed first"], source_indices=[1])
    assert pkg.children(document, "autor") == [first]
    assert first.get("{urn:vendor}source") == "first-source"


def test_filename_validation_matches_ech0160_ascii_subset():
    from ph_backend import files

    allowed = "AZaz09!#$%()+,-.=@[]{}~_ "
    assert files.is_valid_name(allowed)
    assert files.sanitize_name(allowed) == allowed
    assert not files.is_valid_name("name&value.txt")
    assert not files.is_valid_name("name\\value.txt")
    assert not files.is_valid_name("é.txt")
    assert not files.is_valid_name(".")
    assert not files.is_valid_name("..")


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
@pytest.mark.parametrize("delivery_type", ["GEVER", "FILES"])
def test_file_eigenschaft_roundtrips_in_sip(tmp_path, version, delivery_type):
    from ph_backend import files, package_factory

    pkg = package_factory.create_package(tmp_path, f"SIP_20261106_Test_Eigenschaft_{version}_{delivery_type}", "Test",
                                         version=version, delivery_type=delivery_type)
    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    source = tmp_path / "table.csv"
    source.write_text("a;b\n1;2\n", encoding="utf-8")
    files.import_paths(pkg, content, [source])
    file_element = next(element for element in pkg.children(content, "datei"))
    pkg.set_file_properties(file_element, [("Feldtrenner", ";"), ("Zeichensatz", "UTF-8")])
    assert pkg.file_properties(file_element) == [("Feldtrenner", ";"), ("Zeichensatz", "UTF-8")]
    assert not [issue for issue in Validator(pkg).run(full=True) if issue["checkId"] == "schema"]

    pkg.save()
    reopened = SipPackage.open_folder(pkg.root_dir, read_only=True)
    reopened_file = next(element for element in reopened.iter_kind("datei")
                         if reopened.text(element, "name") == "table.csv")
    assert reopened.file_properties(reopened_file) == [("Feldtrenner", ";"), ("Zeichensatz", "UTF-8")]


def test_scalar_bulk_edit_cannot_overwrite_repeated_authors(sip_dir):
    from ph_backend import structure

    pkg = SipPackage.open_folder(sip_dir)
    document = next(pkg.iter_kind("dokument"))
    pkg.set_repeated_texts(document, "autor", ["First", "Second"])
    assert "autor" not in {field["tag"] for field in structure.mass_edit_fields("dokument", pkg)}
    with pytest.raises(structure.StructureError, match="lists"):
        structure.mass_edit(pkg, pkg.classification_system, "dokument", "autor", "Not a list")
    assert pkg.repeated_texts(document, "autor") == ["First", "Second"]


def test_repeated_values_preserve_multiline_content(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    document = next(pkg.iter_kind("dokument"))
    pkg.set_repeated_texts(document, "autor", ["First line\nSecond line", "Other author"])
    assert pkg.repeated_texts(document, "autor") == ["First line\nSecond line", "Other author"]


def test_additional_data_on_all_allowed_objects(sample_sip):
    pkg = SipPackage.open_folder(sample_sip)
    info = xsd_info.for_package(pkg)
    targets = [pkg.root, pkg.delivery, pkg.classification_system] + [
        next(pkg.iter_kind(k)) for k in ("ordnungssystemposition", "dossier", "dokument")]
    for el in targets:
        assert info.element(xsd_info.type_name(pkg, el, info), "zusatzDaten") is not None
        pkg.set_additional_data(el, [("Herkunft", "Scan"), ("Projekt", "A-17")])
    assert pkg.additional_data(pkg.delivery) == [("Herkunft", "Scan"), ("Projekt", "A-17")]
    assert not [i for i in Validator(pkg).run(full=False) if i["checkId"] == "schema"]
    pkg.save()
    reopened = SipPackage.open_folder(sample_sip)
    assert reopened.additional_data(next(reopened.iter_kind("dossier"))) == [("Herkunft", "Scan"), ("Projekt", "A-17")]
    reopened.set_additional_data(reopened.delivery, [("", "")])
    assert reopened.child(reopened.delivery, "zusatzDaten") is None


def test_additional_data_and_search(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    dossier = next(pkg.iter_kind("dossier"))
    pkg.set_additional_data(dossier, [("Signatur", "XY-1")])
    hits = search.simple_search(pkg, "xy-1")
    assert hits and hits[0]["field"] == "Zusatzdaten"
    found = search.advanced_search(pkg, [search.Criterion("dossier", "zusatzDaten", "Signatur")])
    assert [h["objectName"] for h in found] == ["Dossier mit Datei"]


def test_notes_roundtrip(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    dossier = next(pkg.iter_kind("dossier"))
    note = notes.add_note(pkg, dossier, "Bitte prüfen", author="tester")
    assert notes.has_notes(pkg, dossier)
    rows = notes.all_notes(pkg)
    assert rows[0]["comment"] == "Bitte prüfen" and rows[0]["author"] == "tester"
    notes.edit_note(pkg, note, "Erledigt")
    pkg.save()
    reopened = SipPackage.open_folder(sip_dir)
    assert notes.all_notes(reopened)[0]["comment"] == "Erledigt"
    notes.delete_note(reopened, reopened.element(notes.all_notes(reopened)[0]["noteKey"]))
    assert notes.all_notes(reopened) == []


def test_notes_are_schema_valid(sample_sip):
    pkg = SipPackage.open_folder(sample_sip)
    for el in [pkg.delivery, next(pkg.iter_kind("dossier")), next(pkg.iter_kind("datei"))]:
        notes.add_note(pkg, el, "Notiz")
    assert not [i for i in Validator(pkg).run(full=False) if i["checkId"] == "schema"]
    with pytest.raises(notes.NoteError):
        notes.add_note(pkg, next(pkg.iter_kind("ordner")), "x")


def test_simple_and_advanced_search(sip_dir):
    pkg = SipPackage.open_folder(sip_dir)
    assert len(search.simple_search(pkg, "BERICHT")) >= 2
    hits = search.advanced_search(pkg, [search.Criterion("dossier", "titel", "dossier"),
                                        search.Criterion("dossier", "aktenzeichen", "1.1")])
    assert [h["objectName"] for h in hits] == ["Dossier mit Datei"]
    assert hits[0]["value1"] == "1.1"
    empty = search.advanced_search(pkg, [search.Criterion("dossier", "schutzfrist", empty=True)])
    assert len(empty) == 2
    assert search.advanced_search(pkg, [search.Criterion("dossier", "titel", "x"),
                                        search.Criterion("dokument", "titel", "x")]) == []
    with pytest.raises(search.TooManyHits):
        search.simple_search(pkg, "e", max_hits=1)


def test_statistics(sample_sip):
    pkg = SipPackage.open_folder(sample_sip)
    data = statistics.overview(pkg)
    labels = {r["label"]: r["value"] for r in data["records"]}
    assert int(labels["Anzahl Dossiers"]) > 0 and int(labels["Maximale OSP Verzeichnungstiefe"]) >= 1
    assert data["positionsPerLevel"] and data["documentsByFileCount"]
    chart = statistics.time_span_comparison(pkg)
    assert [b["label"] for b in chart["bars"]][:3] == ["Aktenbildner", "Ordnungssystem", "Ablieferung"]
    assert chart["bars"][0]["state"] == "open"
    assert chart["from"] <= 1966 < chart["to"]
    assert statistics.protection_periods(pkg)


def test_sampling(sample_sip):
    pkg = SipPackage.open_folder(sample_sip)
    total = sum(1 for d in pkg.iter_kind("dossier") if pkg.kind(d) == "dossier")
    assert len(sampling.draw_sample(pkg, sampling.ABSOLUTE, 3, random.Random(1))) == min(3, total)
    assert len(sampling.draw_sample(pkg, sampling.PERCENT, 100)) == total
    assert sampling.draw_sample(pkg, sampling.ABSOLUTE, 0) == []


@pytest.mark.parametrize("report", list(reports.REPORTS))
def test_reports(sip_dir, report):
    pkg = SipPackage.open_folder(sip_dir)
    pkg.set_text(next(pkg.iter_kind("dossier")), "titel", "<script>x</script>")
    html = reports.build_html(pkg, report, "Tester")
    assert "<script>" not in html and "Testamt" in html
