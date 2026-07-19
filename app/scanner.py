from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime

from sqlalchemy import select

from .config import MOVIES_PATH, SYSTEM_FOLDERS
from .database import Release, ScanProgress, ScanRun, SessionLocal
from .settings_service import get_settings
from .srrdb import ScanCancelled, check_exact_release

logger = logging.getLogger(__name__)

_scan_lock = threading.Lock()
_stop_event = threading.Event()


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


def recover_interrupted_scan() -> None:
    """Clear stale running state left behind by a container restart."""
    db = SessionLocal()
    try:
        progress = _get_progress(db)

        if progress.is_running:
            completed_at = datetime.utcnow()
            progress.is_running = False
            progress.phase = "interrupted"
            progress.current_release = None
            progress.completed_at = completed_at
            progress.message = (
                "The previous scan was interrupted by an application restart. "
                "Press Start Scan to resume pending verification."
            )

            run = db.scalars(
                select(ScanRun)
                .where(ScanRun.status == "running")
                .order_by(ScanRun.started_at.desc())
                .limit(1)
            ).first()

            if run is not None:
                run.status = "interrupted"
                run.completed_at = completed_at
                run.error_message = (
                    "Application restarted while the scan was running."
                )

            db.commit()
    finally:
        db.close()


def request_stop() -> bool:
    if not _scan_lock.locked():
        return False

    _stop_event.set()

    db = SessionLocal()
    try:
        progress = _get_progress(db)
        if progress.is_running:
            progress.phase = "stopping"
            progress.message = "Stopping and cancelling the active request..."
            db.commit()
    finally:
        db.close()

    return True


def _check_stop() -> None:
    if _stop_event.is_set():
        raise ScanCancelled("Scan stopped by user.")


def _start_scan() -> int:
    db = SessionLocal()
    try:
        run = ScanRun()
        db.add(run)
        db.flush()

        progress = _get_progress(db)
        progress.is_running = True
        progress.phase = "inventory"
        progress.current_release = None
        progress.processed_count = 0
        progress.total_count = 0
        progress.verified_count = 0
        progress.unverified_count = 0
        progress.not_found_count = 0
        progress.api_error_count = 0
        progress.skipped_count = 0
        progress.started_at = datetime.utcnow()
        progress.completed_at = None
        progress.message = "Reading movie folders..."

        db.commit()
        return run.id
    finally:
        db.close()


def _inventory_archive(run_id: int) -> None:
    settings = get_settings()
    _check_stop()

    if not MOVIES_PATH.exists() or not MOVIES_PATH.is_dir():
        raise RuntimeError(f"Movie path is unavailable: {MOVIES_PATH}")

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
    now = datetime.utcnow()
    current = set(folder_names)

    db = SessionLocal()
    try:
        run = db.get(ScanRun, run_id)
        progress = _get_progress(db)

        progress.phase = "inventory"
        progress.total_count = len(folder_names)
        progress.processed_count = 0
        progress.skipped_count = skipped_count
        progress.message = "Adding folders to the searchable inventory..."
        run.skipped_folders = skipped_count
        db.commit()

        releases = db.scalars(select(Release)).all()
        by_name = {release.folder_name: release for release in releases}

        new_count = 0
        missing_count = 0

        for index, name in enumerate(folder_names, start=1):
            _check_stop()

            release = by_name.get(name)
            if release is None:
                release = Release(
                    folder_name=name,
                    normalized_name=normalize(name),
                    first_seen=now,
                    last_seen=now,
                    is_present=True,
                    verification_status="pending",
                    status="pending",
                )
                db.add(release)
                new_count += 1
            else:
                release.last_seen = now
                release.is_present = True

            progress.processed_count = index

            if index % 250 == 0:
                db.commit()

        for release in releases:
            _check_stop()

            if (
                release.folder_name not in current
                and release.is_present
            ):
                release.is_present = False
                missing_count += 1

        run.folders_found = len(folder_names)
        run.new_folders = new_count
        run.missing_folders = missing_count

        progress.phase = "inventory_complete"
        progress.current_release = None
        progress.processed_count = len(folder_names)
        progress.total_count = len(folder_names)
        progress.message = (
            f"Inventory complete. All {len(folder_names)} folders "
            "are searchable."
        )
        db.commit()
    finally:
        db.close()


async def _verify_releases(run_id: int) -> None:
    settings = get_settings()

    db = SessionLocal()
    try:
        progress = _get_progress(db)

        to_check_ids = db.scalars(
            select(Release.id)
            .where(
                Release.is_present.is_(True),
                Release.ignored.is_(False),
                Release.verification_status.in_(["pending", "unverified"]),
            )
            .order_by(Release.folder_name)
        ).all()

        progress.phase = "verifying"
        progress.processed_count = 0
        progress.total_count = len(to_check_ids)
        progress.current_release = None
        progress.message = (
            "No releases require verification."
            if not to_check_ids
            else "Inventory is searchable. Checking releases against SRRDB..."
        )
        db.commit()

        for index, release_id in enumerate(to_check_ids, start=1):
            _check_stop()

            release = db.get(Release, release_id)
            if release is None:
                continue

            progress.current_release = release.folder_name
            progress.message = "Checking SRRDB..."
            db.commit()

            status, matched, error = await check_exact_release(
                release.folder_name,
                settings.srrdb_delay_seconds,
                stop_requested=_stop_event.is_set,
            )

            _check_stop()

            release.verification_status = status
            release.status = status
            release.matched_release = matched
            release.error_message = error
            release.last_checked = datetime.utcnow()

            progress.processed_count = index
            if status == "verified":
                progress.verified_count += 1
            elif status == "unverified":
                progress.unverified_count += 1

            db.commit()

        present_releases = db.scalars(
            select(Release).where(
                Release.is_present.is_(True),
                Release.ignored.is_(False),
            )
        ).all()

        completed_at = datetime.utcnow()
        run = db.get(ScanRun, run_id)
        run.completed_at = completed_at
        run.status = "completed"
        run.exact_matches = sum(
            release.verification_status == "verified"
            for release in present_releases
        )
        run.unverified = sum(
            release.verification_status == "unverified"
            for release in present_releases
        )
        run.not_found = 0
        run.api_errors = 0

        progress.is_running = False
        progress.phase = "complete"
        progress.current_release = None
        progress.completed_at = completed_at
        progress.message = "Scan completed."
        db.commit()
    finally:
        db.close()


def _record_stopped(run_id: int) -> None:
    db = SessionLocal()
    try:
        completed_at = datetime.utcnow()
        run = db.get(ScanRun, run_id)
        progress = _get_progress(db)

        if run is not None:
            run.completed_at = completed_at
            run.status = "stopped"
            run.error_message = None

        progress.is_running = False
        progress.phase = "stopped"
        progress.current_release = None
        progress.completed_at = completed_at
        progress.message = (
            "Scan stopped. Saved results remain available; unfinished "
            "releases will resume on the next scan."
        )
        db.commit()
    finally:
        db.close()


def _record_failure(run_id: int, exc: Exception) -> None:
    db = SessionLocal()
    try:
        completed_at = datetime.utcnow()
        run = db.get(ScanRun, run_id)
        progress = _get_progress(db)

        if run is not None:
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


async def _scan_async() -> None:
    if not _scan_lock.acquire(blocking=False):
        logger.info("A scan is already running; request ignored.")
        return

    run_id: int | None = None
    _stop_event.clear()

    try:
        run_id = _start_scan()
        _inventory_archive(run_id)
        await _verify_releases(run_id)

    except ScanCancelled:
        logger.info("Scan stopped by user.")
        if run_id is not None:
            _record_stopped(run_id)
    except Exception as exc:
        logger.exception("Scan failed")
        if run_id is not None:
            _record_failure(run_id, exc)
    finally:
        _stop_event.clear()
        _scan_lock.release()


def run_scan() -> None:
    asyncio.run(_scan_async())
