"""Recoverable study-group deletion. Callers hold the job and catalog locks.

Same-filesystem renames quarantine complete directories before one SQLite commit.
The durable journal and atomic presence/absence of *all* job rows decide recovery:
restore before commit, finish cleanup after commit. This is not filesystem/SQL
atomicity; ambiguous states fail closed and require repair.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from .job_archives import ArchiveError, MAX_JSON_BYTES
from .storage import Store

JOB_ID = re.compile(r"[0-9a-f]{32}")
MAX_STUDY_JOBS = 100000


def safe_local(path: Path) -> None:
    if any(part.is_symlink() for part in (*reversed(path.parents), path)):
        raise ArchiveError("Study deletion paths must not contain symlinks.")


def sync_directory(path: Path) -> None:
    # Linux is the supported server platform. Windows lacks directory fsync;
    # process-interruption recovery still uses the journal there.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class StudyDeletion:
    def __init__(self, jobs_root: Path, store: Store):
        self.root = jobs_root
        self.store = store
        self.quarantine = jobs_root / ".study-deletions"
        self.recovery_required = False

    def recover(self) -> None:
        """Run at startup and before explicit preview/deletion, never on polls."""
        safe_local(self.quarantine)
        if not self.quarantine.exists():
            self.recovery_required = False
            return
        journals = sorted(self.quarantine.iterdir())
        if not journals:
            self.recovery_required = False
            return
        retained = self.store.job_ids()
        self.recovery_required = True
        for journal in journals:
            safe_local(journal)
            if not JOB_ID.fullmatch(journal.name) or not journal.is_dir():
                raise ArchiveError(
                    "Unknown study deletion journal; repair the quarantine before proceeding."
                )
            manifest = journal / "manifest.json"
            safe_local(manifest)
            if not manifest.exists() and not any(journal.iterdir()):
                # Crash after removing the manifest but before rmdir, or before
                # initial journal publication. No job data can be in this directory.
                journal.rmdir()
                sync_directory(self.quarantine)
                continue
            if not manifest.is_file() or manifest.stat().st_size > MAX_JSON_BYTES:
                raise ArchiveError(
                    "Unreadable study deletion journal; preserve the quarantine and repair it."
                )
            try:
                data = json.loads(manifest.read_text())
            except (OSError, ValueError) as error:
                raise ArchiveError(
                    "Unreadable study deletion journal; preserve the quarantine and repair it."
                ) from error
            ids = data.get("job_ids") if isinstance(data, dict) else None
            if (
                not isinstance(ids, list)
                or not 1 <= len(ids) <= MAX_STUDY_JOBS
                or any(
                    not isinstance(identifier, str) or not JOB_ID.fullmatch(identifier)
                    for identifier in ids
                )
                or len(set(ids)) != len(ids)
                or data.get("version") != 1
            ):
                raise ArchiveError(
                    "Invalid study deletion journal; repair its job ownership first."
                )
            allowed_entries = {"manifest.json", *ids}
            if any(path.name not in allowed_entries for path in journal.iterdir()):
                raise ArchiveError(
                    "Unexpected study deletion artifacts; preserve the quarantine and repair it."
                )
            present = set(ids) & retained
            if present and present != set(ids):
                raise ArchiveError(
                    "Study deletion has mixed job records; preserve the quarantine and repair the database."
                )
            if present:
                for identifier in ids:
                    staged, original = journal / identifier, self.root / identifier
                    safe_local(staged)
                    safe_local(original)
                    if staged.exists():
                        if original.exists() or not staged.is_dir():
                            raise ArchiveError(
                                "Study recovery would overwrite existing data. Repair the quarantine first."
                            )
                        os.rename(staged, original)
                    elif not original.is_dir():
                        raise ArchiveError(
                            "Study recovery cannot find a job directory. Preserve the journal and restore its data."
                        )
                sync_directory(self.root)
                sync_directory(journal)
            else:
                # A committed deletion may have partially completed cleanup. Only
                # quarantine contents are removed; never a newly created job path.
                for identifier in ids:
                    safe_local(journal / identifier)
                    if (self.root / identifier).exists():
                        raise ArchiveError(
                            "A deleted job path was reused; preserve its quarantine and repair ownership."
                        )
                try:
                    # Keep the manifest until every staged directory is gone.
                    for identifier in ids:
                        staged = journal / identifier
                        if staged.exists():
                            shutil.rmtree(staged)
                except OSError:
                    continue  # Records are committed; retry reclamation later.
            try:
                manifest.unlink()
                journal.rmdir()
                sync_directory(self.quarantine)
            except OSError:
                if present:
                    raise
                # Committed state remains consistent; retry empty journal cleanup.
                continue
        self.recovery_required = False

    def delete(self, identifiers: list[str]) -> bool:
        """Return whether committed filesystem reclamation still needs retry."""
        if (
            not identifiers
            or len(identifiers) > MAX_STUDY_JOBS
            or len(set(identifiers)) != len(identifiers)
            or any(not JOB_ID.fullmatch(identifier) for identifier in identifiers)
        ):
            raise ArchiveError("Invalid study deletion scope.")
        self.recover()
        safe_local(self.quarantine)
        self.quarantine.mkdir(mode=0o700, exist_ok=True)
        journal = self.quarantine / uuid4().hex
        journal.mkdir(mode=0o700)
        try:
            with (journal / "manifest.json").open("x") as handle:
                json.dump({"version": 1, "job_ids": identifiers}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            sync_directory(journal)
            sync_directory(self.quarantine)
            sync_directory(self.root)
        except BaseException:
            shutil.rmtree(journal)
            raise
        try:
            with self.store.discard_jobs(identifiers):
                for identifier in identifiers:
                    original = self.root / identifier
                    safe_local(original)
                    if not original.is_dir():
                        raise ArchiveError(
                            "A participating job directory changed; refresh the study preview."
                        )
                    os.rename(original, journal / identifier)
                sync_directory(self.root)
                sync_directory(journal)
        except BaseException:
            try:
                self.recover()
            except BaseException as recovery_error:
                self.recovery_required = True
                raise ArchiveError(
                    "Study deletion was interrupted. Data is quarantined for recovery; retry the preview or restart after repairing filesystem access."
                ) from recovery_error
            raise
        self.recover()
        return journal.exists()
