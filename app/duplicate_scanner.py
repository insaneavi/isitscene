from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select

from .database import DuplicateProgress, DuplicateScan, Release, ScanProgress, SessionLocal, UpgradeProgress
from .settings_service import get_settings
from .srrdb import ScanCancelled, lookup_imdb_id, parse_release_name

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_stop = threading.Event()


def _progress(db):
    row = db.get(DuplicateProgress, 1)
    if row is None:
        row = DuplicateProgress(id=1)
        db.add(row)
        db.flush()
    return row


def request_duplicate_stop() -> None:
    _stop.set()
    db = SessionLocal()
    try:
        p = _progress(db)
        if p.is_running:
            p.phase = "stopping"
            p.message = "Stopping Duplicate Finder after the current SRRDB request..."
            db.commit()
    finally:
        db.close()


def recover_interrupted_duplicate_scan() -> None:
    db = SessionLocal()
    try:
        p = _progress(db)
        if not p.is_running:
            return
        now = datetime.utcnow()
        p.is_running = False
        p.phase = "interrupted"
        p.current_release = None
        p.completed_at = now
        p.message = "Previous Duplicate Finder scan was interrupted. You can safely restart it."
        for run in db.scalars(select(DuplicateScan).where(DuplicateScan.status == "running")).all():
            run.status = "interrupted"
            run.completed_at = now
        db.commit()
    finally:
        db.close()


def reset_duplicate_scan_state() -> bool:
    if not _lock.acquire(blocking=False):
        return False
    try:
        _stop.clear()
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            p = _progress(db)
            p.is_running = False
            p.phase = "reset"
            p.current_release = None
            p.completed_at = now
            p.message = "Duplicate Finder state reset. You can start a new scan."
            for run in db.scalars(select(DuplicateScan).where(DuplicateScan.status == "running")).all():
                run.status = "interrupted"
                run.completed_at = now
            db.commit()
            return True
        finally:
            db.close()
    finally:
        _lock.release()


def duplicate_group_count(db) -> int:
    releases = db.scalars(select(Release).where(Release.is_present.is_(True), Release.ignored.is_(False))).all()
    imdb_groups: dict[str, list[int]] = defaultdict(list)
    fallback: dict[str, list[int]] = defaultdict(list)
    for release in releases:
        if release.imdb_id:
            imdb_groups[release.imdb_id].append(release.id)
        elif release.movie_title and release.movie_year:
            fallback[f"{release.movie_title.casefold()}|{release.movie_year}"].append(release.id)
    return sum(1 for ids in imdb_groups.values() if len(ids) > 1) + sum(1 for ids in fallback.values() if len(ids) > 1)


async def _run() -> None:
    if not _lock.acquire(blocking=False):
        return
    _stop.clear()
    db = SessionLocal()
    run = None
    try:
        for cls in (ScanProgress, UpgradeProgress):
            active = db.get(cls, 1)
            if active and active.is_running:
                return
        releases = db.scalars(select(Release).where(Release.is_present.is_(True), Release.ignored.is_(False)).order_by(Release.folder_name)).all()
        run = DuplicateScan(status="running", eligible_count=len(releases))
        db.add(run)
        p = _progress(db)
        p.is_running = True; p.phase = "metadata"; p.current_release = None
        p.processed_count = 0; p.total_count = len(releases); p.cached_count = 0
        p.looked_up_count = 0; p.group_count = 0; p.error_count = 0
        p.started_at = datetime.utcnow(); p.completed_at = None
        p.message = "Loading cached IMDb metadata and checking new releases..."
        db.commit()
        delay = get_settings().srrdb_delay_seconds
        for index, release in enumerate(releases, 1):
            if _stop.is_set():
                raise ScanCancelled("Duplicate scan stopped by user.")
            source = release.matched_release or release.folder_name
            parsed = parse_release_name(source)
            release.movie_title = " ".join(parsed.title_tokens).strip() or None
            release.movie_year = parsed.year
            p.current_release = source
            if release.imdb_id and release.imdb_source_release == source:
                p.cached_count += 1
            elif release.imdb_lookup_status == "unavailable" and release.imdb_source_release == source:
                p.cached_count += 1
            else:
                p.message = "Looking up missing IMDb metadata in SRRDB..."
                imdb_id, error = await lookup_imdb_id(source, delay, _stop.is_set)
                release.imdb_id = imdb_id
                release.imdb_source_release = source
                release.imdb_checked_at = datetime.utcnow()
                release.imdb_error_message = error
                if error:
                    release.imdb_lookup_status = "api_error"
                    p.error_count += 1
                elif imdb_id:
                    release.imdb_lookup_status = "found"
                else:
                    release.imdb_lookup_status = "unavailable"
                p.looked_up_count += 1
            p.processed_count = index
            run.checked_count = index; run.cached_count = p.cached_count
            run.looked_up_count = p.looked_up_count; run.error_count = p.error_count
            db.commit()
        p.phase = "grouping"; p.current_release = None; p.message = "Grouping releases by cached IMDb ID..."
        db.commit()
        p.group_count = duplicate_group_count(db)
        run.group_count = p.group_count
        now = datetime.utcnow(); run.status = "completed"; run.completed_at = now
        p.is_running = False; p.phase = "complete"; p.completed_at = now
        p.message = f"Duplicate scan complete. {p.group_count} duplicate group(s) found."
        db.commit()
    except ScanCancelled:
        now = datetime.utcnow()
        if run: run.status = "stopped"; run.completed_at = now
        p = _progress(db); p.is_running = False; p.phase = "stopped"; p.current_release = None; p.completed_at = now; p.message = "Duplicate scan stopped by user."
        db.commit()
    except Exception as exc:
        logger.exception("Duplicate scan failed")
        now = datetime.utcnow()
        if run: run.status = "failed"; run.completed_at = now; run.error_message = str(exc)
        p = _progress(db); p.is_running = False; p.phase = "failed"; p.current_release = None; p.completed_at = now; p.message = f"Duplicate scan failed: {exc}"
        db.commit()
    finally:
        db.close(); _stop.clear(); _lock.release()


def run_duplicate_scan() -> None:
    asyncio.run(_run())
