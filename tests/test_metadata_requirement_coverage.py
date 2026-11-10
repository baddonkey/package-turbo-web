from lxml import etree

from ph_backend.metadata_requirements import ENTITY_TAGS, mandatory_metadata_fields
from ph_backend.validation import Validator


NAMESPACE = "http://bar.admin.ch/arelda/v4"


class _MetadataPackage:
    def __init__(self, schema_version, profile, entity):
        self.root = etree.Element(f"{{{NAMESPACE}}}paket", schemaVersion=schema_version)
        self.delivery_type = profile
        self.delivery = None
        self.provenance = None
        self.classification_system = None
        self.table_of_contents = None
        tag = ENTITY_TAGS[entity]
        self.owner = self.root if entity == "Paket" else etree.SubElement(self.root, f"{{{NAMESPACE}}}{tag}")
        if entity == "Ablieferung":
            self.delivery = self.owner
        elif entity == "Provenienz":
            self.provenance = self.owner
        elif entity == "Ordnungssystem":
            self.classification_system = self.owner
        elif entity == "Inhaltsv.":
            self.table_of_contents = self.owner

    @staticmethod
    def children(element, name=None):
        return [child for child in element if isinstance(child.tag, str)
                and (name is None or etree.QName(child).localname == name)]

    def iter_kind(self, *names):
        for name in names:
            yield from self.root.iter(f"{{{NAMESPACE}}}{name}")

    def text(self, element, name):
        child = next(iter(self.children(element, name)), None)
        return (child.text or "").strip() if child is not None else ""

    def child(self, element, name):
        return next(iter(self.children(element, name)), None)

    @staticmethod
    def kind(element):
        return etree.QName(element).localname

    def display_name(self, element):
        return self.text(element, "titel") or self.text(element, "name") or self.kind(element)

    @staticmethod
    def key(element):
        return id(element)


def _set_value(package, requirement):
    field = requirement["xsdName"]
    data_type = requirement["dataType"].lower()
    if field in {"id", "schemaVersion"} or "attribute" in data_type:
        package.owner.set(field, "_test-id" if field == "id" else "5.1")
    else:
        value = etree.SubElement(package.owner, f"{{{NAMESPACE}}}{field}")
        if field != "dateiRef" and ("complex" in data_type
                                    or data_type in {"zeitraum", "historischerzeitraum", "historischerzeitpunkt"}):
            etree.SubElement(value, f"{{{NAMESPACE}}}datum").text = "2000"
        else:
            value.text = "_test-file" if field == "dateiRef" else "test value"


def test_every_mandatory_dictionary_field_is_reported_when_missing_and_accepted_when_present():
    requirements = mandatory_metadata_fields()
    assert len(requirements) == 218

    for requirement in requirements:
        missing_package = _MetadataPackage(requirement["schemaVersion"], requirement["profile"],
                                           requirement["entity"])
        if requirement["xsdName"] == "schemaVersion":
            assert missing_package.root.get("schemaVersion") == requirement["schemaVersion"]
            continue
        missing_validator = Validator(missing_package)
        missing_validator._check_missing_values()
        assert any(issue["checkId"] == "missing_values" and issue["fieldTag"] == requirement["xsdName"]
                   for issue in missing_validator.issues), requirement

        complete_package = _MetadataPackage(requirement["schemaVersion"], requirement["profile"],
                                             requirement["entity"])
        _set_value(complete_package, requirement)
        validator = Validator(complete_package)
        validator._check_missing_values()
        assert not any(issue["checkId"] == "missing_values" and issue["fieldTag"] == requirement["xsdName"]
                       for issue in validator.issues), requirement
