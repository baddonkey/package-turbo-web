"""Export verified SIP preservation companions."""

import os
import shutil
import tempfile
import uuid
from pathlib import Path

from .audit import AuditError, AuditJournal
from .file_workspace import manifest
from .processing import copy_tree, critical_section, digest_file, read_bytes
from .sip import SipPackage


def write_export_file(path: Path, data: bytes, exclusive: bool = False) -> None:
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive and path.exists():
            raise FileExistsError(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_audit_bundle(root: Path, journal_path: Path, destination: Path,
                        redact_values: bool, mets_version: str,
                        operator: str, software_version: str) -> dict:
    root = Path(root).resolve()
    journal_path = Path(journal_path)
    destination = Path(destination).resolve()
    companion = root.with_name(root.name + ".package-turbo").resolve()
    if not destination.is_dir():
        raise AuditError("Choose an existing export directory.")
    if (destination == root or destination.is_relative_to(root)
            or destination == companion or destination.is_relative_to(companion)):
        raise AuditError("The export directory cannot be inside the SIP or its companion.")
    if not journal_path.is_file():
        raise AuditError("The preservation journal does not exist.")
    export_directory = destination / f"{root.name}.package-turbo-export"
    if export_directory.exists():
        raise AuditError(f"Export directory already exists: {export_directory}")

    archive_name = root.name + ".zip"
    premis_name = root.name + ".premis.xml"
    mets_name = root.name + ".mets.xml"
    original_manifest = manifest(root)
    temporary_root = Path(tempfile.mkdtemp(prefix=".package-turbo-export-", dir=destination))
    try:
        snapshot = temporary_root / root.name
        copy_tree(root, snapshot)
        if manifest(snapshot) != original_manifest or manifest(root) != original_manifest:
            raise AuditError("The SIP changed while the export snapshot was being created.")

        archive = temporary_root / archive_name
        SipPackage.open_folder(snapshot, read_only=True).zip_to(archive)
        archive_digest = digest_file(archive, "sha256")

        journal = AuditJournal(journal_path)
        checkpoint = journal.verify()
        metadata_xml = read_bytes(snapshot / "header" / "metadata.xml")
        premis_data = journal.premis_xml(redact_values=redact_values, metadata_xml=metadata_xml,
                                         sip_manifest=original_manifest)
        mets_data = journal.mets_xml(archive_name, archive_digest, premis_filename=premis_name,
                                     version=mets_version, redact_values=redact_values,
                                     metadata_xml=metadata_xml, sip_manifest=original_manifest)
        if journal.verify() != checkpoint:
            raise AuditError("The preservation journal changed during export.")

        output = temporary_root / "output"
        output.mkdir()
        os.replace(archive, output / archive_name)
        write_export_file(output / premis_name, premis_data, exclusive=True)
        write_export_file(output / mets_name, mets_data, exclusive=True)
        with critical_section(check_cancel=False):
            if export_directory.exists():
                raise AuditError(f"Export directory already exists: {export_directory}")
            os.rename(output, export_directory)
            audit_error = ""
            try:
                journal.append("preservation export", agent_identifier=operator,
                               software_version=software_version,
                               detail={"scope": "sip-companion-export", "format": "SIP ZIP + PREMIS 3.0 + METS",
                                       "metsVersion": mets_version, "redacted": redact_values,
                                       "sipSha256": archive_digest, "sourceSequence": checkpoint.sequence,
                                       "sourceDigest": checkpoint.digest})
            except (AuditError, OSError) as exc:
                audit_error = str(exc)
        return {"directory": export_directory, "auditError": audit_error}
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
