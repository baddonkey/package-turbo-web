"""Loading, querying, editing and saving an eCH-0160 Submission Information Package (SIP)."""

import os
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree

from . import schema, timespan, xsd_info
from .processing import MAX_METADATA_BYTES, MetadataTooLargeError, digest_file, read_bytes

HASH_ALGORITHMS = {"MD5": "md5", "SHA-1": "sha1", "SHA-256": "sha256", "SHA-512": "sha512"}


class InvalidPackageError(Exception):
    pass


class UnsupportedChecksumError(ValueError):
    pass


def localname(el) -> str:
    return etree.QName(el).localname if el is not None and isinstance(el.tag, str) else ""


def _safe_parser() -> etree.XMLParser:
    # Never resolve entities or touch the network for user supplied XML.
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)


def parse_xml(path: Path):
    try:
        return etree.ElementTree(etree.fromstring(read_bytes(path), _safe_parser()))
    except MetadataTooLargeError as exc:
        raise InvalidPackageError(str(exc)) from exc
    except etree.XMLSyntaxError as exc:
        raise InvalidPackageError(
            f"Die Datei {path.name} konnte nicht gelesen werden (Zeile {exc.lineno}): {exc.msg}"
        ) from exc
    except OSError as exc:
        raise InvalidPackageError(f"Die Datei {path} konnte nicht geöffnet werden: {exc}") from exc


def file_checksum(path: Path, algorithm: str) -> str:
    name = HASH_ALGORITHMS.get(algorithm.upper())
    if name is None:
        raise UnsupportedChecksumError(f"Unsupported checksum algorithm: {algorithm}")
    return digest_file(path, name)


class SipPackage:
    def __init__(self, metadata_path: Path, tree, root_dir: Path | None, read_only: bool):
        self.metadata_path = Path(metadata_path)
        self.tree = tree
        self.root = tree.getroot()
        self.root_dir = Path(root_dir) if root_dir else None
        self.file_root = self.root_dir
        self.read_only = read_only
        self.ns = etree.QName(self.root).namespace
        self.version_supported = (self.ns == "http://bar.admin.ch/arelda/v4"
                                  and xsd_info.schema_directory(self.root.get("schemaVersion", "")) is not None)
        if not self.version_supported:
            self.read_only = True
        self._by_key: dict[int, object] = {}
        self._key_of: dict[object, int] = {}
        self._next_key = 1
        self._file_index_valid = False
        self._path_of: dict[int, PurePosixPath] = {}
        self._file_by_id: dict[str, object] = {}
        if localname(self.root) != "paket":
            raise InvalidPackageError("Die Datei metadata.xml enthält kein Element <paket>.")
        if self.delivery is None:
            raise InvalidPackageError("Fehlende Entität: Das metadata.xml enthält keine <ablieferung>.")

    # ---------------------------------------------------------------- opening

    @classmethod
    def open_folder(cls, folder: str | Path, read_only: bool = False) -> "SipPackage":
        folder = Path(folder)
        metadata = folder / "header" / "metadata.xml"
        if not folder.is_dir() or not metadata.is_file():
            raise InvalidPackageError(
                f"Beim selektierten Ordner '{folder.name}' handelt es sich um kein gültiges Paket "
                "(Ordner header oder Datei metadata.xml fehlt)."
            )
        return cls(metadata, parse_xml(metadata), folder, read_only)

    @classmethod
    def open_metadata(cls, path: str | Path) -> "SipPackage":
        path = Path(path)
        sip_root = path.parent.parent
        inside_sip = path.parent.name == "header" and (sip_root / "content").is_dir()
        # A standalone metadata.xml can only be viewed, never edited.
        return cls(path, parse_xml(path), sip_root if inside_sip else None, read_only=not inside_sip)

    # ---------------------------------------------------------------- element helpers

    def q(self, name: str) -> str:
        return f"{{{self.ns}}}{name}" if self.ns else name

    @staticmethod
    def children(el, name: str | None = None) -> list:
        if el is None:
            return []
        return [c for c in el if isinstance(c.tag, str) and (name is None or localname(c) == name)]

    def child(self, el, name: str):
        found = self.children(el, name)
        return found[0] if found else None

    def text(self, el, name: str) -> str:
        c = self.child(el, name)
        return (c.text or "").strip() if c is not None else ""

    def key(self, el) -> int:
        k = self._key_of.get(el)
        if k is None:
            k = self._next_key
            self._next_key += 1
            self._key_of[el] = k
            self._by_key[k] = el
        return k

    def element(self, key: int):
        return self._by_key.get(int(key))

    def kind(self, el) -> str:
        name = localname(el)
        if name == "dossier" and localname(el.getparent()) == "dossier":
            return "subdossier"
        return name

    @property
    def delivery(self):
        return self.child(self.root, "ablieferung")

    @property
    def provenance(self):
        return self.child(self.delivery, "provenienz")

    @property
    def classification_system(self):
        return self.child(self.delivery, "ordnungssystem")

    @property
    def table_of_contents(self):
        return self.child(self.root, "inhaltsverzeichnis")

    @property
    def delivery_type(self) -> str:
        return self.text(self.delivery, "ablieferungstyp")

    @property
    def name(self) -> str:
        return self.root_dir.name if self.root_dir else str(self.metadata_path)

    def display_name(self, el) -> str:
        kind = self.kind(el)
        if kind == "ordnungssystemposition":
            return f"{self.text(el, 'nummer')} {self.text(el, 'titel')}".strip()
        if kind in ("dossier", "subdossier", "dokument", "mappe", "vorgang"):
            return self.text(el, "titel") or "(ohne Titel)"
        if kind in ("datei", "ordner"):
            return self.text(el, "name")
        if kind == "ordnungssystem":
            return self.text(el, "name") or "Ordnungssystem"
        if kind == "ablieferung":
            return self.text(el, "ablieferungsnummer") or self.text(el, "ablieferndeStelle") or "Ablieferung"
        if kind == "provenienz":
            return self.text(el, "aktenbildnerName") or "Provenienz"
        if kind == "aktivitaet":
            return self.text(el, "vorschreibung") or "Aktivität"
        if kind == "paket":
            return self.name
        return schema.KIND_LABELS.get(kind, kind)

    def ancestors(self, el) -> list:
        """Chain from the delivery down to (excluding) el, used for the editor breadcrumb."""
        chain = []
        parent = el.getparent()
        while parent is not None and localname(parent) not in ("paket", "inhaltsverzeichnis"):
            chain.append(parent)
            parent = parent.getparent()
        return list(reversed(chain))

    # ---------------------------------------------------------------- editing

    def set_text(self, el, tag: str, value: str, required: bool = False) -> None:
        value = value.strip()
        c = self.child(el, tag)
        if not value and not required:
            if c is not None:
                el.remove(c)
            return
        if c is None:
            c = self._insert_child(el, tag)
        c.text = value

    def repeated_texts(self, el, tag: str) -> list[str]:
        return [(child.text or "") for child in self.children(el, tag)]

    def set_repeated_texts(self, el, tag: str, values: list[str], source_indices: list[int] | None = None) -> None:
        info = xsd_info.for_package(self)
        definition = info.element(xsd_info.type_name(self, el, info), tag)
        if definition is None or definition.max_occurs == 1 or not info.is_simple(definition.type):
            raise ValueError("The package schema does not define a repeated text field here.")
        if len(values) < definition.min_occurs or definition.max_occurs is not None and len(values) > definition.max_occurs:
            raise ValueError("The number of values violates the package schema.")
        children = self.children(el, tag)
        if any(len(child) for child in children):
            raise ValueError("Complex existing values cannot be replaced by plain text.")
        if source_indices is not None:
            existing_indices = [index for index in source_indices if index >= 0]
            if (len(source_indices) != len(values) or len(existing_indices) != len(set(existing_indices))
                    or any(index < -1 or index >= len(children) for index in source_indices)):
                raise ValueError("Repeated-value row identity is stale or invalid.")
            chosen = [children[index] if index >= 0 else etree.Element(self.q(tag)) for index in source_indices]
            for child in children:
                el.remove(child)
            for child, value in zip(chosen, values):
                child.text = value
                self.insert_element(el, child)
            return
        for index, value in enumerate(values):
            child = children[index] if index < len(children) else self._insert_child(el, tag)
            child.text = value
        for child in children[len(values):]:
            el.remove(child)

    def time_span(self, el, tag: str) -> tuple[str, str]:
        span = self.child(el, tag)
        return self.text(self.child(span, "von"), "datum"), self.text(self.child(span, "bis"), "datum")

    def time_span_display(self, el, tag: str) -> tuple[str, str]:
        span = self.child(el, tag)
        return self._point_display(self.child(span, "von")), self._point_display(self.child(span, "bis"))

    def time_point_display(self, el, tag: str) -> str:
        return self._point_display(self.child(el, tag))

    def _point_display(self, point) -> str:
        if point is None:
            return ""
        return timespan.format_point(self.text(point, "ca").lower() == "true", self.text(point, "datum"))

    def _write_point(self, point, text: str) -> None:
        approximate, value = timespan.parse_point(text)
        ca = self.child(point, "ca")
        if approximate and ca is None:
            ca = etree.Element(self.q("ca"))
            point.insert(0, ca)
        if approximate:
            ca.text = "true"
        elif ca is not None:
            point.remove(ca)
        date_el = self.child(point, "datum")
        if date_el is None:
            date_el = etree.SubElement(point, self.q("datum"))
        date_el.text = value

    def set_time_point(self, el, tag: str, text: str) -> None:
        """Raises timespan.DateFormatError for invalid input."""
        point = self.child(el, tag)
        if not text.strip():
            if point is not None:
                el.remove(point)
            return
        timespan.parse_point(text)
        if point is None:
            point = self._insert_child(el, tag)
        self._write_point(point, text)

    def set_time_span(self, el, tag: str, start: str, end: str) -> None:
        """Accepts JJJJ, TT.MM.JJJJ, JJJJ-MM-TT, 'keine Angabe' and a 'ca.' prefix; raises DateFormatError."""
        start, end = start.strip(), end.strip()
        span = self.child(el, tag)
        if not start and not end:
            if span is not None:
                el.remove(span)
            return
        timespan.parse_point(start)
        timespan.parse_point(end)
        if span is None:
            span = self._insert_child(el, tag)
        for part, value in (("von", start), ("bis", end)):
            boundary = self.child(span, part)
            if boundary is None:
                boundary = etree.SubElement(span, self.q(part))
            self._write_point(boundary, value)

    def additional_data(self, el) -> list[tuple[str, str]]:
        """Merkmale of <zusatzDaten> as (name, value) pairs."""
        container = self.child(el, "zusatzDaten")
        return [(m.get("name", ""), (m.text or "").strip()) for m in self.children(container, "merkmal")]

    def set_additional_data(self, el, items: list[tuple[str, str]]) -> None:
        """Replaces all Merkmale; <zusatzDaten> is removed when empty because it needs at least one Merkmal."""
        items = [(n.strip(), v.strip()) for n, v in items if n.strip() or v.strip()]
        container = self.child(el, "zusatzDaten")
        if container is not None:
            el.remove(container)
        if not items:
            return
        container = self._insert_child(el, "zusatzDaten")
        for name, value in items:
            merkmal = etree.SubElement(container, self.q("merkmal"))
            merkmal.set("name", name)
            merkmal.text = value

    def file_properties(self, el) -> list[tuple[str, str]]:
        """Technical name/value properties attached to a datei entry."""
        return [(item.get("name", ""), (item.text or "").strip())
                for item in self.children(el, "eigenschaft")]

    def set_file_properties(self, el, items: list[tuple[str, str]]) -> None:
        if localname(el) != "datei":
            raise ValueError("File properties can only be edited on a datei entry.")
        values = [(str(name).strip(), str(value).strip()) for name, value in items if str(name).strip()]
        for item in self.children(el, "eigenschaft"):
            el.remove(item)
        for name, value in values:
            item = self._insert_child(el, "eigenschaft")
            item.set("name", name)
            item.text = value

    @staticmethod
    def new_id() -> str:
        return "_" + uuid.uuid4().hex[:22]

    def create_child(self, parent, tag: str, with_id: bool = False):
        el = self._insert_child(parent, tag)
        if with_id:
            el.set("id", self.new_id())
        return el

    def remove(self, el) -> None:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
        self.invalidate_file_index()

    def _insert_child(self, el, tag: str):
        return self.insert_element(el, etree.Element(self.q(tag)))

    def insert_element(self, parent, new, before=None):
        """Places new under parent at the position required by the XSD sequence (or before a sibling)."""
        if new.getparent() is not None:
            new.getparent().remove(new)
        if before is not None:
            before.addprevious(new)
            return new
        info = xsd_info.for_package(self)
        definitions = info.elements(xsd_info.type_name(self, parent, info))
        order = [definition.name for definition in definitions] or schema.CHILD_ORDER.get(self.kind(parent), [])
        tag = localname(new)
        rank = order.index(tag) if tag in order else len(order)
        for existing in self.children(parent):
            name = localname(existing)
            if name in order and order.index(name) > rank:
                existing.addprevious(new)
                break
        else:
            parent.append(new)
        return new

    # ---------------------------------------------------------------- files

    def _ensure_file_index(self) -> None:
        if self._file_index_valid:
            return
        self._path_of.clear()
        self._file_by_id.clear()

        def walk(el, base: PurePosixPath):
            for c in self.children(el):
                name = localname(c)
                if name not in ("ordner", "datei"):
                    continue
                path = base / self.text(c, "name")
                self._path_of[self.key(c)] = path
                if name == "datei" and c.get("id"):
                    self._file_by_id[c.get("id")] = c
                if name == "ordner":
                    walk(c, path)

        walk(self.table_of_contents, PurePosixPath())
        self._file_index_valid = True

    def invalidate_file_index(self) -> None:
        self._file_index_valid = False

    def walk_table_of_contents(self):
        """Yields (element, relative posix path) for every ordner/datei entry."""
        self._ensure_file_index()
        for key, path in list(self._path_of.items()):
            yield self._by_key[key], path

    def relpath(self, el) -> PurePosixPath | None:
        self._ensure_file_index()
        return self._path_of.get(self.key(el))

    def fs_path(self, el) -> Path | None:
        rel = self.relpath(el)
        if rel is None or self.root_dir is None:
            return None
        path = self.file_root.joinpath(*rel.parts)
        # Names come from untrusted metadata; refuse anything escaping the package folder.
        if not path.resolve().is_relative_to(self.file_root.resolve()):
            return None
        return path

    def file_by_id(self, ident: str):
        self._ensure_file_index()
        return self._file_by_id.get(ident.strip())

    def referenced_files(self, el) -> list:
        result = []
        for ref in self.children(el, "dateiRef"):
            for identifier in (ref.text or "").split():
                file_el = self.file_by_id(identifier)
                if file_el is not None:
                    result.append(file_el)
        return result

    def referenced_ids(self) -> set[str]:
        return {identifier for reference in self.root.iter(self.q("dateiRef"))
                for identifier in (reference.text or "").split()}

    def reference_attributes(self, owner) -> list[dict]:
        info = xsd_info.for_package(self)
        definition = info.element(xsd_info.type_name(self, owner, info), "dateiRef")
        allowed = info.attributes(definition.type) if definition is not None else []
        result = []
        for index, reference in enumerate(self.children(owner, "dateiRef")):
            identifiers = (reference.text or "").split()
            identifier = " ".join(identifiers)
            names = []
            for file_id in identifiers:
                file_element = self.file_by_id(file_id)
                names.append(self.display_name(file_element) if file_element is not None else file_id)
            known = {attribute.name for attribute in allowed}
            attributes = [{"name": attribute.name, "value": reference.get(attribute.name, ""),
                           "editable": True, "required": attribute.required} for attribute in allowed]
            attributes.extend({"name": name, "value": value, "editable": False, "required": False}
                              for name, value in reference.attrib.items() if name not in known)
            result.append({"index": index, "fileId": identifier, "name": ", ".join(names),
                           "attributes": attributes})
        return result

    def incoming_reference_attributes(self, file_el) -> list[dict]:
        if self.kind(file_el) != "datei":
            return []
        file_id = file_el.get("id", "")
        if not file_id:
            return []

        info = xsd_info.for_package(self)
        result = []
        for owner in self.root.iter():
            references = self.children(owner, "dateiRef")
            if not references:
                continue
            owner_kind = self.kind(owner)
            definition = info.element(xsd_info.type_name(self, owner, info), "dateiRef")
            allowed = info.attributes(definition.type) if definition is not None else []
            known = {attribute.name for attribute in allowed}
            owner_elements = [ancestor for ancestor in self.ancestors(owner) + [owner]
                              if self.kind(ancestor) in ("ordnungssystemposition", "dossier", "subdossier",
                                                        "dokument", "mappe", "vorgang", "aktivitaet")]
            owner_context = " / ".join(
                f"{schema.KIND_LABELS.get(self.kind(element), self.kind(element))}: {self.display_name(element)}"
                for element in owner_elements)
            for index, reference in enumerate(references):
                identifiers = (reference.text or "").split()
                if file_id not in identifiers:
                    continue
                attributes = [{"name": attribute.name, "value": reference.get(attribute.name, ""),
                               "editable": True, "required": attribute.required} for attribute in allowed]
                attributes.extend({"name": name, "value": value, "editable": False, "required": False}
                                  for name, value in reference.attrib.items() if name not in known)
                names = []
                for identifier in identifiers:
                    target = self.file_by_id(identifier)
                    names.append(self.display_name(target) if target is not None else identifier)
                result.append({
                    "ownerKey": self.key(owner),
                    "ownerKind": owner_kind,
                    "ownerKindLabel": schema.KIND_LABELS.get(owner_kind, owner_kind),
                    "ownerName": self.display_name(owner),
                    "ownerContext": owner_context,
                    "index": index,
                    "fileIds": identifiers,
                    "fileId": " ".join(identifiers),
                    "name": ", ".join(names),
                    "attributes": attributes,
                })
        return result

    def reference_targets(self, file_el) -> list[dict]:
        if self.kind(file_el) != "datei":
            return []
        file_id = file_el.get("id", "")
        if not file_id:
            return []

        info = xsd_info.for_package(self)
        result = []
        owner_kinds = {"ablieferung", "ordnungssystem", "ordnungssystemposition", "dossier", "subdossier",
                        "dokument", "mappe", "vorgang", "aktivitaet"}
        for owner in self.root.iter():
            if not isinstance(owner.tag, str):
                continue
            owner_kind = self.kind(owner)
            if owner_kind not in owner_kinds:
                continue
            definition = info.element(xsd_info.type_name(self, owner, info), "dateiRef")
            if definition is None:
                continue
            references = self.children(owner, "dateiRef")
            has_reference = any(file_id in (reference.text or "").split() for reference in references)
            if has_reference and not self.can_repeat_reference_target(owner):
                continue
            if definition.max_occurs is not None and len(references) >= definition.max_occurs:
                continue
            owner_elements = [ancestor for ancestor in self.ancestors(owner) + [owner]
                              if self.kind(ancestor) in owner_kinds]
            owner_context = " / ".join(
                f"{schema.KIND_LABELS.get(self.kind(element), self.kind(element))}: {self.display_name(element)}"
                for element in owner_elements)
            result.append({
                "ownerKey": self.key(owner),
                "ownerKind": owner_kind,
                "ownerKindLabel": schema.KIND_LABELS.get(owner_kind, owner_kind),
                "ownerName": self.display_name(owner),
                "ownerContext": owner_context,
                "hasReference": has_reference,
                "label": owner_context + (" (weitere Referenz)" if has_reference else ""),
            })
        return result

    def can_repeat_reference_target(self, owner) -> bool:
        """Whether this delivery profile's schema permits repeating a file ID on this owner."""
        return self.delivery_type.upper() == "GEVER" and self.kind(owner) == "dokument"

    def set_reference_attribute(self, owner, index: int, name: str, value: str) -> None:
        info = xsd_info.for_package(self)
        definition = info.element(xsd_info.type_name(self, owner, info), "dateiRef")
        allowed = info.attributes(definition.type) if definition is not None else []
        attribute = next((attribute for attribute in allowed if attribute.name == name), None)
        references = self.children(owner, "dateiRef")
        if attribute is None or index < 0 or index >= len(references):
            raise ValueError("The package schema does not allow this reference attribute.")
        value = value.strip()
        if not value and attribute.required:
            raise ValueError("A required reference attribute cannot be removed.")
        if value:
            references[index].set(name, value)
        else:
            references[index].attrib.pop(name, None)

    def iter_kind(self, *names: str):
        for name in names:
            yield from self.root.iter(self.q(name))

    def xsd_path(self) -> Path | None:
        xsd_dir = self.file_root / "header" / "xsd" if self.file_root else self.metadata_path.parent / "xsd"
        candidates = sorted(xsd_dir.glob("*.xsd")) if xsd_dir.is_dir() else []
        for c in candidates:
            if c.name.lower() == "arelda.xsd":
                return c
        return candidates[0] if candidates else None

    # ---------------------------------------------------------------- persistence

    def restore_xml(self, data: bytes) -> None:
        if len(data) > MAX_METADATA_BYTES:
            raise InvalidPackageError(f"metadata.xml exceeds the {MAX_METADATA_BYTES}-byte limit.")
        self.tree = etree.ElementTree(etree.fromstring(data, _safe_parser()))
        self.root = self.tree.getroot()
        self._by_key.clear()
        self._key_of.clear()
        self.invalidate_file_index()

    def save(self) -> None:
        if self.read_only:
            raise PermissionError("Das Paket ist schreibgeschützt geöffnet.")
        if self.file_root != self.root_dir:
            raise PermissionError("Staged files must be saved through the package workspace.")
        tmp = self.metadata_path.with_suffix(".xml.tmp")
        self.tree.write(str(tmp), encoding="UTF-8", xml_declaration=True)
        os.replace(tmp, self.metadata_path)

    def export_metadata(self, dest: str | Path) -> None:
        self.tree.write(str(dest), encoding="UTF-8", xml_declaration=True)

    def zip_to(self, dest: str | Path) -> None:
        if self.file_root != self.root_dir:
            raise InvalidPackageError("Commit staged files before exporting the package.")
        if self.root_dir is None:
            raise InvalidPackageError("Nur ein vollständiges SIP kann gezippt werden.")
        root = Path(self.root_dir)
        root_attributes = getattr(root.lstat(), "st_file_attributes", 0)
        if root.is_symlink() or root_attributes & 0x400 or not root.is_dir():
            raise InvalidPackageError("Linked SIP roots cannot be exported safely.")
        root_resolved = root.resolve()
        destination = Path(dest)
        if destination.resolve().is_relative_to(root_resolved):
            raise InvalidPackageError("The ZIP destination must be outside the SIP.")

        paths = sorted(root.rglob("*"))
        for path in paths:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            mode = path.lstat().st_mode
            if path.is_symlink() or attributes & 0x400:
                raise InvalidPackageError("Linked files and directories cannot be exported safely.")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise InvalidPackageError("Special files cannot be exported safely.")

        base = root.parent
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for path in paths:
                zf.write(path, path.relative_to(base).as_posix())
