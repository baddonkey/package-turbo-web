"""Creating a new, minimal and XSD-valid SIP (user documentation 8.1)."""

import re
import shutil
from datetime import date
from pathlib import Path

from lxml import etree

from . import files, structure
from .sip import SipPackage
from .xsd_info import LATEST_RELEASE, schema_release

ARELDA_NS = "http://bar.admin.ch/arelda/v4"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_VERSION = schema_release(LATEST_RELEASE)[0]
XSD_VERSION_LABEL = f"eCH-0160 v{LATEST_RELEASE} (arelda v4, Schema-Version {SCHEMA_VERSION})"
SIP_NAME = re.compile(r"^SIP_\d{8}_[A-Za-z0-9.\-]+(_[A-Za-z0-9_.\-]+)?$")


class PackageCreationError(Exception):
    pass


def suggested_name(delivering_office: str, reference: str = "", day: date | None = None) -> str:
    day = day or date.today()
    parts = ["SIP", day.strftime("%Y%m%d"), files.sanitize_name(delivering_office).replace("_", "-") or "Stelle"]
    if reference.strip():
        parts.append(files.sanitize_name(reference))
    return "_".join(parts)


def check_name(name: str, validate_sip_name: bool = True) -> None:
    if not files.is_valid_name(name):
        raise PackageCreationError("Der SIP-Name enthält unerlaubte Zeichen (erlaubt: A-Z a-z 0-9 _ . -).")
    if validate_sip_name and not SIP_NAME.match(name):
        raise PackageCreationError("Der SIP-Name entspricht nicht der Spezifikation "
                                   "SIP_[Ablieferungsdatum JJJJMMTT]_[Abliefernde Stelle]_[Referenz].")


def _sub(parent, tag: str, text: str | None = None):
    el = etree.SubElement(parent, f"{{{ARELDA_NS}}}{tag}")
    if text is not None:
        el.text = text
    return el


def _metadata(delivery_type: str, delivering_office: str, creator: str, system_name: str,
              schema_version: str = SCHEMA_VERSION) -> etree._ElementTree:
    root = etree.Element(f"{{{ARELDA_NS}}}paket", nsmap={None: ARELDA_NS, "xsi": XSI_NS})
    root.set(f"{{{XSI_NS}}}schemaLocation", f"{ARELDA_NS} xsd/arelda.xsd")
    root.set(f"{{{XSI_NS}}}type", "paketSIP")
    root.set("schemaVersion", schema_version)
    _sub(root, "paketTyp", "SIP")
    toc = _sub(root, "inhaltsverzeichnis")
    header = _sub(toc, "ordner")
    _sub(header, "name", "header")
    _sub(header, "originalName", "header")
    xsd_folder = _sub(header, "ordner")
    _sub(xsd_folder, "name", "xsd")
    _sub(xsd_folder, "originalName", "xsd")
    content = _sub(toc, "ordner")
    _sub(content, "name", "content")
    _sub(content, "originalName", "content")

    files_type = delivery_type.upper() == "FILES"
    delivery = _sub(root, "ablieferung")
    delivery.set(f"{{{XSI_NS}}}type", "ablieferungFilesSIP" if files_type else "ablieferungGeverSIP")
    _sub(delivery, "ablieferungstyp", "FILES" if files_type else "GEVER")
    _sub(delivery, "ablieferndeStelle", delivering_office)
    provenance = _sub(delivery, "provenienz")
    _sub(provenance, "aktenbildnerName", creator or delivering_office)
    _sub(provenance, "registratur", "keine Angabe")
    system = _sub(delivery, "ordnungssystem")
    _sub(system, "name", system_name)
    return etree.ElementTree(root)


def create_package(parent_dir: str | Path, name: str, delivering_office: str, delivery_type: str = "GEVER",
                   creator: str = "", system_name: str = "Ordnungssystem", csv_path: str | Path | None = None,
                   validate_sip_name: bool = True, version: str = LATEST_RELEASE) -> SipPackage:
    check_name(name, validate_sip_name)
    try:
        schema_version, schema_dir = schema_release(version)
    except ValueError as exc:
        raise PackageCreationError(str(exc)) from exc
    if delivery_type.upper() not in ("GEVER", "FILES"):
        raise PackageCreationError(f"Unsupported delivery type: {delivery_type}")
    if not delivering_office.strip():
        raise PackageCreationError("Die abliefernde Stelle muss angegeben werden.")
    target = Path(parent_dir) / name
    if target.exists():
        raise PackageCreationError(f"Am Speicherort existiert bereits «{name}».")
    xsd_dir = target / "header" / "xsd"
    xsd_dir.mkdir(parents=True)
    (target / "content").mkdir()
    try:
        for xsd in sorted(schema_dir.glob("*.xsd")):
            shutil.copy2(xsd, xsd_dir / xsd.name)
        tree = _metadata(delivery_type, delivering_office.strip(), creator.strip(),
                         system_name.strip() or "Ordnungssystem", schema_version)
        tree.write(str(target / "header" / "metadata.xml"), encoding="UTF-8", xml_declaration=True, pretty_print=True)

        pkg = SipPackage.open_folder(target)
        xsd_entry = next(o for o in pkg.iter_kind("ordner") if pkg.text(o, "name") == "xsd")
        for xsd in sorted(xsd_dir.glob("*.xsd")):
            files.make_file_entry(pkg, xsd_entry, xsd)
        if csv_path:
            structure.import_csv(pkg, csv_path)
        else:
            structure.add_objects(pkg, pkg.classification_system, "ordnungssystemposition", 1)
        pkg.save()
        return pkg
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
