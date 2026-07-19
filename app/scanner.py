from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime

from sqlalchemy import select

from .config import MOVIES_PATH, SYSTEM_FOLDERS
from .database import Release, ScanProgress, ScanRun, SessionLocal
from .settings_service import get_settings
from .srrdb import check_exact_release

logger = logging.getLogger(__name__)
_scan_lock = threading.Lock()


def normalize(name: str) -> str:
    return name.strip().casefold()


def should_scan_folder(
    folder_name: str,
    skip_hidden_system_folders: bool,
) -> bool:
    if not skip_hidden_system_folders:
        return True

    return (
        not folder_name.startswith(".")
        and folder_name not in SYSTEM_FOLDERS
    )


def _get_progress(db) -> ScanProgress:
    progress = db.get(ScanProgress, 1)
    if progress is None:
        progress = ScanProgress(id=1)
        db.add(progress)
        db.flush()
    return progress


def _reset_progress(db, *, message: str | None = None) -> ScanProgress:
    progress = _get_progress(db)
    progress.is_running = True
    progress.phase = "scanning"
    progress.current_release = None
    progress.processed_count = 0
    progress.total_count = 0
    progress.verified_count = 0
    progress.not_found_count = 0
    progress.api_error_count = 0
    progress.skipped_count = 0
    progress.started_at = datetime.utcnow()
    progress.completed_at = None
    progress.message = message
    db.commit()
    return progress


async def _scan_async() -> None:
    if not _scan_lock.acquire(blocking=False):
        logger.info("A scan is already running; request ignored.")
        return

    db = SessionLocal()
    run = ScanRun()
    db.add(run)
    db.commit()

    progress = _reset_progress(db, message="Reading movie folders...")

    try:
        if not MOVIES_PATH.exists() or not MOVIES_PATH.is_dir():
            raise RuntimeError(f"Movie path is unavailable: {MOVIES_PATH}")

        settings = get_settings()

        all_directories = sorted(
            entry.name
            for entry in MOVIES_PATH.iterdir()
            if entry.is_dir()
        )

        folder_names = [
            name
            for name in all_directories
            if should_scan_folder(
                name,
                settings.skip_hidden_system_folders,
            )
        ]

        skipped_count = len(all_directories) - len(folder_names)
        run.skipped_folders = skipped_count
        progress.skipped_count = skipped_count
        progress.message = "Updating archive inventory..."
        db.commit()

        now = datetime.utcnow()
        current = set(folder_names)

        releases = db.scalars(select(Release)).all()
        by_name = {release.folder_name: release for release in releases}

        new_count = 0
        missing_count = 0

        for name in folder_names:
            release = by_name.get(name)

            if release is None:
                release = Release(
                    folder_name=name,
                    normalized_name=normalize(name),
                    first_seen=now,
                    last_seen=now,
                    is_present=True,
                    status="pending",
                )
                db.add(release)
                db.flush()
                new_count += 1
            else:
                release.last_seen = now
                release.is_present = True

        for release in releases:
            if release.folder_name not in current and release.is_present:
                release.is_present = False
                release.status = "missing"
                missing_count += 1

        db.commit()

        to_check = db.scalars(
            select(Release)
            .where(
                Release.is_present.is_(True),
                Release.ignored.is_(False),
                Release.status.in_(["pending", "api_error"]),
            )
            .order_by(Release.folder_name)
        ).all()

        progress.phase = "verifying"
        progress.total_count = len(to_check)
        progress.message = (
            "No releases require verification."
            if not to_check
            else "Checking releases against SRRDB..."
        )
        db.commit()

        for index, release in enumerate(to_check, start=1):
            progress.current_release = release.folder_name
            progress.message = "Checking SRRDB..."
            db.commit()

            status, matched, error = await check_exact_release(
                release.folder_name,
                settings.srrdb_delay_seconds,
            )

            release.status = status
            release.matched_release = matched
            release.error_message = error
            release.last_checked = datetime.utcnow()

            progress.processed_count = index
            if status == "verified":
                progress.verified_count += 1
            elif status == "not_found":
                progress.not_found_count += 1
            elif status == "api_error":
                progress.api_error_count += 1

            db.commit()

        present_releases = db.scalars(
            select(Release).where(
                Release.is_present.is_(True),
                Release.ignored.is_(False),
            )
        ).all()

        completed_at = datetime.utcnow()

        run.completed_at = completed_at
        run.status = "completed"
        run.folders_found = len(folder_names)
        run.new_folders = new_count
        run.missing_folders = missing_count
        run.exact_matches = sum(
            release.status == "verified"
            for release in present_releases
        )
        run.not_found = sum(
            release.status == "not_found"
            for release in present_releases
        )
        run.api_errors = sum(
            release.status == "api_error"
            for release in present_releases
        )

        progress.is_running = False
        progress.phase = "complete"
        progress.current_release = None
        progress.completed_at = completed_at
        progress.message = "Scan completed."
        db.commit()

    except Exception as exc:
        logger.exception("Scan failed")
        completed_at = datetime.utcnow()

        run.completed_at = completed_at
        run.status = "failed"
        run.error_message = str(exc)

        progress.is_running = False
        progress.phase = "failed"
        progress.current_release = None
        progress.completed_at = completed_at
        progress.message = str(exc)
        db.commit()
    finally:
        db.close()
        _scan_lock.release()


def run_scan() -> None:
    asyncio.run(_scan_async())
