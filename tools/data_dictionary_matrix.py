"""Build a profile-specific inventory from official eCH-0160 data dictionaries."""

import argparse
import csv
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

HEADERS = ("release", "schemaVersion", "profile", "entity", "fieldName", "xsdName",
           "requirementLevel", "dataType", "valueRangeSpecified", "multiple", "versioned",
           "attributeCategory", "sourceSheet", "sourceRow", "candidateCodeSurface",
           "testEvidence", "assessment")
RUNTIME_HEADERS = ("release", "schemaVersion", "profile", "entity", "xsdName", "dataType",
                   "sourceSheet", "sourceRow")
SCHEMA_VERSIONS = {"1.0": "4.0", "1.1": "4.1", "1.2.0": "5.0", "1.3.0": "5.1"}
PROFILES = (("GEVER", "Relevanz GEVER"), ("FILES", "Relevanz FILES"))
STRUCTURAL_FIELDS = {"ablieferung", "ablieferungstyp", "provenienz", "ordnungssystem",
                     "ordnungssystemposition", "dossier", "vorgang", "aktivitaet", "mappe",
                     "dokument", "inhaltsverzeichnis", "ordner", "datei", "dateiRef"}
REFERENCE_ATTRIBUTES = {"version", "repraesentation", "information", "reihung"}
TEST_EVIDENCE = {
    "autor": "tests/test_features.py::test_repeated_authors_preserve_order_and_roundtrip",
    "eigenschaft": "tests/test_features.py::test_file_eigenschaft_roundtrips_in_sip",
    "vorgang": "tests/test_structure.py::test_structured_process_activity_creation_and_editing",
    "aktivitaet": "tests/test_structure.py::test_structured_process_activity_creation_and_editing",
    "mappe": "tests/test_structure.py::test_recursive_mappen_use_contextual_schema_type",
    **{name: "tests/test_features.py::test_reference_attributes_follow_version_without_changing_assignments"
       for name in REFERENCE_ATTRIBUTES},
}


def _text(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def candidate_surface(xsd_name: str) -> str:
    if xsd_name in REFERENCE_ATTRIBUTES:
        return "ph_backend/sip.py; ph_web/app.py; web/src/app/app.html"
    if xsd_name == "eigenschaft":
        return "ph_backend/sip.py; ph_web/metadata.py; web/src/app/app.html"
    if xsd_name in STRUCTURAL_FIELDS:
        return "ph_backend/schema.py; ph_backend/structure.py; ph_backend/sip.py; ph_backend/validation.py"
    return "ph_backend/schema.py; ph_web/metadata.py; web/src/app/metadata-field.html"


def dictionary_rows(source: Path, release: str) -> list[dict[str, str]]:
    if release not in SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported eCH-0160 release: {release}")
    workbook = load_workbook(BytesIO(Path(source).read_bytes()), read_only=True, data_only=True)
    rows = []
    for sheet in workbook.worksheets:
        if sheet.title in {"Titelblatt", "Dokumentation"}:
            continue
        header_row = None
        columns = {}
        for row_number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            normalized = {_text(value): index for index, value in enumerate(values) if _text(value)}
            if "Name im XSD" in normalized and "Name" in normalized:
                header_row, columns = row_number, normalized
                break
        if header_row is None:
            continue

        for source_row, values in enumerate(sheet.iter_rows(min_row=header_row + 1, values_only=True),
                                            start=header_row + 1):
            field_name = _text(values[columns["Name"]]) if columns["Name"] < len(values) else ""
            xsd_name = _text(values[columns["Name im XSD"]]) if columns["Name im XSD"] < len(values) else ""
            if not field_name or not xsd_name or field_name.startswith("Entität:"):
                continue
            data_type = _text(values[columns["Datentyp"]]) if columns.get("Datentyp", len(values)) < len(values) else ""
            value_range = (_text(values[columns["Wertebereich/ Werteliste"]])
                           if columns.get("Wertebereich/ Werteliste", len(values)) < len(values) else "")
            value_range_specified = "yes" if value_range else "no"
            multiple = (_text(values[columns["mehrfach vorkommend"]])
                       if columns.get("mehrfach vorkommend", len(values)) < len(values) else "")
            versioned = _text(values[columns["versioniert"]]) if columns.get("versioniert", len(values)) < len(values) else ""
            category = (_text(values[columns["Attribut-Kategorie"]])
                        if columns.get("Attribut-Kategorie", len(values)) < len(values) else "")

            for profile, relevance_column in PROFILES:
                column = columns.get(relevance_column)
                if column is None or column >= len(values):
                    continue
                level = _text(values[column]).upper()
                if level not in {"M", "K"}:
                    continue
                test_evidence = TEST_EVIDENCE.get(xsd_name, "not mapped")
                assessment = "not-assessed"
                if level == "M":
                    test_evidence = (test_evidence + "; " if test_evidence != "not mapped" else "") + (
                        "tests/test_metadata_requirement_coverage.py::"
                        "test_every_mandatory_dictionary_field_is_reported_when_missing_and_accepted_when_present"
                        " (synthetic presence test)")
                    assessment = "presence test covered; semantic and XSD conformance pending"
                rows.append({"release": release, "schemaVersion": SCHEMA_VERSIONS[release],
                             "profile": profile, "entity": sheet.title, "fieldName": field_name,
                             "xsdName": xsd_name, "requirementLevel": level, "dataType": data_type,
                             "valueRangeSpecified": value_range_specified, "multiple": multiple,
                             "versioned": versioned,
                             "attributeCategory": category, "sourceSheet": sheet.title,
                             "sourceRow": str(source_row), "candidateCodeSurface": candidate_surface(xsd_name),
                             "testEvidence": test_evidence, "assessment": assessment})
    return rows


def write_matrix(sources: list[tuple[str, Path]], destination: Path, runtime_destination: Path) -> int:
    rows = []
    for release, source in sources:
        rows.extend(dictionary_rows(source, release))
    if {release for release, _ in sources} != set(SCHEMA_VERSIONS):
        raise ValueError("All four official releases are required to generate the runtime requirements resource.")
    rows.sort(key=lambda row: (row["release"], row["profile"], row["sourceSheet"], int(row["sourceRow"])))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    runtime_rows = [{key: row[key] for key in RUNTIME_HEADERS} for row in rows if row["requirementLevel"] == "M"]
    runtime_destination = Path(runtime_destination)
    runtime_destination.parent.mkdir(parents=True, exist_ok=True)
    with runtime_destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RUNTIME_HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(runtime_rows)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, metavar="RELEASE=FILE",
                        help="Official data-dictionary workbook; repeat for each release")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "docs" / "ech0160-data-dictionary.csv")
    parser.add_argument("--runtime-output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "ph_backend" / "resources"
                        / "metadata_requirements.csv")
    arguments = parser.parse_args()
    sources = []
    for value in arguments.source:
        release, separator, path = value.partition("=")
        if not separator or not path:
            parser.error(f"Invalid source {value!r}; expected RELEASE=FILE")
        sources.append((release, Path(path)))
    count = write_matrix(sources, arguments.output, arguments.runtime_output)
    print(f"Wrote {count} data-dictionary requirement rows to {arguments.output}")


if __name__ == "__main__":
    main()