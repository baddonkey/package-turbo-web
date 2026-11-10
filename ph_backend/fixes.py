"""Fix-up commands offered in the context menu of the Validierungshilfe (user documentation chapter 10)."""

from . import files, structure
from .sip import SipPackage, file_checksum, localname

LABELS = {
    "recompute_checksum": "Prüfsumme neu berechnen",
    "delete_fs": "Im Dateisystem löschen",
    "add_toc": "Ins Inhaltsverzeichnis übernehmen",
    "remove_metadata": "In den Metadaten löschen",
    "restore_header": "Ins SIP übernehmen",
    "set_delivery_type": "Ablieferungstyp setzen",
    "propagate_up": "Nach oben übernehmen",
    "normalize_names": "Datei- und Ordnernamen normalisieren",
    "shorten_paths": "Pfadlängen bereinigen",
}
# Actions that change the file system; the metadata is saved right away to stay consistent with it.
FILE_SYSTEM_ACTIONS = {"delete_fs", "add_toc", "restore_header", "normalize_names", "shorten_paths",
                       "remove_metadata"}
CONFIRM_ACTIONS = {"delete_fs", "remove_metadata", "normalize_names", "shorten_paths"}


def actions_for(issue: dict) -> list[str]:
    check = issue.get("checkId")
    if check == "checksum":
        return ["recompute_checksum"]
    if check == "illegal_entries":
        return ["delete_fs"]
    if check == "header_content":
        return ["restore_header"] if issue.get("path") == "header/xsd" else ["delete_fs"]
    if check in ("surplus_file", "surplus_folder"):
        return ["add_toc", "delete_fs"]
    if check in ("missing_file", "missing_folder", "wrong_file", "wrong_folder"):
        return ["remove_metadata"]
    if check == "missing_values" and issue.get("fieldTag") == "ablieferungstyp":
        return ["set_delivery_type"]
    if check == "time_span_hierarchy":
        return ["propagate_up"]
    if check == "charset":
        return ["normalize_names"]
    if check == "path_length":
        return ["shorten_paths"]
    return []


def apply(pkg: SipPackage, action: str, issues: list[dict]) -> int:
    """Runs a fix-up for the selected issues; returns the number of affected objects."""
    count = 0
    if action == "normalize_names":
        return files.normalize_names(pkg)
    if action == "shorten_paths":
        return files.shorten_paths(pkg)
    if action == "restore_header":
        return files.restore_header_xsd(pkg)
    if action == "set_delivery_type":
        files.set_delivery_type(pkg)
        return 1
    for issue in issues:
        if action not in actions_for(issue):
            continue
        el = pkg.element(issue.get("key", 0)) if issue.get("key") else None
        if action == "recompute_checksum" and el is not None and localname(el) == "datei":
            path = pkg.fs_path(el)
            if path is not None and path.is_file():
                algorithm = pkg.text(el, "pruefalgorithmus") or files.DEFAULT_ALGORITHM
                old = pkg.text(el, "pruefsumme")
                new = file_checksum(path, algorithm)
                pkg.set_text(el, "pruefsumme", new.upper() if old.isupper() else new, required=True)
                count += 1
        elif action == "delete_fs" and issue.get("path"):
            files.delete_from_file_system(pkg, issue["path"])
            count += 1
        elif action == "add_toc" and issue.get("path"):
            files.add_to_table_of_contents(pkg, issue["path"])
            count += 1
        elif action == "remove_metadata" and el is not None and el.getparent() is not None:
            files.remove_from_metadata(pkg, el)
            count += 1
        elif action == "propagate_up" and el is not None:
            structure.propagate_time_span_up(pkg, el)
            count += 1
    return count
