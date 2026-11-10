"""FastAPI entry point for the local Package Turbo browser workspace."""

import logging
import os
import shutil
import tempfile
from zipfile import ZIP_STORED, ZipFile
from collections import Counter
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ph_backend import files, fixes, notes, reports, sampling, search, statistics, structure
from ph_backend.audit import AuditError
from ph_backend.audit_export import build_audit_bundle
from ph_backend.file_workspace import FileWorkspace, UnchangedOperation, has_file_draft, manifest
from ph_backend.package_factory import PackageCreationError
from ph_backend.processing import digest_bytes
from ph_backend.retention import RetentionManager
from ph_backend.sip import InvalidPackageError
from ph_backend.validation import CHECKS, Validator
from ph_backend.workspace import MetadataWorkspace, companion_directory

from .actions import FILE_ACTIONS, content_entry, file_action, metadata_action
from .auth import AuthSettings, LocalAuth
from .metadata import MetadataView, xml_bytes
from .models import (
    AdvancedSearch, AuditEvent, CheckResult, ChildCreate, CleanupPreview, FieldEdit, History, MetadataBatch, MetadataCommit, MetadataEdit, MetadataPreview, NameValues, NodeDetail, NodePage, NoteCreate,
    NoteRow, NotesPage, PackageAction, PackageCreate,
    PackageSummary, ReferenceEdit, SampleResult, SampleRow, SearchHit, SearchOption, SearchResult, Statistics, ValidationFix, ValidationIssue, ValidationResult,
)
from .storage import WEB_SOFTWARE_VERSION, OpenPackage, PackageStore, Settings, WebError, safe_part

LOG = logging.getLogger(__name__)
API_PREFIX = "/api/v1"

IfMatch = Annotated[str | None, Header(description='Exact quoted ETag of the metadata revision.')]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=200)]


def create_app(settings: Settings | None = None, static_dir: Path | None = None,
               auth_settings: AuthSettings | None = None) -> FastAPI:
    store = PackageStore(settings or Settings.from_env())
    auth = LocalAuth(auth_settings or AuthSettings.from_env(), store.settings.operator)

    @asynccontextmanager
    async def lifespan(_app):
        store.initialize()
        yield

    app = FastAPI(title="Package Turbo API", version="1.0.0", lifespan=lifespan, docs_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(store.settings.allowed_hosts), www_redirect=False)
    api = APIRouter(prefix=API_PREFIX)
    app.include_router(auth.router())
    app.middleware("http")(auth.middleware)

    @app.get("/docs", include_in_schema=False)
    def api_docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=app.openapi_url or "",
            title=f"{app.title} - Swagger UI",
            swagger_js_url="/swagger-ui/swagger-ui-bundle.js",
            swagger_css_url="/swagger-ui/swagger-ui.css",
        )

    @app.exception_handler(WebError)
    async def web_error(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(AuditError)
    async def audit_error(_request, exc):
        LOG.warning("Preservation workspace conflict: %s", exc)
        return JSONResponse({"detail": "The preservation workspace could not be verified or updated."}, status_code=409)

    @app.exception_handler(PackageCreationError)
    @app.exception_handler(InvalidPackageError)
    async def invalid_sip(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith(API_PREFIX) or request.url.path in ("/docs", "/redoc", "/openapi.json"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def workspace(current: OpenPackage) -> MetadataWorkspace:
        return MetadataWorkspace(
            current.sip.metadata_path, current.sip.root_dir, operator=store.settings.operator,
            software_version=WEB_SOFTWARE_VERSION,
        )

    @contextmanager
    def opened(package_id: UUID, if_match: str | None = None):
        with store.open(package_id) as current:
            journal = companion_directory(current.sip.root_dir) / "journal.sqlite3"
            if journal.is_file():
                # Reconcile interrupted commits before exposing their revision.
                metadata = workspace(current)
                if has_file_draft(metadata.journal):
                    FileWorkspace(metadata).discard()
            if if_match is not None and if_match != current.etag:
                raise WebError(412, "This SIP changed. Reload the workspace before continuing.")
            yield current

    def mutation(package_id: UUID, node_id: str, if_match: str, response: Response, event: str,
                 operation: Callable, detail: dict) -> NodeDetail:
        with opened(package_id, if_match) as current:
            if current.sip.read_only:
                raise WebError(409, "This SIP schema version is read-only.")
            view = MetadataView(current)
            element = view.element(node_id)
            before = xml_bytes(current.sip)
            try:
                operation(view, element)
            except (ValueError, structure.StructureError, notes.NoteError) as exc:
                raise WebError(422, str(exc)) from exc
            after = xml_bytes(current.sip)
            if after != before:
                metadata_workspace = workspace(current)
                if metadata_workspace.save(after, [{
                    "event_type": event, "detail": {"objectId": node_id, **detail},
                }]):
                    current = OpenPackage(current.id, current.sip, digest_bytes(after))
            response.headers["ETag"] = current.etag
            return MetadataView(current).detail(element, node_id)

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/packages")
    def list_packages(offset: Offset = 0, limit: Limit = 50) -> list[PackageSummary]:
        return store.list(offset, limit)

    @api.post("/packages", status_code=201)
    def new_package(command: PackageCreate, response: Response) -> PackageSummary:
        try:
            result = store.create(command)
        except (ValueError, structure.StructureError) as exc:
            raise WebError(422, str(exc)) from exc
        response.headers["Location"] = f"{API_PREFIX}/packages/{result.id}"
        response.headers["ETag"] = f'"{result.revision}"'
        return result

    async def receive_upload(request: Request, source: Path) -> None:
        maximum = store.settings.max_upload_bytes
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError as exc:
                raise WebError(400, "Invalid Content-Length.") from exc
            if length < 0:
                raise WebError(400, "Invalid Content-Length.")
            if length > maximum:
                raise WebError(413, "File exceeds the configured upload limit.")
        size = 0
        async with await anyio.open_file(source, "wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    raise WebError(413, "File exceeds the configured upload limit.")
                if chunk:
                    await output.write(chunk)

    @api.post("/packages/import", status_code=201, openapi_extra={"requestBody": {
        "required": True, "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
    }})
    async def import_package(
        request: Request, response: Response, name: Annotated[str, Query(min_length=1, max_length=255)] = "imported-sip",
        read_only: bool = False,
    ) -> PackageSummary:
        """Import one SIP ZIP. The optional name is used for a ZIP without a wrapping folder."""
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/zip":
            raise WebError(415, "Send the ZIP as application/zip.")
        with store.staging() as temporary:
            source = temporary / "upload.zip"
            await receive_upload(request, source)
            result = await anyio.to_thread.run_sync(store.import_zip, source, name, read_only)
        response.headers["Location"] = f"{API_PREFIX}/packages/{result.id}"
        response.headers["ETag"] = f'"{result.revision}"'
        return result

    @api.post("/packages/import-metadata", status_code=201, openapi_extra={"requestBody": {
        "required": True, "content": {"application/xml": {"schema": {"type": "string", "format": "binary"}}},
    }})
    async def import_metadata(request: Request, response: Response,
                              name: Annotated[str, Query(min_length=1, max_length=255)] = "metadata.xml") -> PackageSummary:
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/xml":
            raise WebError(415, "Send metadata.xml as application/xml.")
        with store.staging() as temporary:
            source = temporary / "metadata.xml"
            await receive_upload(request, source)
            result = await anyio.to_thread.run_sync(store.import_metadata, source, name)
        response.headers["Location"] = f"{API_PREFIX}/packages/{result.id}"
        response.headers["ETag"] = f'"{result.revision}"'
        return result

    @api.get("/packages/{package_id}")
    def get_package(package_id: UUID, response: Response) -> PackageSummary:
        with opened(package_id) as current:
            response.headers["ETag"] = current.etag
            return current.summary()

    @api.get("/packages/{package_id}/nodes/{node_id}")
    def get_node(package_id: UUID, node_id: str, response: Response, if_match: IfMatch = None) -> NodeDetail:
        with opened(package_id, if_match) as current:
            view = MetadataView(current)
            response.headers["ETag"] = current.etag
            return view.detail(view.element(node_id), node_id)

    @api.get("/packages/{package_id}/nodes/{node_id}/children")
    def list_children(package_id: UUID, node_id: str, if_match: IfMatch = None,
                      offset: Offset = 0, limit: Limit = 50) -> NodePage:
        with opened(package_id, if_match) as current:
            view = MetadataView(current)
            element = view.element(node_id)
            children = view.children(element)
            entries = [(child, f"{node_id}.{index}") for index, child in enumerate(children)]
            entries.extend((file, view.identifier(file)) for file in dict.fromkeys(current.sip.referenced_files(element)))
            return NodePage(
                items=[view.node(element, identifier) for element, identifier in entries[offset:offset + limit]],
                next_offset=offset + limit if len(entries) > offset + limit else None,
                revision=current.revision,
            )

    @api.get("/packages/{package_id}/dossiers")
    def list_dossiers(package_id: UUID, if_match: IfMatch = None,
                      offset: Offset = 0, limit: Limit = 50) -> NodePage:
        with opened(package_id, if_match) as current:
            view = MetadataView(current)
            dossiers = [element for element in current.sip.iter_kind("dossier") if current.sip.kind(element) == "dossier"]
            return NodePage(
                items=[view.node(element, view.identifier(element)) for element in dossiers[offset:offset + limit]],
                next_offset=offset + limit if len(dossiers) > offset + limit else None,
                revision=current.revision,
            )

    def apply_metadata_edit(view: MetadataView, command: MetadataEdit):
        if view.sip.read_only:
            raise WebError(409, "This SIP schema version is read-only.")
        element = view.element(command.node_id)
        try:
            view.edit(element, command.edit)
        except ValueError as exc:
            raise WebError(422, str(exc)) from exc
        return element

    @api.post("/packages/{package_id}/metadata/preview")
    def preview_metadata(package_id: UUID, command: MetadataEdit,
                         if_match: Annotated[str, Header()]) -> MetadataPreview:
        """Validate one edit against the saved revision without writing it."""
        with opened(package_id, if_match) as current:
            before = xml_bytes(current.sip)
            view = MetadataView(current)
            element = apply_metadata_edit(view, command)
            field = next(field for field in view.fields(element) if field.tag == command.edit.tag)
            return MetadataPreview(field=field, changed=xml_bytes(current.sip) != before)

    @api.post("/packages/{package_id}/metadata/commit")
    def commit_metadata(package_id: UUID, command: MetadataBatch, response: Response,
                        if_match: Annotated[str, Header()]) -> MetadataCommit:
        """Validate and persist all pending field edits in one recoverable commit."""
        with opened(package_id, if_match) as current:
            before = xml_bytes(current.sip)
            view = MetadataView(current)
            touched = {}
            seen = set()
            events = []
            for entry in command.edits:
                key = (entry.node_id, entry.edit.tag)
                if key in seen:
                    raise WebError(422, "A metadata batch may only edit each field once.")
                seen.add(key)
                element = apply_metadata_edit(view, entry)
                touched[entry.node_id] = element
                events.append({"event_type": "web metadata edit", "detail": {
                    "objectId": entry.node_id, "field": entry.edit.tag,
                }})
            after = xml_bytes(current.sip)
            if before != after and workspace(current).save(after, events):
                current = OpenPackage(current.id, current.sip, digest_bytes(after))
            response.headers["ETag"] = current.etag
            updated = MetadataView(current)
            return MetadataCommit(revision=current.revision, nodes=[
                updated.detail(element, node_id) for node_id, element in touched.items()
            ])

    @api.patch("/packages/{package_id}/nodes/{node_id}/field")
    def update_field(package_id: UUID, node_id: str, command: FieldEdit, response: Response,
                     if_match: Annotated[str, Header()]) -> NodeDetail:
        return mutation(package_id, node_id, if_match, response, "web metadata edit",
                        lambda view, element: view.edit(element, command), {"field": command.tag})

    @api.put("/packages/{package_id}/nodes/{node_id}/properties/{group}")
    def update_properties(package_id: UUID, node_id: str, group: Literal["additional", "file"],
                          command: NameValues, response: Response, if_match: Annotated[str, Header()]) -> NodeDetail:
        def edit(view: MetadataView, element):
            detail = view.detail(element, node_id)
            values = [(item.name, item.value) for item in command.items]
            if group == "additional" and detail.additional_data is not None:
                view.sip.set_additional_data(element, values)
            elif group == "file" and detail.file_properties is not None:
                view.sip.set_file_properties(element, values)
            else:
                raise WebError(422, "Eigenschaften werden für dieses Objekt nicht unterstützt.")
        return mutation(package_id, node_id, if_match, response, "web property edit", edit, {"group": group})

    @api.patch("/packages/{package_id}/nodes/{node_id}/reference-attribute")
    def update_reference_attribute(package_id: UUID, node_id: str, command: ReferenceEdit, response: Response,
                                   if_match: Annotated[str, Header()]) -> NodeDetail:
        return mutation(package_id, node_id, if_match, response, "web reference attribute edit",
                        lambda view, element: view.sip.set_reference_attribute(element, command.index, command.name, command.value),
                        {"field": command.name, "referenceIndex": command.index})

    @api.post("/packages/{package_id}/nodes/{node_id}/children")
    def add_child(package_id: UUID, node_id: str, command: ChildCreate, response: Response,
                  if_match: Annotated[str, Header()]) -> NodeDetail:
        return mutation(package_id, node_id, if_match, response, "web structure creation",
                        lambda view, element: structure.add_objects(view.sip, element, command.kind, title=command.title),
                        {"kind": command.kind})

    @api.post("/packages/{package_id}/nodes/{node_id}/notes")
    def add_note(package_id: UUID, node_id: str, command: NoteCreate, response: Response,
                 if_match: Annotated[str, Header()]) -> NodeDetail:
        return mutation(package_id, node_id, if_match, response, "web note creation",
                        lambda view, element: notes.add_note(view.sip, element, command.text, store.settings.operator),
                        {})

    def stage_file_action(current: OpenPackage, action: Callable, event: dict, *, force=False) -> None:
        transaction = FileWorkspace(workspace(current))

        def apply(_staged):
            before = xml_bytes(current.sip)
            action(MetadataView(current))
            if not force and xml_bytes(current.sip) == before:
                raise UnchangedOperation(False)
            return True

        if transaction.stage_operation(current.sip, apply, [event]):
            transaction.commit(current.sip, [event])

    @api.post("/packages/{package_id}/actions")
    def package_action(package_id: UUID, command: PackageAction,
                       if_match: Annotated[str, Header()]) -> PackageSummary:
        with opened(package_id, if_match) as current:
            if current.sip.read_only:
                raise WebError(409, "This SIP schema version is read-only.")
            event = {"event_type": "web package operation", "detail": {
                "action": command.action, "objectId": command.node_id,
            }}
            try:
                if command.action in FILE_ACTIONS:
                    stage_file_action(current, lambda view: file_action(view, command), event)
                else:
                    before = xml_bytes(current.sip)
                    metadata_action(MetadataView(current), command)
                    after = xml_bytes(current.sip)
                    if before != after:
                        workspace(current).save(after, [event])
            except (files.FileOperationError, structure.StructureError, ValueError, FileExistsError) as exc:
                raise WebError(422, str(exc)) from exc
            return OpenPackage(current.id, current.sip, digest_bytes(current.sip.metadata_path.read_bytes())).summary()

    def replace_content_file(package_id: UUID, node_id: str, if_match: str, source: Path) -> PackageSummary:
        with opened(package_id, if_match) as current:
            if current.sip.read_only:
                raise WebError(409, "This SIP schema version is read-only.")

            def replace(view: MetadataView):
                target = content_entry(view, view.element(node_id))
                if view.sip.kind(target) != "datei":
                    raise WebError(422, "Wählen Sie eine Datei zum Ersetzen.")
                files.replace_file(view.sip, target, source)

            try:
                stage_file_action(current, replace, {"event_type": "web content replacement", "detail": {"objectId": node_id}}, force=True)
            except (files.FileOperationError, ValueError) as exc:
                raise WebError(422, str(exc)) from exc
            return OpenPackage(current.id, current.sip, digest_bytes(current.sip.metadata_path.read_bytes())).summary()

    @api.put("/packages/{package_id}/nodes/{node_id}/content", openapi_extra={"requestBody": {
        "required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }})
    async def replace_content(package_id: UUID, node_id: str, request: Request,
                              if_match: Annotated[str, Header()]) -> PackageSummary:
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
            raise WebError(415, "Send content files as application/octet-stream.")
        with store.staging() as temporary:
            source = temporary / "replacement"
            await receive_upload(request, source)
            return await anyio.to_thread.run_sync(replace_content_file, package_id, node_id, if_match, source)

    def commit_attachment(package_id: UUID, node_id: str, if_match: str, source: Path) -> NodeDetail:
        with opened(package_id, if_match) as current:
            if current.sip.read_only:
                raise WebError(409, "This SIP schema version is read-only.")
            view = MetadataView(current)
            if view.upload_folder(view.element(node_id)) is None:
                raise WebError(422, "This object cannot receive content files.")
            transaction = FileWorkspace(workspace(current))
            events = [{"event_type": "web content import", "detail": {"objectId": node_id, "name": source.name}}]

            def attach(_staged):
                view.attach(view.element(node_id), source)

            try:
                transaction.stage_operation(current.sip, attach, events)
                transaction.commit(current.sip, events)
            except (files.FileOperationError, ValueError, structure.StructureError) as exc:
                raise WebError(422, str(exc)) from exc
            updated = OpenPackage(current.id, current.sip, digest_bytes(xml_bytes(current.sip)))
            view = MetadataView(updated)
            # File additions may insert a document or file under the selected object.
            return view.detail(view.element(node_id), node_id)

    @api.post("/packages/{package_id}/nodes/{node_id}/attachments", openapi_extra={"requestBody": {
        "required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }})
    async def upload_attachment(
        package_id: UUID, node_id: str, request: Request, response: Response,
        name: Annotated[str, Query(min_length=1, max_length=180)], if_match: Annotated[str, Header()],
    ) -> NodeDetail:
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
            raise WebError(415, "Send content files as application/octet-stream.")
        if not safe_part(name) or not files.is_valid_name(name) or name != name.strip():
            raise WebError(422, "Use a portable eCH-0160 filename of at most 180 characters.")
        with store.staging() as temporary:
            source = temporary / name
            await receive_upload(request, source)
            result = await anyio.to_thread.run_sync(commit_attachment, package_id, node_id, if_match, source)
        response.headers["ETag"] = f'"{result.revision}"'
        return result

    @api.get("/packages/{package_id}/search")
    def search_package(package_id: UUID, q: Annotated[str, Query(min_length=1, max_length=160)],
                       if_match: IfMatch = None) -> SearchResult:
        with opened(package_id, if_match) as current:
            view = MetadataView(current)
            try:
                rows = search.simple_search(current.sip, q, max_hits=500)
            except search.TooManyHits as exc:
                raise WebError(422, "Search found more than 500 results. Refine the query.") from exc
            return SearchResult(revision=current.revision, hits=[
                SearchHit(node_id=view.identifier(current.sip.element(row["key"])), object=row["object"],
                          name=row["objectName"], field=row["field"], value=row["value"][:1000])
                for row in rows
            ])

    @api.get("/search-fields")
    def advanced_search_fields() -> dict[str, list[SearchOption]]:
        return {kind: [SearchOption.model_validate(option) for option in search.searchable_fields(kind)]
                for kind in (*search.SEARCHABLE_KINDS, "subdossier")}

    @api.post("/packages/{package_id}/search/advanced")
    def advanced_search_package(package_id: UUID, command: AdvancedSearch, if_match: IfMatch = None) -> SearchResult:
        if command.kind not in (*search.SEARCHABLE_KINDS, "subdossier"):
            raise WebError(422, "Unbekannter Objekttyp.")
        labels = {field["tag"]: field["label"] for field in search.searchable_fields(command.kind)}
        if any(criterion.tag not in labels for criterion in command.criteria):
            raise WebError(422, "Unbekanntes Metadatum für diesen Objekttyp.")
        with opened(package_id, if_match) as current:
            view = MetadataView(current)
            criteria = [search.Criterion(command.kind, field.tag, field.value, field.empty) for field in command.criteria]
            try:
                rows = search.advanced_search(current.sip, criteria, max_hits=500)
            except search.TooManyHits as exc:
                raise WebError(422, str(exc)) from exc
            return SearchResult(revision=current.revision, hits=[
                SearchHit(node_id=view.identifier(current.sip.element(row["key"])), object=row["object"],
                          name=row["objectName"], field=" · ".join(labels[field.tag] for field in criteria),
                          value=" · ".join(row[f"value{index}"][:1000] for index in range(len(criteria))))
                for row in rows
            ])

    @api.get("/packages/{package_id}/sample")
    def sample_dossiers(package_id: UUID, mode: Literal["absolute", "percent"] = "absolute",
                        size: Annotated[int, Query(ge=1, le=500)] = 10, if_match: IfMatch = None) -> SampleResult:
        if mode == "percent" and size > 100:
            raise WebError(422, "Prozentwert muss zwischen 1 und 100 liegen.")
        with opened(package_id, if_match) as current:
            view = MetadataView(current)
            rows = sampling.draw_sample(current.sip, mode, size)
            if len(rows) > 500:
                raise WebError(422, "Die Stichprobe ist auf 500 Dossiers begrenzt.")
            return SampleResult(revision=current.revision, items=[
                SampleRow(node_id=view.identifier(current.sip.element(row["key"])),
                          reference=row["reference"], name=row["objectName"]) for row in rows
            ])

    @api.get("/packages/{package_id}/metadata.xml", response_class=Response,
             responses={200: {"content": {"application/xml": {"schema": {"type": "string", "format": "binary"}}}}})
    def export_metadata(package_id: UUID):
        with opened(package_id) as current:
            return Response(current.sip.metadata_path.read_bytes(), media_type="application/xml",
                            headers={"Content-Disposition": 'attachment; filename="metadata.xml"'})

    @api.get("/packages/{package_id}/reports/{report}", response_class=HTMLResponse)
    def package_report(package_id: UUID, report: Literal["classification", "dossiers", "delivery"]):
        with opened(package_id) as current:
            return HTMLResponse(reports.build_html(current.sip, report, store.settings.operator))

    @api.post("/packages/{package_id}/validation")
    def validate(package_id: UUID, mode: Literal["metadata", "full"] = "metadata",
                 if_match: IfMatch = None) -> ValidationResult:
        with opened(package_id, if_match) as current:
            view = MetadataView(current)
            full = mode == "full" and not current.metadata_only
            rows = Validator(current.sip, validate_packaged_schema=False).run(full=full)
            issues = [row for row in rows if row["checkId"] != "validity_failed"]
            counts = Counter(row["severity"] for row in issues)
            by_check = Counter((row["checkId"], row["severity"]) for row in issues)
            return ValidationResult(
                revision=current.revision, errors=counts["error"], warnings=counts["warning"],
                truncated=len(issues) > 500,
                checks=[
                    CheckResult(id=check.id, label=check.name, group=check.group,
                                errors=by_check[check.id, "error"], warnings=by_check[check.id, "warning"])
                    for check in CHECKS
                    if check.id not in ("droid_format", "validity_failed") and (full or not check.full_only)
                ],
                issues=[
                    ValidationIssue(
                        node_id=view.identifier(current.sip.element(row["key"])), severity=row["severity"],
                        check_id=row["checkId"], check=row["check"], object_kind=row["object"], name=row["objectName"],
                        description=row["description"][:3000], field=row["field"], value=row["value"][:1000], path=row["path"],
                        fixes={} if current.sip.read_only else {action: fixes.LABELS[action] for action in fixes.actions_for(row)},
                    )
                    for row in issues[:500]
                ],
            )

    @api.post("/packages/{package_id}/validation/fix")
    def fix_validation(package_id: UUID, command: ValidationFix,
                       if_match: Annotated[str, Header()]) -> PackageSummary:
        with opened(package_id, if_match) as current:
            if current.sip.read_only:
                raise WebError(409, "This SIP schema version is read-only.")
            view = MetadataView(current)
            issues = Validator(current.sip, validate_packaged_schema=False).run(full=True)
            selected = [
                issue for issue in issues
                if issue["checkId"] == command.check_id and issue["path"] == command.path
                and view.identifier(current.sip.element(issue["key"])) == command.node_id
                and command.action in fixes.actions_for(issue)
            ]
            if not selected:
                raise WebError(409, "Der ausgewählte Validierungshinweis ist nicht mehr vorhanden.")
            event = {"event_type": "web validation correction", "detail": {"action": command.action, "checkId": command.check_id}}
            try:
                if command.action in fixes.FILE_SYSTEM_ACTIONS:
                    stage_file_action(current, lambda staged: fixes.apply(staged.sip, command.action, selected), event, force=True)
                else:
                    before = xml_bytes(current.sip)
                    fixes.apply(current.sip, command.action, selected)
                    after = xml_bytes(current.sip)
                    if before != after:
                        workspace(current).save(after, [event])
            except (files.FileOperationError, structure.StructureError, ValueError, FileExistsError) as exc:
                raise WebError(422, str(exc)) from exc
            return OpenPackage(current.id, current.sip, digest_bytes(current.sip.metadata_path.read_bytes())).summary()

    @api.get("/packages/{package_id}/notes")
    def list_notes(package_id: UUID, if_match: IfMatch = None,
                   offset: Offset = 0, limit: Limit = 50) -> NotesPage:
        with opened(package_id, if_match) as current:
            view = MetadataView(current)
            rows = notes.all_notes(current.sip)
            return NotesPage(
                items=[NoteRow(
                    node_id=view.identifier(current.sip.element(row["noteKey"])),
                    object_name=row["objectName"], object_kind=row["object"],
                    text=row["comment"], created=row["created"], author=row["author"],
                ) for row in rows[offset:offset + limit]],
                next_offset=offset + limit if len(rows) > offset + limit else None,
            )

    @api.get("/packages/{package_id}/statistics")
    def get_statistics(package_id: UUID, if_match: IfMatch = None) -> Statistics:
        with opened(package_id, if_match) as current:
            return Statistics.model_validate(statistics.overview(current.sip))

    @api.get("/packages/{package_id}/history")
    def history(package_id: UUID, offset: Offset = 0, limit: Limit = 50) -> History:
        with opened(package_id) as current:
            journal = companion_directory(current.sip.root_dir) / "journal.sqlite3"
            records = workspace(current).journal.records() if journal.is_file() else []
            rows = []
            for record in records[::-1][offset:offset + limit]:
                payload = record["payload"]
                rows.append(AuditEvent(
                    sequence=payload["sequence"], type=payload["eventType"], operator=payload["agentIdentifier"],
                    time=payload["eventDateTime"], outcome=payload["outcome"], digest=record["digest"],
                ))
            return History(items=rows, next_offset=offset + limit if len(records) > offset + limit else None)

    @api.get("/packages/{package_id}/history/event/{sequence}")
    def history_event(package_id: UUID, sequence: int) -> dict:
        with opened(package_id) as current:
            journal = companion_directory(current.sip.root_dir) / "journal.sqlite3"
            records = workspace(current).journal.records() if journal.is_file() else []
            if sequence < 1 or sequence > len(records):
                raise WebError(404, "Historienereignis nicht gefunden.")
            return records[sequence - 1]

    @api.get("/packages/{package_id}/storage/cleanup")
    def preview_cleanup(package_id: UUID, keep_count: Annotated[int, Query(ge=1, le=100)] = 3,
                        minimum_age_days: Annotated[int, Query(ge=0, le=3650)] = 7,
                        if_match: IfMatch = None) -> CleanupPreview:
        with opened(package_id, if_match) as current:
            if current.sip.read_only:
                raise WebError(409, "Schreibgeschützte Pakete können nicht bereinigt werden.")
            return CleanupPreview.model_validate(RetentionManager(workspace(current)).preview(keep_count, minimum_age_days))

    @api.post("/packages/{package_id}/storage/cleanup")
    def apply_cleanup(package_id: UUID, command: CleanupPreview,
                      if_match: Annotated[str, Header()]) -> dict[str, int]:
        with opened(package_id, if_match) as current:
            if current.sip.read_only:
                raise WebError(409, "Schreibgeschützte Pakete können nicht bereinigt werden.")
            manager = RetentionManager(workspace(current))
            preview = manager.preview(command.keepCount, command.minimumAgeDays)
            if preview != command.model_dump():
                raise WebError(409, "Die Vorschau ist nicht mehr aktuell. Prüfen Sie eine neue Vorschau.")
            return {"removed_bytes": manager.apply(preview)}

    @api.get("/packages/{package_id}/history/premis", response_class=Response)
    def export_premis(package_id: UUID, redacted: bool = True):
        with opened(package_id) as current:
            journal_path = companion_directory(current.sip.root_dir) / "journal.sqlite3"
            if not journal_path.is_file():
                raise WebError(404, "Das Paket besitzt noch keine Metadaten-Historie.")
            journal = workspace(current).journal
            checkpoint = journal.verify()
            metadata = current.sip.metadata_path.read_bytes()
            inventory = manifest(current.sip.root_dir)
            data = journal.premis_xml(redact_values=redacted, metadata_xml=metadata, sip_manifest=inventory)
            if inventory != manifest(current.sip.root_dir) or checkpoint != journal.verify():
                raise WebError(409, "Paket oder Historie wurden während des Exports geändert.")
            journal.append("preservation export", agent_identifier=store.settings.operator,
                           software_version=WEB_SOFTWARE_VERSION, detail={
                               "scope": "companion-export", "format": "PREMIS 3.0", "redacted": redacted,
                               "sourceSequence": checkpoint.sequence, "sourceDigest": checkpoint.digest,
                           })
            return Response(data, media_type="application/xml", headers={
                "Content-Disposition": 'attachment; filename="premis.xml"',
            })

    @api.get("/packages/{package_id}/history/bundle", response_class=FileResponse)
    def export_audit_bundle(package_id: UUID, version: Literal["2", "1.12.1"] = "2", redacted: bool = True):
        temporary = Path(tempfile.mkdtemp(prefix=".audit-export-", dir=store.root))
        archive = temporary / "preservation.zip"
        try:
            with opened(package_id) as current:
                journal = companion_directory(current.sip.root_dir) / "journal.sqlite3"
                if not journal.is_file():
                    raise WebError(404, "Das Paket besitzt noch keine Metadaten-Historie.")
                result = build_audit_bundle(current.sip.root_dir, journal, temporary, redacted,
                                            version, store.settings.operator, WEB_SOFTWARE_VERSION)
                if result["auditError"]:
                    LOG.warning("Preservation export could not record its audit event: %s", result["auditError"])
                with ZipFile(archive, "w", ZIP_STORED) as output:
                    for path in sorted(result["directory"].iterdir()):
                        output.write(path, arcname=path.name)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return FileResponse(archive, filename=archive.name, media_type="application/zip",
                            background=BackgroundTask(shutil.rmtree, temporary, ignore_errors=True))

    @api.get("/packages/{package_id}/nodes/{node_id}/content", response_class=FileResponse,
             responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
    def download_content(package_id: UUID, node_id: str, revision: Annotated[str, Query(pattern="^[0-9a-f]{64}$")]):
        with opened(package_id, f'"{revision}"') as current:
            view = MetadataView(current)
            path = view.content_path(view.element(node_id))
            if path is None:
                raise WebError(404, "This object has no downloadable content file.")
            # Hold a private reference so another request can atomically swap the SIP directory.
            temporary = Path(tempfile.mkdtemp(prefix=".download-", dir=store.root))
            snapshot = temporary / "content"
            try:
                try:
                    os.link(path, snapshot)
                except OSError:
                    shutil.copyfile(path, snapshot)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
            return FileResponse(snapshot, filename=path.name, media_type="application/octet-stream",
                                background=BackgroundTask(shutil.rmtree, temporary, ignore_errors=True))

    @api.get("/packages/{package_id}/export", response_class=FileResponse,
             responses={200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}})
    def export_package(package_id: UUID, if_match: IfMatch = None):
        temporary = Path(tempfile.mkdtemp(prefix=".export-", dir=store.root))
        archive = temporary / "package.zip"
        try:
            with opened(package_id, if_match) as current:
                if current.metadata_only:
                    raise WebError(422, "Eine einzelne Metadatendatei kann nicht als SIP exportiert werden.")
                current.sip.zip_to(archive)
                filename = f"{current.sip.name}.zip"
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return FileResponse(archive, filename=filename, media_type="application/zip",
                            background=BackgroundTask(shutil.rmtree, temporary, ignore_errors=True))

    app.include_router(api)
    configured_static = os.getenv("PACKAGE_TURBO_STATIC_DIR")
    static_dir = static_dir or (Path(configured_static) if configured_static else None)
    if static_dir:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="browser")
    return app


app = create_app()
