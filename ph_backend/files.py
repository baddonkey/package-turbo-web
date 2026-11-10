"""File system operations of the Paket-Navigator and validation fix-ups (user documentation 8.2, 10.4, 10.6)."""

import re
import shutil
import unicodedata
import uuid
from pathlib import Path, PurePosixPath

from .sip import SipPackage, file_checksum, localname
from .processing import copy_file

ALLOWED_NAME = re.compile(r"^[A-Za-z0-9!#$%()+,\-.=@\[\]{}~_ ]+$")
_ALLOWED_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%()+,-.=@[]{}~_ ")
DEFAULT_ALGORITHM = "MD5"
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


class FileOperationError(Exception):
    pass


def is_valid_name(name: str) -> bool:
    return bool(name) and bool(ALLOWED_NAME.fullmatch(name)) and name not in (".", "..")


def sanitize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name.translate(_UMLAUTS))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = "".join(character if character in _ALLOWED_NAME_CHARS else "_" for character in text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "_"


def _require_sip(pkg: SipPackage) -> None:
    if pkg.root_dir is None:
        raise FileOperationError("Diese Funktion steht nur für ein vollständiges SIP zur Verfügung.")
    if pkg.read_only:
        raise FileOperationError("Das Paket ist schreibgeschützt geöffnet.")


def _content_folder(pkg: SipPackage):
    return next((o for o in pkg.children(pkg.table_of_contents, "ordner") if pkg.text(o, "name") == "content"), None)


def _folder_path(pkg: SipPackage, folder) -> Path:
    path = pkg.fs_path(folder)
    if path is None:
        raise FileOperationError("Der Ordner liegt ausserhalb des Pakets.")
    return path


def _in_content(pkg: SipPackage, el) -> bool:
    rel = pkg.relpath(el)
    return rel is not None and rel.parts[:1] == ("content",) and len(rel.parts) > 1


def _find(pkg: SipPackage, parent, tag: str, name: str):
    return next((c for c in pkg.children(parent, tag) if pkg.text(c, "name") == name), None)


def _unique_name(pkg: SipPackage, parent, name: str, exclude=None) -> str:
    taken = {pkg.text(c, "name").lower() for c in pkg.children(parent) if c is not exclude
             and localname(c) in ("ordner", "datei")}
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, n = name, 1
    while candidate.lower() in taken:
        candidate = f"{stem}_{n}{suffix}"
        n += 1
    return candidate


def make_folder_entry(pkg: SipPackage, parent, name: str):
    folder = pkg.create_child(parent, "ordner")
    pkg.set_text(folder, "name", name, required=True)
    pkg.set_text(folder, "originalName", name)
    pkg.invalidate_file_index()
    return folder


def make_file_entry(pkg: SipPackage, parent, path: Path, original_name: str | None = None):
    file_el = pkg.create_child(parent, "datei", with_id=True)
    pkg.set_text(file_el, "name", path.name, required=True)
    pkg.set_text(file_el, "originalName", original_name or path.name)
    pkg.set_text(file_el, "pruefalgorithmus", DEFAULT_ALGORITHM, required=True)
    pkg.set_text(file_el, "pruefsumme", file_checksum(path, DEFAULT_ALGORITHM), required=True)
    pkg.invalidate_file_index()
    return file_el


def _update_checksum(pkg: SipPackage, file_el) -> None:
    path = pkg.fs_path(file_el)
    algorithm = pkg.text(file_el, "pruefalgorithmus") or DEFAULT_ALGORITHM
    pkg.set_text(file_el, "pruefalgorithmus", algorithm, required=True)
    pkg.set_text(file_el, "pruefsumme", file_checksum(path, algorithm), required=True)


def remove_references(pkg: SipPackage, file_ids: set[str]) -> int:
    count = 0
    for ref in list(pkg.root.iter(pkg.q("dateiRef"))):
        if (ref.text or "").strip() in file_ids:
            ref.getparent().remove(ref)
            count += 1
    return count


# ---------------------------------------------------------------- Paket-Navigator

def import_paths(pkg: SipPackage, target_folder, sources, merge: bool = True, overwrite: bool = False) -> dict:
    """Copies files/folders from outside into a folder of 'content' and lists them in the table of contents."""
    _require_sip(pkg)
    if not (_in_content(pkg, target_folder) or target_folder is _content_folder(pkg)):
        raise FileOperationError("Primärdaten können nur in den Ordner content importiert werden.")
    root = pkg.root_dir.resolve()
    stats = {"files": 0, "folders": 0, "replaced": 0, "skipped": 0}

    def copy_into(folder_el, source: Path) -> None:
        attributes = getattr(source.lstat(), "st_file_attributes", 0)
        if source.is_symlink() or attributes & 0x400:
            raise FileOperationError("Linked sources and junctions cannot be imported safely.")
        dest_dir = _folder_path(pkg, folder_el)
        if source.is_dir():
            existing = _find(pkg, folder_el, "ordner", source.name)
            if existing is not None and not merge:
                stats["skipped"] += 1
                return
            if existing is None:
                (dest_dir / source.name).mkdir(exist_ok=True)
                existing = make_folder_entry(pkg, folder_el, source.name)
                stats["folders"] += 1
            for child in sorted(source.iterdir()):
                copy_into(existing, child)
        elif source.is_file():
            existing = _find(pkg, folder_el, "datei", source.name)
            if existing is not None and not overwrite:
                stats["skipped"] += 1
                return
            copy_file(source, dest_dir / source.name)
            if existing is None:
                make_file_entry(pkg, folder_el, dest_dir / source.name)
                stats["files"] += 1
            else:
                _update_checksum(pkg, existing)
                stats["replaced"] += 1

    for source in map(Path, sources):
        if (source.resolve().is_relative_to(root) or source.resolve().is_relative_to(pkg.file_root.resolve())
            or source.resolve().is_relative_to(root.with_name(root.name + ".package-turbo"))):
            raise FileOperationError("Dateien aus dem Paket selbst können nicht importiert werden.")
        copy_into(target_folder, source)
    pkg.invalidate_file_index()
    return stats


def create_folder(pkg: SipPackage, parent_folder, name: str):
    _require_sip(pkg)
    if not is_valid_name(name):
        raise FileOperationError(f"Der Name «{name}» enthält unerlaubte Zeichen (erlaubt: A-Z a-z 0-9 _ . -).")
    if not (_in_content(pkg, parent_folder) or parent_folder is _content_folder(pkg)):
        raise FileOperationError("Neue Ordner können nur im Ordner content erstellt werden.")
    if _find(pkg, parent_folder, "ordner", name) is not None or _find(pkg, parent_folder, "datei", name) is not None:
        raise FileOperationError(f"Ein Objekt mit dem Namen «{name}» existiert bereits.")
    (_folder_path(pkg, parent_folder) / name).mkdir()
    return make_folder_entry(pkg, parent_folder, name)


def rename(pkg: SipPackage, el, new_name: str, validate: bool = True) -> None:
    _require_sip(pkg)
    if not _in_content(pkg, el):
        raise FileOperationError("Nur Ordner und Dateien im Ordner content können umbenannt werden.")
    if validate and not is_valid_name(new_name):
        raise FileOperationError(f"Der Name «{new_name}» enthält unerlaubte Zeichen (erlaubt: A-Z a-z 0-9 _ . -).")
    old_name = pkg.text(el, "name")
    if new_name == old_name:
        return
    if _unique_name(pkg, el.getparent(), new_name, exclude=el) != new_name:
        raise FileOperationError(f"Ein Objekt mit dem Namen «{new_name}» existiert bereits.")
    source = pkg.fs_path(el)
    if source is None or not source.exists():
        raise FileOperationError(f"«{old_name}» existiert im Dateisystem nicht.")
    target = source.with_name(new_name)
    # A case-only rename needs an intermediate step on case-insensitive file systems.
    if target.exists() and target.resolve() != source.resolve():
        raise FileOperationError(f"«{new_name}» existiert bereits im Dateisystem.")
    temp = source.with_name(".renaming-" + uuid.uuid4().hex)
    source.rename(temp)
    try:
        temp.rename(target)
    except OSError:
        if not source.exists():
            temp.rename(source)
        raise
    if not pkg.text(el, "originalName"):
        pkg.set_text(el, "originalName", old_name)
    pkg.set_text(el, "name", new_name, required=True)
    pkg.invalidate_file_index()


def delete_entries(pkg: SipPackage, elements) -> int:
    """Deletes folders/files from disk and metadata, including all assignments of the removed files."""
    _require_sip(pkg)
    removed_ids: set[str] = set()
    count = 0
    for el in elements:
        if not _in_content(pkg, el):
            raise FileOperationError("Nur Ordner und Dateien im Ordner content können gelöscht werden.")
    for el in elements:
        path = pkg.fs_path(el)
        removed_ids |= {f.get("id") for f in el.iter(pkg.q("datei")) if f.get("id")}
        if path is not None and path.is_dir():
            shutil.rmtree(path)
        elif path is not None and path.exists():
            path.unlink()
        pkg.remove(el)
        count += 1
    remove_references(pkg, removed_ids)
    return count


def replace_file(pkg: SipPackage, file_el, source: Path) -> None:
    """Datei ersetzen: new content, same name and metadata, new checksum."""
    _require_sip(pkg)
    attributes = getattr(source.lstat(), "st_file_attributes", 0)
    if source.is_symlink() or attributes & 0x400:
        raise FileOperationError("Linked sources cannot replace package files.")
    target = pkg.fs_path(file_el)
    if target is None:
        raise FileOperationError("Die Datei liegt ausserhalb des Pakets.")
    copy_file(source, target, preserve=False)
    _update_checksum(pkg, file_el)


def replacement_candidates(pkg: SipPackage, source_dir: Path) -> list[tuple[object, Path]]:
    available = {p.name: p for p in Path(source_dir).iterdir() if p.is_file()}
    result = []
    for el, rel in pkg.walk_table_of_contents():
        if localname(el) == "datei" and rel.parts[:1] == ("content",) and rel.name in available:
            result.append((el, available[rel.name]))
    return result


def replace_files(pkg: SipPackage, candidates) -> list[str]:
    names = []
    for el, source in candidates:
        replace_file(pkg, el, source)
        names.append(pkg.text(el, "name"))
    return names


# ---------------------------------------------------------------- normalization (10.4.3, 10.4.10)

def _content_entries_bottom_up(pkg: SipPackage):
    entries = [(el, rel) for el, rel in pkg.walk_table_of_contents() if rel.parts[:1] == ("content",) and len(rel.parts) > 1]
    return sorted(entries, key=lambda item: len(item[1].parts), reverse=True)


def normalize_names(pkg: SipPackage) -> int:
    """Datei- und Ordnernamen normalisieren: replaces characters that are not allowed."""
    _require_sip(pkg)
    count = 0
    for el, rel in _content_entries_bottom_up(pkg):
        if is_valid_name(rel.name):
            continue
        rename(pkg, el, _unique_name(pkg, el.getparent(), sanitize_name(rel.name), exclude=el))
        count += 1
    return count


def shorten_paths(pkg: SipPackage) -> int:
    """Pfadlängen bereinigen: folders become O1, O2, ..., files P1.ext, P2.ext, ... (original names are kept)."""
    _require_sip(pkg)
    entries = _content_entries_bottom_up(pkg)
    folder_no, file_no, count = 0, 0, 0
    for el, rel in entries:
        if localname(el) == "ordner":
            folder_no += 1
            new = f"O{folder_no}"
        else:
            file_no += 1
            new = f"P{file_no}{PurePosixPath(rel.name).suffix}"
        if new != rel.name:
            rename(pkg, el, new)
            count += 1
    return count


def denormalize(pkg: SipPackage) -> int:
    """Datei- und Ordnernamen denormalisieren: restores the original names."""
    _require_sip(pkg)
    count = 0
    for el, rel in _content_entries_bottom_up(pkg):
        original = pkg.text(el, "originalName")
        if original and original != rel.name and "/" not in original and "\\" not in original:
            rename(pkg, el, original, validate=False)
            count += 1
    return count


# ---------------------------------------------------------------- validation fix-ups (10.4.7, 10.6)

def _resolve_inside(pkg: SipPackage, rel_posix: str) -> Path:
    path = pkg.file_root.joinpath(*PurePosixPath(rel_posix).parts)
    if not path.resolve().is_relative_to(pkg.file_root.resolve()) or path.resolve() == pkg.file_root.resolve():
        raise FileOperationError("Der Pfad liegt ausserhalb des Pakets.")
    return path


def delete_from_file_system(pkg: SipPackage, rel_posix: str) -> None:
    """Unzulässige / überzählige Datei oder Ordner im Dateisystem löschen."""
    _require_sip(pkg)
    path = _resolve_inside(pkg, rel_posix)
    if path == pkg.file_root / "header" / "metadata.xml" or path == pkg.file_root / "header":
        raise FileOperationError("metadata.xml und der Ordner header können nicht gelöscht werden.")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def add_to_table_of_contents(pkg: SipPackage, rel_posix: str) -> int:
    """Datei/Ordner ins Inhaltsverzeichnis übernehmen (creates missing parent folder entries)."""
    _require_sip(pkg)
    path = _resolve_inside(pkg, rel_posix)
    parts = PurePosixPath(rel_posix).parts
    parent = pkg.table_of_contents
    for name in parts[:-1]:
        existing = _find(pkg, parent, "ordner", name)
        parent = existing if existing is not None else make_folder_entry(pkg, parent, name)
    added = 0

    def add(parent_el, fs_path: Path) -> None:
        nonlocal added
        if fs_path.is_dir():
            folder = _find(pkg, parent_el, "ordner", fs_path.name)
            if folder is None:
                folder = make_folder_entry(pkg, parent_el, fs_path.name)
            for child in sorted(fs_path.iterdir()):
                add(folder, child)
        elif _find(pkg, parent_el, "datei", fs_path.name) is None:
            make_file_entry(pkg, parent_el, fs_path)
            added += 1

    add(parent, path)
    pkg.invalidate_file_index()
    return added


def remove_from_metadata(pkg: SipPackage, el) -> None:
    """Datei / Ordner in den Metadaten löschen (missing on disk)."""
    ids = {f.get("id") for f in el.iter(pkg.q("datei")) if f.get("id")}
    pkg.remove(el)
    remove_references(pkg, ids)


def set_delivery_type(pkg: SipPackage) -> str:
    """Ablieferungstyp setzen according to the xsi:type of the delivery."""
    xsi_type = pkg.delivery.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
    value = "FILES" if "Files" in xsi_type else "GEVER"
    pkg.set_text(pkg.delivery, "ablieferungstyp", value, required=True)
    return value


def restore_header_xsd(pkg: SipPackage) -> int:
    """Adds missing bundled schemas matching the package's declared version."""
    from .xsd_info import schema_directory

    _require_sip(pkg)
    schema_dir = schema_directory(pkg.root.get("schemaVersion", ""))
    if schema_dir is None:
        raise FileOperationError("No matching bundled schema is available; restore the package's original XSDs.")
    xsd_dir = pkg.file_root / "header" / "xsd"
    xsd_dir.mkdir(parents=True, exist_ok=True)
    added = 0
    for source in sorted(schema_dir.glob("*.xsd")):
        if not (xsd_dir / source.name).exists():
            shutil.copy2(source, xsd_dir / source.name)
            added += 1
    add_to_table_of_contents(pkg, "header/xsd")
    return added
