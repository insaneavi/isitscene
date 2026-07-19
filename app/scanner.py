from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime

from sqlalchemy import select

from .config import MOVIES_PATH, SYSTEM_FOLDERS
from .database import Release, ScanRun, SessionLocal
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


async def _scan_async() -> None:
    if not _scan_lock.acquire(blocking=False):
        logger.info("A scan is already running; request ignored.")
        return

    db = SessionLocal()
    run = ScanRun()
    db.add(run)
    db.commit()

    try:
        if not MOVIES_PATH.exists() or not MOVIES_PATH.is_dir():
            raise RuntimeError(
                f"Movie path is unavailable: {MOVIES_PATH}"
            )

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

        run.skipped_folders = (
            len(all_directories) - len(folder_names)
        )

        now = datetime.utcnow()
        current = set(folder_names)

        releases = db.scalars(select(Release)).all()
        by_name = {
            release.folder_name: release
            for release in releases
        }

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
            if (
                release.folder_name not in current
                and release.is_present
            ):
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

        for release in to_check:
            status, matched, error = await check_exact_release(
                release.folder_name,
                settings.srrdb_delay_seconds,
            )
            release.status = status
            release.matched_release = matched
            release.error_message = error
            release.last_checked = datetime.utcnow()
            db.commit()

        present_releases = db.scalars(
            select(Release).where(
                Release.is_present.is_(True),
                Release.ignored.is_(False),
            )
        ).all()

        run.completed_at = datetime.utcnow()
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
        db.commit()

    except Exception as exc:
        logger.exception("Scan failed")
        run.completed_at = datetime.utcnow()
        run.status = "failed"
        run.error_message = str(exc)
        db.commit()
    finally:
        db.close()
        _scan_lock.release()


def run_scan() -> None:
    asyncio.run(_scan_async())
