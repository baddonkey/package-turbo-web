"""Explicit, previewed cleanup of verified older rollback copies only."""

import re
from datetime import datetime, timedelta, timezone

import rfc8785

from .audit import AuditError
from .file_workspace import manifest, remove_staging_tree
from .processing import protect_commit, tree_size

ROLLBACK_NAME = re.compile(r"rollback-[0-9a-f]{32}\Z")


class RetentionManager:
    def __init__(self, workspace):
        self.workspace = workspace
        self.directory = workspace.directory
        self.journal = workspace.journal
        self._issued_preview = None

    def _no_prepared_commit(self):
        with self.journal._connection("ro") as connection:
            for table in ("metadata_prepared", "package_prepared"):
                exists = connection.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone()
                if exists and connection.execute(f"SELECT id FROM {table}").fetchone():
                    raise AuditError("Recovery must finish before storage cleanup.")

    def _verify_copy(self, name, digest):
        if not ROLLBACK_NAME.fullmatch(name):
            raise AuditError("Only registered older rollback copies can be removed.")
        path = self.directory / name
        files = manifest(path)
        if not files:
            raise AuditError("A previewed rollback copy is missing.")
        for item in path.rglob("*"):
            if item.is_file() and item.stat().st_nlink != 1:
                raise AuditError("Shared files are protected from rollback cleanup.")
        actual = self.workspace._hash(rfc8785.dumps(files))
        if actual != digest:
            raise AuditError("A rollback copy changed after it was registered.")
        return tree_size(path)

    def preview(self, keep_count=3, minimum_age_days=7, now=None):
        if not 1 <= keep_count <= 100 or not 0 <= minimum_age_days <= 3650:
            raise AuditError("Retention limits are outside the supported range.")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise AuditError("Retention evaluation requires a timezone-aware date.")
        with self.workspace._locked():
            self._no_prepared_commit()
            self.workspace._check_baseline()
            with self.journal._connection("ro") as connection:
                header, records = self.journal._verify(connection)
            registered = {}
            for record in records:
                event = record["payload"]
                if event["eventType"] == "rollback copy retained" and event["outcome"] == "success":
                    registered[event["detail"]["path"]] = event
            protected = [{"path": path.name, "reason": "active or unregistered data"}
                         for path in self.directory.iterdir() if path.is_dir() and path.name not in registered]
            copies = [event for name, event in registered.items() if (self.directory / name).exists()]
            copies.sort(key=lambda event: event["sequence"], reverse=True)
            candidates = []
            for index, event in enumerate(copies):
                detail = event["detail"]
                archived = datetime.fromisoformat(event["eventDateTime"])
                if index < keep_count - 1 or now - archived < timedelta(days=minimum_age_days):
                    protected.append({"path": detail["path"], "reason": "retention policy"})
                    continue
                try:
                    size = self._verify_copy(detail["path"], detail["manifestSha256"])
                except AuditError as exc:
                    protected.append({"path": detail["path"], "reason": str(exc)})
                    continue
                candidates.append({"path": detail["path"], "bytes": size,
                                   "manifestSha256": detail["manifestSha256"], "archived": event["eventDateTime"]})
            result = dict(packageIdentifier=header["package_identifier"], headDigest=header["digest"],
                          keepCount=keep_count, minimumAgeDays=minimum_age_days,
                          candidates=candidates, protected=protected,
                          reclaimBytes=sum(item["bytes"] for item in candidates))
            self._issued_preview = rfc8785.dumps(result)
            return result

    def apply(self, preview):
        if self._issued_preview is None or rfc8785.dumps(preview) != self._issued_preview:
            raise AuditError("Cleanup requires the exact issued preview. Review a new preview.")
        with self.workspace._locked():
            self._no_prepared_commit()
            self.workspace._check_baseline()
            checkpoint = self.journal.verify()
            if (preview.get("packageIdentifier") != checkpoint.package_identifier
                    or preview.get("headDigest") != checkpoint.digest):
                raise AuditError("The cleanup preview is stale. Review a new preview.")
            registered = {record["payload"]["detail"]["path"]: record["payload"]["detail"]
                          for record in self.journal.records()
                          if record["payload"]["eventType"] == "rollback copy retained"}
            candidates = preview.get("candidates", [])
            if len({item["path"] for item in candidates}) != len(candidates):
                raise AuditError("Duplicate cleanup targets are not allowed.")
            for item in candidates:
                record = registered.get(item["path"])
                if record is None or record["manifestSha256"] != item["manifestSha256"]:
                    raise AuditError("Unregistered cleanup target.")
                if self._verify_copy(item["path"], item["manifestSha256"]) != item["bytes"]:
                    raise AuditError("Cleanup size changed; review a new preview.")
            if not candidates:
                return 0
            protect_commit()
            preparation = self.journal.append("storage cleanup preparation", agent_identifier=self.workspace.operator,
                                              software_version=self.workspace.software_version,
                                              detail={"scope": "older-rollback-copies", "candidates": candidates})
            self._issued_preview = None
            transaction = preparation["payload"]["transactionIdentifier"]
            completed = []
            try:
                for item in candidates:
                    self._verify_copy(item["path"], item["manifestSha256"])
                    remove_staging_tree(self.directory / item["path"])
                    completed.append(item["path"])
            except BaseException:
                self.journal.append("storage cleanup", agent_identifier=self.workspace.operator,
                                    software_version=self.workspace.software_version, outcome="failure",
                                    transaction_identifier=transaction, detail={"completed": completed})
                raise
            self.journal.append("storage cleanup", agent_identifier=self.workspace.operator,
                                software_version=self.workspace.software_version, transaction_identifier=transaction,
                                detail={"completed": completed, "bytes": preview["reclaimBytes"]})
            return sum(item["bytes"] for item in candidates)