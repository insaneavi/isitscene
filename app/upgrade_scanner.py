from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime

from sqlalchemy import delete, select

from .database import (
    Release,
    ScanProgress,
    SessionLocal,
    UpgradeCandidate,
    UpgradeProgress,
    UpgradeResult,
    UpgradeScan,
)
from .settings_service import get_settings
from .srrdb import ScanCancelled, find_uhd_upgrades

logger = logging.getLogger(__name__)
_upgrade_lock = threading.Lock()
_stop_event = threading.Event()


def _eligible(name: str) -> bool:
    folded = name.casefold()
    is_bluray = "bluray" in folded or "blu-ray" in folded
    is_web = any(tag in folded for tag in ("web-dl", "webrip", ".web.", " web "))
    already_uhd = any(tag in folded for tag in ("2160p", ".uhd.", "ultrahd"))
    return is_bluray and not is_web and not already_uhd


def request_upgrade_stop() -> None:
    """Request cancellation and immediately expose the stopping state."""
    _stop_event.set()
    db = SessionLocal()
    try:
        progress = _progress(db)
        if progress.is_running:
            progress.phase = "stopping"
            progress.message = (
                "Stopping Collection Upgrade after the current SRRDB request..."
            )
            db.commit()
    finally:
        db.close()


def recover_interrupted_upgrade_scan() -> None:
    """Clear a stale running state left behind by a restart or worker exit."""
    db = SessionLocal()
    try:
        progress = _progress(db)
        if not progress.is_running:
            return

        now = datetime.utcnow()
        progress.is_running = False
        progress.phase = "interrupted"
        progress.current_release = None
        progress.completed_at = now
        progress.message = (
            "Previous Collection Upgrade scan was interrupted. "
            "You can safely start it again."
        )

        active_runs = db.scalars(
            select(UpgradeScan).where(UpgradeScan.status == "running")
        ).all()
        for run in active_runs:
            run.status = "interrupted"
            run.completed_at = now
            run.error_message = (
                "Application restarted or the previous scan worker exited."
            )

        db.commit()
    finally:
        db.close()


def reset_upgrade_scan_state() -> bool:
    """Force-clear stale UI/database state when no worker owns the scan lock."""
    if not _upgrade_lock.acquire(blocking=False):
        return False

    try:
        _stop_event.clear()
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            progress = _progress(db)
            progress.is_running = False
            progress.phase = "reset"
            progress.current_release = None
            progress.completed_at = now
            progress.message = (
                "Collection Upgrade scan state was reset. "
                "You can start a new scan."
            )

            active_runs = db.scalars(
                select(UpgradeScan).where(UpgradeScan.status == "running")
            ).all()
            for run in active_runs:
                run.status = "interrupted"
                run.completed_at = now
                run.error_message = "Scan state was manually reset."

            db.commit()
            return True
        finally:
            db.close()
    finally:
        _upgrade_lock.release()


def _progress(db):
    row = db.get(UpgradeProgress, 1)
    if row is None:
        row = UpgradeProgress(id=1)
        db.add(row)
        db.flush()
    return row


async def _run() -> None:
    if not _upgrade_lock.acquire(blocking=False):
        logger.info("Collection Upgrade is already running; start request ignored.")
        return

    _stop_event.clear()
    db = SessionLocal()
    run = None
    try:
        normal = db.get(ScanProgress, 1)
        if normal and normal.is_running:
            return

        releases = db.scalars(
            select(Release).where(
                Release.is_present.is_(True),
                Release.verification_status == "verified",
                Release.ignored.is_(False),
            ).order_by(Release.folder_name)
        ).all()
        eligible = [r for r in releases if _eligible(r.matched_release or r.folder_name)]

        run = UpgradeScan(status="running", eligible_count=len(eligible))
        db.add(run)
        progress = _progress(db)
        progress.is_running = True
        progress.phase = "checking"
        progress.current_release = None
        progress.processed_count = 0
        progress.total_count = len(eligible)
        progress.upgrades_found = 0
        progress.no_upgrade_count = 0
        progress.imdb_missing_count = 0
        progress.api_error_count = 0
        progress.started_at = datetime.utcnow()
        progress.completed_at = None
        progress.message = "Checking verified Blu-ray releases for strict UHD upgrades..."
        db.commit()

        settings = get_settings()
        for index, release in enumerate(eligible, start=1):
            if _stop_event.is_set():
                raise ScanCancelled("Upgrade scan stopped by user.")

            current = release.matched_release or release.folder_name
            progress.current_release = current
            progress.message = "Resolving IMDb ID and searching SRRDB..."
            db.commit()

            result = db.scalar(select(UpgradeResult).where(UpgradeResult.release_id == release.id))
            if result is None:
                result = UpgradeResult(release_id=release.id, current_release=current)
                db.add(result)
                db.flush()
            else:
                result.current_release = current
                db.execute(delete(UpgradeCandidate).where(UpgradeCandidate.upgrade_result_id == result.id))

            imdb_id, candidates, error = await find_uhd_upgrades(
                current,
                settings.srrdb_delay_seconds,
                stop_requested=_stop_event.is_set,
            )
            result.imdb_id = imdb_id
            result.checked_at = datetime.utcnow()
            result.error_message = error
            result.candidate_count = len(candidates)

            if error:
                result.status = "api_error"
                progress.api_error_count += 1
            elif not imdb_id:
                result.status = "imdb_unavailable"
                progress.imdb_missing_count += 1
            elif candidates:
                result.status = "upgrade_available"
                progress.upgrades_found += 1
                for candidate in candidates:
                    db.add(UpgradeCandidate(
                        upgrade_result_id=result.id,
                        release_name=candidate.release_name,
                        srrdb_url=candidate.url,
                    ))
            else:
                result.status = "no_upgrade"
                progress.no_upgrade_count += 1

            progress.processed_count = index
            run.checked_count = index
            run.upgrades_found = progress.upgrades_found
            run.no_upgrade_count = progress.no_upgrade_count
            run.imdb_missing_count = progress.imdb_missing_count
            run.api_error_count = progress.api_error_count
            db.commit()

        completed = datetime.utcnow()
        run.status = "completed"
        run.completed_at = completed
        progress.is_running = False
        progress.phase = "complete"
        progress.current_release = None
        progress.completed_at = completed
        progress.message = f"Upgrade scan complete. {progress.upgrades_found} upgrade(s) found."
        db.commit()
    except ScanCancelled:
        if run:
            run.status = "stopped"
            run.completed_at = datetime.utcnow()
        progress = _progress(db)
        progress.is_running = False
        progress.phase = "stopped"
        progress.current_release = None
        progress.completed_at = datetime.utcnow()
        progress.message = "Upgrade scan stopped by user."
        db.commit()
    except Exception as exc:
        logger.exception("Collection Upgrade scan failed")
        if run:
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = datetime.utcnow()
        progress = _progress(db)
        progress.is_running = False
        progress.phase = "failed"
        progress.current_release = None
        progress.completed_at = datetime.utcnow()
        progress.message = f"Upgrade scan failed: {exc}"
        db.commit()
    finally:
        db.close()
        _stop_event.clear()
        _upgrade_lock.release()


def run_upgrade_scan() -> None:
    asyncio.run(_run())
