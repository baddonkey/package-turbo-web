"""Public, versioned API contracts for the browser workspace."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
FieldValue = Annotated[str, StringConstraints(max_length=32_768)]
ObjectKind = Literal["ordnungssystemposition", "dossier", "dokument", "mappe", "vorgang", "aktivitaet"]


class PackageCreate(Input):
    delivering_office: ShortText
    name: ShortText | None = None
    reference: ShortText | None = None
    creator: ShortText | None = None
    classification_csv: Annotated[str, StringConstraints(max_length=524288)] | None = None
    system_name: ShortText = "Ordnungssystem"
    delivery_type: Literal["GEVER", "FILES"] = "GEVER"
    release: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"


class PackageSummary(BaseModel):
    id: UUID
    name: str
    schema_version: str
    delivery_type: str
    read_only: bool
    revision: str
    metadata_only: bool = False
    role: Literal["owner", "editor", "viewer"] | None = None


class SharingGrant(BaseModel):
    id: UUID
    display_name: str
    role: Literal["viewer", "editor"]


class PackageSharing(BaseModel):
    version: int
    grants: list[SharingGrant]


class SharingChange(Input):
    user_id: UUID
    role: Literal["viewer", "editor"] | None
    version: Annotated[int, Field(ge=1)]


class Node(BaseModel):
    id: str
    name: str
    kind: str
    label: str
    has_children: bool
    reference: str
    timespan: str
    appearance: str


class NodePage(BaseModel):
    items: list[Node]
    next_offset: int | None
    revision: str


class MetadataField(BaseModel):
    tag: str
    label: str
    kind: Literal["text", "repeated", "timepoint", "timespan", "display"]
    values: list[str]
    editable: bool
    primary: bool
    min_items: int
    max_items: int | None
    max_length: int | None = None
    choices: list[str]


class NameValue(BaseModel):
    name: str
    value: str


class NameValueInput(Input):
    name: ShortText
    value: FieldValue


class NameValues(Input):
    items: Annotated[list[NameValueInput], Field(max_length=200)]


class ReferenceAttribute(BaseModel):
    name: str
    value: str
    editable: bool
    required: bool


class ReferenceRow(BaseModel):
    index: int
    name: str
    attributes: list[ReferenceAttribute]


class ReferenceEdit(Input):
    index: Annotated[int, Field(ge=0)]
    name: ShortText
    value: FieldValue


class NodeDetail(BaseModel):
    node: Node
    parent_id: str | None
    fields: list[MetadataField]
    references: list[Node]
    owner_ids: list[str] = Field(default_factory=list)
    note_count: int = 0
    allowed_children: list[ObjectKind]
    can_add_note: bool
    can_upload_file: bool
    file_name: str | None
    additional_data: list[NameValue] | None = None
    file_properties: list[NameValue] | None = None
    reference_attributes: list[ReferenceRow] = Field(default_factory=list)
    revision: str


class FieldEdit(Input):
    tag: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    values: Annotated[list[FieldValue], Field(max_length=200)]
    source_indices: Annotated[list[int], Field(max_length=200)] | None = None


class MetadataEdit(Input):
    node_id: Annotated[str, StringConstraints(pattern=r"^0(?:\.[0-9]+)*$", max_length=300)]
    edit: FieldEdit


class MetadataBatch(Input):
    edits: Annotated[list[MetadataEdit], Field(min_length=1, max_length=200)]


class MetadataPreview(BaseModel):
    field: MetadataField
    changed: bool


class MetadataCommit(BaseModel):
    revision: str
    nodes: list[NodeDetail]


class ChildCreate(Input):
    kind: ObjectKind
    count: Annotated[int, Field(ge=1, le=100)] = 1
    title: ShortText | None = None


class NoteCreate(Input):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class SearchHit(BaseModel):
    node_id: str
    object: str
    name: str
    field: str
    value: str


class SearchResult(BaseModel):
    revision: str
    hits: list[SearchHit]


class ValidationIssue(BaseModel):
    node_id: str
    severity: Literal["error", "warning"]
    check_id: str
    check: str
    object_kind: str
    name: str
    description: str
    field: str
    value: str
    path: str
    fixes: dict[str, str]


class CheckResult(BaseModel):
    id: str
    label: str
    group: str
    errors: int
    warnings: int


class ValidationResult(BaseModel):
    revision: str
    errors: int
    warnings: int
    issues: list[ValidationIssue]
    truncated: bool
    checks: list[CheckResult]


class NoteRow(BaseModel):
    node_id: str
    owner_id: str
    object_name: str
    object_kind: str
    text: str
    created: str
    author: str


class NotesPage(BaseModel):
    items: list[NoteRow]
    next_offset: int | None


class Statistic(BaseModel):
    label: str
    value: str


class Statistics(BaseModel):
    records: list[Statistic]
    technical: list[Statistic]
    positionsPerLevel: list[Statistic]
    subdossiersPerLevel: list[Statistic]
    documentsByFileCount: list[Statistic]


class AuditEvent(BaseModel):
    sequence: int
    type: str
    operator: str
    time: str
    outcome: str
    digest: str


class History(BaseModel):
    items: list[AuditEvent]
    next_offset: int | None


class SearchCriterion(Input):
    tag: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    value: Annotated[str, StringConstraints(max_length=160)] = ""
    empty: bool = False


class AdvancedSearch(Input):
    kind: str
    criteria: Annotated[list[SearchCriterion], Field(min_length=1, max_length=3)]


class SearchOption(BaseModel):
    tag: str
    label: str


class SampleRow(BaseModel):
    node_id: str
    reference: str
    name: str


class SampleResult(BaseModel):
    revision: str
    items: list[SampleRow]


class PackageAction(Input):
    action: Literal["delete-object", "move-up", "move-down", "move-object", "assign-files",
                    "remove-assignment", "create-documents", "renumber", "accumulate-timespans",
                    "mass-edit", "mass-assign", "delete-note", "edit-note", "document-for-file", "new-folder", "rename-entry", "delete-entry"]
    node_id: str
    target_ids: Annotated[list[str], Field(max_length=100)] = Field(default_factory=list)
    name: ShortText | None = None
    scheme: Literal["decimal", "hundred"] = "decimal"
    kind: str = ""
    tag: str = ""
    value: FieldValue = ""
    end: FieldValue = ""
    position_levels: Annotated[int, Field(ge=0, le=20)] = 1
    dossier_levels: Annotated[int, Field(ge=0, le=20)] = 1
    create_documents: bool = False
    include_top: bool = False


class ValidationFix(Input):
    check_id: str
    node_id: str
    path: str = ""
    action: str




class RollbackCandidate(BaseModel):
    path: str
    bytes: int
    manifestSha256: str
    archived: str


class ProtectedStorage(BaseModel):
    path: str
    reason: str


class CleanupPreview(BaseModel):
    packageIdentifier: str
    headDigest: str
    keepCount: Annotated[int, Field(ge=1, le=100)]
    minimumAgeDays: Annotated[int, Field(ge=0, le=3650)]
    candidates: list[RollbackCandidate]
    protected: list[ProtectedStorage]
    reclaimBytes: int
