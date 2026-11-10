"""Service identities and access control, separate from preservation journals."""

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

Role = Literal["owner", "editor", "viewer"]
Permission = Literal["view", "edit", "manage"]


@dataclass(frozen=True)
class Principal:
    id: UUID
    issuer: str
    subject: str
    display_name: str


class AccessDenied(Exception):
    def __init__(self, status: int):
        self.status = status
        messages = {404: "Package not found.", 403: "Insufficient package permissions.",
                    412: "Sharing changed. Reload its permissions before continuing."}
        super().__init__(messages[status])


class ServiceDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connection(self, *, write: bool = False):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(self.path, timeout=5) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        with self.connection(write=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("Service database was created by a newer application version.")
            if version == 2:
                return
            statements = (
                """CREATE TABLE users (
                    id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL,
                    display_name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    UNIQUE(issuer, subject))""",
                """CREATE TABLE packages (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
                    state TEXT NOT NULL CHECK(state IN ('pending','ready')),
                    summary TEXT NOT NULL, acl_version INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL)""",
                """CREATE TABLE grants (
                    package_id TEXT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    role TEXT NOT NULL CHECK(role IN ('editor','viewer')),
                    PRIMARY KEY(package_id, user_id))""",
                """CREATE TABLE security_events (
                    id INTEGER PRIMARY KEY, created_at REAL NOT NULL,
                    actor_id TEXT NOT NULL REFERENCES users(id), event TEXT NOT NULL,
                    package_id TEXT, detail TEXT NOT NULL)""",
                "CREATE INDEX packages_owner ON packages(owner_id, state)",
                "CREATE INDEX grants_user ON grants(user_id, package_id)",
            )
            if version == 0:
                for statement in statements:
                    connection.execute(statement)
            connection.execute("""CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf TEXT NOT NULL, expires_at REAL NOT NULL)""")
            connection.execute("CREATE INDEX sessions_expiry ON sessions(expires_at)")
            connection.execute("PRAGMA user_version=2")

    @staticmethod
    def _principal(row: sqlite3.Row) -> Principal:
        return Principal(UUID(row["id"]), row["issuer"], row["subject"], row["display_name"])

    def register_user(self, issuer: str, subject: str, display_name: str) -> Principal:
        if not issuer.strip() or not subject.strip() or not display_name.strip():
            raise ValueError("Identity issuer, subject and display name are required.")
        with self.connection(write=True) as connection:
            connection.execute("""INSERT INTO users(id, issuer, subject, display_name) VALUES(?,?,?,?)
                ON CONFLICT(issuer, subject) DO UPDATE SET display_name=excluded.display_name""",
                (str(uuid4()), issuer, subject, display_name))
            row = connection.execute("SELECT * FROM users WHERE issuer=? AND subject=?",
                                     (issuer, subject)).fetchone()
            if not row["enabled"]:
                raise AccessDenied(403)
            return self._principal(row)

    def user(self, user_id: UUID) -> Principal:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=? AND enabled=1", (str(user_id),)).fetchone()
            if row is None:
                raise AccessDenied(404)
            return self._principal(row)

    def create_session(self, token_hash: str, principal: Principal, csrf: str,
                       expires_at: float, previous_hash: str, now: float) -> bool:
        with self.connection(write=True) as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at<=? OR token_hash=?", (now, previous_hash))
            if connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= 1000:
                return False
            connection.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                               (token_hash, str(principal.id), csrf, expires_at))
            return True

    def session(self, token_hash: str, now: float) -> tuple[Principal, str, float] | None:
        with self.connection() as connection:
            row = connection.execute("""SELECT u.*, s.csrf, s.expires_at FROM sessions s
                JOIN users u ON u.id=s.user_id AND u.enabled=1
                WHERE s.token_hash=? AND s.expires_at>?""", (token_hash, now)).fetchone()
            return (self._principal(row), row["csrf"], row["expires_at"]) if row else None

    def revoke_session(self, token_hash: str) -> None:
        with self.connection(write=True) as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def revoke_all_sessions(self) -> None:
        with self.connection(write=True) as connection:
            connection.execute("DELETE FROM sessions")

    def prepare_package(self, package_id: UUID, owner: Principal, summary: dict) -> None:
        with self.connection(write=True) as connection:
            connection.execute("""INSERT INTO packages(id, owner_id, state, summary, created_at)
                VALUES(?,?,'pending',?,?)""",
                (str(package_id), str(owner.id), json.dumps(summary), time.time()))

    def publish_package(self, package_id: UUID) -> None:
        with self.connection(write=True) as connection:
            if connection.execute("UPDATE packages SET state='ready' WHERE id=?", (str(package_id),)).rowcount != 1:
                raise AccessDenied(404)

    def pending_packages(self) -> list[UUID]:
        with self.connection() as connection:
            return [UUID(row[0]) for row in connection.execute("SELECT id FROM packages WHERE state='pending'")]

    def abandon_package(self, package_id: UUID) -> None:
        with self.connection(write=True) as connection:
            connection.execute("DELETE FROM packages WHERE id=? AND state='pending'", (str(package_id),))

    @staticmethod
    def _authorize(connection: sqlite3.Connection, package_id: UUID, principal: Principal,
                   permission: Permission) -> tuple[Role, int]:
        if permission not in ("view", "edit", "manage"):
            raise ValueError("Unknown package permission.")
        row = connection.execute("""SELECT p.owner_id, p.acl_version, g.role FROM packages p
            JOIN users u ON u.id=? AND u.enabled=1
            LEFT JOIN grants g ON g.package_id=p.id AND g.user_id=u.id
            WHERE p.id=? AND p.state='ready'""", (str(principal.id), str(package_id))).fetchone()
        if row is None:
            raise AccessDenied(404)
        role = "owner" if row["owner_id"] == str(principal.id) else row["role"]
        if role is None:
            raise AccessDenied(404)
        if permission == "manage" and role != "owner" or permission == "edit" and role == "viewer":
            raise AccessDenied(403)
        return role, row["acl_version"]

    def authorize(self, package_id: UUID, principal: Principal, permission: Permission = "view") -> tuple[Role, int]:
        with self.connection() as connection:
            return self._authorize(connection, package_id, principal, permission)

    def list_packages(self, principal: Principal, offset: int, limit: int) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("""SELECT p.summary FROM packages p
                JOIN users u ON u.id=? AND u.enabled=1
                LEFT JOIN grants g ON g.package_id=p.id AND g.user_id=u.id
                WHERE p.state='ready' AND (p.owner_id=u.id OR g.user_id IS NOT NULL)
                ORDER BY p.id LIMIT ? OFFSET ?""", (str(principal.id), limit, offset))
            return [json.loads(row[0]) for row in rows]

    def update_summary(self, package_id: UUID, summary: dict) -> None:
        with self.connection(write=True) as connection:
            connection.execute("UPDATE packages SET summary=? WHERE id=?", (json.dumps(summary), str(package_id)))

    def sharing(self, package_id: UUID, principal: Principal) -> dict:
        with self.connection() as connection:
            _, version = self._authorize(connection, package_id, principal, "manage")
            rows = connection.execute("""SELECT u.id, u.display_name, g.role FROM grants g
                JOIN users u ON u.id=g.user_id WHERE g.package_id=? ORDER BY u.id""", (str(package_id),))
            return {"version": version, "grants": [dict(row) for row in rows]}

    def set_grant(self, package_id: UUID, actor: Principal, user_id: UUID,
                  role: Literal["editor", "viewer"] | None, expected_version: int) -> dict:
        if role not in ("editor", "viewer", None):
            raise ValueError("Invalid sharing role.")
        with self.connection(write=True) as connection:
            _, version = self._authorize(connection, package_id, actor, "manage")
            if version != expected_version:
                raise AccessDenied(412)
            target = connection.execute("SELECT id FROM users WHERE id=? AND enabled=1", (str(user_id),)).fetchone()
            if target is None or user_id == actor.id:
                raise ValueError("Choose another known, enabled user.")
            if role is None:
                connection.execute("DELETE FROM grants WHERE package_id=? AND user_id=?", (str(package_id), str(user_id)))
            else:
                connection.execute("""INSERT INTO grants VALUES(?,?,?)
                    ON CONFLICT(package_id,user_id) DO UPDATE SET role=excluded.role""",
                    (str(package_id), str(user_id), role))
            connection.execute("UPDATE packages SET acl_version=acl_version+1 WHERE id=?", (str(package_id),))
            connection.execute("""INSERT INTO security_events(created_at,actor_id,event,package_id,detail)
                VALUES(?,?,'package sharing',?,?)""",
                (time.time(), str(actor.id), str(package_id), json.dumps({"user_id": str(user_id), "role": role})))
        return self.sharing(package_id, actor)