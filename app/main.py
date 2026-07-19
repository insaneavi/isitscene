from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .config import (
    APP_NAME,
    SCAN_INTERVAL_HOURS,
    SKIP_HIDDEN_SYSTEM_FOLDERS,
)
from .database import Release, ScanRun, SessionLocal, init_db
from .scanner import run_scan

logging.basicConfig(level=logging.INFO)
scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(
        run_scan,
        "interval",
        hours=SCAN_INTERVAL_HOURS,
        id="daily",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "application": APP_NAME,
        "skip_hidden_system_folders": SKIP_HIDDEN_SYSTEM_FOLDERS,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    db = SessionLocal()
    try:
        present = Release.is_present.is_(True)
        count_queries = {
            "total": (present,),
            "verified": (present, Release.status == "verified"),
            "not_found": (present, Release.status == "not_found"),
            "pending": (present, Release.status == "pending"),
            "api_error": (present, Release.status == "api_error"),
            "missing": (Release.is_present.is_(False),),
        }
        counts = {
            key: (
                db.scalar(
                    select(func.count())
                    .select_from(Release)
                    .where(*conditions)
                )
                or 0
            )
            for key, conditions in count_queries.items()
        }
        latest = db.scalars(
            select(ScanRun)
            .order_by(ScanRun.started_at.desc())
            .limit(1)
        ).first()

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "counts": counts,
                "latest": latest,
                "skip_hidden_system_folders": (
                    SKIP_HIDDEN_SYSTEM_FOLDERS
                ),
            },
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

        return templates.TemplateResponse(
            request=request,
            name="releases.html",
            context={
                "items": db.scalars(statement).all(),
                "q": q,
                "status": status,
            },
        )
    finally:
        db.close()


@app.post("/scan")
def scan(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scan)
    return RedirectResponse("/", status_code=303)
