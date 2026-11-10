"""Figures for the editor tab 'Statistik' (user documentation 10.8)."""

from collections import Counter

from . import schema, timespan
from .sip import SipPackage, localname


def _depth(el, tag_q: str) -> int:
    depth, node = 0, el
    while node is not None and node.tag == tag_q:
        depth += 1
        node = node.getparent()
    return depth


def overview(pkg: SipPackage) -> dict:
    osp_q, dossier_q = pkg.q("ordnungssystemposition"), pkg.q("dossier")
    positions = list(pkg.iter_kind("ordnungssystemposition"))
    dossiers = list(pkg.iter_kind("dossier"))
    top_dossiers = [d for d in dossiers if pkg.kind(d) == "dossier"]
    subdossiers = [d for d in dossiers if pkg.kind(d) == "subdossier"]
    documents = list(pkg.iter_kind("dokument"))
    osp_levels = Counter(_depth(p, osp_q) for p in positions)
    sub_levels = Counter(_depth(d, dossier_q) - 1 for d in subdossiers)
    files_per_document = Counter(len(pkg.children(d, "dateiRef")) for d in documents)

    entries = list(pkg.walk_table_of_contents())
    content_files = [el for el, rel in entries if localname(el) == "datei" and rel.parts[:1] == ("content",)]
    size = 0
    for el in content_files:
        path = pkg.fs_path(el)
        if path is not None and path.is_file():
            size += path.stat().st_size
    metadata_mb = pkg.metadata_path.stat().st_size / 1024 ** 2 if pkg.metadata_path.is_file() else 0

    def rows(pairs):
        return [{"label": str(label), "value": str(value)} for label, value in pairs]

    return {
        "records": rows([
            ("Anzahl Ordnungssystempositionen", len(positions)),
            ("Anzahl Dossiers", len(top_dossiers)),
            ("Maximale OSP Verzeichnungstiefe", max(osp_levels, default=0)),
            ("Maximale Subdossier Verzeichnungstiefe", max(sub_levels, default=0)),
            ("Anzahl Dokumente", len(documents)),
            ("Anzahl Mappen", sum(1 for _ in pkg.iter_kind("mappe"))),
            ("Anzahl Vorgänge", sum(1 for _ in pkg.iter_kind("vorgang"))),
        ]),
        "positionsPerLevel": rows((f"Stufe {level}", count) for level, count in sorted(osp_levels.items())),
        "subdossiersPerLevel": rows((f"Stufe {level}", count) for level, count in sorted(sub_levels.items())),
        "documentsByFileCount": rows((f"{n} Datei(en)", count) for n, count in sorted(files_per_document.items())),
        "technical": rows([
            ("Grösse metadata.xml (MB)", f"{metadata_mb:.2f}"),
            ("Anzahl Dateien", len(content_files)),
            ("Umfang Primärdaten (MB)", f"{size / 1024 ** 2:.2f}" if pkg.root_dir else "-"),
        ]),
    }


def protection_periods(pkg: SipPackage) -> list[dict]:
    counter = Counter()
    delivery = pkg.delivery
    counter[("Ablieferung", pkg.text(delivery, "schutzfristenkategorie"), pkg.text(delivery, "schutzfrist"))] += 1
    for el in pkg.iter_kind("ordnungssystemposition", "dossier"):
        category, period = pkg.text(el, "schutzfristenkategorie"), pkg.text(el, "schutzfrist")
        if category or period or localname(el) == "dossier":
            counter[(schema.KIND_LABELS[pkg.kind(el)], category, period)] += 1
    return [
        {"level": level, "category": category or "-", "period": period or "-", "count": count}
        for (level, category, period), count in sorted(counter.items())
    ]


def _year_value(d) -> float:
    return d.year + (d.timetuple().tm_yday - 1) / 366


def _bar(label: str, spans: list[tuple[str, str]]) -> dict:
    """One bar of the time span comparison: 'clear', 'open' (one side unknown), 'unknown' or 'none'."""
    if not spans:
        return {"label": label, "state": "none", "text": "keine Angaben"}
    lowers = [timespan.parse_lower(s) for s, _ in spans]
    uppers = [timespan.parse_upper(e) for _, e in spans]
    any_open = any(timespan.is_open(s) or timespan.is_open(e) for s, e in spans)
    known_lower = [d for d in lowers if d]
    known_upper = [d for d in uppers if d]
    if not known_lower and not known_upper:
        return {"label": label, "state": "unknown", "text": timespan.NOT_SPECIFIED}
    start = min(known_lower) if known_lower else None
    end = max(known_upper) if known_upper else None
    return {
        "label": label,
        "state": "open" if any_open else "clear",
        "start": _year_value(start) if start else None,
        "end": _year_value(end) + 1 / 366 if end else None,
        "openStart": start is None,
        "openEnd": end is None,
        "text": f"{start.year if start else timespan.NOT_SPECIFIED} - {end.year if end else timespan.NOT_SPECIFIED}",
    }


def time_span_comparison(pkg: SipPackage) -> dict:
    def spans(elements, tag):
        result = []
        for el in elements:
            if pkg.child(el, tag) is not None:
                result.append(pkg.time_span(el, tag))
        return result

    bars = [
        _bar("Aktenbildner", spans([pkg.provenance] if pkg.provenance is not None else [], "existenzzeitraum")),
        _bar("Ordnungssystem", spans([pkg.classification_system] if pkg.classification_system is not None else [],
                                     "anwendungszeitraum")),
        _bar("Ablieferung", spans([pkg.delivery], "entstehungszeitraum")),
        _bar("Dossiers", spans(pkg.iter_kind("dossier"), "entstehungszeitraum")),
        _bar("Dokumente", spans(pkg.iter_kind("dokument"), "entstehungszeitraum")),
    ]
    values = [b[k] for b in bars for k in ("start", "end") if b.get(k) is not None]
    low = int(min(values)) if values else 0
    high = int(max(values)) + 1 if values else 1
    return {"bars": bars, "from": low, "to": max(high, low + 1)}
