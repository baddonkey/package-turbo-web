"""Generate a review inventory from the bundled eCH-0160 XML Schema sets.

This inventories XSD declarations only. It is not a substitute for the normative
standard text/data dictionary or evidence of complete application conformance.
"""

import argparse
import csv
import json
from pathlib import Path

from lxml import etree

from ph_backend import schema as backend_schema
from ph_backend.xsd_info import BUNDLED_XSD_DIR

XS = "{http://www.w3.org/2001/XMLSchema}"
RELEASES = (("1.0", "4.0", BUNDLED_XSD_DIR / "1.0"),
            ("1.1", "4.1", BUNDLED_XSD_DIR / "1.1"),
            ("1.2", "5.0", BUNDLED_XSD_DIR),
            ("1.3", "5.1", BUNDLED_XSD_DIR / "1.3"))
FACETS = {"enumeration", "pattern", "length", "minLength", "maxLength", "minInclusive", "maxInclusive",
          "minExclusive", "maxExclusive", "totalDigits", "fractionDigits", "whiteSpace"}
HEADERS = ("echRelease", "areldaSchemaVersion", "profile", "complexType", "declarationKind", "member",
           "valueType", "minOccurs", "maxOccurs", "use", "facets", "implementationSurface",
           "testEvidence", "reviewStatus")
ELEMENT_TAGS = {"inhaltsverzeichnis", "ablieferung", "provenienz", "ordnungssystem", "ordnungssystemposition",
                "dossier", "dokument", "vorgang", "aktivitaet", "mappe", "datei", "ordner",
                "archivischerVorgang", "archivischeNotiz", "zusatzDaten", "dateiRef", "eigenschaft",
                "unstrukturierterAnhang", "referenzUebernahmedossier"}
TEST_EVIDENCE = {
    ("element", "autor"): "tests/test_features.py::test_repeated_authors_preserve_order_and_roundtrip",
    ("element", "vorgang"): "tests/test_structure.py::test_structured_process_activity_creation_and_editing;test_v10_legacy_process_is_preserved_without_structured_conversion;test_files_schema_does_not_offer_gever_processes",
    ("element", "aktivitaet"): "tests/test_structure.py::test_structured_process_activity_creation_and_editing;test_files_schema_does_not_offer_gever_processes",
    ("element", "mappe"): "tests/test_structure.py::test_recursive_mappen_use_contextual_schema_type;test_v12_mappen_reject_v13_only_recursion",
    ("attribute", "version"): "tests/test_features.py::test_reference_attributes_follow_version_without_changing_assignments",
    ("attribute", "repraesentation"): "tests/test_features.py::test_reference_attributes_follow_version_without_changing_assignments",
    ("attribute", "information"): "tests/test_features.py::test_reference_attributes_follow_version_without_changing_assignments",
    ("attribute", "reihung"): "tests/test_features.py::test_reference_attributes_follow_version_without_changing_assignments",
}


def _local(name: str | None) -> str:
    return (name or "").split(":")[-1]


def _facets(simple_types: dict, type_name: str, seen=None) -> list[dict]:
    seen = set() if seen is None else seen
    if not type_name or type_name in seen:
        return []
    seen.add(type_name)
    definition = simple_types.get(type_name)
    if definition is None:
        return []
    found = []
    restriction = definition.find(XS + "restriction")
    if restriction is not None:
        found.extend({"name": etree.QName(facet).localname, "value": facet.get("value", "")}
                     for facet in restriction if isinstance(facet.tag, str) and etree.QName(facet).localname in FACETS)
        found.extend(_facets(simple_types, _local(restriction.get("base")), seen))
    union = definition.find(XS + "union")
    if union is not None:
        for member in (union.get("memberTypes") or "").split():
            found.append({"name": "unionMemberType", "value": _local(member)})
            found.extend(_facets(simple_types, _local(member), seen))
    list_type = definition.find(XS + "list")
    if list_type is not None and list_type.get("itemType"):
        found.append({"name": "listItemType", "value": _local(list_type.get("itemType"))})
    return found


def _kind_for_type(type_name: str) -> str:
    value = type_name
    for suffix in ("Gever", "Files"):
        if value.endswith(suffix):
            value = value[:-len(suffix)]
    kinds = {"paket": "paket", "ablieferung": "ablieferung", "ablieferungSIP": "ablieferung",
             "ablieferungGeverSIP": "ablieferung", "ablieferungFilesSIP": "ablieferung",
             "provenienz": "provenienz", "ordnungssystem": "ordnungssystem",
             "ordnungssystemposition": "ordnungssystemposition", "dossier": "dossier", "dokument": "dokument",
             "vorgangAktivitaet": "vorgang", "aktivitaet": "aktivitaet", "mappe": "mappe",
             "datei": "datei", "ordner": "ordner", "archivischerVorgang": "archivischerVorgang"}
    for base, kind in kinds.items():
        if value == base or value.startswith(base):
            return kind
    return ""


def inventory_rows(schema_root: Path = BUNDLED_XSD_DIR) -> list[dict[str, str]]:
    rows = []
    schema_root = Path(schema_root)
    for release, schema_version, bundled_directory in RELEASES:
        directory = schema_root if bundled_directory == BUNDLED_XSD_DIR else schema_root / bundled_directory.name
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        etree.XMLSchema(etree.parse(str(directory / "arelda.xsd"), parser))
        complex_types, simple_types, global_elements, global_attributes, attribute_groups = {}, {}, {}, {}, {}
        for path in sorted(directory.glob("*.xsd")):
            root = etree.parse(str(path), parser).getroot()
            for definition in root.findall(XS + "complexType"):
                if definition.get("name"):
                    complex_types[definition.get("name")] = definition
            for definition in root.findall(XS + "simpleType"):
                if definition.get("name"):
                    simple_types[definition.get("name")] = definition
            for definition in root.findall(XS + "element"):
                if definition.get("name"):
                    global_elements[definition.get("name")] = definition
            for definition in root.findall(XS + "attribute"):
                if definition.get("name"):
                    global_attributes[definition.get("name")] = definition
            for definition in root.findall(XS + "attributeGroup"):
                if definition.get("name"):
                    attribute_groups[definition.get("name")] = definition

        def append_row(type_name, profile, kind, declaration_kind, member, value_type="", minimum="", maximum="",
                       use="", facets=None, implementation="", status="", evidence=""):
            implementation = implementation or "unmapped; schema declaration only"
            rows.append({"echRelease": release, "areldaSchemaVersion": schema_version, "profile": profile,
                         "complexType": type_name, "declarationKind": declaration_kind, "member": member,
                         "valueType": value_type, "minOccurs": minimum, "maxOccurs": maximum, "use": use,
                         "facets": json.dumps(facets or [], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                         "implementationSurface": implementation, "testEvidence": evidence,
                         "reviewStatus": status})

        for type_name in sorted(complex_types):
            kind = _kind_for_type(type_name)
            profile = "FILES" if type_name.endswith("Files") else "GEVER" if type_name.endswith("Gever") else "shared"
            append_row(type_name, profile, kind, "complexType", type_name,
                       implementation="ph_backend/xsd_info.py::SchemaInfo; ph_backend/validation.py::_check_schema",
                       status="context/extension workflow mapping required")

            def elements_in(particle, parent_min=1, parent_max=1):
                for child in particle:
                    if not isinstance(child.tag, str):
                        continue
                    local = etree.QName(child).localname
                    if local not in ("element", "sequence", "choice", "all"):
                        continue
                    minimum = parent_min * int(child.get("minOccurs", "1"))
                    max_text = child.get("maxOccurs", "1")
                    maximum = None if parent_max is None or max_text == "unbounded" else parent_max * int(max_text)
                    if local != "element":
                        yield from elements_in(child, 0 if local == "choice" else minimum, maximum)
                        continue
                    name = child.get("name") or _local(child.get("ref"))
                    declaration = global_elements.get(name, child)
                    value_type = _local(child.get("type") or declaration.get("type")) or "anonymous"
                    if value_type == "anonymous":
                        review, facets = "anonymous value requires workflow review", []
                    elif value_type in complex_types:
                        review, facets = "complex value requires workflow review", []
                    elif name in ELEMENT_TAGS:
                        review, facets = "structural workflow requires per-context review", _facets(simple_types, value_type)
                        implementation = "ph_backend/structure.py; ph_backend/files.py; ph_backend/sip.py"
                    elif any(field.tag == name for field in backend_schema.field_defs(kind)):
                        review = ("catalogued editor field; workflow test required" if maximum == 1 else
                                  "repeated-value editor; version workflow test required")
                        facets = _facets(simple_types, value_type)
                        implementation = "ph_backend/schema.py; ph_web/metadata.py; web/src/app/metadata-field.html"
                    elif value_type in simple_types or value_type in ("string", "token", "integer", "boolean", "date", "dateTime"):
                        review, facets = "dynamic scalar candidate; workflow test required", _facets(simple_types, value_type)
                        implementation = "ph_web/metadata.py; web/src/app/metadata-field.html"
                    else:
                        review, facets = "unresolved declaration requires review", []
                        implementation = ""
                    if value_type == "anonymous" or value_type in complex_types:
                        implementation = "ph_backend/sip.py; ph_backend/validation.py::_check_schema"
                    evidence = TEST_EVIDENCE.get(("element", name),
                                                 "tests/test_samples.py (conditional fixtures); schema validation only")
                    if name == "mappe" and release not in ("1.2", "1.3"):
                        evidence = "schema validation only; complete mappe workflow test missing"
                    if evidence.startswith("tests/test_") and "conditional fixtures" not in evidence:
                        review = "tested workflow slice; broader requirement review remains"
                    append_row(type_name, profile, kind, "element", name or "<unresolved-ref>", value_type,
                               minimum, "unbounded" if maximum is None else maximum, facets=facets,
                               implementation=implementation, status=review, evidence=evidence)

            def attributes_in(container, active=None):
                active = set() if active is None else active
                for child in container:
                    if not isinstance(child.tag, str):
                        continue
                    local = etree.QName(child).localname
                    if local == "attributeGroup":
                        group_name = _local(child.get("ref"))
                        if group_name not in active and group_name in attribute_groups:
                            yield from attributes_in(attribute_groups[group_name], active | {group_name})
                    elif local == "attribute":
                        name = child.get("name") or _local(child.get("ref"))
                        declaration = global_attributes.get(name, child)
                        attr_type = _local(child.get("type") or declaration.get("type"))
                        supported = (name in {"version", "repraesentation", "information", "reihung"}
                                     and "dateiRef" in type_name and release in ("1.2", "1.3"))
                        implementation = "ph_backend/sip.py; ph_web/app.py; web/src/app/app.html" if supported else ""
                        evidence = TEST_EVIDENCE.get(("attribute", name), "unmapped attribute workflow")
                        append_row(type_name, profile, kind, "attribute", name or "<unresolved-ref>", attr_type,
                                   use=child.get("use", declaration.get("use", "optional")),
                                   facets=_facets(simple_types, attr_type),
                                   implementation=implementation,
                                   status="tested workflow slice; broader requirement review remains" if supported
                                   else "attribute workflow review required", evidence=evidence)

            def effective_members(name, active=None):
                active = set() if active is None else active
                if name in active or name not in complex_types:
                    return
                definition = complex_types[name]
                extension = definition.find(f"{XS}complexContent/{XS}extension")
                if extension is None:
                    extension = definition.find(f"{XS}complexContent/{XS}restriction")
                if extension is None:
                    extension = definition.find(f"{XS}simpleContent/{XS}extension")
                if extension is None:
                    extension = definition.find(f"{XS}simpleContent/{XS}restriction")
                next_active = active | {name}
                if extension is not None:
                    yield from effective_members(_local(extension.get("base")), next_active)
                    container = extension
                else:
                    container = definition
                for child in container:
                    if isinstance(child.tag, str) and etree.QName(child).localname in ("sequence", "choice", "all"):
                        yield from elements_in(child)
                yield from attributes_in(container)

            rows.extend(effective_members(type_name))

        for type_name in sorted(simple_types):
            facets = _facets(simple_types, type_name)
            if facets:
                append_row("", "shared", "", "simpleType", type_name, facets=facets,
                           implementation="ph_backend/xsd_info.py::SchemaInfo.simple/choices",
                           status="value constraints require editor and negative-test review",
                           evidence="tests/test_structure.py::test_xsd_choices (sample types only)")
    return rows


def write_inventory(destination: Path, schema_root: Path = BUNDLED_XSD_DIR):
    rows = inventory_rows(schema_root)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path,
                        default=Path(__file__).resolve().parents[1] / "docs" / "ech0160-xsd-inventory.csv")
    arguments = parser.parse_args()
    count = write_inventory(arguments.output)
    print(f"Wrote {count} XSD declaration rows to {arguments.output}")


if __name__ == "__main__":
    main()