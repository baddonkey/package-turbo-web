"""Notes (archivischeNotiz) on objects of the package (user documentation 14.1)."""

import getpass
from datetime import date

from . import schema, xsd_info
from .sip import SipPackage, localname

NOTE_TAG = "archivischeNotiz"


class NoteError(Exception):
    pass


def can_have_notes(pkg: SipPackage, el) -> bool:
    info = xsd_info.for_package(pkg)
    return info.element(xsd_info.type_name(pkg, el, info), NOTE_TAG) is not None


def notes_of(pkg: SipPackage, el) -> list:
    return pkg.children(el, NOTE_TAG)


def has_notes(pkg: SipPackage, el) -> bool:
    return bool(notes_of(pkg, el))


def default_author() -> str:
    try:
        return getpass.getuser()
    except OSError:
        return ""


def add_note(pkg: SipPackage, el, text: str, author: str | None = None):
    if not can_have_notes(pkg, el):
        raise NoteError(f"Zu {schema.KIND_LABELS.get(pkg.kind(el), pkg.kind(el))} können keine Notizen erfasst werden.")
    note = pkg.create_child(el, NOTE_TAG, with_id=True)
    note.append(note.makeelement(pkg.q("notizDatum")))
    note[-1].text = date.today().isoformat()
    author = default_author() if author is None else author
    if author:
        note.append(note.makeelement(pkg.q("notizErfasser")))
        note[-1].text = author[:100]
    note.append(note.makeelement(pkg.q("notizBeschreibung")))
    note[-1].text = text
    return note


def edit_note(pkg: SipPackage, note, text: str) -> None:
    pkg.set_text(note, "notizBeschreibung", text, required=True)


def delete_note(pkg: SipPackage, note) -> None:
    pkg.remove(note)


def all_notes(pkg: SipPackage) -> list[dict]:
    rows = []
    for note in pkg.root.iter(pkg.q(NOTE_TAG)):
        owner = note.getparent()
        kind = pkg.kind(owner)
        rows.append({
            "object": schema.KIND_LABELS.get(kind, kind),
            "objectName": pkg.display_name(owner),
            "comment": pkg.text(note, "notizBeschreibung"),
            "created": pkg.text(note, "notizDatum"),
            "author": pkg.text(note, "notizErfasser"),
            "key": pkg.key(owner),
            "noteKey": pkg.key(note),
        })
    return rows


def owners_with_notes(pkg: SipPackage) -> set[int]:
    return {pkg.key(n.getparent()) for n in pkg.root.iter(pkg.q(NOTE_TAG))}


def is_note(el) -> bool:
    return localname(el) == NOTE_TAG
