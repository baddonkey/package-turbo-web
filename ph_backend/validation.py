"""Package structure, metadata, fixity and archival acceptance checks."""

import csv
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from lxml import etree

from . import file_formats, files, schema, timespan, xsd_info
from .metadata_requirements import ENTITY_TAGS, RELEASE_BY_SCHEMA_VERSION, mandatory_metadata_fields
from .sip import SipPackage, UnsupportedChecksumError, file_checksum, localname

ERROR = "error"
WARNING = "warning"

MAX_PATH_LENGTH = 180
MAX_PACKAGE_SIZE = 8 * 1024 ** 3
MAX_FILES_IN_PACKAGE = 1_000_000
RECOMMENDED_FILES_PER_FOLDER = 5_000
_SIP_NAME = re.compile(r"^SIP_\d{8}_.+")
_OFFICE_EXTENSIONS = {".doc", ".docm", ".docx", ".dot", ".dotm", ".dotx", ".ppt", ".pptm",
                      ".pptx", ".pps", ".ppsm", ".ppsx", ".pot", ".potm", ".potx", ".xls",
                      ".xlsb", ".xlsm", ".xlsx", ".xlt", ".xltm", ".xltx"}


def _terminate_droid_process_tree(process) -> None:
    if os.name == "nt":
        # The Windows DROID launcher is a batch file which starts Java as a child process.
        try:
            result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is None or result.returncode != 0:
            try:
                process.terminate()
            except OSError:
                pass
    else:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait()


@dataclass(frozen=True)
class Check:
    id: str
    name: str
    group: str
    full_only: bool


_STRUCTURE = "Ablieferungsstruktur"
_CLASSIFICATION = "Ordnungssystem"
_FILE_SYSTEM = "Metadaten und Dateisystem"
_PLAUSIBILITY = "Vollständigkeit und Plausibilität"

CHECKS = [
    Check("file_format", "Dateiformat", _STRUCTURE, True),
    Check("droid_format", "Formaterkennung (DROID)", _STRUCTURE, True),
    Check("encryption", "Verschlüsselte Primärdatei", _STRUCTURE, True),
    Check("package_size", "Paketgrösse", _STRUCTURE, True),
    Check("file_count", "Dateianzahl im SIP", _STRUCTURE, True),
    Check("folder_file_count", "Dateien pro Ordner", _STRUCTURE, True),
    Check("folder_level", "Dossier- und Mappenordner", _STRUCTURE, True),
    Check("path_length", "Pfadlänge", _STRUCTURE, False),
    Check("checksum", "Prüfsumme", _STRUCTURE, True),
    Check("invalid_reference", "Dateireferenz", _FILE_SYSTEM, False),
    Check("sip_name", "SIP-Name", _STRUCTURE, True),
    Check("invalid_value", "Ungültige Angabe", _STRUCTURE, False),
    Check("illegal_entries", "Unzulässige Datei", _STRUCTURE, True),
    Check("header_content", "SIP", _STRUCTURE, True),
    Check("files_documentation", "FILES-Dokumentation", _STRUCTURE, True),
    Check("empty_folders", "Leere Ordner", _STRUCTURE, False),
    Check("charset", "Zeichensatz", _STRUCTURE, False),
    Check("dossier_title", "Dossier Titel", _CLASSIFICATION, False),
    Check("position_number", "Ordnungssystemposition Nummer", _CLASSIFICATION, False),
    Check("positions_and_dossiers", "Ordnungssystempositionen und Dossiers", _CLASSIFICATION, True),
    Check("wrong_file", "Falsche Datei", _FILE_SYSTEM, True),
    Check("wrong_folder", "Falscher Ordner", _FILE_SYSTEM, True),
    Check("missing_file", "Fehlende Datei", _FILE_SYSTEM, True),
    Check("missing_folder", "Fehlender Ordner", _FILE_SYSTEM, True),
    Check("surplus_file", "Überzählige Datei", _FILE_SYSTEM, True),
    Check("surplus_folder", "Überzähliger Ordner", _FILE_SYSTEM, True),
    Check("document_without_files", "Dokument ohne Dateien", _PLAUSIBILITY, False),
    Check("invalid_time_span", "Falsche Zeitraum Angaben", _PLAUSIBILITY, False),
    Check("missing_values", "Fehlende Angaben", _PLAUSIBILITY, False),
    Check("identical_files", "Identische Dateien", _PLAUSIBILITY, True),
    Check("unassigned_files", "Keine Zuweisung", _PLAUSIBILITY, False),
    Check("empty_dossiers", "Leere Dossiers", _PLAUSIBILITY, False),
    Check("protection_period", "Schutzfrist", _PLAUSIBILITY, False),
    Check("time_span_hierarchy", "Zeitraum", _PLAUSIBILITY, False),
    Check("open_time_span", "Offener Zeitraum", _PLAUSIBILITY, False),
    Check("schema", "Allgemein", _PLAUSIBILITY, False),
    Check("validity_failed", "Gültigkeitsprüfung fehlgeschlagen", _PLAUSIBILITY, True),
]
CHECK_NAMES = [c.name for c in CHECKS]
_NAME_OF = {c.id: c.name for c in CHECKS}
_COMPLEX_DATA_TYPES = {"zeitraum", "historischerzeitraum", "historischerzeitpunkt"}


def _validate_local_schema_dependencies(schema_path: Path, schema_directory: Path,
                                       parser: etree.XMLParser) -> None:
    schema_root = Path(schema_directory).resolve()
    dependency_tags = {"include", "import", "redefine", "override"}
    for candidate in sorted(schema_root.glob("*.xsd")):
        document = etree.parse(str(candidate), parser)
        for element in document.getroot().iter():
            if not isinstance(element.tag, str) or etree.QName(element).localname not in dependency_tags:
                continue
            location = element.get("schemaLocation")
            if not location:
                continue
            parsed = urlsplit(location)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in location:
                raise OSError("XSD dependencies must be local files inside header/xsd.")
            relative = PurePosixPath(unquote(parsed.path))
            if relative.is_absolute() or ":" in location or any(part in ("", ".", "..") for part in relative.parts):
                raise OSError("XSD dependencies must not escape header/xsd.")
            dependency = (candidate.parent / Path(*relative.parts)).resolve()
            if not dependency.is_relative_to(schema_root) or not dependency.is_file():
                raise OSError("XSD dependency is missing or escapes header/xsd.")


class Validator:
    def __init__(self, pkg: SipPackage, validate_sip_name: bool = True, identify_formats: bool = False, *,
                 validate_packaged_schema: bool = True):
        self.pkg = pkg
        self.validate_sip_name = validate_sip_name
        self.identify_formats = identify_formats
        self.validate_packaged_schema = validate_packaged_schema
        self.issues: list[dict] = []
        self._schema_cache: dict[Path, etree.XMLSchema] = {}

    # ---------------------------------------------------------------- api

    def run(self, full: bool, progress=None, cancelled=None) -> list[dict]:
        """Runs all continuous checks and, if full, also the expensive ones."""
        self.issues = []
        steps = [c for c in CHECKS if full or not c.full_only]
        for i, check in enumerate(steps):
            if cancelled and cancelled():
                break
            check_method = getattr(self, "_check_" + check.id)
            if check.id == "droid_format":
                check_method(cancelled=cancelled)
            else:
                check_method()
            if progress:
                progress(int((i + 1) / len(steps) * 100))
        return self.issues

    def _add(self, check_id: str, el, description: str, field: str = "", value: str = "", severity: str = ERROR,
             object_kind: str | None = None, object_name: str | None = None, path: str = "") -> None:
        pkg = self.pkg
        kind = object_kind if object_kind is not None else (pkg.kind(el) if el is not None else "paket")
        self.issues.append({
            "severity": severity,
            "severityLabel": "Fehler" if severity == ERROR else "Warnung",
            "checkId": check_id,
            "check": _NAME_OF[check_id],
            "object": schema.KIND_LABELS.get(kind, kind),
            "objectName": object_name if object_name is not None else (pkg.display_name(el) if el is not None else pkg.name),
            "description": description,
            "field": schema.label_for(field) if field else "",
            "fieldTag": field,
            "value": value,
            "key": pkg.key(el) if el is not None else 0,
            "path": path,
        })

    # ---------------------------------------------------------------- helpers

    def _toc_entries(self):
        return list(self.pkg.walk_table_of_contents())

    def _content_files(self):
        return [(el, p) for el, p in self._toc_entries() if localname(el) == "datei" and p.parts[:1] == ("content",)]

    def _fs_entries(self) -> dict[PurePosixPath, Path]:
        root = self.pkg.file_root
        result = {}
        for top in ("header", "content"):
            base = root / top
            if base.is_dir():
                result[PurePosixPath(top)] = base
                for path in base.rglob("*"):
                    result[PurePosixPath(path.relative_to(root).as_posix())] = path
        return result

    def _is_metadata_file(self, rel: PurePosixPath) -> bool:
        return rel == PurePosixPath("header/metadata.xml")

    def _has_files(self, el) -> bool:
        pkg = self.pkg
        if pkg.children(el, "dateiRef"):
            return True
        return any(self._has_files(c) for c in pkg.children(el) if localname(c) in ("dossier", "dokument", "mappe"))

    # ---------------------------------------------------------------- structure

    def _check_file_format(self):
        """Rates content files with the KOST-CECO KaD; recommended and accepted formats pass."""
        for el, rel in self._content_files():
            fmt = file_formats.classify(rel.name)
            if fmt.rating == file_formats.NOT_ACCEPTED:
                self._add("file_format", el,
                          f"Das Format {fmt.name} ist gemäss KOST-Katalog archivischer Dateiformate (KaD) "
                          "nicht archivtauglich und muss konvertiert werden.", "name", rel.name)
            elif fmt.rating == file_formats.UNKNOWN:
                self._add("file_format", el,
                          f"Das Format {fmt.name} ist im KOST-Katalog archivischer Dateiformate (KaD) nicht erfasst.",
                          "name", rel.name, WARNING)

    def _check_droid_format(self, cancelled=None):
        if not self.identify_formats or self.pkg.file_root is None:
            return
        content_directory = Path(self.pkg.file_root) / "content"
        if not content_directory.is_dir():
            return

        entries_by_path = {}
        for element, relative in self._content_files():
            path = self.pkg.fs_path(element)
            if path is not None and path.is_file():
                path_key = os.path.normcase(os.path.abspath(path))
                entries_by_path[path_key] = (element, relative)
        if not entries_by_path:
            return

        configured_executable = os.environ.get("DROID_EXECUTABLE", "droid")
        executable = shutil.which(configured_executable)
        if executable is None and Path(configured_executable).is_file():
            executable = configured_executable
        if executable is None:
            self._add("droid_format", None,
                      "DROID wurde nicht gefunden; die Dateiendung konnte nicht mit dem Dateiformat abgeglichen werden.",
                      severity=WARNING)
            return

        try:
            with tempfile.TemporaryDirectory(prefix="package-turbo-droid-") as temporary_directory:
                report_path = Path(temporary_directory) / "identification.csv"
                process = subprocess.Popen(
                    [executable, "-q", "-R", "-co", "FILE_PATH", "NAME", "EXTENSION_MISMATCH", "PUID",
                     "FORMAT_NAME", "-a", str(content_directory), "-o", str(report_path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 300
                while process.poll() is None:
                    if cancelled and cancelled():
                        _terminate_droid_process_tree(process)
                        return
                    if time.monotonic() >= deadline:
                        _terminate_droid_process_tree(process)
                        self._add("droid_format", None,
                                  "Die DROID-Formaterkennung wurde nach 300 Sekunden abgebrochen.", severity=WARNING)
                        return
                    time.sleep(0.1)

                if process.returncode != 0 or not report_path.is_file():
                    detail = f"Exit-Code {process.returncode}"
                    self._add("droid_format", None,
                              f"DROID konnte die Dateien nicht identifizieren ({detail}).", severity=WARNING)
                    return

                with report_path.open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    required_columns = {"FILE_PATH", "EXTENSION_MISMATCH", "PUID", "FORMAT_NAME"}
                    if not reader.fieldnames or not required_columns.issubset(reader.fieldnames):
                        raise csv.Error("DROID report is missing required columns")
                    for row in reader:
                        reported_path = (row.get("FILE_PATH") or "").strip()
                        if not reported_path:
                            continue
                        path_key = os.path.normcase(os.path.abspath(reported_path))
                        entry = entries_by_path.get(path_key)
                        if entry is None:
                            continue
                        element, relative = entry
                        format_name = (row.get("FORMAT_NAME") or "").strip() or "unbekanntes Format"
                        puid = (row.get("PUID") or "").strip()
                        identification = f"{format_name} ({puid})" if puid else format_name
                        identified_format = file_formats.classify_puid(puid)
                        if identified_format and identified_format.rating == file_formats.NOT_ACCEPTED:
                            already_reported = any(issue["checkId"] == "file_format"
                                                   and issue["key"] == self.pkg.key(element)
                                                   for issue in self.issues)
                            if not already_reported:
                                self._add("file_format", element,
                                          f"Das von DROID identifizierte Format {identification} ist gemäss "
                                          "KOST-Katalog archivischer Dateiformate (KaD) nicht archivtauglich.",
                                          "name", relative.name, ERROR, path=relative.as_posix())
                        if row.get("EXTENSION_MISMATCH", "").strip().lower() == "true":
                            self._add("droid_format", element,
                                      f"Die Dateiendung stimmt nicht mit der DROID-Identifikation {identification} überein.",
                                      "name", relative.name, ERROR, path=relative.as_posix())
        except (OSError, UnicodeError, csv.Error, subprocess.SubprocessError) as exc:
            self._add("droid_format", None,
                      f"DROID konnte die Dateien nicht identifizieren ({exc}).", severity=WARNING)

    def _check_encryption(self):
        import msoffcrypto
        from msoffcrypto.exceptions import FileFormatError
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        for element, relative_path in self._content_files():
            path = self.pkg.fs_path(element)
            if path is None or not path.is_file():
                continue
            encrypted = False
            if relative_path.suffix.lower() == ".pdf":
                try:
                    encrypted = PdfReader(str(path), strict=False).is_encrypted
                except (PdfReadError, OSError, ValueError):
                    pass
            if not encrypted:
                try:
                    if zipfile.is_zipfile(path):
                        with zipfile.ZipFile(path) as archive:
                            encrypted = any(info.flag_bits & 0x1 for info in archive.infolist())
                except (OSError, zipfile.BadZipFile):
                    pass
            if not encrypted and relative_path.suffix.lower() in _OFFICE_EXTENSIONS:
                try:
                    with path.open("rb") as stream:
                        encrypted = msoffcrypto.OfficeFile(stream).is_encrypted()
                except (FileFormatError, OSError, ValueError):
                    pass
            if encrypted:
                self._add("encryption", element,
                          "Die Primärdatei ist passwortgeschützt oder verschlüsselt und muss vor der Ablieferung entschlüsselt werden.",
                          "name", relative_path.as_posix())

    def _check_package_size(self):
        if not self.pkg.root_dir:
            return
        total = sum(p.stat().st_size for p in self.pkg.file_root.rglob("*") if p.is_file())
        if total > MAX_PACKAGE_SIZE:
            self._add("package_size", None,
                      f"Das Paket ist {total / 1024 ** 3:.2f} GB gross (maximal 8 GB). "
                      "Kontaktieren Sie vor der Vorbereitung oder Übermittlung das zuständige Archiv.",
                      value=str(total), severity=WARNING)

    def _check_file_count(self):
        files_in_package = [(element, path) for element, path in self._toc_entries()
                            if localname(element) == "datei"]
        if len(files_in_package) > MAX_FILES_IN_PACKAGE:
            self._add("file_count", None,
                      f"Das SIP enthält {len(files_in_package)} Dateien (maximal {MAX_FILES_IN_PACKAGE:,} erlaubt).",
                      value=str(len(files_in_package)))

    def _check_folder_file_count(self):
        for folder in self.pkg.iter_kind("ordner"):
            direct_files = [child for child in self.pkg.children(folder) if localname(child) == "datei"]
            if len(direct_files) > RECOMMENDED_FILES_PER_FOLDER:
                self._add("folder_file_count", folder,
                          f"Der Ordner enthält {len(direct_files)} Dateien; empfohlen sind höchstens "
                          f"{RECOMMENDED_FILES_PER_FOLDER:,}.", "name", self.pkg.text(folder, "name"), WARNING)

    def _check_folder_level(self):
        levels = set()
        for reference in self.pkg.root.iter(self.pkg.q("dateiRef")):
            owner = reference.getparent()
            while owner is not None and localname(owner) not in ("dossier", "subdossier", "mappe"):
                owner = owner.getparent()
            if owner is None:
                continue
            for identifier in (reference.text or "").split():
                element = self.pkg.file_by_id(identifier)
                path = self.pkg.relpath(element) if element is not None else None
                if path is not None and len(path.parts) > 1 and path.parts[0] == "content":
                    levels.add(len(path.parts) - 2)
        if len(levels) > 1:
            self._add("folder_level", None,
                      "Die Ordner der Dossiers und Mappen liegen nicht auf gleicher Ebene unter content/. "
                      "Eine einheitliche Ebene wird empfohlen.",
                      value=", ".join(str(level) for level in sorted(levels)), severity=WARNING)

    def _check_path_length(self):
        top = self.pkg.root_dir.name if self.pkg.root_dir else ""
        for el, rel in self._toc_entries():
            full = f"{top}/{rel.as_posix()}" if top else rel.as_posix()
            if len(full) >= MAX_PATH_LENGTH:
                self._add("path_length", el, f"Der Pfad ist {len(full)} Zeichen lang (weniger als 180 erlaubt).",
                          "name", full, WARNING)

    def _check_checksum(self):
        pkg = self.pkg
        if not pkg.root_dir:
            return
        for el, rel in self._toc_entries():
            if localname(el) != "datei" or self._is_metadata_file(rel):
                continue
            expected = pkg.text(el, "pruefsumme")
            algorithm = pkg.text(el, "pruefalgorithmus") or "MD5"
            path = pkg.fs_path(el)
            if not expected:
                self._add("checksum", el, "Für die Datei ist keine Prüfsumme erfasst.", "pruefsumme")
                continue
            if path is None or not path.is_file():
                continue
            try:
                actual = file_checksum(path, algorithm)
            except UnsupportedChecksumError as exc:
                self._add("checksum", el, str(exc), "pruefalgorithmus", algorithm)
                continue
            if actual.lower() != expected.lower():
                self._add("checksum", el, f"Die Prüfsumme ({algorithm}) stimmt nicht mit dem Dateiinhalt überein.",
                          "pruefsumme", expected)

    def _check_sip_name(self):
        if not self.validate_sip_name or not self.pkg.root_dir:
            return
        name = self.pkg.root_dir.name
        if not _SIP_NAME.match(name):
            self._add("sip_name", None,
                      "Der Name des Pakets entspricht nicht dem Muster SIP_[Ablieferungsdatum]_[Abliefernde Stelle]_Referenz.",
                      value=name)

    def _check_invalid_value(self):
        """Simple type checks that the XSD would also catch, reported per field."""
        pkg = self.pkg
        for el in pkg.iter_kind("dossier", "dokument", "ablieferung", "ordnungssystemposition"):
            period = pkg.text(el, "schutzfrist")
            if period and not period.isdigit():
                self._add("invalid_value", el, "Die Schutzfrist muss eine Anzahl Jahre (Ganzzahl) sein.",
                          "schutzfrist", period)

    def _check_illegal_entries(self):
        root = self.pkg.file_root
        if not root:
            return
        for path in root.iterdir():
            if path.name not in ("header", "content") or not path.is_dir():
                self._add("illegal_entries", None,
                          "Im Toplevel-Ordner des Pakets sind nur die Ordner header und content erlaubt.",
                          value=path.name, object_kind="datei" if path.is_file() else "ordner", object_name=path.name,
                          path=path.name)

    def _check_header_content(self):
        root = self.pkg.file_root
        if not root:
            return
        header = root / "header"
        if not (root / "content").is_dir():
            self._add("header_content", None, "Der Ordner content fehlt.", value="content")
        if not header.is_dir():
            self._add("header_content", None, "Der Ordner header fehlt.", value="header", path="header")
            return
        if not (header / "xsd").is_dir() or not any((header / "xsd").glob("*.xsd")):
            self._add("header_content", None, "Der Ordner header/xsd mit den XSD-Dateien fehlt.", value="header/xsd",
                      path="header/xsd")
        for path in header.iterdir():
            if path.name not in ("metadata.xml", "xsd"):
                self._add("header_content", None, "Im Ordner header sind nur metadata.xml und der Ordner xsd erlaubt.",
                          value=path.name, object_kind="datei" if path.is_file() else "ordner", object_name=path.name,
                          path=f"header/{path.name}")
        for relative, path in self._fs_entries().items():
            if (len(relative.parts) < 2 or relative.parts[0] != "header" or not path.is_file()
                    or relative == PurePosixPath("header/metadata.xml")
                    or len(relative.parts) == 2):
                continue
            if relative.parts[1] == "xsd" and relative.suffix.lower() == ".xsd":
                continue
            self._add("header_content", None, "Nur XSD-Schemas dürfen zusätzlich in header/ liegen.",
                      value=relative.as_posix(), object_kind="datei", object_name=path.name,
                      path=relative.as_posix())

    def _check_files_documentation(self):
        pkg = self.pkg
        if pkg.delivery_type.upper() != "FILES" or pkg.root_dir is None:
            return
        comparison = self._compare_file_system()
        if comparison is None:
            return
        fs, toc = comparison
        documentation = PurePosixPath("content/1_DOK")
        data = PurePosixPath("content/2_DATEN")
        if not any(path in fs or path in toc for path in (documentation, data)):
            return

        for path in (documentation, data):
            filesystem_entry = fs.get(path)
            metadata_entry = toc.get(path)
            if filesystem_entry is None or not filesystem_entry.is_dir():
                self._add("files_documentation", None,
                          f"Für ein FILES-SIP mit integrierter Dokumentation fehlt der Ordner {path.name}/.",
                          value=path.as_posix(), object_kind="ordner", object_name=path.name, path=path.as_posix())
            if metadata_entry is None or localname(metadata_entry) != "ordner":
                self._add("files_documentation", None,
                          f"Der Ordner {path.name}/ muss im metadata.xml-Inhaltsverzeichnis aufgeführt sein.",
                          value=path.as_posix(), object_kind="ordner", object_name=path.name, path=path.as_posix())

        data_files = [(element, path) for element, path in self._content_files()
                  if path.parts[:2] == data.parts and path in fs and fs[path].is_file()]
        if not data_files:
            self._add("files_documentation", None, "Der Ordner 2_DATEN/ enthält keine aufgelisteten Dateien.",
                      value=data.as_posix(), object_kind="ordner", object_name="2_DATEN", path=data.as_posix())
            return
        siard_ids = {element.get("id", "") for element, path in data_files
                 if path.name.lower().endswith(".siard")}
        assigned_to_dossier = siard_ids and any(
            file_element.get("id", "") in siard_ids
            for dossier in self.pkg.iter_kind("dossier")
            for file_element in self.pkg.referenced_files(dossier))
        if siard_ids and not assigned_to_dossier:
            self._add("files_documentation", None,
                      "Mindestens ein Dossier muss eine Datei aus 2_DATEN/ referenzieren.",
                      value=data.as_posix(), object_kind="dossier", object_name="2_DATEN")

    def _check_empty_folders(self):
        pkg = self.pkg

        def has_files(folder) -> bool:
            return bool(pkg.children(folder, "datei")) or any(has_files(o) for o in pkg.children(folder, "ordner"))

        for el, rel in self._toc_entries():
            if localname(el) == "ordner" and len(rel.parts) > 1 and not has_files(el):
                self._add("empty_folders", el, "Der Ordner enthält keine Dateien.", "name", rel.as_posix(), WARNING)

    def _check_charset(self):
        for el, rel in self._toc_entries():
            if not files.is_valid_name(rel.name):
                self._add("charset", el, "Der Name enthält unzulässige Zeichen (erlaubt: A-Z a-z 0-9 _ . -).",
                          "name", rel.name)

    # ---------------------------------------------------------------- classification system

    def _check_dossier_title(self):
        pkg = self.pkg
        for el in pkg.iter_kind("dossier"):
            title = pkg.text(el, "titel")
            if title and len(title) < 5:
                self._add("dossier_title", el, "Der Titel des Dossiers ist kürzer als 5 Zeichen und vermutlich nicht sprechend.",
                          "titel", title, WARNING)

    def _check_position_number(self):
        pkg = self.pkg
        parents = [pkg.classification_system] + list(pkg.iter_kind("ordnungssystemposition"))
        for parent in parents:
            seen = defaultdict(list)
            for position in pkg.children(parent, "ordnungssystemposition"):
                number = pkg.text(position, "nummer")
                if number:
                    seen[number].append(position)
            for number, positions in seen.items():
                if len(positions) > 1:
                    for position in positions:
                        self._add("position_number", position,
                                  "Die Nummer der Ordnungssystemposition ist nicht eindeutig.", "nummer", number)

    def _check_positions_and_dossiers(self):
        pkg = self.pkg
        for position in pkg.iter_kind("ordnungssystemposition"):
            if pkg.children(position, "ordnungssystemposition") and pkg.children(position, "dossier"):
                self._add("positions_and_dossiers", position,
                          "Dossiers dürfen nur auf der untersten Stufe des Ordnungssystems (Rubriken) eröffnet werden.")

    # ---------------------------------------------------------------- metadata vs. file system

    def _compare_file_system(self):
        if not self.pkg.root_dir:
            return None
        fs = self._fs_entries()
        toc = {rel: el for el, rel in self._toc_entries()}
        return fs, toc

    def _check_wrong_file(self):
        cmp = self._compare_file_system()
        if cmp:
            fs, toc = cmp
            for rel, el in toc.items():
                if localname(el) == "datei" and rel in fs and fs[rel].is_dir():
                    self._add("wrong_file", el, "Im Dateisystem existiert an dieser Stelle ein Ordner.", "name", rel.as_posix())

    def _check_wrong_folder(self):
        cmp = self._compare_file_system()
        if cmp:
            fs, toc = cmp
            for rel, el in toc.items():
                if localname(el) == "ordner" and rel in fs and fs[rel].is_file():
                    self._add("wrong_folder", el, "Im Dateisystem existiert an dieser Stelle eine Datei.", "name", rel.as_posix())

    def _check_missing_file(self):
        cmp = self._compare_file_system()
        if cmp:
            fs, toc = cmp
            for rel, el in toc.items():
                if localname(el) == "datei" and rel not in fs:
                    self._add("missing_file", el, "Die Datei existiert im Dateisystem nicht.", "name", rel.as_posix())

    def _check_missing_folder(self):
        cmp = self._compare_file_system()
        if cmp:
            fs, toc = cmp
            for rel, el in toc.items():
                if localname(el) == "ordner" and rel not in fs:
                    self._add("missing_folder", el, "Der Ordner existiert im Dateisystem nicht.", "name", rel.as_posix())

    def _check_surplus_file(self):
        cmp = self._compare_file_system()
        if cmp:
            fs, toc = cmp
            for rel, path in fs.items():
                if path.is_file() and rel not in toc and not self._is_metadata_file(rel):
                    self._add("surplus_file", None, "Die Datei ist in den Metadaten nicht aufgeführt.",
                              value=rel.as_posix(), object_kind="datei", object_name=rel.name, path=rel.as_posix())

    def _check_surplus_folder(self):
        cmp = self._compare_file_system()
        if cmp:
            fs, toc = cmp
            for rel, path in fs.items():
                if path.is_dir() and rel not in toc:
                    self._add("surplus_folder", None, "Der Ordner ist in den Metadaten nicht aufgeführt.",
                              value=rel.as_posix(), object_kind="ordner", object_name=rel.name, path=rel.as_posix())

    def _check_invalid_reference(self):
        pkg = self.pkg
        for owner in pkg.root.iter():
            for reference in pkg.children(owner, "dateiRef"):
                identifiers = (reference.text or "").split()
                if not identifiers:
                    self._add("invalid_reference", owner, "Die Dateireferenz enthält keine Datei-ID.",
                              "dateiRef")
                    continue
                for identifier in identifiers:
                    file_element = pkg.file_by_id(identifier)
                    path = pkg.relpath(file_element) if file_element is not None else None
                    if path is None or not path.parts or path.parts[0] != "content":
                        self._add("invalid_reference", owner,
                                  "Die Dateireferenz verweist auf keine Primärdatei im Ordner content/.",
                                  "dateiRef", identifier)

    # ---------------------------------------------------------------- completeness and plausibility

    def _check_document_without_files(self):
        pkg = self.pkg
        for el in pkg.iter_kind("dokument"):
            if pkg.text(el, "erscheinungsform").lower() == "digital" and not pkg.children(el, "dateiRef"):
                self._add("document_without_files", el, "Das digitale Dokument enthält keine Dateien.", severity=WARNING)

    def _spans(self):
        pkg = self.pkg
        for el in pkg.iter_kind("ablieferung", "dossier", "dokument"):
            start, end = pkg.time_span(el, "entstehungszeitraum")
            if start or end:
                yield el, start, end

    def _check_invalid_time_span(self):
        for el, start, end in self._spans():
            lower, upper = timespan.parse_lower(start), timespan.parse_upper(end)
            if lower and upper and upper < lower:
                self._add("invalid_time_span", el, "Das Enddatum liegt vor dem Anfangsdatum.",
                          "entstehungszeitraum", timespan.format_span(start, end))

    def _check_missing_values(self):
        pkg = self.pkg
        release = RELEASE_BY_SCHEMA_VERSION.get(pkg.root.get("schemaVersion", ""))
        profile = pkg.delivery_type.upper()
        if release is None or profile not in {"GEVER", "FILES"}:
            return
        for requirement in mandatory_metadata_fields():
            if requirement["release"] != release or requirement["profile"] != profile:
                continue
            entity = requirement["entity"]
            if entity == "Paket":
                owners = [pkg.root]
            elif entity == "Ablieferung":
                owners = [pkg.delivery] if pkg.delivery is not None else []
            elif entity == "Provenienz":
                owners = [pkg.provenance] if pkg.provenance is not None else []
            elif entity == "Inhaltsv.":
                owners = [pkg.table_of_contents] if pkg.table_of_contents is not None else []
            else:
                owners = list(pkg.iter_kind(ENTITY_TAGS[entity]))
            field = requirement["xsdName"]
            data_type = requirement["dataType"].lower()
            is_attribute = field in {"id", "schemaVersion"} or "attribute" in data_type
            is_complex = ("complex" in data_type or data_type in _COMPLEX_DATA_TYPES) and field != "dateiRef"
            for owner in owners:
                if is_attribute:
                    value = owner.get(field, "")
                    missing = not value.strip()
                else:
                    children = pkg.children(owner, field)
                    values = [(child.text or "").strip() for child in children]
                    value = next((item for item in values if item), "")
                    missing = not children if is_complex else not children or not any(values)
                if missing:
                    self._add("missing_values", owner, "Das vorgeschriebene Metadatum ist nicht ausgefüllt.",
                              field, value)

        for dossier in pkg.iter_kind("dossier"):
            span = pkg.child(dossier, "entstehungszeitraum")
            approximate = span is not None and any(
                pkg.text(endpoint, "ca").lower() == "true" for endpoint in pkg.children(span))
            if approximate and not pkg.text(dossier, "entstehungszeitraumAnmerkung"):
                self._add("missing_values", dossier,
                          "Für den geschätzten Entstehungszeitraum fehlt die Begründung.",
                          "entstehungszeitraumAnmerkung")

    def _check_identical_files(self):
        pkg = self.pkg
        by_checksum = defaultdict(list)
        for el, _ in self._content_files():
            checksum = pkg.text(el, "pruefsumme").lower()
            if checksum:
                by_checksum[checksum].append(el)
        for checksum, files in by_checksum.items():
            if len(files) > 1:
                for el in files:
                    self._add("identical_files", el, f"Die Datei ist {len(files)}-mal im Paket vorhanden.",
                              "pruefsumme", checksum, WARNING)

    def _check_unassigned_files(self):
        referenced = self.pkg.referenced_ids()
        for el, rel in self._content_files():
            if el.get("id", "") not in referenced:
                self._add("unassigned_files", el, "Die Datei ist weder einem Dossier noch einem Dokument zugewiesen.",
                          "name", rel.as_posix())

    def _check_empty_dossiers(self):
        for el in self.pkg.iter_kind("dossier"):
            if not self._has_files(el):
                self._add("empty_dossiers", el, "Das Dossier enthält keine Dateien.", severity=WARNING)

    def _check_protection_period(self):
        pkg = self.pkg
        delivery = pkg.delivery
        category, period = pkg.text(delivery, "schutzfristenkategorie"), pkg.text(delivery, "schutzfrist")
        scopes = set()
        if category or period:
            scopes.add("Ablieferung")
        if bool(category) != bool(period):
            missing = "schutzfrist" if category else "schutzfristenkategorie"
            self._add("protection_period", delivery, "Schutzfristenkategorie und Schutzfrist müssen beide ausgefüllt sein.", missing)
        pairs = {(category, period)} if category and period else set()
        for position in pkg.iter_kind("ordnungssystemposition"):
            c, p = pkg.text(position, "schutzfristenkategorie"), pkg.text(position, "schutzfrist")
            if c or p:
                scopes.add("Ordnungssystemposition")
            if bool(c) != bool(p):
                missing = "schutzfrist" if c else "schutzfristenkategorie"
                self._add("protection_period", position,
                          "Schutzfristenkategorie und Schutzfrist müssen beide ausgefüllt sein.", missing)
        for el in pkg.iter_kind("dossier"):
            c, p = pkg.text(el, "schutzfristenkategorie"), pkg.text(el, "schutzfrist")
            if c or p:
                scopes.add("Dossier")
            if bool(c) != bool(p):
                missing = "schutzfrist" if c else "schutzfristenkategorie"
                self._add("protection_period", el, "Schutzfristenkategorie und Schutzfrist müssen beide ausgefüllt sein.", missing)
            elif c:
                pairs.add((c, p))
        if len(scopes) > 1:
            self._add("protection_period", delivery,
                      "Schutzfristen müssen im Paket einheitlich auf derselben Stufe erfasst werden.",
                      "schutzfristenkategorie", ", ".join(sorted(scopes)))
        if len(pairs) > 1:
            listing = ", ".join(f"{c}/{p}" for c, p in sorted(pairs))
            self._add("protection_period", delivery, "Die Schutzfristen sind im Paket nicht einheitlich ausgefüllt.",
                      "schutzfrist", listing)

    def _check_time_span_hierarchy(self):
        pkg = self.pkg
        for el in pkg.iter_kind("dossier", "dokument"):
            parent = el.getparent()
            parent_name = localname(parent)
            if parent_name == "ordnungssystemposition":
                parent = pkg.delivery
            elif parent_name != "dossier":
                continue
            start, end = pkg.time_span(el, "entstehungszeitraum")
            p_start, p_end = pkg.time_span(parent, "entstehungszeitraum")
            lower, upper = timespan.parse_lower(start), timespan.parse_upper(end)
            p_lower, p_upper = timespan.parse_lower(p_start), timespan.parse_upper(p_end)
            if (lower and p_lower and lower < p_lower) or (upper and p_upper and upper > p_upper):
                self._add("time_span_hierarchy", el,
                          f"Der Zeitraum liegt ausserhalb des übergeordneten Zeitraums ({timespan.format_span(p_start, p_end)}).",
                          "entstehungszeitraum", timespan.format_span(start, end), WARNING)

    def _check_open_time_span(self):
        pkg = self.pkg
        for el in pkg.iter_kind("dossier"):
            start, end = pkg.time_span(el, "entstehungszeitraum")
            if timespan.is_open(end):
                self._add("open_time_span", el, "Der Zeitraum des Dossiers ist offen («keine Angabe»).",
                          "entstehungszeitraum", timespan.format_span(start, end), WARNING)

    def _check_schema(self):
        delivery_type = self.pkg.delivery_type.upper()
        expected_type = {"GEVER": "ablieferungGeverSIP", "FILES": "ablieferungFilesSIP"}.get(delivery_type)
        if expected_type:
            xsi_type = self.pkg.delivery.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
            prefix, separator, local_type = xsi_type.partition(":")
            type_namespace = self.pkg.delivery.nsmap.get(prefix) if separator else self.pkg.delivery.nsmap.get(None)
            if (type_namespace, local_type if separator else xsi_type) != (self.pkg.ns, expected_type):
                self._add("schema", self.pkg.delivery,
                          f"Der Ablieferungstyp {delivery_type} stimmt nicht mit dem xsi:type "
                          f"{xsi_type!r} überein.", value=xsi_type, severity=ERROR)

        schema_version = self.pkg.root.get("schemaVersion", "")
        trusted_directory = xsd_info.schema_directory(schema_version)
        if trusted_directory is None:
            self._add("schema", None, f"Die schemaVersion {schema_version!r} wird nicht unterstützt.",
                      value=schema_version, severity=ERROR)
            return
        schema_directory = (Path(self.pkg.file_root) / "header" / "xsd" if self.pkg.file_root
                            else self.pkg.metadata_path.parent / "xsd")
        xsd = schema_directory / "arelda.xsd"
        package_valid = False
        package_schema_available = False
        if not xsd.is_file():
            self._add("schema", None, "Die referenzierte XSD-Datei header/xsd/arelda.xsd fehlt.",
                      value="arelda.xsd", severity=WARNING)
        elif self.validate_packaged_schema:
            try:
                xml_schema = self._schema_cache.get(xsd)
                if xml_schema is None:
                    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
                    resolved_schema_directory = schema_directory.resolve()
                    if not xsd.resolve().is_relative_to(resolved_schema_directory):
                        raise OSError("The XSD entry point must be inside the SIP header/xsd directory.")
                    _validate_local_schema_dependencies(xsd, resolved_schema_directory, parser)
                    xml_schema = etree.XMLSchema(etree.parse(str(xsd), parser))
                    self._schema_cache[xsd] = xml_schema
                package_schema_available = True
                package_valid = xml_schema.validate(self.pkg.tree)
                if not package_valid:
                    prefix = ("Das Paket entspricht nicht den Anforderungen für die Ablieferung ans Archiv. Die metadata.xml-Datei "
                              "konnte nicht erfolgreich gegenüber dem mitgelieferten XML-Schema validiert werden")
                    for error in list(xml_schema.error_log)[:200]:
                        self._add("schema", None, f"{prefix} (Zeile {error.line}): {error.message}", value=str(error.line))
            except (etree.XMLSchemaParseError, etree.XMLSyntaxError, OSError) as exc:
                self._add("schema", None, f"Das XML-Schema konnte nicht geladen werden: {exc}",
                          value=xsd.name, severity=WARNING)

        trusted_xsd = trusted_directory / "arelda.xsd"
        try:
            trusted_schema = self._schema_cache.get(trusted_xsd)
            if trusted_schema is None:
                parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
                _validate_local_schema_dependencies(trusted_xsd, trusted_directory, parser)
                trusted_schema = etree.XMLSchema(etree.parse(str(trusted_xsd), parser))
                self._schema_cache[trusted_xsd] = trusted_schema
        except (etree.XMLSchemaParseError, etree.XMLSyntaxError, OSError) as exc:
            self._add("schema", None, f"Das gebündelte XML-Schema konnte nicht geladen werden: {exc}",
                      value=trusted_xsd.name, severity=ERROR)
            return
        if not trusted_schema.validate(self.pkg.tree) and (package_valid or not package_schema_available):
            prefix = (f"Die metadata.xml-Datei ist nicht mit dem vertrauenswürdigen eCH-Schema {schema_version} "
                      "kompatibel; das mitgelieferte Schema ist möglicherweise abweichend")
            for error in list(trusted_schema.error_log)[:200]:
                self._add("schema", None, f"{prefix} (Zeile {error.line}): {error.message}", value=str(error.line))

    def _check_validity_failed(self):
        self.add_validity_summary()

    def add_validity_summary(self) -> None:
        if any(i["severity"] == ERROR for i in self.issues):
            self._add("validity_failed", None,
                      "Das Paket enthält noch Fehler. Beheben Sie alle Fehler und validieren Sie das Paket erneut.")


def summarize(issues: list[dict]) -> list[dict]:
    """Per-check error/warning counts for the editor tab 'Validierung'."""
    counts = {c.id: [0, 0] for c in CHECKS}
    for issue in issues:
        counts[issue["checkId"]][0 if issue["severity"] == ERROR else 1] += 1
    return [
        {"group": c.group, "checkId": c.id, "check": c.name, "errors": counts[c.id][0],
         "warnings": counts[c.id][1], "fullOnly": c.full_only}
        for c in CHECKS
    ]
