from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime

from sqlalchemy import select

from .config import MOVIES_PATH, SYSTEM_FOLDERS
from .settings_service import get_settings
from .database import Release, ScanRun, SessionLocal
from .srrdb import check_exact_release

logger = logging.getLogger(__name__)
_scan_lock = threading.Lock()


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
        if not MOVIES_PATH.is_dir():
            raise RuntimeError(f"Movie path unavailable: {MOVIES_PATH}")

        names = sorted(
            entry.name
            for entry in MOVIES_PATH.iterdir()
            if entry.is_dir() and should_scan_folder(entry.name)
        )

        now = datetime.utcnow()
        current = set(names)
        existing = db.scalars(select(Release)).all()
        by_name = {release.folder_name: release for release in existing}
        new_count = 0
        missing_count = 0

        for name in names:
            release = by_name.get(name)
            if release is None:
                release = Release(
                    folder_name=name,
                    first_seen=now,
                    last_seen=now,
                    is_present=True,
                    status="pending",
                )
                db.add(release)
                new_count += 1
            else:
                release.last_seen = now
                release.is_present = True

        for release in existing:
            if release.folder_name not in current and release.is_present:
                release.is_present = False
                release.status = "missing"
                missing_count += 1

        db.commit()

        pending = db.scalars(
            select(Release)
            .where(
                Release.is_present.is_(True),
                Release.ignored.is_(False),
                Release.status.in_(["pending", "api_error"]),
            )
            .order_by(Release.folder_name)
        ).all()

        for release in pending:
            (
                release.status,
                release.matched_release,
                release.error_message,
            ) = await check_exact_release(release.folder_name)
            release.last_checked = datetime.utcnow()
            db.commit()

        present = db.scalars(
            select(Release).where(
                Release.is_present.is_(True),
                Release.ignored.is_(False),
            )
        ).all()

        run.completed_at = datetime.utcnow()
        run.status = "completed"
        run.folders_found = len(names)
        run.new_folders = new_count
        run.missing_folders = missing_count
        run.exact_matches = sum(
            release.status == "verified" for release in present
        )
        run.not_found = sum(
            release.status == "not_found" for release in present
        )
        run.api_errors = sum(
            release.status == "api_error" for release in present
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
