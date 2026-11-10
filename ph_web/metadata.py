"""Translate XML entities and fields into the web contract."""

from lxml import etree

from ph_backend import files, notes, schema, structure, timespan, xsd_info
from ph_backend.metadata_requirements import ENTITY_TAGS, is_mandatory_metadata_field
from ph_backend.sip import localname

from .models import FieldEdit, MetadataField, NameValue, Node, NodeDetail, ReferenceRow
from .storage import OpenPackage, WebError

NAVIGABLE = set(ENTITY_TAGS.values()) - {"dateiRef"} | {"mappe"}


class MetadataView:
    def __init__(self, package: OpenPackage):
        self.package = package
        self.sip = package.sip
        self.schema = xsd_info.for_package(self.sip)
        self.schema_version = self.sip.root.get("schemaVersion", "")
        self._child_indices = {}

    def children(self, element):
        return [child for child in self.sip.children(element) if localname(child) in NAVIGABLE]

    def element(self, identifier: str):
        node = self.sip.root
        if identifier == "0":
            return node
        try:
            root, *parts = identifier.split(".")
            if root != "0" or not parts:
                raise ValueError
            for part in parts:
                if not part.isascii() or not part.isdecimal() or str(int(part)) != part:
                    raise ValueError
                node = self.children(node)[int(part)]
        except (ValueError, IndexError) as exc:
            raise WebError(404, "Metadata object does not exist in this revision.") from exc
        return node

    def identifier(self, element) -> str:
        while element is not None and localname(element) not in NAVIGABLE:
            element = element.getparent()
        indices = []
        while element is not None and element is not self.sip.root:
            parent = element.getparent()
            if parent not in self._child_indices:
                self._child_indices[parent] = {child: index for index, child in enumerate(self.children(parent))}
            index = self._child_indices[parent].get(element)
            if index is not None:
                indices.append(str(index))
            element = parent
        return ".".join(["0", *reversed(indices)])

    def node(self, element, identifier: str) -> Node:
        kind = self.sip.kind(element)
        name = self.sip.display_name(element)
        if kind == notes.NOTE_TAG:
            name = self.sip.text(element, "notizBeschreibung")[:80] or "Notiz"
        label = {"archivischeNotiz": "Notiz", "inhaltsverzeichnis": "Paketinhalt"}.get(
            kind, schema.KIND_LABELS.get(kind, kind),
        )
        appearance = self.sip.text(element, "erscheinungsform")
        if kind == "ablieferung":
            forms = {self.sip.text(document, "erscheinungsform") for document in self.sip.iter_kind("dokument")} - {""}
            appearance = next(iter(forms)) if len(forms) == 1 else "gemischt" if forms else ""
        return Node(
            id=identifier, name=label if kind == "inhaltsverzeichnis" else name, kind=kind, label=label,
            has_children=any(localname(child) in NAVIGABLE for child in element) or bool(self.sip.referenced_files(element)),
            reference=self.sip.text(element, "aktenzeichen"),
            timespan=timespan.format_span(*self.sip.time_span_display(element, "entstehungszeitraum"))
            if self.sip.child(element, "entstehungszeitraum") is not None else "",
            appearance=appearance,
        )

    def fields(self, element) -> list[MetadataField]:
        sip = self.sip
        kind = sip.kind(element)
        definition = xsd_info.type_name(sip, element, self.schema)
        declarations = {field.name: field for field in self.schema.elements(definition)}
        field_catalog = {field.tag: field for field in schema.field_defs(kind)}
        existing = {}
        for child in sip.children(element):
            existing.setdefault(localname(child), []).append(child)
        fields = []
        for tag in dict.fromkeys([*field_catalog, *declarations, *existing]):
            if tag in NAVIGABLE or tag == "dateiRef":
                continue
            field = field_catalog.get(tag)
            declaration = declarations.get(tag)
            children = existing.get(tag, [])
            if declaration is None and not children:
                continue
            minimum = declaration.min_occurs if declaration else 0
            if is_mandatory_metadata_field(self.schema_version, sip.delivery_type, kind, tag):
                minimum = max(minimum, 1)
            maximum = declaration.max_occurs if declaration else len(children)
            field_type = declaration.type if declaration else ""
            simple = self.schema.simple(field_type)
            if field_type in {"historischerZeitraum", "zeitraum"}:
                mode, values = "timespan", list(sip.time_span_display(element, tag))
            elif field_type == "historischerZeitpunkt":
                mode, values = "timepoint", [sip.time_point_display(element, tag)]
            elif declaration and self.schema.is_simple(field_type) and not any(len(child) for child in children):
                mode = "text" if maximum == 1 else "repeated"
                values = [(child.text or "") for child in children]
            elif children:
                mode = "display"
                values = [" ".join("".join(child.itertext()).split()) for child in children]
            elif tag == "zusatzDaten" and declaration:
                mode, values = "display", []
            else:
                continue
            editable = (
                not sip.read_only and mode != "display" and tag not in schema.STRUCTURAL_TAGS
                and (field is None or field.type != schema.READONLY)
                and all(child.tag == sip.q(tag) for child in children)
                and (maximum is None or len(children) <= maximum)
            )
            fields.append(MetadataField(
                tag=tag, label="Zusatzdaten" if tag == "zusatzDaten" else field.label if field else schema.label_for(tag), kind=mode, values=values,
                editable=editable, primary=field.primary if field else False, min_items=minimum, max_items=maximum,
                max_length=simple.max_length, choices=simple.enums or (["true", "false"] if simple.boolean else []),
            ))
        return fields

    def detail(self, element, identifier: str) -> NodeDetail:
        sip = self.sip
        path = self.content_path(element)
        definition = xsd_info.type_name(sip, element, self.schema)
        additional = self.schema.element(definition, "zusatzDaten") is not None
        properties = self.schema.element(definition, "eigenschaft") is not None and localname(element) == "datei"
        owners = []
        if localname(element) == "datei" and element.get("id"):
            owners = list(dict.fromkeys(self.identifier(reference.getparent())
                          for reference in sip.root.iter(sip.q("dateiRef"))
                          if element.get("id") in (reference.text or "").split()))
        references = []
        for row in sip.reference_attributes(element):
            if row["attributes"]:
                references.append(ReferenceRow(index=row["index"], name=row["name"], attributes=[
                    {**item, "editable": item["editable"] and not sip.read_only} for item in row["attributes"]
                ]))
        return NodeDetail(
            node=self.node(element, identifier), parent_id=identifier.rpartition(".")[0] or None,
            fields=self.fields(element),
            references=[self.node(file, self.identifier(file)) for file in dict.fromkeys(sip.referenced_files(element))],
            owner_ids=owners,
            note_count=len(notes.notes_of(sip, element)),
            allowed_children=[] if sip.read_only else structure.allowed_children(sip, element),
            can_add_note=not sip.read_only and notes.can_have_notes(sip, element),
            can_upload_file=self.upload_folder(element) is not None,
            file_name=path.name if path else None, revision=self.package.revision,
            additional_data=[NameValue(name=name, value=value) for name, value in sip.additional_data(element)]
            if additional else None,
            file_properties=[NameValue(name=name, value=value) for name, value in sip.file_properties(element)]
            if properties else None,
            reference_attributes=references,

        )

    def upload_folder(self, element):
        sip = self.sip
        if sip.read_only:
            return None
        kind = sip.kind(element)
        if kind == "ordner":
            folder = element
        elif kind in ("dossier", "subdossier", "dokument", "mappe"):
            definition = xsd_info.type_name(sip, element, self.schema)
            if self.schema.element(definition, "dateiRef") is None and "dokument" not in structure.allowed_children(sip, element):
                return None
            folder = next((child for child in sip.children(sip.table_of_contents, "ordner")
                           if sip.text(child, "name") == "content"), None)
        else:
            return None
        if folder is None:
            return None
        relative = sip.relpath(folder)
        if relative is None or relative.parts[:1] != ("content",) or ".." in relative.parts:
            return None
        path = sip.fs_path(folder)
        return folder if path and path.resolve().is_relative_to(sip.file_root / "content") and path.is_dir() else None

    def attach(self, element, source):
        folder = self.upload_folder(element)
        if folder is None:
            raise WebError(422, "Files can only be added to content folders or eligible archival objects.")
        destination = self.sip.fs_path(folder)
        name = source.name.casefold()
        if (any(child.name.casefold() == name for child in destination.iterdir())
                or any(self.sip.text(child, "name").casefold() == name for child in self.sip.children(folder))):
            raise WebError(409, "A file or folder with this name already exists at the upload destination.")
        files.import_paths(self.sip, folder, [source])
        if folder is not element:
            entry = next(child for child in self.sip.children(folder, "datei")
                         if self.sip.text(child, "name") == source.name)
            structure.assign_files(self.sip, element, [entry])

    def content_path(self, element):
        sip = self.sip
        if sip.kind(element) != "datei":
            return None
        relative = sip.relpath(element)
        if relative is None or relative.parts[:1] != ("content",) or ".." in relative.parts:
            return None
        path = sip.fs_path(element)
        if path and path.resolve().is_relative_to(sip.root_dir / "content") and path.is_file():
            return path
        return None

    def edit(self, element, command: FieldEdit) -> None:
        field = next((field for field in self.fields(element) if field.tag == command.tag), None)
        if field is None or not field.editable:
            raise WebError(422, "This field cannot be edited in the browser.")
        values = command.values
        if command.source_indices is not None and field.kind != "repeated":
            raise WebError(422, "Row identity applies only to repeated fields.")
        if field.kind == "timespan":
            if len(values) != 2:
                raise WebError(422, "A time span needs a start and end value.")
            if field.min_items and not any(value.strip() for value in values):
                raise WebError(422, "This field is required.")
            self.sip.set_time_span(element, command.tag, *values)
            return
        if field.kind == "timepoint":
            if len(values) != 1 or (field.min_items and not values[0].strip()):
                raise WebError(422, "Enter a value for this time point.")
            self.sip.set_time_point(element, command.tag, values[0])
            return
        if field.kind == "text":
            if len(values) > 1:
                raise WebError(422, "This field accepts a single value.")
            values = [values[0].strip()] if values and values[0].strip() else []
        if len(values) < field.min_items or (field.max_items is not None and len(values) > field.max_items):
            raise WebError(422, "The number of values violates the package schema.")
        for value in values:
            if field.choices and value not in field.choices:
                raise WebError(422, "Choose a value declared in the package schema.")
            if field.max_length is not None and len(value) > field.max_length:
                raise WebError(422, f"This field accepts at most {field.max_length} characters.")
        if field.kind == "repeated":
            self.sip.set_repeated_texts(element, command.tag, values, command.source_indices)
        else:
            self.sip.set_text(element, command.tag, values[0] if values else "", required=bool(field.min_items))


def xml_bytes(sip) -> bytes:
    return etree.tostring(sip.tree, encoding="UTF-8", xml_declaration=True)
