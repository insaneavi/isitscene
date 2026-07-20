from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import quote_plus
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .config import APP_NAME
from .database import Release, ScanProgress, ScanRun, SessionLocal, init_db
from .scanner import (
    recover_interrupted_scan,
    refresh_library_changes,
    request_stop,
    run_scan,
)
from .settings_service import get_settings, save_settings

logging.basicConfig(level=logging.INFO)

scheduler = BackgroundScheduler()


def refresh_scheduler() -> None:
    settings = get_settings()

    existing = scheduler.get_job("scheduled_scan")
    if existing is not None:
        scheduler.remove_job("scheduled_scan")

    if settings.auto_scan_enabled:
        scheduler.add_job(
            run_scan,
            "interval",
            hours=settings.scan_interval_hours,
            id="scheduled_scan",
            replace_existing=True,
            max_instances=1,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    recover_interrupted_scan()
    scheduler.start()
    refresh_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


_RELEASE_YEAR_PATTERN = re.compile(
    r"(?<!\d)(?:19\d{2}|20\d{2}|21\d{2})(?!\d)"
)
_RELEASE_TECH_PATTERN = re.compile(
    r"(?i)(?:^|[._\s-])"
    r"(?:480[pi]|576[pi]|720p|1080[pi]|2160p|4k|"
    r"bluray|blu-ray|uhd|web-dl|webrip|hdtv|dvdrip|"
    r"bdrip|brrip|remux|repack|proper|x264|x265|"
    r"h264|h265|hevc|xvid|av1)"
    r"(?=$|[._\s-])"
)


def movie_title_from_release_name(folder_name: str) -> str:
    """Extract the readable movie title before year or technical tags."""
    name = folder_name.strip()
    boundaries = []

    year_match = _RELEASE_YEAR_PATTERN.search(name)
    tech_match = _RELEASE_TECH_PATTERN.search(name)

    if year_match:
        boundaries.append(year_match.start())
    if tech_match:
        boundaries.append(tech_match.start())

    if boundaries:
        name = name[:min(boundaries)]

    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" -")


def bluray_search_url(folder_name: str) -> str:
    title = movie_title_from_release_name(folder_name)
    return (
        "https://www.blu-ray.com/movies/search.php"
        f"?keyword={quote_plus(title)}&action=search"
    )


templates.env.globals["movie_title_from_release_name"] = (
    movie_title_from_release_name
)
templates.env.globals["bluray_search_url"] = bluray_search_url


@app.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "application": APP_NAME,
        "settings": {
            "skip_hidden_system_folders": (
                settings.skip_hidden_system_folders
            ),
            "auto_scan_enabled": settings.auto_scan_enabled,
            "scan_interval_hours": settings.scan_interval_hours,
            "srrdb_delay_seconds": settings.srrdb_delay_seconds,
        },
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    db = SessionLocal()
    try:
        present = Release.is_present.is_(True)

        counts = {
            "total": db.scalar(
                select(func.count()).select_from(Release).where(present)
            ) or 0,
            "verified": db.scalar(
                select(func.count()).select_from(Release).where(
                    present, Release.verification_status == "verified"
                )
            ) or 0,
            "unverified": db.scalar(
                select(func.count()).select_from(Release).where(
                    present, Release.verification_status == "unverified"
                )
            ) or 0,
            "pending": db.scalar(
                select(func.count()).select_from(Release).where(
                    present, Release.verification_status == "pending"
                )
            ) or 0,
            "missing": db.scalar(
                select(func.count()).select_from(Release).where(
                    Release.is_present.is_(False)
                )
            ) or 0,
            "review_pending": db.scalar(
                select(func.count()).select_from(Release).where(
                    present,
                    Release.verification_status == "unverified",
                    Release.review_status == "pending",
                )
            ) or 0,
            "review_keep": db.scalar(
                select(func.count()).select_from(Release).where(
                    present,
                    Release.verification_status == "unverified",
                    Release.review_status == "keep",
                )
            ) or 0,
            "review_replace": db.scalar(
                select(func.count()).select_from(Release).where(
                    present,
                    Release.verification_status == "unverified",
                    Release.review_status == "replace",
                )
            ) or 0,
            "candidate_found": db.scalar(
                select(func.count()).select_from(Release).where(
                    present,
                    Release.verification_status == "unverified",
                    Release.candidate_release.is_not(None),
                )
            ) or 0,
            "candidate_missing": db.scalar(
                select(func.count()).select_from(Release).where(
                    present,
                    Release.verification_status == "unverified",
                    Release.candidate_release.is_(None),
                )
            ) or 0,
        }

        latest_scan = db.scalars(
            select(ScanRun).order_by(ScanRun.started_at.desc()).limit(1)
        ).first()

        recent = db.scalars(
            select(Release).order_by(Release.first_seen.desc()).limit(10)
        ).all()

        recent_results = db.scalars(
            select(Release)
            .where(Release.last_checked.is_not(None))
            .order_by(Release.last_checked.desc())
            .limit(20)
        ).all()

        progress = db.get(ScanProgress, 1)

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "counts": counts,
                "latest_scan": latest_scan,
                "recent": recent,
                "recent_results": recent_results,
                "progress": progress,
                "settings": get_settings(),
            },
        )
    finally:
        db.close()


@app.get("/api/scan-status")
def scan_status():
    db = SessionLocal()
    try:
        progress = db.get(ScanProgress, 1)
        if progress is None:
            return JSONResponse(
                {
                    "is_running": False,
                    "phase": "idle",
                    "current_release": None,
                    "processed_count": 0,
                    "total_count": 0,
                    "verified_count": 0,
                    "unverified_count": 0,
                    "skipped_count": 0,
                    "message": "No scan has run yet.",
                    "started_at": None,
                    "completed_at": None,
                }
            )

        return JSONResponse(
            {
                "is_running": progress.is_running,
                "phase": progress.phase,
                "current_release": progress.current_release,
                "processed_count": progress.processed_count,
                "total_count": progress.total_count,
                "verified_count": progress.verified_count,
                "unverified_count": progress.unverified_count,
                "skipped_count": progress.skipped_count,
                "message": progress.message,
                "started_at": (
                    progress.started_at.isoformat()
                    if progress.started_at
                    else None
                ),
                "completed_at": (
                    progress.completed_at.isoformat()
                    if progress.completed_at
                    else None
                ),
            }
        )
    finally:
        db.close()


@app.get("/releases", response_class=HTMLResponse)
def releases(
    request: Request,
    q: str = "",
    verification: str = "",
    inventory: str = "",
):
    db = SessionLocal()
    try:
        statement = select(Release).order_by(Release.folder_name)

        if q:
            statement = statement.where(
                Release.folder_name.ilike(f"%{q}%")
            )

        if verification:
            statement = statement.where(
                Release.verification_status == verification
            )

        if inventory == "present":
            statement = statement.where(Release.is_present.is_(True))
        elif inventory == "removed":
            statement = statement.where(Release.is_present.is_(False))

        items = db.scalars(statement).all()

        return templates.TemplateResponse(
            request=request,
            name="releases.html",
            context={
                "items": items,
                "q": q,
                "verification": verification,
                "inventory": inventory,
            },
        )
    finally:
        db.close()



@app.get("/collection-review", response_class=HTMLResponse)
def collection_review(
    request: Request,
    q: str = "",
    review_status: str = "",
    candidate_status: str = "",
):
    db = SessionLocal()
    try:
        statement = (
            select(Release)
            .where(
                Release.is_present.is_(True),
                Release.verification_status == "unverified",
            )
            .order_by(Release.folder_name)
        )

        if q:
            statement = statement.where(
                Release.folder_name.ilike(f"%{q}%")
            )

        if review_status:
            statement = statement.where(
                Release.review_status == review_status
            )

        if candidate_status == "found":
            statement = statement.where(
                Release.candidate_release.is_not(None)
            )
        elif candidate_status == "none":
            statement = statement.where(
                Release.candidate_release.is_(None)
            )

        items = db.scalars(statement).all()

        return templates.TemplateResponse(
            request=request,
            name="collection_review.html",
            context={
                "items": items,
                "q": q,
                "review_status": review_status,
                "candidate_status": candidate_status,
            },
        )
    finally:
        db.close()


@app.post("/collection-review/{release_id}")
def update_collection_review(
    release_id: int,
    review_status: str = Form("pending"),
    review_comment: str = Form(""),
    q: str = Form(""),
    active_filter: str = Form(""),
    candidate_filter: str = Form(""),
):
    allowed_statuses = {"pending", "keep", "replace", "ignored"}
    selected_status = (
        review_status
        if review_status in allowed_statuses
        else "pending"
    )

    db = SessionLocal()
    try:
        release = db.get(Release, release_id)
        if (
            release is not None
            and release.is_present
            and release.verification_status == "unverified"
        ):
            release.review_status = selected_status
            release.review_comment = review_comment.strip()
            release.last_reviewed = datetime.utcnow()
            db.commit()
    finally:
        db.close()

    query_parts = []
    if q:
        query_parts.append(f"q={q}")
    if active_filter:
        query_parts.append(f"review_status={active_filter}")

    destination = "/collection-review"
    if query_parts:
        from urllib.parse import urlencode
        values = {}
        if q:
            values["q"] = q
        if active_filter:
            values["review_status"] = active_filter
        if candidate_filter:
            values["candidate_status"] = candidate_filter
        destination += "?" + urlencode(values)

    return RedirectResponse(destination, status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"settings": get_settings()},
    )


@app.post("/settings")
def update_settings(
    skip_hidden_system_folders: str | None = Form(None),
    auto_scan_enabled: str | None = Form(None),
    scan_interval_hours: int = Form(24),
    srrdb_delay_seconds: float = Form(1.5),
):
    save_settings(
        skip_hidden_system_folders=(
            skip_hidden_system_folders is not None
        ),
        auto_scan_enabled=(auto_scan_enabled is not None),
        scan_interval_hours=scan_interval_hours,
        srrdb_delay_seconds=srrdb_delay_seconds,
    )
    refresh_scheduler()
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/collection-review/refresh")
def refresh_collection_library(background_tasks: BackgroundTasks):
    background_tasks.add_task(refresh_library_changes)
    return RedirectResponse(
        "/collection-review?refresh_started=1",
        status_code=303,
    )


@app.post("/scan/start")
def start_scan(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scan)
    return RedirectResponse("/", status_code=303)


@app.post("/scan/stop")
def stop_scan():
    request_stop()
    return RedirectResponse("/", status_code=303)


@app.post("/releases/{release_id}/ignore")
def toggle_ignore(release_id: int):
    db = SessionLocal()
    try:
        release = db.get(Release, release_id)
        if release:
            release.ignored = not release.ignored
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/releases", status_code=303)


@app.post("/releases/{release_id}/notes")
def update_notes(release_id: int, notes: str = Form("")):
    db = SessionLocal()
    try:
        release = db.get(Release, release_id)
        if release:
            release.notes = notes
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/releases", status_code=303)
