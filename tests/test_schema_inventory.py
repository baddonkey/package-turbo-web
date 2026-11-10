import csv
import json

from tools.schema_inventory import inventory_rows, write_inventory


def test_inventory_compiles_and_covers_all_bundled_releases():
    rows = inventory_rows()
    assert len(rows) > 2500
    assert {row["echRelease"] for row in rows} == {"1.0", "1.1", "1.2", "1.3"}
    assert {row["areldaSchemaVersion"] for row in rows} == {"4.0", "4.1", "5.0", "5.1"}
    assert {row["declarationKind"] for row in rows} >= {"complexType", "element", "attribute", "simpleType"}
    assert all(row["implementationSurface"] and row["reviewStatus"] for row in rows)


def test_inventory_records_repeated_attributes_facets_and_recursion():
    rows = inventory_rows()
    authors = [row for row in rows if row["declarationKind"] == "element" and row["member"] == "autor"
               and row["complexType"] == "dokumentGever"]
    assert {row["echRelease"] for row in authors} == {"1.0", "1.1", "1.2", "1.3"}
    assert all(row["minOccurs"] == 0 and row["maxOccurs"] == "unbounded" for row in authors)
    attributes = {(row["echRelease"], row["member"]) for row in rows
                  if row["declarationKind"] == "attribute" and row["complexType"] == "dateiRef"}
    expected = {"version", "repraesentation", "information", "reihung"}
    assert {(release, name) for release, name in attributes if release in ("1.0", "1.1")} == set()
    assert {(release, name) for release, name in attributes if release in ("1.2", "1.3")} == {
        (release, name) for release in ("1.2", "1.3") for name in expected}
    assert all(row["reviewStatus"].startswith("tested workflow slice") for row in rows
               if row["declarationKind"] == "attribute" and row["complexType"] == "dateiRef")
    recursive = [row for row in rows if row["echRelease"] == "1.3"
                 and row["complexType"] == "mappeDossierGever" and row["member"] == "mappe"]
    assert recursive and all(row["maxOccurs"] == "unbounded" for row in recursive)
    legacy_ref = [row for row in rows if row["echRelease"] == "1.0"
                  and row["declarationKind"] == "simpleType" and row["member"] == "dateiRef"]
    assert legacy_ref and any({"name": "length", "value": "1"} in json.loads(row["facets"])
                              for row in legacy_ref)


def test_inventory_generator_is_deterministic(tmp_path):
    first, second = tmp_path / "first.csv", tmp_path / "nested" / "second.csv"
    count = write_inventory(first)
    assert write_inventory(second) == count
    with first.open(encoding="utf-8-sig", newline="") as stream:
        parsed = list(csv.DictReader(stream))
    assert len(parsed) == count
    assert first.read_bytes() == second.read_bytes()