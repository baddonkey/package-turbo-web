"""Reads value constraints (choice lists, booleans, required elements) from the arelda XSDs of a package."""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lxml import etree

XS = "{http://www.w3.org/2001/XMLSchema}"
BUNDLED_XSD_DIR = Path(__file__).resolve().parent / "resources" / "xsd"
SCHEMA_RELEASES = {
    "1.3": ("5.1", BUNDLED_XSD_DIR / "1.3"),
    "1.2": ("5.0", BUNDLED_XSD_DIR),
    "1.1": ("4.1", BUNDLED_XSD_DIR / "1.1"),
    "1.0": ("4.0", BUNDLED_XSD_DIR / "1.0"),
}
LATEST_RELEASE = "1.3"


def schema_release(version: str) -> tuple[str, Path]:
    if version not in SCHEMA_RELEASES:
        raise ValueError(f"Unsupported creation version: eCH-0160 {version}")
    return SCHEMA_RELEASES[version]


def schema_directory(schema_version: str) -> Path | None:
    return next((directory for version, directory in SCHEMA_RELEASES.values() if version == schema_version), None)


@dataclass
class SimpleInfo:
    enums: list[str] = field(default_factory=list)
    boolean: bool = False
    max_length: int | None = None


@dataclass(frozen=True)
class ElementInfo:
    name: str
    type: str
    min_occurs: int
    max_occurs: int | None = 1


@dataclass(frozen=True)
class AttributeInfo:
    name: str
    type: str
    required: bool


def _strip_prefix(name: str | None) -> str:
    return (name or "").split(":")[-1]


class SchemaInfo:
    def __init__(self, xsd_dir: Path | None):
        self._simple: dict[str, etree._Element] = {}
        self._complex: dict[str, etree._Element] = {}
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        for path in sorted(Path(xsd_dir).glob("*.xsd")) if xsd_dir is not None else []:
            root = etree.parse(str(path), parser).getroot()
            for st in root.iter(XS + "simpleType"):
                if st.get("name"):
                    self._simple[st.get("name")] = st
            for ct in root.iter(XS + "complexType"):
                if ct.get("name"):
                    self._complex[ct.get("name")] = ct

    def has_type(self, name: str) -> bool:
        return name in self._complex

    def is_simple(self, name: str) -> bool:
        return name in self._simple or name in ("string", "token", "integer", "boolean", "date", "dateTime")

    def attributes(self, type_name: str) -> list[AttributeInfo]:
        definition = self._complex.get(type_name)
        if definition is None:
            return []
        extension = definition.find(f"{XS}complexContent/{XS}extension")
        if extension is None:
            extension = definition.find(f"{XS}simpleContent/{XS}extension")
        result = self.attributes(_strip_prefix(extension.get("base"))) if extension is not None else []
        container = extension if extension is not None else definition
        for attribute in container.findall(XS + "attribute"):
            if attribute.get("name") and attribute.get("use") != "prohibited":
                result.append(AttributeInfo(attribute.get("name"), _strip_prefix(attribute.get("type")),
                                            attribute.get("use") == "required"))
        return result

    def elements(self, type_name: str) -> list[ElementInfo]:
        """Child elements of a complex type in sequence order, including those of extended base types."""
        ct = self._complex.get(type_name)
        if ct is None:
            return []
        result: list[ElementInfo] = []
        ext = ct.find(f"{XS}complexContent/{XS}extension")
        if ext is not None:
            result.extend(self.elements(_strip_prefix(ext.get("base"))))
            container = ext
        else:
            container = ct
        def collect(particle, minimum: int = 1, maximum: int | None = 1):
            for child in particle:
                if child.tag not in (XS + "element", XS + "sequence", XS + "choice", XS + "all"):
                    continue
                child_minimum = minimum * int(child.get("minOccurs", "1"))
                occurrence = child.get("maxOccurs", "1")
                child_maximum = None if maximum is None or occurrence == "unbounded" else maximum * int(occurrence)
                if child.tag == XS + "element" and child.get("name"):
                    result.append(ElementInfo(child.get("name"), _strip_prefix(child.get("type")),
                                              child_minimum, child_maximum))
                elif child.tag != XS + "element":
                    collect(child, 0 if child.tag == XS + "choice" else child_minimum, child_maximum)

        collect(container)
        return result

    def element(self, type_name: str, tag: str) -> ElementInfo | None:
        return next((e for e in self.elements(type_name) if e.name == tag), None)

    def simple(self, type_name: str) -> SimpleInfo:
        info = SimpleInfo()
        self._collect(type_name, info, set())
        return info

    def _collect(self, type_name: str, info: SimpleInfo, seen: set) -> None:
        if type_name in seen:
            return
        seen.add(type_name)
        if type_name == "boolean":
            info.boolean = True
            return
        st = self._simple.get(type_name)
        if st is None:
            return
        restriction = st.find(XS + "restriction")
        if restriction is not None:
            info.enums.extend(e.get("value") for e in restriction.findall(XS + "enumeration")
                              if e.get("value") not in info.enums)
            max_length = restriction.find(XS + "maxLength")
            if max_length is not None and info.max_length is None:
                info.max_length = int(max_length.get("value"))
            self._collect(_strip_prefix(restriction.get("base")), info, seen)
        union = st.find(XS + "union")
        if union is not None:
            for member in (union.get("memberTypes") or "").split():
                self._collect(_strip_prefix(member), info, seen)

    def choices(self, type_name: str, tag: str) -> list[str]:
        el = self.element(type_name, tag)
        if el is None:
            return []
        info = self.simple(el.type)
        if info.boolean:
            return ["true", "false"]
        return info.enums

    def is_required(self, type_name: str, tag: str) -> bool:
        el = self.element(type_name, tag)
        return el is not None and el.min_occurs > 0


@lru_cache(maxsize=8)
def load(xsd_dir: str) -> SchemaInfo:
    return SchemaInfo(Path(xsd_dir))


def for_package(pkg) -> SchemaInfo:
    xsd = pkg.xsd_path()
    directory = xsd.parent if xsd else schema_directory(pkg.root.get("schemaVersion", ""))
    return load(str(directory)) if directory is not None else SchemaInfo(None)


_SUFFIXED = {
    "provenienz": "provenienz",
    "ordnungssystem": "ordnungssystem",
    "ordnungssystemposition": "ordnungssystemposition",
    "dossier": "dossier",
    "subdossier": "dossier",
    "dokument": "dokument",
}
_PLAIN = {
    "datei": "datei",
    "ordner": "ordner",
    "mappe": "mappe",
    "vorgang": "vorgangAktivitaet",
    "aktivitaet": "aktivitaet",
    "archivischerVorgang": "archivischerVorgang",
    "archivischeNotiz": "archivischeNotiz",
}
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def type_name(pkg, el, info: SchemaInfo) -> str:
    """Complex type of an instance element (GEVER/FILES variants are chosen by the delivery type)."""
    explicit = el.get(_XSI_TYPE)
    if explicit:
        return _strip_prefix(explicit)
    parent = el.getparent()
    if parent is not None and isinstance(parent.tag, str):
        definition = info.element(type_name(pkg, parent, info), etree.QName(el).localname)
        if definition is not None and definition.type:
            return definition.type
    kind = pkg.kind(el)
    if kind in _SUFFIXED:
        base = _SUFFIXED[kind]
        suffix = "Files" if pkg.delivery_type.upper() == "FILES" else "Gever"
        return base + suffix if info.has_type(base + suffix) else base
    return _PLAIN.get(kind, kind)
