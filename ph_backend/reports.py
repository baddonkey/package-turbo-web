"""Printable HTML reports: Ordnungssystem, Dossierverzeichnis, Ablieferungsverzeichnis."""

from datetime import date
from html import escape
from . import statistics, timespan
from .sip import SipPackage

REPORTS = {
    "classification": "Ordnungssystem",
    "dossiers": "Dossierverzeichnis",
    "delivery": "Ablieferungsverzeichnis",
}

_STYLE = """
<style>
body { font-family: sans-serif; font-size: 9pt; }
h1 { font-size: 18pt; } h2 { font-size: 13pt; margin-top: 18px; }
table { border-collapse: collapse; width: 100%; }
th { background: #e0e0e0; text-align: left; padding: 3px; }
td { padding: 3px; border-bottom: 1px solid #cccccc; vertical-align: top; }
.osp { font-weight: bold; }
</style>
"""


def _e(value) -> str:
    return escape(str(value or ""))


def _cover(pkg: SipPackage, title: str, reporter: str) -> str:
    delivery = pkg.delivery
    provenance = pkg.provenance
    rows = [
        ("Aktenbildner", pkg.text(provenance, "aktenbildnerName")),
        ("Abliefernde Stelle", pkg.text(delivery, "ablieferndeStelle")),
        ("Ablieferungsnummer", pkg.text(delivery, "ablieferungsnummer")),
        ("Angebotsnummer", pkg.text(delivery, "angebotsnummer")),
        ("Ablieferungstyp", pkg.delivery_type),
        ("Entstehungszeitraum", timespan.format_span(*pkg.time_span_display(delivery, "entstehungszeitraum"))),
        ("Schutzfristenkategorie", pkg.text(delivery, "schutzfristenkategorie")),
        ("Schutzfrist", pkg.text(delivery, "schutzfrist")),
    ]
    stats = statistics.overview(pkg)
    rows += [(r["label"], r["value"]) for r in stats["records"] + stats["technical"]]
    rows += [("Paket", pkg.name), ("Erstellt am", date.today().strftime("%d.%m.%Y")), ("Erstellt durch", reporter)]
    body = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in rows)
    return f"<h1>{_e(title)}</h1><table>{body}</table>"


def _dossier_row(pkg: SipPackage, dossier, indent: int) -> str:
    span = timespan.format_span(*pkg.time_span_display(dossier, "entstehungszeitraum"))
    pad = "&nbsp;" * 4 * indent
    return (f"<tr><td>{_e(pkg.text(dossier, 'aktenzeichen'))}</td><td>{pad}{_e(pkg.text(dossier, 'titel'))}</td>"
            f"<td>{_e(span)}</td><td>{_e(pkg.text(dossier, 'erscheinungsform'))}</td></tr>")


def _dossier_rows(pkg: SipPackage, parent, indent: int = 0) -> str:
    html = ""
    for dossier in pkg.children(parent, "dossier"):
        html += _dossier_row(pkg, dossier, indent) + _dossier_rows(pkg, dossier, indent + 1)
    return html


def _classification(pkg: SipPackage, with_dossiers: bool) -> str:
    rows = ""

    def walk(parent, depth: int) -> None:
        nonlocal rows
        for position in pkg.children(parent, "ordnungssystemposition"):
            pad = "&nbsp;" * 4 * depth
            rows += (f"<tr class='osp'><td>{_e(pkg.text(position, 'nummer'))}</td>"
                     f"<td colspan='3'>{pad}{_e(pkg.text(position, 'titel'))}</td></tr>")
            if with_dossiers:
                rows += _dossier_rows(pkg, position, depth + 1)
            walk(position, depth + 1)

    walk(pkg.classification_system, 0)
    header = ("<tr><th>Nummer / Aktenzeichen</th><th>Titel</th><th>Entstehungszeitraum</th><th>Form</th></tr>"
              if with_dossiers else "<tr><th>Nummer</th><th colspan='3'>Titel</th></tr>")
    return f"<table>{header}{rows}</table>"


def build_html(pkg: SipPackage, report: str, reporter: str = "") -> str:
    title = REPORTS[report]
    cover = _cover(pkg, title, reporter)
    if report == "classification":
        body = _classification(pkg, with_dossiers=False)
    elif report == "dossiers":
        rows = "".join(_dossier_row(pkg, d, 0) for d in pkg.iter_kind("dossier") if pkg.kind(d) == "dossier")
        body = ("<table><tr><th>Aktenzeichen</th><th>Titel</th><th>Entstehungszeitraum</th><th>Form</th></tr>"
                f"{rows}</table>")
    else:
        body = _classification(pkg, with_dossiers=True)
    system_name = pkg.text(pkg.classification_system, "name")
    return (f"<html><head>{_STYLE}</head><body>{cover}<h2 style='page-break-before: always'>"
            f"{_e(title)} {_e(system_name)}</h2>{body}</body></html>")


