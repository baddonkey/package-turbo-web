import csv
from collections import Counter
from pathlib import Path

from openpyxl import Workbook

from tools.data_dictionary_matrix import dictionary_rows


MATRIX = Path(__file__).resolve().parents[1] / "docs" / "ech0160-data-dictionary.csv"
RUNTIME_REQUIREMENTS = Path(__file__).resolve().parents[1] / "ph_backend" / "resources" / "metadata_requirements.csv"


def test_data_dictionary_matrix_covers_profiles_and_release_mappings():
    with MATRIX.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    assert len(rows) == 969
    assert {row["release"] for row in rows} == {"1.0", "1.1", "1.2.0", "1.3.0"}
    assert {row["profile"] for row in rows} == {"GEVER", "FILES"}
    assert all(row["requirementLevel"] in {"M", "K"} for row in rows)
    assert all(row["assessment"] == ("presence test covered; semantic and XSD conformance pending"
                                     if row["requirementLevel"] == "M" else "not-assessed") for row in rows)
    assert all(row["sourceSheet"] and row["sourceRow"] for row in rows)
    assert all("test_metadata_requirement_coverage.py" in row["testEvidence"]
               for row in rows if row["requirementLevel"] == "M")
    assert "valueRangeSpecified" in rows[0] and "valueRange" not in rows[0]

    mandatory = Counter((row["release"], row["profile"]) for row in rows
                        if row["requirementLevel"] == "M")
    assert mandatory[("1.3.0", "GEVER")] == 38
    assert mandatory[("1.3.0", "FILES")] == 28


def test_data_dictionary_matrix_preserves_repeated_author_requirement():
    with MATRIX.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    authors = [row for row in rows if row["release"] == "1.3.0" and row["entity"] == "Dokument"
               and row["xsdName"] == "autor"]
    assert {row["profile"] for row in authors} == {"GEVER", "FILES"}
    assert all(row["requirementLevel"] == "K" and row["multiple"] == "x" for row in authors)
    assert all("test_repeated_authors_preserve_order_and_roundtrip" in row["testEvidence"] for row in authors)


def test_data_dictionary_parser_reads_xlsx_content_with_legacy_xls_extension(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Dokument"
    sheet.append(("Entität: Dokument",))
    sheet.append(())
    sheet.append(("Name", "Name im XSD", "Definition", "Relevanz GEVER", "Relevanz FILES",
                  "Datentyp", "Wertebereich/ Werteliste", "Beispiel", "mehrfach vorkommend"))
    sheet.append(("Autor", "autor", "omitted from output", "k", "k", "text4", None, None, "x"))
    xlsx = tmp_path / "dictionary.xlsx"
    legacy_name = tmp_path / "dictionary.xls"
    workbook.save(xlsx)
    legacy_name.write_bytes(xlsx.read_bytes())

    rows = dictionary_rows(legacy_name, "1.0")

    assert len(rows) == 2
    assert {row["profile"] for row in rows} == {"GEVER", "FILES"}
    assert all(row["release"] == "1.0" and row["schemaVersion"] == "4.0" for row in rows)
    assert all(row["sourceRow"] == "4" and row["multiple"] == "x" for row in rows)
    assert "definition" not in rows[0]


def test_runtime_requirements_match_every_mandatory_dictionary_entry():
    with MATRIX.open(encoding="utf-8-sig", newline="") as stream:
        matrix_rows = list(csv.DictReader(stream))
    with RUNTIME_REQUIREMENTS.open(encoding="utf-8-sig", newline="") as stream:
        runtime_rows = list(csv.DictReader(stream))

    keys = ("release", "schemaVersion", "profile", "entity", "xsdName", "dataType", "sourceSheet", "sourceRow")
    mandatory = {tuple(row[key] for key in keys) for row in matrix_rows if row["requirementLevel"] == "M"}
    runtime = {tuple(row[key] for key in keys) for row in runtime_rows}

    assert runtime == mandatory