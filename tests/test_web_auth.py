"""Authentication gates every workspace surface, including direct downloads."""

import pytest
from fastapi.testclient import TestClient

from ph_web.app import create_app
from ph_web.auth import AuthSettings, CSRF_COOKIE, SESSION_COOKIE, SESSION_SECONDS, hash_password
from ph_web.storage import Settings

PASSWORD = "test-only-long-password"
AUTH = AuthSettings(username="archivist", password_hash=hash_password(PASSWORD))
LOGIN = "/api/v1/auth/login"
SESSION = "/api/v1/auth/session"
PACKAGES = "/api/v1/packages"


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "web", operator="Archivist")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, auth_settings=AUTH), base_url="http://localhost") as client:
        yield client


def login(client):
    result = client.post(LOGIN, json={"username": AUTH.username, "password": PASSWORD})
    assert result.status_code == 200, result.text
    return result


@pytest.mark.parametrize("path", [
    PACKAGES, PACKAGES + "/00000000-0000-0000-0000-000000000000/export",
    PACKAGES + "/00000000-0000-0000-0000-000000000000/nodes/0/content",
    "/docs", "/redoc", "/openapi.json", SESSION, PACKAGES + "/",
])
def test_anonymous_reads_and_downloads_are_protected(client, path):
    result = client.get(path)
    assert result.status_code == 401
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"


def test_public_health_and_static_login(settings, tmp_path):
    (tmp_path / "index.html").write_text("<app-root></app-root>")
    with TestClient(create_app(settings, static_dir=tmp_path, auth_settings=AUTH),
                    base_url="http://localhost") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert client.post(PACKAGES, json={}).status_code == 401


def test_login_session_csrf_logout_and_cookie_flags(client):
    result = login(client)
    assert result.json()["subject"] == "archivist"
    assert result.json()["display_name"] == "Archivist"
    assert result.json()["user_id"]
    cookies = result.headers.get_list("set-cookie")
    assert "HttpOnly" in cookies[0]
    assert "HttpOnly" not in cookies[1]
    assert all("SameSite=strict" in cookie and "Path=/" in cookie for cookie in cookies)
    assert client.get(SESSION).json() == result.json()
    assert client.get(PACKAGES).status_code == 200
    assert client.get("/openapi.json").status_code == 200
    command = {"delivering_office": "Testamt"}
    assert client.post(PACKAGES, json=command).status_code == 403
    assert client.post(PACKAGES, json=command, headers={"X-XSRF-TOKEN": "wrong"}).status_code == 403
    headers = {"X-XSRF-TOKEN": client.cookies[CSRF_COOKIE]}
    assert client.post(PACKAGES, json=command, headers=headers).status_code == 201
    old_session = client.cookies[SESSION_COOKIE]
    assert client.post("/api/v1/auth/logout", json={}).status_code == 403
    assert client.post("/api/v1/auth/logout", json={}, headers=headers).status_code == 204
    assert SESSION_COOKIE not in client.cookies
    assert CSRF_COOKIE not in client.cookies
    assert client.get(SESSION).status_code == 401
    assert client.get(PACKAGES, headers={"Cookie": f"{SESSION_COOKIE}={old_session}"}).status_code == 401


def test_wrong_credentials_origin_and_login_throttle(client):
    assert client.post(LOGIN, json={"username": AUTH.username, "password": PASSWORD},
                       headers={"Origin": "https://other.example"}).status_code == 403
    for index in range(20):
        result = client.post(LOGIN, json={"username": AUTH.username if index % 2 else "unknown",
                                         "password": "wrong"})
        assert result.status_code == 401
        assert result.json()["detail"] == "Benutzername oder Passwort ist falsch."
    assert client.post(LOGIN, json={"username": AUTH.username, "password": PASSWORD}).status_code == 429


def test_login_throttle_resets_after_one_minute(client, monkeypatch):
    import ph_web.auth as auth_module

    now = auth_module.time.monotonic()
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: now)
    for _ in range(20):
        assert client.post(LOGIN, json={"username": AUTH.username, "password": "wrong"}).status_code == 401
    assert client.post(LOGIN, json={"username": AUTH.username, "password": PASSWORD}).status_code == 429
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: now + 60)
    assert login(client).status_code == 200


def test_session_capacity_is_bounded_and_expired_entries_are_reclaimed(settings, monkeypatch):
    import ph_web.auth as auth_module

    now = auth_module.time.monotonic()
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: now)
    auth = auth_module.LocalAuth(AUTH, settings.operator)
    auth._sessions = {
        str(index): auth_module.Session(auth.identity, "csrf", now + SESSION_SECONDS)
        for index in range(1000)
    }
    request = auth_module.Request({
        "type": "http", "method": "POST", "scheme": "http", "path": LOGIN,
        "headers": [(b"host", b"localhost"), (b"content-type", b"application/json")],
        "server": ("localhost", 80),
    })
    command = auth_module.Login(username=AUTH.username, password=PASSWORD)
    with pytest.raises(auth_module.WebError) as failure:
        auth.login(command, request, auth_module.Response())
    assert failure.value.status == 503
    assert len(auth._sessions) == 1000
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: now + SESSION_SECONDS)
    auth.login(command, request, auth_module.Response())
    assert len(auth._sessions) == 1


def test_session_rotation_expiry_and_restart(client, settings, monkeypatch):
    import ph_web.auth as auth_module

    now = auth_module.time.time()
    monkeypatch.setattr(auth_module.time, "time", lambda: now)
    login(client)
    old_cookie = client.cookies[SESSION_COOKIE]
    login(client)
    assert client.cookies[SESSION_COOKIE] != old_cookie
    assert client.get(SESSION, headers={"Cookie": f"{SESSION_COOKIE}={old_cookie}"}).status_code == 401
    with TestClient(create_app(settings, auth_settings=AUTH), base_url="http://localhost") as restarted:
        result = restarted.get(SESSION, headers={"Cookie": f"{SESSION_COOKIE}={client.cookies[SESSION_COOKIE]}"})
        assert result.status_code == 200
        assert result.json()["user_id"] == client.get(SESSION).json()["user_id"]
    monkeypatch.setattr(auth_module.time, "time", lambda: now + SESSION_SECONDS)
    assert client.get(SESSION).status_code == 401


def test_secure_cookie_and_missing_configuration(settings):
    secure = AuthSettings(username=AUTH.username, password_hash=AUTH.password_hash, secure_cookie=True)
    with TestClient(create_app(settings, auth_settings=secure), base_url="https://localhost") as client:
        assert all("Secure" in cookie for cookie in login(client).headers.get_list("set-cookie"))
        assert client.get(SESSION).status_code == 200
    with TestClient(create_app(settings, auth_settings=AuthSettings()), base_url="http://localhost") as client:
        assert client.get(PACKAGES).status_code == 401
        assert client.post(LOGIN, json={"username": AUTH.username, "password": PASSWORD}).status_code == 503


@pytest.mark.parametrize("options", [
    {"username": "user"}, {"password_hash": AUTH.password_hash},
    {"username": "user", "password_hash": "invalid"},
])
def test_invalid_configuration_is_explicit(options):
    with pytest.raises(ValueError):
        AuthSettings(**options)


def test_service_catalog_identity_ownership_and_sharing_survive_reopen(tmp_path):
    from uuid import uuid4

    from ph_web.database import AccessDenied, ServiceDatabase

    database = ServiceDatabase(tmp_path / "service.sqlite3")
    database.initialize()
    owner = database.register_user("https://sso.example/realms/archive", "owner", "Owner")
    stranger = database.register_user(owner.issuer, "stranger", "Owner")
    package_id = uuid4()
    summary = {"id": str(package_id), "name": "private"}
    database.prepare_package(package_id, owner, summary)
    with pytest.raises(AccessDenied) as failure:
        database.authorize(package_id, owner)
    assert failure.value.status == 404
    database.publish_package(package_id)
    assert database.list_packages(owner, 0, 50) == [{**summary, "role": "owner"}]
    assert database.list_packages(stranger, 0, 50) == []
    with pytest.raises(AccessDenied) as failure:
        database.authorize(package_id, stranger)
    assert failure.value.status == 404
    grant = database.set_grant(package_id, owner, stranger.id, "viewer", 1)
    assert grant["version"] == 2
    reopened = ServiceDatabase(database.path)
    reopened.initialize()
    renamed = reopened.register_user(owner.issuer, owner.subject, "Renamed")
    assert renamed.id == owner.id
    assert reopened.authorize(package_id, stranger) == ("viewer", 2)
    with pytest.raises(AccessDenied) as failure:
        reopened.authorize(package_id, stranger, "edit")
    assert failure.value.status == 403
    with pytest.raises(AccessDenied):
        reopened.set_grant(package_id, owner, stranger.id, "editor", 1)
    reopened.set_grant(package_id, owner, stranger.id, "editor", 2)
    assert reopened.authorize(package_id, stranger, "edit") == ("editor", 3)
    with pytest.raises(AccessDenied):
        reopened.authorize(package_id, stranger, "manage")
    reopened.set_grant(package_id, owner, stranger.id, None, 3)
    assert reopened.list_packages(stranger, 0, 50) == []


def test_package_publication_reconciles_without_exposing_unowned_folders(settings, monkeypatch):
    from ph_web.database import AccessDenied
    from ph_web.models import PackageCreate
    from ph_web.storage import PackageStore

    store = PackageStore(settings)
    store.initialize()
    owner = store.database.register_user("local", "owner", "Owner")
    publish = store.database.publish_package

    def interrupted(_package_id):
        raise RuntimeError("Simulated failure after filesystem publication")

    monkeypatch.setattr(store.database, "publish_package", interrupted)
    with pytest.raises(RuntimeError):
        store.create(PackageCreate(delivering_office="Testamt"), owner)
    package_id = store.database.pending_packages()[0]
    assert store.list(owner, 0, 50) == []
    with pytest.raises(AccessDenied):
        with store.open(package_id, owner):
            pytest.fail("A pending package must not be exposed.")
    monkeypatch.setattr(store.database, "publish_package", publish)
    store.initialize()
    assert store.database.pending_packages() == []
    assert store.list(owner, 0, 50)[0].id == package_id
    other = store.database.register_user("local", "other", "Other")
    with pytest.raises(AccessDenied):
        with store.open(package_id, other):
            pytest.fail("An unrelated user must not open a package.")
    store.database.set_grant(package_id, owner, other.id, "viewer", 1)
    with store.open(package_id, other) as current:
        assert current.id == package_id


def test_legacy_workspace_migration_is_explicit_and_preserves_package_bytes(settings, sip_dir):
    import shutil
    from uuid import uuid4

    from ph_web.database import ServiceDatabase
    from tools.migrate_web_workspaces import migrate

    package_id = uuid4()
    workspace = settings.data_dir / str(package_id)
    target = workspace / "sip" / sip_dir.name
    shutil.copytree(sip_dir, target)
    (workspace / "name").write_text(sip_dir.name, encoding="utf-8")
    companion = target.with_name(target.name + ".package-turbo")
    companion.mkdir()
    (companion / "journal.sqlite3").write_bytes(b"Preservation bytes must remain unchanged")
    before = {str(path.relative_to(workspace)): path.read_bytes()
              for path in workspace.rglob("*") if path.is_file()}
    issuer = "https://sso.example/realms/archive"
    dry_run = migrate(settings.data_dir, [package_id], issuer, "verified-subject", "Owner")
    assert dry_run[0]["id"] == str(package_id)
    assert not (settings.data_dir / "service.sqlite3").exists()
    applied = migrate(settings.data_dir, [package_id], issuer, "verified-subject", "Owner", apply=True)
    assert applied == dry_run
    for name, data in before.items():
        assert (workspace / name).read_bytes() == data
    database = ServiceDatabase(settings.data_dir / "service.sqlite3")
    owner = database.register_user(issuer, "verified-subject", "Renamed")
    assert database.list_packages(owner, 0, 50) == [{**summary, "role": "owner"} for summary in applied]
    with pytest.raises(ValueError, match="already has a catalog entry"):
        migrate(settings.data_dir, [package_id], issuer, "other-subject", "Other", apply=True)


def test_database_sessions_and_permissions_work_in_another_process(settings):
    import hashlib
    import json
    import subprocess
    import sys
    import time
    from uuid import uuid4

    from ph_web.database import ServiceDatabase

    database = ServiceDatabase(settings.data_dir / "service.sqlite3")
    database.initialize()
    user = database.register_user("https://sso.example/realms/archive", "owner", "Owner")
    package_id = uuid4()
    database.prepare_package(package_id, user, {"id": str(package_id)})
    database.publish_package(package_id)
    token_hash = hashlib.sha256(b"opaque-test-session").hexdigest()
    now = time.time()
    assert database.create_session(token_hash, user, "csrf", now + 60, "", now)
    code = """
import json, sys, time
from pathlib import Path
from uuid import UUID
from ph_web.database import ServiceDatabase
database = ServiceDatabase(Path(sys.argv[1]))
database.initialize()
principal, csrf, expires = database.session(sys.argv[2], time.time())
role, version = database.authorize(UUID(sys.argv[3]), principal)
print(json.dumps({'user_id': str(principal.id), 'role': role, 'csrf': csrf}))
"""
    result = subprocess.run([sys.executable, "-c", code, str(database.path), token_hash, str(package_id)],
                            capture_output=True, text=True, timeout=15, check=True)
    assert json.loads(result.stdout) == {"user_id": str(user.id), "role": "owner", "csrf": "csrf"}
    with database.connection(write=True) as connection:
        connection.execute("UPDATE users SET enabled=0 WHERE id=?", (str(user.id),))
    assert database.session(token_hash, now) is None


def test_revocation_is_rechecked_after_waiting_for_package_lock(settings, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from ph_web.database import AccessDenied
    from ph_web.models import PackageCreate
    from ph_web.storage import PackageStore

    store = PackageStore(settings)
    store.initialize()
    owner = store.database.register_user("local", "owner", "Owner")
    viewer = store.database.register_user("local", "viewer", "Viewer")
    package = store.create(PackageCreate(delivering_office="Testamt"), owner)
    store.database.set_grant(package.id, owner, viewer.id, "viewer", 1)
    initial_check = Event()
    authorize = store.database.authorize

    def checked(package_id, principal, permission="view"):
        result = authorize(package_id, principal, permission)
        if principal.id == viewer.id:
            initial_check.set()
        return result

    monkeypatch.setattr(store.database, "authorize", checked)

    def read():
        try:
            with store.open(package.id, viewer):
                return 200
        except AccessDenied as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=1) as threads:
        with store.access(package.id, owner, "manage"):
            pending = threads.submit(read)
            assert initial_check.wait(timeout=5)
            store.database.set_grant(package.id, owner, viewer.id, None, 2)
        assert pending.result(timeout=5) == 404
