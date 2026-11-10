"""Allowlisted web actions backed by the shared package domain."""

from ph_backend import files, notes, structure
from ph_backend.sip import localname

from .metadata import MetadataView
from .models import PackageAction
from .storage import WebError, safe_part

FILE_ACTIONS = {"new-folder", "rename-entry", "delete-entry"}


def content_entry(view: MetadataView, element, *, folder_only=False, allow_root=False):
    pkg = view.sip
    relative = pkg.relpath(element)
    kinds = {"ordner"} if folder_only else {"ordner", "datei"}
    if (localname(element) not in kinds or not relative or relative.parts[:1] != ("content",)
            or (not allow_root and len(relative.parts) < 2)
            or not all(safe_part(part) for part in relative.parts)):
        raise WebError(422, "Diese Aktion ist nur für Primärdaten im Ordner content verfügbar.")
    return element


def metadata_action(view: MetadataView, command: PackageAction):
    pkg = view.sip
    element = view.element(command.node_id)
    targets = [view.element(identifier) for identifier in dict.fromkeys(command.target_ids)]
    action = command.action
    if action in {"delete-object", "move-up", "move-down", "move-object"}:
        if localname(element) not in structure.CONTAINERS:
            raise WebError(422, "Dieser Objekttyp kann nicht verschoben oder gelöscht werden.")
    match action:
        case "delete-note":
            if not notes.is_note(element):
                raise WebError(422, "Wählen Sie eine Notiz.")
            notes.delete_note(pkg, element)
        case "delete-object":
            structure.delete(pkg, [element])
        case "move-up" | "move-down":
            structure.move_relative(pkg, element, -1 if action == "move-up" else 1)
        case "move-object":
            if len(targets) != 1:
                raise WebError(422, "Wählen Sie genau ein Zielobjekt.")
            structure.move(pkg, element, targets[0])
        case "assign-files" | "remove-assignment":
            if not targets or any(localname(file) != "datei" for file in targets):
                raise WebError(422, "Wählen Sie die zuzuweisenden Dateien.")
            if action == "assign-files":
                structure.assign_files(pkg, element, targets, command.create_documents)
            else:
                for target in targets:
                    structure.unassign(pkg, element, target)
        case "create-documents":
            if pkg.kind(element) not in {"dossier", "subdossier"}:
                raise WebError(422, "Wählen Sie ein Dossier.")
            structure.documents_for_files(pkg, element)
        case "renumber":
            structure.renumber(pkg, command.scheme)
        case "accumulate-timespans":
            if pkg.kind(element) not in {"dossier", "subdossier"}:
                raise WebError(422, "Wählen Sie ein Dossier.")
            structure.accumulate_time_spans(pkg, element)
        case "mass-edit":
            available = {field["tag"] for field in structure.mass_edit_fields(command.kind, pkg)}
            if command.tag not in available:
                raise WebError(422, "Dieses Metadatum ist für die Massenmutation nicht verfügbar.")
            structure.mass_edit(pkg, element, command.kind, command.tag, command.value, command.end)
        case "mass-assign":
            if not targets:
                raise WebError(422, "Wählen Sie mindestens einen Ordner.")
            folders = [content_entry(view, target, folder_only=True, allow_root=True) for target in targets]
            structure.mass_assign(pkg, folders, element, command.position_levels, command.dossier_levels,
                                  command.create_documents, command.include_top)


def file_action(view: MetadataView, command: PackageAction):
    pkg = view.sip
    element = view.element(command.node_id)
    action = command.action
    name = command.name
    if action in {"new-folder", "rename-entry"} and (
        name is None or not safe_part(name) or not files.is_valid_name(name)
    ):
        raise WebError(422, "Der Name entspricht nicht den SIP-Dateinamenregeln.")
    if action == "new-folder":
        folder = content_entry(view, element, folder_only=True, allow_root=True)
        path = pkg.fs_path(folder)
        if not path or not path.is_dir():
            raise WebError(422, "Der Zielordner fehlt.")
        if any(child.name.casefold() == name.casefold() for child in path.iterdir()):
            raise WebError(409, "Der Name ist bereits belegt.")
        files.create_folder(pkg, folder, name)
    else:
        target = content_entry(view, element)
        if action == "rename-entry":
            files.rename(pkg, target, name)
        else:
            files.delete_entries(pkg, [target])
