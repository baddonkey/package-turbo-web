"""Local credentials and opaque sessions, independent of package operations."""

import getpass
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from .storage import WebError

LOG = logging.getLogger(__name__)
SESSION_COOKIE = "package_turbo_session"
CSRF_COOKIE = "XSRF-TOKEN"
SESSION_SECONDS = 8 * 60 * 60


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=5, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


@dataclass(frozen=True)
class AuthSettings:
    username: str = ""
    password_hash: str = field(default="", repr=False)
    secure_cookie: bool = False

    def __post_init__(self) -> None:
        if bool(self.username) != bool(self.password_hash):
            raise ValueError("Configure both the login username and password hash.")
        if self.username and (not self.username.strip() or len(self.username) > 100):
            raise ValueError("The login username must be non-blank and at most 100 characters.")
        if self.password_hash:
            parts = self.password_hash.split("$")
            if (len(parts) != 3 or parts[0] != "scrypt" or len(parts[1]) != 32
                    or len(parts[2]) != 64
                    or any(c not in "0123456789abcdef" for c in parts[1] + parts[2])):
                raise ValueError("Generate the login password hash with python -m ph_web.auth.")

    @classmethod
    def from_env(cls) -> "AuthSettings":
        secure = os.getenv("PACKAGE_TURBO_COOKIE_SECURE", "false").lower()
        if secure not in ("true", "false"):
            raise ValueError("PACKAGE_TURBO_COOKIE_SECURE must be true or false.")
        return cls(
            username=os.getenv("PACKAGE_TURBO_LOGIN_USERNAME", ""),
            password_hash=os.getenv("PACKAGE_TURBO_LOGIN_PASSWORD_HASH", ""),
            secure_cookie=secure == "true",
        )


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class Identity(BaseModel):
    subject: str
    display_name: str


@dataclass(frozen=True)
class Session:
    identity: Identity
    csrf: str
    expires: float


class LocalAuth:
    def __init__(self, settings: AuthSettings, operator: str):
        self.settings = settings
        self.identity = Identity(subject=settings.username, display_name=operator)
        self._sessions: dict[str, Session] = {}
        self._attempts: deque[float] = deque()
        self._lock = Lock()

    def session(self, request: Request) -> Session | None:
        token = request.cookies.get(SESSION_COOKIE, "")
        key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            session = self._sessions.get(key)
            if session and session.expires <= time.monotonic():
                del self._sessions[key]
                return None
            return session

    def require(self, request: Request) -> Session:
        session = self.session(request)
        if session is None:
            raise WebError(401, "Bitte melden Sie sich an.")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            csrf = request.headers.get("X-XSRF-TOKEN", "")
            if not csrf or not hmac.compare_digest(csrf.encode(), session.csrf.encode()):
                raise WebError(403, "Ungültiger Sicherheitstoken. Laden Sie die Seite neu.")
        request.state.identity = session.identity
        return session

    def login(self, command: Login, request: Request, response: Response) -> Identity:
        if not self.settings.username:
            raise WebError(503, "Die lokale Anmeldung ist noch nicht konfiguriert.")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise WebError(403, "Eine Anmeldung von einer anderen Website ist nicht erlaubt.")
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise WebError(415, "Die Anmeldung benötigt application/json.")
        now = time.monotonic()
        with self._lock:
            while self._attempts and self._attempts[0] <= now - 60:
                self._attempts.popleft()
            if len(self._attempts) >= 20:
                raise WebError(429, "Zu viele Anmeldeversuche. Warten Sie eine Minute.")
            self._attempts.append(now)
        _, salt, expected = self.settings.password_hash.split("$")
        actual = hashlib.scrypt(command.password.encode(), salt=bytes.fromhex(salt),
                                n=16384, r=8, p=5, dklen=32)
        valid_password = hmac.compare_digest(actual, bytes.fromhex(expected))
        valid_username = hmac.compare_digest(command.username.encode(), self.settings.username.encode())
        if not (valid_password and valid_username):
            LOG.warning("Local login rejected.")
            raise WebError(401, "Benutzername oder Passwort ist falsch.")
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions = {key: value for key, value in self._sessions.items() if value.expires > now}
            if len(self._sessions) >= 1000:
                raise WebError(503, "Zu viele aktive Sitzungen. Versuchen Sie es später erneut.")
            previous = request.cookies.get(SESSION_COOKIE, "")
            self._sessions.pop(hashlib.sha256(previous.encode()).hexdigest(), None)
            self._sessions[hashlib.sha256(token.encode()).hexdigest()] = Session(
                self.identity, csrf, now + SESSION_SECONDS,
            )
        for name, value, http_only in ((SESSION_COOKIE, token, True), (CSRF_COOKIE, csrf, False)):
            response.set_cookie(name, value, max_age=SESSION_SECONDS, httponly=http_only,
                                secure=self.settings.secure_cookie, samesite="strict", path="/")
        return self.identity

    def logout(self, request: Request, response: Response) -> None:
        self.require(request)
        token = request.cookies.get(SESSION_COOKIE, "")
        with self._lock:
            self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        for name in (SESSION_COOKIE, CSRF_COOKIE):
            response.delete_cookie(name, path="/", secure=self.settings.secure_cookie, samesite="strict")

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])

        @router.post("/login")
        def login(command: Login, request: Request, response: Response) -> Identity:
            return self.login(command, request, response)

        @router.get("/session")
        def session(request: Request) -> Identity:
            return self.require(request).identity

        @router.post("/logout", status_code=204)
        def logout(request: Request, response: Response) -> None:
            self.logout(request, response)

        return router

    async def middleware(self, request: Request, call_next):
        path = request.url.path
        protected = (path == "/api" or path.startswith("/api/")
                     or path in ("/docs", "/redoc", "/openapi.json"))
        if protected and path not in ("/api/v1/auth/login", "/api/v1/health"):
            try:
                self.require(request)
            except WebError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=exc.status,
                                    headers={"Cache-Control": "no-store"})
        return await call_next(request)


if __name__ == "__main__":
    password = getpass.getpass("New login password (at least 12 characters): ")
    if not 12 <= len(password) <= 1024:
        raise SystemExit("Use a password of 12 to 1024 characters.")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match.")
    print(hash_password(password))
