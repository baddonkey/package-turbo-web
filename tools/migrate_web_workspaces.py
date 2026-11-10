"""Explicit, offline owner assignment for existing web workspaces."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from filelock import FileLock

from ph_backend.processing import digest_file
from ph_web.database import ServiceDatabase
from ph_web.storage import OpenPackage, WebSipPackage, safe_part


def inventory(data_dir: Path, package_ids: list[UUID]) -> list[dict]:
    database = ServiceDatabase(data_dir / "service.sqlite3")
    if database.path.is_file():
        with database.connection() as connection:
            registered = {row[0] for row in connection.execute("SELECT id FROM packages")}
    else:
        registered = set()
    summaries = []
    for package_id in package_ids:
        if str(package_id) in registered:
            raise ValueError(f"Package {package_id} already has a catalog entry; ownership will not be overwritten.")
        workspace = data_dir / str(package_id)
        if workspace.is_symlink() or not workspace.is_dir():
            raise ValueError(f"Package {package_id} is not a private workspace directory.")
        name = (workspace / "name").read_text(encoding="utf-8")
        if not safe_part(name):
            raise ValueError(f"Package {package_id} has an unsafe name.")
        package_dir = workspace / "sip" / name
        if not package_dir.resolve().is_relative_to(workspace.resolve()):
            raise ValueError(f"Package {package_id} resolves outside its workspace.")
        metadata_only = (workspace / "metadata-only").is_file()
        pkg = WebSipPackage.open_folder(package_dir, read_only=metadata_only or (workspace / "read-only").is_file())
        summaries.append(OpenPackage(package_id, pkg, digest_file(pkg.metadata_path, "sha256"), metadata_only)
                         .summary().model_dump(mode="json"))
    return summaries


def migrate(data_dir: Path, package_ids: list[UUID], issuer: str, subject: str,
            display_name: str, *, apply: bool = False) -> list[dict]:
    if not issuer.strip() or not subject.strip() or not display_name.strip():
        raise ValueError("Provide the verified identity issuer, subject and display name.")
    if not package_ids or len(set(package_ids)) != len(package_ids):
        raise ValueError("Select one or more distinct workspace UUIDs explicitly.")
    root = data_dir.resolve()
    if not apply:
        return inventory(root, package_ids)
    if not root.is_dir():
        raise ValueError("The existing workspace data directory does not exist.")
    with FileLock(root / "catalog.lock", timeout=5):
        database = ServiceDatabase(root / "service.sqlite3")
        database.initialize()
        summaries = inventory(root, package_ids)
        owner = database.register_user(issuer, subject, display_name)
        for summary in summaries:
            package_id = UUID(summary["id"])
            with FileLock(root / str(package_id) / "access.lock", timeout=5):
                current = inventory(root, [package_id])[0]
                if current != summary:
                    raise ValueError("A package changed during migration. Stop the service and retry.")
                database.prepare_package(package_id, owner, summary)
                database.publish_package(package_id)
        database.revoke_all_sessions()
        return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description="Assign selected existing packages to a verified identity. Stop the service and back up first.")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--package-id", required=True, action="append", type=UUID)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--apply", action="store_true", help="Write catalog ownership. Default is a read-only dry run.")
    args = parser.parse_args()
    try:
        summaries = migrate(args.data_dir, args.package_id, args.issuer, args.subject, args.display_name, apply=args.apply)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Migration refused: {exc}\n")
    print(json.dumps({"applied": args.apply, "owner": {"issuer": args.issuer, "subject": args.subject},
                      "packages": summaries}, indent=2))


if __name__ == "__main__":
    main()