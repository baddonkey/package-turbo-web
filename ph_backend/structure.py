"""Editing the classification system (Ordnungssystem-Navigator, user documentation 8.1.2 and 8.3, 9.2, 9.4)."""

import csv
import re
from pathlib import Path

from . import schema, timespan, xsd_info
from .sip import SipPackage, localname


class StructureError(Exception):
    pass


CONTAINERS = {
    "ordnungssystemposition": ("ordnungssystem", "ordnungssystemposition", "mappe"),
    "dossier": ("ordnungssystemposition", "dossier", "subdossier", "mappe"),
    "dokument": ("dossier", "subdossier", "mappe"),
    "mappe": ("ablieferung", "ordnungssystem", "ordnungssystemposition", "dossier", "subdossier", "mappe"),
    "vorgang": ("dossier", "subdossier"),
    "aktivitaet": ("vorgang",),
}
DEFAULT_TITLES = {
    "ordnungssystemposition": "Neue Ordnungssystemposition",
    "dossier": "Neues Dossier",
    "dokument": "Neues Dokument",
    "mappe": "Neue Mappe",
    "vorgang": "Neuer Vorgang",
    "aktivitaet": "Neue Aktivität",
}


def _check_parent(pkg: SipPackage, parent, tag: str) -> None:
    kind = pkg.kind(parent)
    if kind not in CONTAINERS[tag]:
        raise StructureError(f"{schema.KIND_LABELS.get(tag, tag)} kann nicht unter "
                             f"{schema.KIND_LABELS.get(kind, kind)} angelegt werden.")
    info = xsd_info.for_package(pkg)
    parent_type = xsd_info.type_name(pkg, parent, info)
    if tag in ("vorgang", "aktivitaet") and not info.has_type(parent_type):
        raise StructureError("This package version does not support a structured process/activity here.")
    if info.has_type(parent_type):
        definition = info.element(parent_type, tag)
        if definition is None or (definition.max_occurs is not None
                                  and len(pkg.children(parent, tag)) >= definition.max_occurs):
            raise StructureError(f"{tag} is not permitted here by the package schema ({parent_type}).")
        if tag in ("vorgang", "aktivitaet") and not info.has_type(definition.type):
            raise StructureError("This package version does not support a structured process/activity here.")
    if tag == "ordnungssystemposition" and pkg.children(parent, "dossier"):
        raise StructureError("Unter einer Ordnungssystemposition mit Dossiers können keine weiteren "
                             "Ordnungssystempositionen angelegt werden.")
    if tag == "dossier" and kind == "ordnungssystemposition" and pkg.children(parent, "ordnungssystemposition"):
        raise StructureError("Dossiers können nur auf der untersten Stufe des Ordnungssystems angelegt werden.")


def allowed_children(pkg: SipPackage, parent) -> list[str]:
    result = []
    for tag in CONTAINERS:
        try:
            _check_parent(pkg, parent, tag)
        except StructureError:
            continue
        result.append(tag)
    return result


def _number_of(pkg: SipPackage, el) -> str:
    kind = pkg.kind(el)
    if kind == "ordnungssystemposition":
        return pkg.text(el, "nummer")
    if kind in ("dossier", "subdossier"):
        return pkg.text(el, "aktenzeichen")
    return ""


def _next_number(pkg: SipPackage, parent, tag: str) -> str:
    field = "nummer" if tag == "ordnungssystemposition" else "aktenzeichen"
    used = {pkg.text(c, field) for c in pkg.children(parent, tag)}
    prefix = _number_of(pkg, parent)
    separator = "" if tag == "ordnungssystemposition" else ("." if prefix else "")
    index = len(pkg.children(parent, tag))
    while True:
        candidate = f"{prefix}{separator}{index}"
        if candidate not in used:
            return candidate
        index += 1


def add_objects(pkg: SipPackage, parent, tag: str, count: int = 1, title: str | None = None) -> list:
    """Neue Ordnungssystemposition / Dossier / Dokument / Mappe (also several at once)."""
    _check_parent(pkg, parent, tag)
    created = []
    for _ in range(max(1, count)):
        _check_parent(pkg, parent, tag)
        info = xsd_info.for_package(pkg)
        element_info = info.element(xsd_info.type_name(pkg, parent, info), tag)
        attributes = info.attributes(element_info.type) if element_info is not None else []
        el = pkg.create_child(parent, tag, with_id=any(attribute.name == "id" for attribute in attributes))
        if tag == "ordnungssystemposition":
            pkg.set_text(el, "nummer", _next_number(pkg, parent, tag), required=True)
        elif tag == "dossier":
            pkg.set_text(el, "aktenzeichen", _next_number(pkg, parent, tag), required=True)
        pkg.set_text(el, "vorschreibung" if tag == "aktivitaet" else "titel", title or DEFAULT_TITLES[tag], required=True)
        if tag == "dokument":
            pkg.set_text(el, "erscheinungsform", "digital")
        fill_required(pkg, el)
        created.append(el)
    return created


_SPAN_TYPES = {"historischerZeitraum", "zeitraum"}


def fill_required(pkg: SipPackage, el) -> None:
    """Adds XSD-mandatory simple elements that are still missing, with neutral default values."""
    info = xsd_info.for_package(pkg)
    for element in info.elements(xsd_info.type_name(pkg, el, info)):
        if element.min_occurs == 0 or pkg.child(el, element.name) is not None:
            continue
        if element.name in CONTAINERS:
            child = pkg.create_child(el, element.name, with_id=True)
            fill_required(pkg, child)
            continue
        if element.name in schema.STRUCTURAL_TAGS:
            continue
        if element.type in _SPAN_TYPES:
            pkg.set_time_span(el, element.name, timespan.NOT_SPECIFIED, timespan.NOT_SPECIFIED)
            continue
        choices = info.simple(element.type).enums
        default = "digital" if "digital" in choices else (choices[0] if choices else timespan.NOT_SPECIFIED)
        pkg.set_text(el, element.name, default, required=True)


def delete(pkg: SipPackage, elements) -> int:
    count = 0
    for el in elements:
        if localname(el) in ("ordnungssystem", "ablieferung", "paket"):
            raise StructureError("Das Ordnungssystem selbst kann nicht gelöscht werden.")
        if el.getparent() is not None:
            pkg.remove(el)
            count += 1
    return count


def unassign(pkg: SipPackage, owner, file_el) -> bool:
    ident = file_el.get("id", "")
    for ref in pkg.children(owner, "dateiRef"):
        if (ref.text or "").strip() == ident:
            owner.remove(ref)
            return True
    return False


def _is_ancestor(candidate, el) -> bool:
    node = el
    while node is not None:
        if node is candidate:
            return True
        node = node.getparent()
    return False


def move(pkg: SipPackage, el, new_parent, before=None) -> None:
    tag = localname(el)
    if tag not in CONTAINERS:
        raise StructureError("Dieses Objekt kann nicht verschoben werden.")
    if _is_ancestor(el, new_parent):
        raise StructureError("Ein Objekt kann nicht in sich selbst verschoben werden.")
    if new_parent is not el.getparent():
        _check_parent(pkg, new_parent, tag)
    pkg.insert_element(new_parent, el, before=before)


def move_relative(pkg: SipPackage, el, offset: int) -> bool:
    """Moves el up (-1) or down (+1) among its siblings of the same type."""
    siblings = pkg.children(el.getparent(), localname(el))
    index = siblings.index(el) + offset
    if not 0 <= index < len(siblings):
        return False
    if offset < 0:
        siblings[index].addprevious(el)
    else:
        siblings[index].addnext(el)
    return True


def _accepts_file_refs(pkg: SipPackage, el) -> bool:
    info = xsd_info.for_package(pkg)
    return info.element(xsd_info.type_name(pkg, el, info), "dateiRef") is not None


def _title_for_file(pkg: SipPackage, file_el) -> str:
    name = pkg.text(file_el, "originalName") or pkg.text(file_el, "name")
    return Path(name).stem or name


def assign_files(pkg: SipPackage, target, files, create_documents: bool = False) -> int:
    """Assigns files to a dossier, document or folder (Mappe); GEVER dossiers get one document per file."""
    kind = pkg.kind(target)
    if kind not in ("dossier", "subdossier", "dokument", "mappe"):
        raise StructureError("Dateien können nur Dossiers, Dokumenten oder Mappen zugewiesen werden.")
    existing = {(r.text or "").strip() for r in pkg.children(target, "dateiRef")}
    use_documents = kind in ("dossier", "subdossier") and (create_documents or not _accepts_file_refs(pkg, target))
    count = 0
    for file_el in files:
        ident = file_el.get("id")
        if not ident or ident in existing:
            continue
        holder = add_objects(pkg, target, "dokument", 1, _title_for_file(pkg, file_el))[0] if use_documents else target
        ref = pkg.create_child(holder, "dateiRef")
        ref.text = ident
        count += 1
    return count


def add_file_reference(pkg: SipPackage, target, file_el) -> bool:
    """Adds one reference, allowing repeated GEVER document links where the XSD permits them."""
    kind = pkg.kind(target)
    if kind not in ("dossier", "subdossier", "dokument", "mappe"):
        raise StructureError("Dateien können nur Dossiers, Dokumenten oder Mappen zugewiesen werden.")
    info = xsd_info.for_package(pkg)
    if info.element(xsd_info.type_name(pkg, target, info), "dateiRef") is None:
        raise StructureError("Dieses Objekt kann gemäss Paket-XSD keine Dateien direkt referenzieren.")
    ident = file_el.get("id", "")
    if not ident:
        raise StructureError("Die ausgewählte Datei hat keine ID.")
    already_referenced = any(ident in (reference.text or "").split()
                             for reference in pkg.children(target, "dateiRef"))
    if already_referenced and not pkg.can_repeat_reference_target(target):
        raise StructureError("Diese Datei wird von diesem Objekt bereits referenziert.")
    reference = pkg.create_child(target, "dateiRef")
    reference.text = ident
    return True


def document_for_file(pkg: SipPackage, owner, file_el):
    """Neues Dokument für diese Datei: replaces a direct file reference of a dossier by a document."""
    if not unassign(pkg, owner, file_el):
        raise StructureError("Die Datei ist dem Objekt nicht direkt zugewiesen.")
    doc = add_objects(pkg, owner, "dokument", 1, _title_for_file(pkg, file_el))[0]
    pkg.create_child(doc, "dateiRef").text = file_el.get("id")
    return doc


def documents_for_files(pkg: SipPackage, dossier) -> int:
    files = pkg.referenced_files(dossier)
    for file_el in files:
        document_for_file(pkg, dossier, file_el)
    return len(files)


def renumber(pkg: SipPackage, scheme: str = "decimal") -> int:
    """Ordnungssystem neu nummerieren: 'decimal' (freie Zehnergliederung) or 'hundred' (00-99 per level)."""
    width, limit = (1, 10) if scheme == "decimal" else (2, 100)
    count = 0

    def walk(parent, prefix: str) -> None:
        nonlocal count
        positions = pkg.children(parent, "ordnungssystemposition")
        if len(positions) > limit:
            raise StructureError(f"Auf einer Stufe sind höchstens {limit} Positionen möglich "
                                 f"({len(positions)} unter «{pkg.display_name(parent)}»).")
        for index, position in enumerate(positions):
            number = f"{prefix}{index:0{width}d}"
            pkg.set_text(position, "nummer", number, required=True)
            count += 1
            walk(position, number)

    walk(pkg.classification_system, "")
    return count


def _span_bounds(pkg: SipPackage, elements):
    lower, upper = None, None
    for el in elements:
        start, end = pkg.time_span(el, "entstehungszeitraum")
        lo, up = timespan.parse_lower(start), timespan.parse_upper(end)
        if lo and (lower is None or lo < lower[0]):
            lower = (lo, start)
        if up and (upper is None or up > upper[0]):
            upper = (up, end)
    return (lower[1] if lower else ""), (upper[1] if upper else "")


def accumulate_time_spans(pkg: SipPackage, el) -> tuple[str, str]:
    """Zeiträume kumulieren: span of el covers all spans below it ('keine Angabe' is ignored)."""
    if localname(el) == "ablieferung":
        sources = list(pkg.iter_kind("dossier"))
    else:
        sources = [d for d in el.iter(pkg.q("dossier"), pkg.q("dokument")) if d is not el]
    start, end = _span_bounds(pkg, sources)
    if start or end:
        pkg.set_time_span(el, "entstehungszeitraum", start or timespan.NOT_SPECIFIED, end or timespan.NOT_SPECIFIED)
    return start, end


def propagate_time_span_up(pkg: SipPackage, el):
    """'nach oben übernehmen': widens the parent span (dossier or delivery) so that it contains el."""
    parent = el.getparent()
    if localname(parent) != "dossier":
        parent = pkg.delivery
    start, end = _span_bounds(pkg, [parent, el])
    pkg.set_time_span(parent, "entstehungszeitraum", start or timespan.NOT_SPECIFIED, end or timespan.NOT_SPECIFIED)
    return parent


# ---------------------------------------------------------------- mass assignment (8.3.1)

def mass_assign(pkg: SipPackage, folders, target, osp_levels: int, dossier_levels: int,
                create_documents: bool = False, include_top: bool = False) -> tuple[int, int]:
    """Builds positions and dossiers from folders of the package. Returns (created objects, unassigned files)."""
    target_kind = pkg.kind(target)
    if target_kind == "ordnungssystem" and osp_levels < 1:
        raise StructureError("Die Struktur muss mindestens eine Ordnungssystemposition enthalten "
                             "(Anzahl OSP Stufen mindestens 1).")
    if target_kind == "ordnungssystemposition":
        if pkg.children(target, "dossier") and osp_levels > 0:
            raise StructureError("Unter einer Ordnungssystemposition mit Dossiers können keine OSP Stufen erstellt werden.")
        if pkg.children(target, "ordnungssystemposition") and osp_levels == 0:
            raise StructureError("Dossiers können nicht neben Ordnungssystempositionen angelegt werden.")
    if target_kind in ("dossier", "subdossier") and osp_levels > 0:
        raise StructureError("Unter einem Dossier können keine Ordnungssystempositionen angelegt werden.")
    if target_kind not in ("ordnungssystem", "ordnungssystemposition", "dossier", "subdossier"):
        raise StructureError("Die Massenzuweisung ist nur auf das Ordnungssystem, eine OSP oder ein Dossier möglich.")

    created = 0
    unassigned = 0

    def owner_for_files(holder):
        return holder if pkg.kind(holder) in ("dossier", "subdossier") else None

    def place_files(folder, holder) -> None:
        nonlocal unassigned
        files = pkg.children(folder, "datei")
        owner = owner_for_files(holder)
        if owner is None:
            unassigned += len(files)
            return
        assign_files(pkg, owner, files, create_documents)

    def walk(folder, parent, depth: int) -> None:
        nonlocal created
        title = pkg.text(folder, "originalName") or pkg.text(folder, "name")
        holder = parent
        if depth <= osp_levels:
            holder = add_objects(pkg, parent, "ordnungssystemposition", 1, title)[0]
            created += 1
        elif depth <= osp_levels + dossier_levels:
            holder = add_objects(pkg, parent, "dossier", 1, title)[0]
            created += 1
        place_files(folder, holder)
        for sub in pkg.children(folder, "ordner"):
            walk(sub, holder, depth + 1)

    for folder in folders:
        if include_top:
            walk(folder, target, 1)
        else:
            place_files(folder, target)
            for sub in pkg.children(folder, "ordner"):
                walk(sub, target, 1)
    return created, unassigned


# ---------------------------------------------------------------- CSV import (8.1.2)

def _normalized_number(value: str) -> str:
    return re.sub(r"[\s.\-/]", "", value)


def import_csv(pkg: SipPackage, path: str | Path) -> int:
    """Imports positions and dossiers from a '|' separated CSV with a mandatory 'nummer' column."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter="|"))
    if not rows or "nummer" not in (rows[0].keys() if rows else []):
        raise StructureError("Die CSV-Datei muss eine Kopfzeile mit der Spalte «nummer» enthalten "
                             "(Trennzeichen «|»).")
    system = pkg.classification_system
    created: list[tuple[str, object]] = []
    for line, row in enumerate(rows, start=2):
        number = (row.get("nummer") or "").strip()
        kind = (row.get("type") or "ordnungssystemposition").strip().lower() or "ordnungssystemposition"
        if kind not in ("ordnungssystemposition", "dossier"):
            raise StructureError(f"Zeile {line}: ungültiger Typ «{kind}» (erlaubt: ordnungssystemposition, dossier).")
        if not number:
            raise StructureError(f"Zeile {line}: die Spalte «nummer» ist leer.")
        key = _normalized_number(number)
        allowed = CONTAINERS["ordnungssystemposition" if kind == "ordnungssystemposition" else "dossier"]
        parent = system
        best = -1
        for other_key, el in created:
            if key.startswith(other_key) and len(other_key) < len(key) and len(other_key) > best \
                    and pkg.kind(el) in allowed:
                parent, best = el, len(other_key)
        if kind == "dossier" and parent is system:
            raise StructureError(f"Zeile {line}: für das Dossier «{number}» wurde keine übergeordnete Position gefunden.")
        el = add_objects(pkg, parent, kind, 1, (row.get("titel") or "").strip() or None)[0]
        pkg.set_text(el, "nummer" if kind == "ordnungssystemposition" else "aktenzeichen", number, required=True)
        text_tags = {d.tag for d in schema.field_defs(pkg.kind(el)) if d.type in (schema.TEXT, schema.MULTILINE)}
        for column, value in row.items():
            if column in text_tags and column not in ("nummer", "aktenzeichen") and value and value.strip():
                pkg.set_text(el, column, value)
        created.append((key, el))
    return len(created)


# ---------------------------------------------------------------- mass edit (9.2)

MASS_EDIT_KINDS = ("ordnungssystemposition", "dossier", "dokument", "mappe", "datei")


def _objects_below(pkg: SipPackage, el, kind: str) -> list:
    if kind == "datei":
        refs = {(r.text or "").strip() for r in el.iter(pkg.q("dateiRef"))}
        return [f for f in (pkg.file_by_id(i) for i in refs) if f is not None]
    found = [e for e in el.iter(pkg.q(kind))]
    return found


def mass_edit_kinds(pkg: SipPackage, el) -> list[str]:
    return [k for k in MASS_EDIT_KINDS if _objects_below(pkg, el, k)]


def mass_edit_fields(kind: str, pkg: SipPackage | None = None) -> list[dict]:
    fields = [{"tag": definition.tag, "label": definition.label, "type": definition.type}
              for definition in schema.field_defs(kind)
              if definition.type in (schema.TEXT, schema.MULTILINE, schema.TIME_SPAN) and not definition.required]
    if pkg is None:
        return fields
    info = xsd_info.for_package(pkg)
    sample = next(pkg.iter_kind(kind), None)
    if sample is None:
        return []
    type_name = xsd_info.type_name(pkg, sample, info)
    return [field for field in fields if (definition := info.element(type_name, field["tag"])) is not None
            and definition.max_occurs == 1]


def mass_edit(pkg: SipPackage, el, kind: str, tag: str, value: str, end: str = "") -> int:
    """Sets (or with an empty value deletes) a field on all objects of a kind at or below el."""
    definition = next((d for d in schema.field_defs(kind) if d.tag == tag), None)
    if definition is None:
        raise StructureError("Dieses Metadatum kann nicht mehrfach editiert werden.")
    targets = _objects_below(pkg, el, kind)
    info = xsd_info.for_package(pkg)
    for target in targets:
        element_info = info.element(xsd_info.type_name(pkg, target, info), tag)
        if element_info is not None and element_info.max_occurs != 1:
            raise StructureError("Repeated values must be edited as lists, not by scalar bulk edit.")
    for target in targets:
        if definition.type == schema.TIME_SPAN:
            pkg.set_time_span(target, tag, value, end)
        else:
            pkg.set_text(target, tag, value)
    return len(targets)
