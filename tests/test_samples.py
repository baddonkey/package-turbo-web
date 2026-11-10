"""Regression tests with the public KOST-CECO eCH-0160 test packages (downloaded to samples/, skipped otherwise)."""

import os
from pathlib import Path

import pytest

from ph_backend import statistics
from ph_backend.sip import SipPackage
from ph_backend.validation import Validator

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
REQUIRED_KOST_SAMPLE = "SIP_20260421_KOST_eCH1.3F_1a-3d-IO"

SAMPLES = [
    "SIP_20070923_eCH1.0G_RIS",
    "SIP_20070923_eCH1.1G_RIS_vorgang",
    "SIP_20120628_eCH1.0F_BfB",
    "SIP_20220328_KOST_eCH1.2G_1a-3d-IO",
    "SIP_20220328_KOST_eCH1.2F_1a-3d-IO",
    "SIP_20260421_KOST_eCH1.3F_1a-3d-IO",
]


def _sample_folder(name, folder):
    if folder.is_dir():
        return folder
    if name == REQUIRED_KOST_SAMPLE and os.environ.get("PACKAGE_TURBO_REQUIRE_KOST_FIXTURE") == "1":
        raise FileNotFoundError(f"Required conformance fixture is missing: {folder}")
    pytest.skip(f"{name} not available in samples/")


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_versions(name):
    folder = _sample_folder(name, SAMPLES_DIR / name)
    pkg = SipPackage.open_folder(folder, read_only=True)
    issues = Validator(pkg).run(full=True)
    found = {i["checkId"] for i in issues}
    assert not found & {"schema", "checksum", "missing_file", "surplus_file", "wrong_file"}
    if pkg.root.get("schemaVersion") in {"5.0", "5.1"}:
        assert not any(i["checkId"] == "missing_values" and i["fieldTag"] == "entstehungszeitraum"
                       for i in issues)
    assert statistics.time_span_comparison(pkg)["bars"]


def test_generated_v13_gever_edge_sip_roundtrips_and_validates(tmp_path):
    from ph_backend import files, package_factory, structure
    from ph_backend.sip import SipPackage

    pkg = package_factory.create_package(tmp_path, "SIP_20261106_GEVERe2e_Test", "Testamt",
                                         version="1.3", delivery_type="GEVER")
    position = next(pkg.iter_kind("ordnungssystemposition"))
    dossier = structure.add_objects(pkg, position, "dossier")[0]
    process = structure.add_objects(pkg, dossier, "vorgang")[0]
    activity = structure.add_objects(pkg, process, "aktivitaet")[0]
    mappe = structure.add_objects(pkg, dossier, "mappe")[0]
    nested_mappe = structure.add_objects(pkg, mappe, "mappe")[0]
    document = structure.add_objects(pkg, dossier, "dokument")[0]
    nested_document = structure.add_objects(pkg, nested_mappe, "dokument")[0]
    pkg.set_repeated_texts(document, "autor", ["Ada Example", "Lin Example"])

    content = next(element for element in pkg.iter_kind("ordner") if pkg.text(element, "name") == "content")
    sources = []
    for filename, text in (("record.txt", "record bytes"), ("nested.txt", "nested bytes")):
        source = tmp_path / filename
        source.write_text(text, encoding="utf-8")
        sources.append(source)
    files.import_paths(pkg, content, sources)
    imported_files = list(pkg.children(content, "datei"))
    structure.assign_files(pkg, document, imported_files[:1])
    structure.assign_files(pkg, nested_document, imported_files[1:])
    pkg.set_reference_attribute(document, 0, "version", "1")

    issues = Validator(pkg).run(full=True)
    assert not {issue["checkId"] for issue in issues} & {
        "schema", "checksum", "missing_file", "surplus_file", "wrong_file", "missing_values", "unassigned_files"
    }, issues
    pkg.save()

    reopened = SipPackage.open_folder(pkg.root_dir, read_only=True)
    reopened_dossier = next(reopened.iter_kind("dossier"))
    reopened_document = reopened.children(reopened_dossier, "dokument")[0]
    outer_mappe = reopened.children(reopened_dossier, "mappe")[0]
    assert len(reopened.children(outer_mappe, "mappe")) == 1
    assert len(reopened.children(reopened_dossier, "vorgang")) == 1
    assert len(reopened.children(next(reopened.iter_kind("vorgang")), "aktivitaet")) == 1
    assert reopened.repeated_texts(reopened_document, "autor") == ["Ada Example", "Lin Example"]
    assert reopened.children(reopened_document, "dateiRef")[0].get("version") == "1"
    assert not {issue["checkId"] for issue in Validator(reopened).run(full=True)} & {
        "schema", "checksum", "missing_file", "surplus_file", "wrong_file", "missing_values", "unassigned_files"
    }


def test_conformance_mode_requires_v13_kost_fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("PACKAGE_TURBO_REQUIRE_KOST_FIXTURE", "1")
    with pytest.raises(FileNotFoundError, match=REQUIRED_KOST_SAMPLE):
        _sample_folder(REQUIRED_KOST_SAMPLE, tmp_path / REQUIRED_KOST_SAMPLE)
