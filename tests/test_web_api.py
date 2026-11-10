"""Web contract regressions for import, editing, and recoverable preservation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZipInfo

import pytest
from lxml import etree
from fastapi.testclient import TestClient as BaseTestClient

from ph_backend.sip import SipPackage
from ph_web.app import create_app as create_authenticated_app
from ph_web.auth import AuthSettings, CSRF_COOKIE, hash_password
from ph_web.storage import Settings

API = "/api/v1/packages"
TEST_PASSWORD = "test-only-password"
TEST_AUTH = AuthSettings(username="archivist", password_hash=hash_password(TEST_PASSWORD))


def create_app(settings):
    return create_authenticated_app(settings, auth_settings=TEST_AUTH)


class TestClient(BaseTestClient):
    __test__ = False

    def __enter__(self):
        super().__enter__()
        result = self.post("/api/v1/auth/login", json={"username": "archivist", "password": TEST_PASSWORD})
        assert result.status_code == 200, result.text
        self.headers["X-XSRF-TOKEN"] = self.cookies[CSRF_COOKIE]
        return self


@pytest.fixture
def web_settings(tmp_path):
    return Settings(data_dir=tmp_path / "web", operator="Archivist")


@pytest.fixture
def client(web_settings):
    with TestClient(create_app(web_settings), base_url="http://localhost") as connection:
        yield connection


def create(client, **options):
    response = client.post(API, json={"delivering_office": "Testamt", **options})
    assert response.status_code == 201, response.text
    assert response.headers["location"] == f"{API}/{response.json()['id']}"
    assert response.headers["etag"] == f'"{response.json()["revision"]}"'
    return response


def upload(client, content, **kwargs):
    return client.post(API + "/import", content=content, headers={"Content-Type": "application/zip"}, **kwargs)


def test_api_docs_use_local_swagger_assets(client):
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "/swagger-ui/swagger-ui-bundle.js" in docs.text
    assert "/swagger-ui/swagger-ui.css" in docs.text
    assert "cdn.jsdelivr.net" not in docs.text
    assert client.get("/openapi.json").status_code == 200


def node_url(client, package_url, kind):
    result = client.get(package_url + "/nodes/0/children").json()
    node_id = next(item["id"] for item in result["items"] if item["kind"] == kind)
    return package_url + "/nodes/" + node_id


def test_create_edit_audit_and_export_survive_restart(client, web_settings):
    created = create(client)
    base = created.headers["location"]
    delivery = node_url(client, base, "ablieferung")
    update = client.patch(delivery + "/field", headers={"If-Match": created.headers["etag"]},
                          json={"tag": "ablieferndeStelle", "values": ["Staatsarchiv"]})
    assert update.status_code == 200, update.text
    assert update.headers["etag"] != created.headers["etag"]
    assert update.json()["node"]["name"] == "Staatsarchiv"

    with TestClient(create_app(web_settings), base_url="http://localhost") as restarted:
        result = restarted.get(base + "/search", params={"q": "Staatsarchiv"})
        assert result.status_code == 200
        assert result.json()["hits"][0]["node_id"] == update.json()["node"]["id"]
        events = restarted.get(base + "/history").json()["items"]
        assert [item["type"] for item in events] == [
            "metadata commit", "web metadata edit", "metadata commit preparation",
        ]
        assert {item["operator"] for item in events} == {restarted.get("/api/v1/auth/session").json()["user_id"]}
        export = restarted.get(base + "/export")
        assert export.status_code == 200
        assert export.headers["content-type"] == "application/zip"
        with ZipFile(BytesIO(export.content)) as archive:
            name = created.json()["name"]
            assert b"Staatsarchiv" in archive.read(name + "/header/metadata.xml")
            assert name + "/content/" in archive.namelist()
            assert not any("journal.sqlite3" in entry for entry in archive.namelist())
        assert not list(web_settings.data_dir.glob(".export-*"))


def test_simultaneous_edits_to_same_revision_commit_only_once(client):
    created = create(client)
    delivery = node_url(client, created.headers["location"], "ablieferung")
    headers = {"If-Match": created.headers["etag"]}

    def edit(value):
        return client.patch(delivery + "/field", headers=headers,
                            json={"tag": "ablieferndeStelle", "values": [value]})

    with ThreadPoolExecutor(max_workers=2) as threads:
        responses = list(threads.map(edit, ["First", "Second"]))
    assert sorted(response.status_code for response in responses) == [200, 412]
    assert client.get(delivery, headers=headers).status_code == 412
    winner = next(response for response in responses if response.status_code == 200)
    assert client.get(delivery).json()["node"] == winner.json()["node"]


def test_context_note_edit_and_bulk_creation_are_audited(client):
    created = create(client)
    base = created.headers["location"]
    delivery = node_url(client, base, "ablieferung")
    system_id = next(item["id"] for item in client.get(delivery + "/children").json()["items"]
                     if item["kind"] == "ordnungssystem")
    system = base + "/nodes/" + system_id
    previous_count = len([item for item in client.get(system + "/children").json()["items"]
                          if item["kind"] == "ordnungssystemposition"])
    batch = client.post(system + "/children", headers={"If-Match": created.headers["etag"]},
                        json={"kind": "ordnungssystemposition", "count": 3, "title": "Bulk"})
    assert batch.status_code == 200, batch.text
    positions = client.get(system + "/children").json()["items"]
    assert len([item for item in positions if item["kind"] == "ordnungssystemposition"]) == previous_count + 3
    parent = positions[0]["id"]
    dossier = client.post(base + "/nodes/" + parent + "/children", headers={"If-Match": batch.headers["etag"]},
                          json={"kind": "dossier", "title": "Notes"})
    assert dossier.status_code == 200, dossier.text
    dossier_id = next(item["id"] for item in client.get(base + "/nodes/" + parent + "/children").json()["items"]
                      if item["kind"] == "dossier")
    note = client.post(base + "/nodes/" + dossier_id + "/notes", headers={"If-Match": dossier.headers["etag"]},
                       json={"text": "Original"})
    assert note.status_code == 200, note.text
    row = client.get(base + "/notes").json()["items"][0]
    assert row["owner_id"] == dossier_id
    edited = client.post(base + "/actions", headers={"If-Match": note.headers["etag"]},
                         json={"action": "edit-note", "node_id": row["node_id"], "value": "Changed"})
    assert edited.status_code == 200, edited.text
    assert client.get(base + "/notes").json()["items"][0]["text"] == "Changed"
    assert client.get(base + "/history").json()["items"][0]["type"] == "metadata commit"


def test_context_folder_import_and_batch_replacement_are_atomic(client):
    created = create(client, delivery_type="FILES")
    base = created.headers["location"]
    toc = node_url(client, base, "inhaltsverzeichnis")
    folder_id = next(item["id"] for item in client.get(toc + "/children").json()["items"] if item["name"] == "content")
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        output.writestr("records/one.txt", b"one")
        output.writestr("records/two.txt", b"two")
    imported = client.post(base + "/nodes/" + folder_id + "/content-import", content=archive.getvalue(),
                           headers={"Content-Type": "application/zip", "If-Match": created.headers["etag"]})
    assert imported.status_code == 200, imported.text
    export = client.get(base + "/export")
    with ZipFile(BytesIO(export.content)) as output:
        assert output.read(created.json()["name"] + "/content/records/one.txt") == b"one"
    replacement = BytesIO()
    with ZipFile(replacement, "w") as output:
        output.writestr("replacements/one.txt", b"changed")
    replaced = client.post(toc + "/content-import?replace=true", content=replacement.getvalue(),
                           headers={"Content-Type": "application/zip", "If-Match": '"' + imported.json()["revision"] + '"'})
    assert replaced.status_code == 200, replaced.text
    with ZipFile(BytesIO(client.get(base + "/export").content)) as output:
        assert output.read(created.json()["name"] + "/content/records/one.txt") == b"changed"
        assert output.read(created.json()["name"] + "/content/records/two.txt") == b"two"
    unsafe = BytesIO()
    with ZipFile(unsafe, "w") as output:
        output.writestr("../escape.txt", b"bad")
    rejected = client.post(base + "/nodes/" + folder_id + "/content-import", content=unsafe.getvalue(),
                           headers={"Content-Type": "application/zip", "If-Match": '"' + replaced.json()["revision"] + '"'})
    assert rejected.status_code == 422
    assert client.get(base).json()["revision"] == replaced.json()["revision"]
    collision = BytesIO()
    with ZipFile(collision, "w") as output:
        output.writestr("records/ONE.txt", b"must not overwrite")
    refused = client.post(base + "/nodes/" + folder_id + "/content-import", content=collision.getvalue(),
                          headers={"Content-Type": "application/zip", "If-Match": '"' + replaced.json()["revision"] + '"'})
    assert refused.status_code == 409
    assert client.get(base).json()["revision"] == replaced.json()["revision"]


def test_all_package_routes_hide_private_packages_before_side_effects(client, web_settings, monkeypatch):
    created = create(client)
    package_id = created.json()["id"]
    other_auth = AuthSettings(username="other", password_hash=TEST_AUTH.password_hash)
    app = create_authenticated_app(web_settings, auth_settings=other_auth)
    with BaseTestClient(app, base_url="http://localhost") as other:
        assert other.post("/api/v1/auth/login", json={"username": "other", "password": TEST_PASSWORD}).status_code == 200
        other.headers["X-XSRF-TOKEN"] = other.cookies[CSRF_COOKIE]
        own = create(other)
        assert [item["id"] for item in other.get(API).json()] == [own.json()["id"]]

        def forbidden(*_args, **_kwargs):
            pytest.fail("An unauthorized request touched package storage.")

        monkeypatch.setattr(app.state.store, "open", forbidden)
        monkeypatch.setattr(app.state.store, "staging", forbidden)
        with ExitStack() as stack:
            from unittest.mock import patch

            stack.enter_context(patch("ph_web.app.tempfile.mkdtemp", side_effect=forbidden))
            for route in app.routes:
                if "{package_id}" not in getattr(route, "path", ""):
                    continue
                path = route.path.replace("{package_id}", package_id)
                for parameter, value in {"node_id": "0", "group": "additional", "report": "delivery", "sequence": "1"}.items():
                    path = path.replace("{" + parameter + "}", value)
                for method in route.methods - {"HEAD", "OPTIONS"}:
                    result = other.request(method, path, json={}, headers={"If-Match": created.headers["etag"]})
                    assert result.status_code == 404, (method, path, result.text)
        assert other.get(API + "/" + str(uuid4())).status_code == 404


def test_owner_sharing_viewer_editor_and_revocation(client, web_settings):
    created = create(client)
    base = created.headers["location"]
    delivery = node_url(client, base, "ablieferung")
    other_auth = AuthSettings(username="other", password_hash=TEST_AUTH.password_hash)
    app = create_authenticated_app(web_settings, auth_settings=other_auth)
    with BaseTestClient(app, base_url="http://localhost") as other:
        assert other.post("/api/v1/auth/login", json={"username": "other", "password": TEST_PASSWORD}).status_code == 200
        other.headers["X-XSRF-TOKEN"] = other.cookies[CSRF_COOKIE]
        user = app.state.store.database.register_user("urn:package-turbo:local", "other", "Other")
        grant = {"user_id": str(user.id), "role": "viewer", "version": 1}
        assert client.put(base + "/sharing", json=grant).json()["version"] == 2
        assert other.get(base).status_code == 200
        assert other.get(base).json()["role"] == "viewer"
        assert other.get(base + "/metadata.xml").status_code == 200
        assert other.post(base + "/validation").status_code == 200
        assert other.get(base + "/sharing").status_code == 403
        assert other.get(base + "/storage/cleanup").status_code == 403
        edit = {"tag": "ablieferndeStelle", "values": ["Shared editor"]}
        assert other.patch(delivery + "/field", json=edit, headers={"If-Match": created.headers["etag"]}).status_code == 403
        grant.update(role="editor", version=2)
        assert client.put(base + "/sharing", json=grant).json()["version"] == 3
        edited = other.patch(delivery + "/field", json=edit, headers={"If-Match": created.headers["etag"]})
        assert edited.status_code == 200, edited.text
        events = other.get(base + "/history").json()["items"]
        assert {event["operator"] for event in events} == {str(user.id)}
        assert other.put(base + "/sharing", json={**grant, "version": 3}).status_code == 403
        assert client.put(base + "/sharing", json=grant).status_code == 412
        assert client.put(base + "/sharing", json={**grant, "role": None, "version": 3}).json()["version"] == 4
        assert other.get(base).status_code == 404
        assert other.get(API).json() == []
        assert client.get(API).json()[0]["revision"] == edited.headers["etag"].strip('"')


def test_required_readonly_and_noop_edits(client):
    created = create(client)
    base = created.headers["location"]
    delivery = node_url(client, base, "ablieferung")
    command = {"tag": "ablieferndeStelle", "values": ["Testamt"]}
    assert client.patch(delivery + "/field", json=command).status_code == 422
    headers = {"If-Match": created.headers["etag"]}
    unchanged = client.patch(delivery + "/field", headers=headers, json=command)
    assert unchanged.status_code == 200
    assert unchanged.headers["etag"] == created.headers["etag"]
    assert client.get(base + "/history").json()["items"] == []
    for tag, values in [("ablieferndeStelle", []), ("ablieferungstyp", ["FILES"]), ("not-a-field", ["x"])]:
        assert client.patch(delivery + "/field", headers=headers, json={"tag": tag, "values": values}).status_code == 422


@pytest.mark.parametrize(("release", "schema_version"), [("1.0", "4.0"), ("1.1", "4.1"), ("1.2", "5.0"), ("1.3", "5.1")])
def test_supported_releases_can_be_created_and_validated(client, release, schema_version):
    created = create(client, release=release, delivery_type="FILES")
    assert created.json()["schema_version"] == schema_version
    base = created.headers["location"]
    validation = client.post(base + "/validation")
    assert validation.status_code == 200, validation.text
    assert not [issue for issue in validation.json()["issues"] if issue["check_id"] == "schema"]
    assert client.get(base + "/statistics").json()["records"]


def test_zip_import_and_content_download(client, sip_dir, tmp_path):
    original = tmp_path / "original.zip"
    SipPackage.open_folder(sip_dir).zip_to(original)
    imported = upload(client, original.read_bytes())
    assert imported.status_code == 201, imported.text
    assert imported.json()["name"] == sip_dir.name
    base = imported.headers["location"]
    found = client.get(base + "/search", params={"q": "p000002.txt"}).json()
    path = base + "/nodes/" + found["hits"][0]["node_id"] + "/content"
    response = client.get(path, params={"revision": imported.json()["revision"]})
    assert response.content == b"hello world\n"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert client.get(path, params={"revision": "0" * 64}).status_code == 412


def test_import_unknown_schema_in_read_only_mode(client, sip_dir):
    metadata = (sip_dir / "header" / "metadata.xml").read_text()
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        output.writestr("header/metadata.xml", metadata.replace('schemaVersion="4.0"', 'schemaVersion="99"'))
    imported = upload(client, archive.getvalue(), params={"name": sip_dir.name})
    assert imported.status_code == 201, imported.text
    assert imported.json()["read_only"]
    base = imported.headers["location"]
    assert client.patch(base + "/nodes/0/field", headers={"If-Match": imported.headers["etag"]},
                        json={"tag": "paketTyp", "values": ["SIP"]}).status_code == 409


def test_uploaded_xsd_does_not_drive_validation_or_editing(client, sip_dir):
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        output.write(sip_dir / "header" / "metadata.xml", "header/metadata.xml")
        output.writestr("header/xsd/arelda.xsd", b"not XML")
    imported = upload(client, archive.getvalue())
    assert imported.status_code == 201, imported.text
    base = imported.headers["location"]
    response = client.get(node_url(client, base, "ablieferung"))
    assert response.status_code == 200
    assert any(field["editable"] for field in response.json()["fields"])
    validation = client.post(base + "/validation").json()
    assert all("konnte nicht geladen" not in issue["description"] for issue in validation["issues"])


@pytest.mark.parametrize("member", ["../outside.txt", "content/../../outside.txt", "content/AUX.txt", "/outside.txt"])
def test_reject_unsafe_archive_members_and_leave_no_workspace(client, web_settings, member):
    before = set(web_settings.data_dir.iterdir())
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        output.writestr(member, b"data")
    assert upload(client, archive.getvalue()).status_code == 422
    assert set(web_settings.data_dir.iterdir()) == before
    assert client.get(API).json() == []
    assert client.app.state.store.database.pending_packages() == []


def test_reject_archive_symlinks_and_case_collisions(client):
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        linked = ZipInfo("content/link")
        linked.create_system = 3
        linked.external_attr = 0o120777 << 16
        output.writestr(linked, "../other")
    assert upload(client, archive.getvalue()).status_code == 422
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        output.writestr("content/Foo/a.txt", "a")
        output.writestr("content/foo/b.txt", "b")
    assert upload(client, archive.getvalue()).status_code == 422


def test_upload_limits_cover_chunked_requests_and_expanded_members(tmp_path):
    settings = Settings(data_dir=tmp_path / "web", max_upload_bytes=200, max_expanded_bytes=16)
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        before = set(settings.data_dir.iterdir())
        assert upload(client, b"x" * 201).status_code == 413
        assert upload(client, iter([b"x" * 105, b"y" * 105])).status_code == 413
        archive = BytesIO()
        with ZipFile(archive, "w") as output:
            output.writestr("header/metadata.xml", "x" * 17)
        assert upload(client, archive.getvalue()).status_code == 413
        assert client.get(API).json() == []
        assert client.app.state.store.database.pending_packages() == []
    assert set(settings.data_dir.iterdir()) == before


def test_structure_and_notes_use_same_preservation_boundary(client):
    created = create(client)
    base = created.headers["location"]
    delivery = node_url(client, base, "ablieferung")
    classification = next(child for child in client.get(delivery + "/children").json()["items"]
                          if child["kind"] == "ordnungssystem")
    classification_url = base + "/nodes/" + classification["id"]
    position = next(child for child in client.get(classification_url + "/children").json()["items"]
                    if child["kind"] == "ordnungssystemposition")
    position_url = base + "/nodes/" + position["id"]
    child = client.post(position_url + "/children", headers={"If-Match": created.headers["etag"]},
                        json={"kind": "dossier", "title": "Minutes"})
    assert child.status_code == 200, child.text
    dossier = client.get(position_url + "/children").json()["items"][0]
    assert dossier["name"] == "Minutes"
    note = client.post(base + "/nodes/" + dossier["id"] + "/notes", headers={"If-Match": child.headers["etag"]},
                       json={"text": "Reviewed for transfer."})
    assert note.status_code == 200, note.text
    assert "web note creation" in {event["type"] for event in client.get(base + "/history").json()["items"]}


def test_upload_assign_and_export_content_in_one_commit(client, sip_dir):
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        for path in sip_dir.rglob("*"):
            output.write(path, path.relative_to(sip_dir.parent).as_posix())
    imported = upload(client, archive.getvalue())
    assert imported.status_code == 201, imported.text
    base = imported.headers["location"]
    hits = client.get(base + "/search", params={"q": "Leer"}).json()["hits"]
    dossier = base + "/nodes/" + hits[0]["node_id"]
    assert client.get(dossier).json()["can_upload_file"]
    endpoint = dossier + "/attachments"
    headers = {"If-Match": imported.headers["etag"], "Content-Type": "application/octet-stream"}
    attached = client.post(endpoint, params={"name": "agenda.txt"}, content=b"Agenda", headers=headers)
    assert attached.status_code == 200, attached.text
    fresh = {"If-Match": attached.headers["etag"], "Content-Type": "application/octet-stream"}
    duplicate = client.post(endpoint, params={"name": "AGENDA.txt"}, content=b"Duplicate", headers=fresh)
    assert duplicate.status_code == 409, duplicate.text
    children = client.get(dossier + "/children").json()["items"]
    document = base + "/nodes/" + next(child["id"] for child in children if child["name"] == "agenda")
    file_id = client.get(document).json()["references"][0]["id"]
    assert client.get(base + "/nodes/" + file_id + "/content",
                      params={"revision": attached.json()["revision"]}).content == b"Agenda"
    assert "package commit" in {event["type"] for event in client.get(base + "/history").json()["items"]}
    with ZipFile(BytesIO(client.get(base + "/export").content)) as exported:
        assert exported.read(sip_dir.name + "/content/agenda.txt") == b"Agenda"
    validation = client.post(base + "/validation", params={"mode": "full"})
    assert validation.status_code == 200, validation.text
    assert all(issue["check_id"] != "checksum" for issue in validation.json()["issues"])


def test_reject_untrusted_host(client):
    assert client.get("/api/v1/health", headers={"Host": "unrelated.example"}).status_code == 400


def test_web_recovers_interrupted_content_commit(client, monkeypatch):
    from ph_backend.file_workspace import FileWorkspace

    created = create(client)
    base = created.headers["location"]
    toc = node_url(client, base, "inhaltsverzeichnis")
    content = next(child for child in client.get(toc + "/children").json()["items"] if child["name"] == "content")
    content_url = base + "/nodes/" + content["id"]

    def interrupt(*args, **kwargs):
        raise OSError("simulated interruption after the package directory swap")

    with monkeypatch.context() as patch:
        patch.setattr(FileWorkspace, "_finish", interrupt)
        with pytest.raises(OSError, match="simulated interruption"):
            client.post(content_url + "/attachments", params={"name": "recovered.txt"}, content=b"Committed",
                        headers={"If-Match": created.headers["etag"], "Content-Type": "application/octet-stream"})

    reopened = client.get(base)
    assert reopened.status_code == 200, reopened.text
    assert reopened.headers["etag"] != created.headers["etag"]
    children = client.get(content_url + "/children").json()["items"]
    assert [child["name"] for child in children] == ["recovered.txt"]
    history = client.get(base + "/history").json()["items"]
    assert history[0]["type"] == "package commit recovery"
    assert history[0]["outcome"] == "success"


def test_generated_sip_name_handles_spaces_punctuation_and_length(client):
    office = "Stadt Zürich & Partner"
    created = create(client, delivering_office=office, reference="Transfer " + "A" * 150)
    summary = created.json()
    assert "_Stadt-Zuerich-Partner_Transfer-" in summary["name"]
    assert len(summary["name"]) <= 180
    delivery = node_url(client, created.headers["location"], "ablieferung")
    assert client.get(delivery).json()["node"]["name"] == office


def test_dossier_navigator_and_validation_summary(client, sip_dir, tmp_path):
    archive = tmp_path / "source.zip"
    SipPackage.open_folder(sip_dir).zip_to(archive)
    created = upload(client, archive.read_bytes())
    assert created.status_code == 201, created.text
    base = created.headers["location"]
    response = client.get(base + "/dossiers", params={"limit": 1})
    assert response.status_code == 200, response.text
    page = response.json()
    dossier = page["items"][0]
    assert dossier["name"] == "Dossier mit Datei"
    assert dossier["reference"] == "1.1"
    assert dossier["timespan"] == "2007 - 2008"
    assert page["next_offset"] == 1
    assert client.get(base + "/dossiers", params={"offset": 1}).json()["items"][0]["name"] == "Leer"

    records = client.get(base + "/nodes/" + dossier["id"] + "/children").json()["items"]
    document = next(item for item in records if item["kind"] == "dokument")
    assert document["has_children"]
    referenced = client.get(base + "/nodes/" + document["id"] + "/children").json()["items"][0]
    assert referenced["name"] == "p000001.pdf"
    assert referenced["id"] == client.get(base + "/nodes/" + document["id"]).json()["references"][0]["id"]

    validation = client.post(base + "/validation").json()
    assert any(check["id"] == "schema" for check in validation["checks"])
    assert sum(check["errors"] for check in validation["checks"]) == validation["errors"]
    assert sum(check["warnings"] for check in validation["checks"]) == validation["warnings"]

    noted = client.post(base + "/nodes/" + dossier["id"] + "/notes",
                        headers={"If-Match": created.headers["etag"]}, json={"text": "Geprüft"})
    assert noted.status_code == 200, noted.text
    note = client.get(base + "/notes").json()["items"][0]
    assert (note["object_name"], note["text"], note["author"]) == ("Dossier mit Datei", "Geprüft", "Archivist")
    assert client.get(base + "/nodes/" + note["node_id"]).json()["node"]["kind"] == "archivischeNotiz"


def test_preview_and_batch_commit_are_revision_guarded_and_atomic(client):
    created = create(client)
    base = created.headers["location"]
    delivery = node_url(client, base, "ablieferung")
    delivery_id = delivery.rsplit("/", 1)[1]
    provenance_id = next(item["id"] for item in client.get(delivery + "/children").json()["items"]
                         if item["kind"] == "provenienz")
    original = {"If-Match": created.headers["etag"]}
    office = {"node_id": delivery_id, "edit": {"tag": "ablieferndeStelle", "values": ["Archiv A"]}}
    creator = {"node_id": provenance_id, "edit": {"tag": "aktenbildnerName", "values": ["Amt B"]}}
    preview = client.post(base + "/metadata/preview", json=office, headers=original)
    assert preview.json()["field"]["values"] == ["Archiv A"]
    assert preview.json()["changed"]
    assert client.get(delivery).json()["node"]["name"] == "Testamt"
    assert client.get(base + "/history").json()["items"] == []

    invalid = {"node_id": provenance_id, "edit": {"tag": "aktenbildnerName", "values": []}}
    assert client.post(base + "/metadata/commit", json={"edits": [office, invalid]}, headers=original).status_code == 422
    assert client.get(delivery).json()["node"]["name"] == "Testamt"

    saved = client.post(base + "/metadata/commit", json={"edits": [office, creator]}, headers=original)
    assert saved.status_code == 200, saved.text
    assert {node["node"]["name"] for node in saved.json()["nodes"]} == {"Archiv A", "Amt B"}
    assert saved.headers["etag"] != created.headers["etag"]
    events = client.get(base + "/history").json()["items"]
    assert [event["type"] for event in events].count("metadata commit") == 1
    assert [event["type"] for event in events].count("web metadata edit") == 2
    assert client.post(base + "/metadata/commit", json={"edits": [office]}, headers=original).status_code == 412


def test_structure_and_primary_content_actions_reuse_package_transactions(client):
    created = create(client)
    base = created.headers["location"]
    toc = node_url(client, base, "inhaltsverzeichnis")
    content_id = next(item["id"] for item in client.get(toc + "/children").json()["items"]
                      if item["name"] == "content")
    headers = {"If-Match": created.headers["etag"]}
    created_folder = client.post(base + "/actions", headers=headers,
                                 json={"action": "new-folder", "node_id": content_id, "name": "Dossier"})
    assert created_folder.status_code == 200, created_folder.text
    parent = f"{base}/nodes/{content_id}"
    folder = client.get(parent + "/children").json()["items"][0]
    assert folder["name"] == "Dossier"
    revision = created_folder.json()["revision"]
    attached = client.post(f"{base}/nodes/{folder['id']}/attachments", params={"name": "test.txt"},
                           headers={"Content-Type": "application/octet-stream", "If-Match": f'"{revision}"'},
                           content=b"original")
    assert attached.status_code == 200, attached.text
    file_id = client.get(f"{base}/nodes/{folder['id']}/children").json()["items"][0]["id"]
    replaced = client.put(f"{base}/nodes/{file_id}/content",
                          headers={"Content-Type": "application/octet-stream", "If-Match": attached.headers["etag"]},
                          content=b"replacement")
    assert replaced.status_code == 200, replaced.text
    assert client.get(f"{base}/nodes/{file_id}/content", params={"revision": replaced.json()["revision"]}).content == b"replacement"
    removed = client.post(base + "/actions", headers={"If-Match": f'"{replaced.json()["revision"]}"'},
                          json={"action": "delete-entry", "node_id": file_id})
    assert removed.status_code == 200, removed.text
    assert client.get(f"{base}/nodes/{folder['id']}/children").json()["items"] == []
    deletion = next(event for event in client.get(base + "/history").json()["items"]
                    if event["type"] == "file deletion")
    payload = client.get(base + "/history/event/" + str(deletion["sequence"])).json()["payload"]
    assert payload["detail"]["path"] == "content/Dossier/test.txt"
    assert payload["detail"]["before"]["type"] == "file"
    assert payload["detail"]["after"] is None
    namespaces = {"p": "http://www.loc.gov/premis/v3"}
    schema_path = Path(__file__).resolve().parents[1] / "ph_backend/resources/preservation/premis-v3-0.xsd"
    for redacted in (True, False):
        premis = client.get(base + "/history/premis", params={"redacted": str(redacted).lower()})
        assert premis.status_code == 200, premis.text
        root = etree.fromstring(premis.content)
        exported = root.xpath("p:event[p:eventType='file deletion']", namespaces=namespaces)
        assert len(exported) == 1
        assert exported[0].findtext("p:eventIdentifier/p:eventIdentifierValue", namespaces=namespaces) == payload["eventIdentifier"]
        assert exported[0].xpath("p:linkingObjectIdentifier/p:linkingObjectRole/text()", namespaces=namespaces) == ["source"]
        etree.XMLSchema(etree.parse(str(schema_path))).assertValid(root)
    forbidden = client.post(base + "/actions", headers={"If-Match": f'"{removed.json()["revision"]}"'},
                             json={"action": "delete-entry", "node_id": content_id})
    assert forbidden.status_code == 422


def test_advanced_search_sampling_reports_and_preservation_export(client, sip_dir, tmp_path):
    archive = tmp_path / "input.zip"
    SipPackage.open_folder(sip_dir).zip_to(archive)
    created = upload(client, archive.read_bytes())
    assert created.status_code == 201, created.text
    base = created.headers["location"]
    advanced = client.post(base + "/search/advanced", json={"kind": "dossier", "criteria": [
        {"tag": "titel", "value": "Datei"}, {"tag": "aktenzeichen", "value": "1.1"},
    ]})
    assert advanced.status_code == 200, advanced.text
    assert [hit["name"] for hit in advanced.json()["hits"]] == ["Dossier mit Datei"]
    sample = client.get(base + "/sample", params={"mode": "percent", "size": 100})
    assert len(sample.json()["items"]) == 2
    assert b"Dossier mit Datei" in client.get(base + "/reports/dossiers").content
    assert b"<paket" in client.get(base + "/metadata.xml").content
    delivery = node_url(client, base, "ablieferung")
    committed = client.patch(delivery + "/field", headers={"If-Match": created.headers["etag"]},
                             json={"tag": "ablieferndeStelle", "values": ["Arkiv"]})
    assert committed.status_code == 200, committed.text
    premis = client.get(base + "/history/premis")
    assert premis.status_code == 200, premis.text
    assert b"premis" in premis.content.lower()
    bundle = client.get(base + "/history/bundle", params={"version": "1.12.1"})
    assert bundle.status_code == 200, bundle.text
    with ZipFile(BytesIO(bundle.content)) as result:
        assert len(result.namelist()) == 3
        assert any(name.endswith(".mets.xml") for name in result.namelist())
        assert any(name.endswith(".premis.xml") for name in result.namelist())
        assert any(name.endswith(".zip") for name in result.namelist())


def test_open_standalone_metadata_and_read_only_sip(client, sip_dir, tmp_path):
    metadata = (sip_dir / "header" / "metadata.xml").read_bytes()
    imported = client.post(API + "/import-metadata", content=metadata, headers={"Content-Type": "application/xml"})
    assert imported.status_code == 201, imported.text
    base = imported.headers["location"]
    assert imported.json()["metadata_only"] and imported.json()["read_only"]
    assert client.get(base).json()["metadata_only"]
    assert client.get(base + "/metadata.xml").content == metadata
    assert client.get(base + "/export").status_code == 422
    assert client.post(base + "/validation", params={"mode": "full"}).status_code == 200
    archive = tmp_path / "readonly.zip"
    SipPackage.open_folder(sip_dir).zip_to(archive)
    read_only = upload(client, archive.read_bytes(), params={"read_only": "true"})
    assert read_only.status_code == 201, read_only.text
    assert client.get(read_only.headers["location"]).json()["read_only"]
    assert client.get(read_only.headers["location"] + "/export").status_code == 200


def test_validation_fix_uses_server_side_issue_and_property_editor(client, sip_dir, tmp_path):
    file = sip_dir / "content" / "d0001" / "p000002.txt"
    file.write_bytes(b"new data")
    archive = tmp_path / "bad-checksum.zip"
    SipPackage.open_folder(sip_dir).zip_to(archive)
    imported = upload(client, archive.read_bytes())
    base = imported.headers["location"]
    before = client.post(base + "/validation", params={"mode": "full"}).json()
    issue = next(issue for issue in before["issues"] if issue["check_id"] == "checksum")
    assert "recompute_checksum" in issue["fixes"]
    fixed = client.post(base + "/validation/fix", headers={"If-Match": imported.headers["etag"]}, json={
        "action": "recompute_checksum", "check_id": "checksum", "node_id": issue["node_id"], "path": issue["path"],
    })
    assert fixed.status_code == 200, fixed.text
    after = client.post(base + "/validation", params={"mode": "full"}).json()
    assert not any(issue["check_id"] == "checksum" for issue in after["issues"])
    delivery = node_url(client, base, "ablieferung")
    details = client.get(delivery).json()
    assert details["additional_data"] == []
    changed = client.put(delivery + "/properties/additional", headers={"If-Match": f'"{fixed.json()["revision"]}"'},
                         json={"items": [{"name": "Transfer", "value": "123"}]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["additional_data"] == [{"name": "Transfer", "value": "123"}]


def test_create_sip_from_classification_csv(client):
    created = create(client, classification_csv="nummer|type|titel\n1|ordnungssystemposition|Allgemeines\n1.1|dossier|Protokolle\n")
    dossiers = client.get(created.headers["location"] + "/dossiers").json()["items"]
    assert [(item["reference"], item["name"]) for item in dossiers] == [("1.1", "Protokolle")]


def test_storage_cleanup_rejects_modified_preview_and_preserves_current_copy(client):
    created = create(client)
    base = created.headers["location"]
    toc = node_url(client, base, "inhaltsverzeichnis")
    content_id = next(item["id"] for item in client.get(toc + "/children").json()["items"] if item["name"] == "content")
    revision = created.json()["revision"]
    for name in ("First", "Second", "Third"):
        action = client.post(base + "/actions", headers={"If-Match": f'"{revision}"'},
                             json={"action": "new-folder", "node_id": content_id, "name": name})
        assert action.status_code == 200, action.text
        revision = action.json()["revision"]
    headers = {"If-Match": f'"{revision}"'}
    cleanup = base + "/storage/cleanup"
    preview = client.get(cleanup, params={"keep_count": 1, "minimum_age_days": 0}, headers=headers).json()
    assert len(preview["candidates"]) == 2
    assert client.post(cleanup, headers=headers, json={**preview, "reclaimBytes": 0}).status_code == 409
    result = client.post(cleanup, headers=headers, json=preview)
    assert result.status_code == 200, result.text
    assert result.json() == {"removed_bytes": preview["reclaimBytes"]}
    assert len(client.get(f"{base}/nodes/{content_id}/children").json()["items"]) == 3
