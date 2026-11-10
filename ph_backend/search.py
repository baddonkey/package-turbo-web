"""Simple and advanced metadata search (user documentation chapter 13)."""

from dataclasses import dataclass

from . import schema, timespan
from .sip import SipPackage, localname

SEARCHABLE_KINDS = ("ablieferung", "provenienz", "ordnungssystem", "ordnungssystemposition", "dossier",
                    "dokument", "vorgang", "aktivitaet", "mappe", "datei", "ordner")
DEFAULT_MAX_HITS = 15000
MAX_CRITERIA = 3


class TooManyHits(Exception):
    pass


@dataclass(frozen=True)
class Criterion:
    kind: str
    tag: str
    value: str = ""
    empty: bool = False


def _value(pkg: SipPackage, el, tag: str) -> str:
    if tag == "zusatzDaten":
        return "; ".join(f"{n}: {v}" for n, v in pkg.additional_data(el))
    child = pkg.child(el, tag)
    if child is None:
        return ""
    if len(child) and pkg.child(child, "von") is not None:
        return timespan.format_span(*pkg.time_span_display(el, tag))
    if len(child) and pkg.child(child, "datum") is not None:
        return pkg.time_point_display(el, tag)
    return (child.text or "").strip()


def _objects(pkg: SipPackage, kind: str):
    if kind in ("dossier", "subdossier"):
        return [d for d in pkg.iter_kind("dossier") if pkg.kind(d) == kind]
    return list(pkg.iter_kind(kind))


def simple_search(pkg: SipPackage, text: str, max_hits: int = DEFAULT_MAX_HITS) -> list[dict]:
    """Case-insensitive substring search over all metadata values."""
    needle = text.strip().lower()
    results = []
    if not needle:
        return results
    for el in pkg.iter_kind(*SEARCHABLE_KINDS):
        for c in pkg.children(el):
            tag = localname(c)
            if tag in schema.STRUCTURAL_TAGS and tag != "zusatzDaten":
                continue
            value = _value(pkg, el, tag)
            if needle in value.lower():
                results.append({
                    "object": schema.KIND_LABELS.get(pkg.kind(el), pkg.kind(el)),
                    "objectName": pkg.display_name(el),
                    "field": schema.label_for(tag),
                    "value": value,
                    "key": pkg.key(el),
                })
                if len(results) > max_hits:
                    raise TooManyHits(f"Die Suche ergibt mehr als {max_hits} Treffer. Bitte grenzen Sie die Suche ein.")
    return results


ADDITIONAL_DATA_KINDS = {"ablieferung", "ordnungssystem", "ordnungssystemposition", "dossier", "subdossier",
                         "dokument", "vorgang", "aktivitaet", "mappe"}


def searchable_fields(kind: str) -> list[dict]:
    fields = [{"tag": d.tag, "label": d.label} for d in schema.field_defs(kind)]
    if kind in ADDITIONAL_DATA_KINDS:
        fields.append({"tag": "zusatzDaten", "label": schema.label_for("zusatzDaten")})
    return fields


def advanced_search(pkg: SipPackage, criteria: list[Criterion], max_hits: int = DEFAULT_MAX_HITS) -> list[dict]:
    """All criteria must match (AND); criteria with different object types can never match together."""
    criteria = [c for c in criteria if c.kind and c.tag][:MAX_CRITERIA]
    if not criteria:
        return []
    kind = criteria[0].kind
    if any(c.kind != kind for c in criteria):
        return []
    results = []
    for el in _objects(pkg, kind):
        values = []
        for c in criteria:
            value = _value(pkg, el, c.tag)
            if c.empty:
                if value:
                    break
            elif c.value.strip().lower() not in value.lower():
                break
            values.append(value)
        else:
            row = {
                "object": schema.KIND_LABELS.get(pkg.kind(el), pkg.kind(el)),
                "objectName": pkg.display_name(el),
                "key": pkg.key(el),
            }
            for index, value in enumerate(values):
                row[f"value{index}"] = value
            results.append(row)
            if len(results) > max_hits:
                raise TooManyHits(f"Die Suche ergibt mehr als {max_hits} Treffer. Bitte grenzen Sie die Suche ein.")
    return results
