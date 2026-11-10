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
    assert result.json() == {"subject": "archivist", "display_name": "Archivist"}
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

    now = auth_module.time.monotonic()
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: now)
    login(client)
    old_cookie = client.cookies[SESSION_COOKIE]
    login(client)
    assert client.cookies[SESSION_COOKIE] != old_cookie
    assert client.get(SESSION, headers={"Cookie": f"{SESSION_COOKIE}={old_cookie}"}).status_code == 401
    with TestClient(create_app(settings, auth_settings=AUTH), base_url="http://localhost") as restarted:
        assert restarted.get(SESSION, headers={"Cookie": f"{SESSION_COOKIE}={client.cookies[SESSION_COOKIE]}"}).status_code == 401
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: now + SESSION_SECONDS)
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
