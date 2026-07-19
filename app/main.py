from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .config import APP_NAME
from .database import Release, ScanProgress, ScanRun, SessionLocal, init_db
from .scanner import run_scan
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
    scheduler.start()
    refresh_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


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
                    present, Release.status == "verified"
                )
            ) or 0,
            "not_found": db.scalar(
                select(func.count()).select_from(Release).where(
                    present, Release.status == "not_found"
                )
            ) or 0,
            "pending": db.scalar(
                select(func.count()).select_from(Release).where(
                    present, Release.status == "pending"
                )
            ) or 0,
            "api_error": db.scalar(
                select(func.count()).select_from(Release).where(
                    present, Release.status == "api_error"
                )
            ) or 0,
            "missing": db.scalar(
                select(func.count()).select_from(Release).where(
                    Release.is_present.is_(False)
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
                    "not_found_count": 0,
                    "api_error_count": 0,
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
                "not_found_count": progress.not_found_count,
                "api_error_count": progress.api_error_count,
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
def releases(request: Request, q: str = "", status: str = ""):
    db = SessionLocal()
    try:
        statement = select(Release).order_by(Release.folder_name)

        if q:
            statement = statement.where(
                Release.folder_name.ilike(f"%{q}%")
            )

        if status:
            statement = statement.where(Release.status == status)

        items = db.scalars(statement).all()

        return templates.TemplateResponse(
            request=request,
            name="releases.html",
            context={"releases": items, "q": q, "status": status},
        )
    finally:
        db.close()


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


@app.post("/scan")
def scan_now(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scan)
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
