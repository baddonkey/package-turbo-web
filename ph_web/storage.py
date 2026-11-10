"""Private filesystem workspaces and bounded SIP ZIP ingestion."""

import os
import re
import stat
import shutil
import tempfile
import unicodedata
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4

from filelock import FileLock, Timeout

from ph_backend.file_workspace import recover_file_commit
from ph_backend.package_factory import create_package, suggested_name
from ph_backend.processing import MAX_METADATA_BYTES, digest_file
from ph_backend.sip import SipPackage

from .database import Permission, Principal, Role, ServiceDatabase
from .models import PackageCreate, PackageSummary

WEB_SOFTWARE_VERSION = f"Package Turbo Web {version('package-turbo')}"
BLOCK_BYTES = 1024 * 1024
MAX_ENTRIES = 10_000
MAX_DEPTH = 40
WINDOWS_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])", re.I)
UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


class WebError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    operator: str = "local operator"
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    max_upload_bytes: int = 256 * BLOCK_BYTES
    max_expanded_bytes: int = 1024 * BLOCK_BYTES

    def __post_init__(self):
        if not self.operator.strip() or len(self.operator) > 100:
            raise ValueError("Configure a non-blank audit operator of at most 100 characters.")
        if min(self.max_upload_bytes, self.max_expanded_bytes) <= 0:
            raise ValueError("Upload and expansion limits must be positive.")

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv("PACKAGE_TURBO_DATA_DIR", ".package-turbo-web")),
            operator=os.getenv("PACKAGE_TURBO_OPERATOR", "local operator"),
            allowed_hosts=tuple(host.strip() for host in
                                os.getenv("PACKAGE_TURBO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")),
            max_upload_bytes=int(os.getenv("PACKAGE_TURBO_MAX_UPLOAD_BYTES", str(256 * BLOCK_BYTES))),
            max_expanded_bytes=int(os.getenv("PACKAGE_TURBO_MAX_EXPANDED_BYTES", str(1024 * BLOCK_BYTES))),
        )


class WebSipPackage(SipPackage):
    def xsd_path(self) -> None:
        # The browser accepts arbitrary SIPs. Only bundled schemas drive its editor.
        return None


@dataclass(frozen=True)
class OpenPackage:
    id: UUID
    sip: WebSipPackage
    revision: str
    metadata_only: bool = False
    role: Role | None = None

    @property
    def etag(self) -> str:
        return f'"{self.revision}"'

    def summary(self) -> PackageSummary:
        return PackageSummary(
            id=self.id, name=self.sip.name, schema_version=self.sip.root.get("schemaVersion", ""),
            delivery_type=self.sip.delivery_type, read_only=self.sip.read_only, revision=self.revision, metadata_only=self.metadata_only, role=self.role,
        )


def safe_part(part: str) -> bool:
    return (
        bool(part) and part not in (".", "..") and not part.endswith((".", " "))
        and not UNSAFE_NAME.search(part) and not WINDOWS_DEVICE.fullmatch(part.split(".")[0].rstrip(" "))
        and len(part.encode("utf-8")) <= 255
    )


def _unpack(source: Path, destination: Path, expanded_limit: int) -> None:
    total = 0
    explicit = set()
    paths = {}
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise WebError(413, f"ZIP contains more than {MAX_ENTRIES:,} entries.")
            for entry in entries:
                name = entry.orig_filename
                directory = name.endswith("/")
                parts = name.removesuffix("/").split("/")
                if len(parts) > MAX_DEPTH or not all(map(safe_part, parts)):
                    raise WebError(422, "ZIP contains an unsupported file or directory name.")
                mode = stat.S_IFMT(entry.external_attr >> 16)
                allowed_mode = stat.S_IFDIR if directory else stat.S_IFREG
                if mode not in (0, allowed_mode) or entry.flag_bits & 1:
                    raise WebError(422, "ZIP entries must be regular, unencrypted files and directories.")
                if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise WebError(422, "ZIP entries must use store or deflate compression.")
                total += entry.file_size
                if total > expanded_limit:
                    raise WebError(413, "Expanded ZIP exceeds the configured size limit.")
                if tuple(parts[-2:]) == ("header", "metadata.xml") and entry.file_size > MAX_METADATA_BYTES:
                    raise WebError(413, "metadata.xml exceeds the package metadata size limit.")
                target = destination
                key = ()
                for index, part in enumerate(parts):
                    key += (unicodedata.normalize("NFC", part).casefold(),)
                    target = target / part
                    is_directory = index < len(parts) - 1 or directory
                    identity = (tuple(parts[:index + 1]), is_directory)
                    if paths.setdefault(key, identity) != identity:
                        raise WebError(422, "ZIP contains conflicting file or directory names.")
                if key in explicit:
                    raise WebError(422, "ZIP contains duplicate entries.")
                explicit.add(key)
                if directory:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with archive.open(entry) as stream, target.open("xb") as output:
                    while chunk := stream.read(BLOCK_BYTES):
                        written += len(chunk)
                        if written > entry.file_size:
                            raise WebError(422, "ZIP member exceeds its declared size.")
                        output.write(chunk)
                if written != entry.file_size:
                    raise WebError(422, "ZIP member does not match its declared size.")
    except (zipfile.BadZipFile, EOFError, UnicodeError, zlib.error) as exc:
        raise WebError(422, "Invalid or incomplete ZIP archive.") from exc


class PackageStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir.resolve()
        self.database = ServiceDatabase(self.root / "service.sqlite3")

    def initialize(self) -> None:
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        with FileLock(self.root / "catalog.lock", timeout=5):
            self.database.initialize()
            self._reconcile_publication()

    def _reconcile_publication(self) -> None:
        for package_id in self.database.pending_packages():
            workspace = self.root / str(package_id)
            if not workspace.exists():
                self.database.abandon_package(package_id)
                continue
            with FileLock(workspace / "access.lock", timeout=5):
                name = (workspace / "name").read_text(encoding="utf-8")
                if not safe_part(name):
                    raise WebError(409, "Pending SIP has an invalid name. Review it before restarting.")
                pkg = WebSipPackage.open_folder(workspace / "sip" / name)
                metadata_only = (workspace / "metadata-only").is_file()
                pkg.read_only = metadata_only or (workspace / "read-only").is_file()
                summary = OpenPackage(package_id, pkg, digest_file(pkg.metadata_path, "sha256"), metadata_only).summary()
                self.database.update_summary(package_id, summary.model_dump(mode="json"))
                self.database.publish_package(package_id)

    @contextmanager
    def staging(self):
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=self.root) as path:
            yield Path(path)

    def _publish(self, stage: Path, name: str, owner: Principal, *, read_only=False, metadata_only=False) -> PackageSummary:
        pkg = WebSipPackage.open_folder(stage / "sip" / name, read_only=read_only or metadata_only)
        if read_only:
            (stage / "read-only").touch()
        if metadata_only:
            (stage / "metadata-only").touch()
        if pkg.tree.docinfo.doctype:
            raise WebError(422, "SIP metadata must not contain a document type declaration.")
        (stage / "name").write_text(name, encoding="utf-8")
        package_id = uuid4()
        revision = digest_file(pkg.metadata_path, "sha256")
        summary = OpenPackage(package_id, pkg, revision, metadata_only).summary()
        with FileLock(self.root / "catalog.lock", timeout=5):
            self.database.prepare_package(package_id, owner, summary.model_dump(mode="json"))
            stage.rename(self.root / str(package_id))
            self.database.publish_package(package_id)
        return summary.model_copy(update={"role": "owner"})

    def create(self, command: PackageCreate, owner: Principal) -> PackageSummary:
        name = command.name
        if name is None:
            suggested = suggested_name(command.delivering_office, command.reference or "")
            portable = re.sub(r"[^A-Za-z0-9_.-]+", "-", suggested)
            name = re.sub(r"-{2,}", "-", portable)[:180].rstrip("._")
        if not safe_part(name):
            raise WebError(422, "Invalid SIP folder name.")
        with self.staging() as temporary:
            stage = temporary / "workspace"
            csv_path = None
            if command.classification_csv:
                csv_path = temporary / "classification.csv"
                csv_path.write_bytes(command.classification_csv.encode("utf-8"))
            create_package(
                stage / "sip", name, command.delivering_office, command.delivery_type,
                creator=command.creator or "", system_name=command.system_name, version=command.release, csv_path=csv_path,
            )
            return self._publish(stage, name, owner)

    def import_zip(self, source: Path, fallback_name: str, read_only: bool, owner: Principal) -> PackageSummary:
        with self.staging() as temporary:
            extracted = temporary / "extracted"
            _unpack(source, extracted, self.settings.max_expanded_bytes)
            stage = temporary / "workspace"
            destination = stage / "sip"
            destination.mkdir(parents=True)
            entries = list(extracted.iterdir()) if extracted.is_dir() else []
            if (extracted / "header" / "metadata.xml").is_file():
                name = fallback_name if safe_part(fallback_name) else "imported-sip"
                extracted.rename(destination / name)
            elif len(entries) == 1 and entries[0].is_dir():
                name = entries[0].name
                entries[0].rename(destination / name)
            else:
                raise WebError(422, "ZIP must contain one SIP with header/metadata.xml.")
            return self._publish(stage, name, owner, read_only=read_only)

    def import_metadata(self, source: Path, name: str, owner: Principal) -> PackageSummary:
        name = name if safe_part(name) else "metadata.xml"
        with self.staging() as temporary:
            stage = temporary / "workspace"
            header = stage / "sip" / name / "header"
            header.mkdir(parents=True)
            shutil.copyfile(source, header / "metadata.xml")
            return self._publish(stage, name, owner, metadata_only=True)

    @contextmanager
    def access(self, package_id: UUID, principal: Principal, permission: Permission = "view"):
        self.database.authorize(package_id, principal, permission)
        workspace = self.root / str(package_id)
        if not workspace.is_dir():
            raise WebError(404, "SIP workspace does not exist.")
        try:
            with FileLock(workspace / "access.lock", timeout=5):
                self.database.authorize(package_id, principal, permission)
                yield workspace
        except Timeout as exc:
            raise WebError(503, "SIP workspace is busy. Try again shortly.") from exc

    @contextmanager
    def open(self, package_id: UUID, principal: Principal, permission: Permission = "view"):
        with self.access(package_id, principal, permission) as workspace:
            role, _ = self.database.authorize(package_id, principal, permission)
            name = (workspace / "name").read_text(encoding="utf-8")
            if not safe_part(name):
                raise WebError(409, "SIP workspace has an invalid name.")
            package_dir = workspace / "sip" / name
            recover_file_commit(package_dir, "package-turbo recovery service", WEB_SOFTWARE_VERSION)
            metadata_only = (workspace / "metadata-only").is_file()
            pkg = WebSipPackage.open_folder(package_dir, read_only=metadata_only or (workspace / "read-only").is_file())
            current = OpenPackage(package_id, pkg, digest_file(pkg.metadata_path, "sha256"), metadata_only, role)
            self.database.update_summary(package_id, current.summary().model_dump(mode="json"))
            yield current
            updated = OpenPackage(package_id, pkg, digest_file(pkg.metadata_path, "sha256"), metadata_only, role)
            self.database.update_summary(package_id, updated.summary().model_dump(mode="json"))

    def list(self, principal: Principal, offset: int, limit: int) -> list[PackageSummary]:
        return [PackageSummary.model_validate(summary) for summary in self.database.list_packages(principal, offset, limit)]
