from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select

from .config import APP_NAME, APP_VERSION, BUILD_DATE, GIT_COMMIT, DATABASE_VERSION
from .database import (
    DuplicateProgress, DuplicateReview, DuplicateScan, MovieList, MovieListCacheState, MovieListItem, MovieListMatch, MovieListSync, Release, ScanProgress,
    ScanRun, SessionLocal, UpgradeCandidate, UpgradeProgress, UpgradeResult,
    UpgradeScan, init_db,
)
from .scanner import (
    recover_interrupted_scan,
    refresh_library_changes,
    request_stop,
    run_scan,
)
from .settings_service import get_settings, save_settings
from .upgrade_scanner import (
    recover_interrupted_upgrade_scan,
    reclassify_upgrade_metadata_errors,
    request_upgrade_stop,
    reset_upgrade_scan_state,
    run_upgrade_scan,
)
from .duplicate_scanner import (
    duplicate_group_count,
    recover_interrupted_duplicate_scan,
    request_duplicate_stop,
    reset_duplicate_scan_state,
    run_duplicate_scan,
)
from .srrdb import parse_release_name
from .movie_lists import ensure_bundled_lists, import_bundled_list
from .movie_list_cache import rebuild_movie_list_cache

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
    ensure_bundled_lists()
    rebuild_movie_list_cache()
    recover_interrupted_scan()
    recover_interrupted_upgrade_scan()
    reclassify_upgrade_metadata_errors()
    recover_interrupted_duplicate_scan()
    scheduler.start()
    refresh_scheduler()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["build_date"] = BUILD_DATE
templates.env.globals["git_commit"] = GIT_COMMIT


def local_datetime(value: datetime | None) -> datetime | None:
    """Treat persisted naive datetimes as UTC and convert for display."""
    if value is None:
        return None
    settings = get_settings()
    source = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return source.astimezone(ZoneInfo(settings.timezone))


def format_datetime(value: datetime | None, include_seconds: bool = False) -> str:
    localized = local_datetime(value)
    if localized is None:
        return "—"
    settings = get_settings()
    if settings.time_format == "24h":
        pattern = "%b %d, %Y %H:%M:%S %Z" if include_seconds else "%b %d, %Y %H:%M %Z"
    else:
        pattern = "%b %d, %Y %-I:%M:%S %p %Z" if include_seconds else "%b %d, %Y %-I:%M %p %Z"
    return localized.strftime(pattern)


def format_date(value: datetime | None) -> str:
    localized = local_datetime(value)
    return localized.strftime("%b %d, %Y") if localized else "—"


def current_timezone_name() -> str:
    return get_settings().timezone


templates.env.globals["format_datetime"] = format_datetime
templates.env.globals["format_date"] = format_date
templates.env.globals["current_timezone_name"] = current_timezone_name


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


def normalize_movie_title(value: str) -> str:
    """Normalize punctuation and spacing for conservative title matching."""
    value = value.casefold()
    value = value.replace("&", " and ")
    value = re.sub(r"['’]", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _unique_title_matches(releases: list[Release]) -> dict[str, Release]:
    grouped: dict[str, list[Release]] = {}
    for release in releases:
        raw_title = release.movie_title or movie_title_from_release_name(
            release.matched_release or release.folder_name
        )
        normalized = normalize_movie_title(raw_title)
        if normalized:
            grouped.setdefault(normalized, []).append(release)
    return {
        title: matches[0]
        for title, matches in grouped.items()
        if len(matches) == 1
    }


def _fuzzy_unique_title_match(
    item_title: str,
    unique_release_titles: dict[str, Release],
) -> Release | None:
    """Return only a single, extremely close title match."""
    normalized_item = normalize_movie_title(item_title)
    if not normalized_item:
        return None

    scores = sorted(
        (
            SequenceMatcher(None, normalized_item, release_title).ratio(),
            release_title,
            release,
        )
        for release_title, release in unique_release_titles.items()
    )
    if not scores:
        return None

    best_score, _, best_release = scores[-1]
    second_score = scores[-2][0] if len(scores) > 1 else 0.0
    if best_score >= 0.98 and best_score > second_score:
        return best_release
    return None


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


@app.get("/api/version")
def api_version():
    return {
        "version": APP_VERSION,
        "build_date": BUILD_DATE,
        "git_commit": GIT_COMMIT,
        "database_version": DATABASE_VERSION,
        "timezone": get_settings().timezone,
        "time_format": get_settings().time_format,
    }


@app.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "ok",
        "application": APP_NAME,
        "version": APP_VERSION,
        "build_date": BUILD_DATE,
        "git_commit": GIT_COMMIT,
        "database_version": DATABASE_VERSION,
        "settings": {
            "skip_hidden_system_folders": (
                settings.skip_hidden_system_folders
            ),
            "auto_scan_enabled": settings.auto_scan_enabled,
            "scan_interval_hours": settings.scan_interval_hours,
            "srrdb_delay_seconds": settings.srrdb_delay_seconds,
            "timezone": settings.timezone,
            "time_format": settings.time_format,
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
        last_successful_scan = db.scalars(
            select(ScanRun)
            .where(
                ScanRun.status == "completed",
                ScanRun.completed_at.is_not(None),
            )
            .order_by(ScanRun.completed_at.desc())
            .limit(1)
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
                "last_successful_scan": last_successful_scan,
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
        last_successful_scan = db.scalars(
            select(ScanRun)
            .where(
                ScanRun.status == "completed",
                ScanRun.completed_at.is_not(None),
            )
            .order_by(ScanRun.completed_at.desc())
            .limit(1)
        ).first()
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
                    "last_successful_at": (
                        last_successful_scan.completed_at.isoformat()
                        if last_successful_scan else None
                    ),
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
                "last_successful_at": (
                    last_successful_scan.completed_at.isoformat()
                    if last_successful_scan else None
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



@app.get("/imdb-metadata-review", response_class=HTMLResponse)
def imdb_metadata_review(
    request: Request,
    q: str = "",
    status: str = "needs_review",
):
    db = SessionLocal()
    try:
        statement = (
            select(UpgradeResult, Release)
            .join(Release, Release.id == UpgradeResult.release_id)
            .where(Release.is_present.is_(True))
            .order_by(UpgradeResult.current_release)
        )
        if status == "needs_review":
            statement = statement.where(
                UpgradeResult.status.in_(
                    ["imdb_metadata_missing", "imdb_unavailable", "api_error"]
                )
            )
        elif status == "metadata_missing":
            statement = statement.where(
                UpgradeResult.status == "imdb_metadata_missing"
            )
        elif status == "unavailable":
            statement = statement.where(
                UpgradeResult.status == "imdb_unavailable"
            )
        elif status == "api_error":
            statement = statement.where(
                UpgradeResult.status == "api_error"
            )
        elif status == "manual":
            statement = statement.where(
                Release.imdb_manual_override.is_(True)
            )

        if q:
            statement = statement.where(
                UpgradeResult.current_release.ilike(f"%{q}%")
            )

        records = db.execute(statement).all()
        rows = []
        for result, release in records:
            parsed = parse_release_name(
                release.matched_release or release.folder_name
            )
            title = release.movie_title or movie_title_from_release_name(
                release.matched_release or release.folder_name
            )
            year = release.movie_year or parsed.year
            search_terms = f"{title} {year or ''}".strip()
            rows.append({
                "result": result,
                "release": release,
                "title": title,
                "year": year,
                "imdb_search_url": (
                    "https://www.imdb.com/find/?q="
                    + quote_plus(search_terms)
                    + "&s=tt&ttype=ft"
                ),
            })

        counts = {
            "metadata_missing": db.scalar(
                select(func.count()).select_from(UpgradeResult).where(
                    UpgradeResult.status == "imdb_metadata_missing"
                )
            ) or 0,
            "unavailable": db.scalar(
                select(func.count()).select_from(UpgradeResult).where(
                    UpgradeResult.status == "imdb_unavailable"
                )
            ) or 0,
            "api_error": db.scalar(
                select(func.count()).select_from(UpgradeResult).where(
                    UpgradeResult.status == "api_error"
                )
            ) or 0,
            "manual": db.scalar(
                select(func.count()).select_from(Release).where(
                    Release.is_present.is_(True),
                    Release.imdb_manual_override.is_(True),
                )
            ) or 0,
        }

        return templates.TemplateResponse(
            request=request,
            name="imdb_metadata_review.html",
            context={
                "rows": rows,
                "q": q,
                "status": status,
                "counts": counts,
            },
        )
    finally:
        db.close()


@app.post("/imdb-metadata-review/save")
async def save_imdb_metadata_review(request: Request):
    form = await request.form()
    return_q = str(form.get("q", ""))
    return_status = str(form.get("status", "needs_review"))
    saved = 0
    invalid = 0

    db = SessionLocal()
    try:
        for key, raw_value in form.multi_items():
            if not key.startswith("imdb_"):
                continue
            try:
                release_id = int(key.removeprefix("imdb_"))
            except ValueError:
                continue

            value = str(raw_value).strip().lower()
            if not value:
                continue
            if not re.fullmatch(r"tt\d{7,10}", value):
                invalid += 1
                continue

            release = db.get(Release, release_id)
            if release is None or not release.is_present:
                continue

            if not release.imdb_manual_override:
                release.imdb_srrdb_id = release.imdb_id
            release.imdb_id = value
            release.imdb_manual_override = True
            release.imdb_manual_updated_at = datetime.utcnow()
            release.imdb_lookup_status = "manual"
            release.imdb_error_message = None

            result = db.scalar(
                select(UpgradeResult).where(
                    UpgradeResult.release_id == release.id
                )
            )
            if result is not None:
                result.imdb_id = value
                result.status = "metadata_resolved"
                result.error_message = None
                result.checked_at = datetime.utcnow()
            saved += 1

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if saved:
        rebuild_movie_list_cache()

    from urllib.parse import urlencode
    params = {
        "status": return_status,
        "q": return_q,
        "saved": saved,
    }
    if invalid:
        params["invalid"] = invalid
    return RedirectResponse(
        "/imdb-metadata-review?" + urlencode(params),
        status_code=303,
    )


@app.post("/imdb-metadata-review/{release_id}/clear")
def clear_imdb_metadata_review_override(release_id: int):
    db = SessionLocal()
    try:
        release = db.get(Release, release_id)
        if release is not None:
            release.imdb_id = release.imdb_srrdb_id
            release.imdb_manual_override = False
            release.imdb_manual_updated_at = datetime.utcnow()
            release.imdb_lookup_status = (
                "found" if release.imdb_id else "not_checked"
            )
            release.imdb_error_message = None
            result = db.scalar(
                select(UpgradeResult).where(
                    UpgradeResult.release_id == release.id
                )
            )
            if result is not None:
                result.imdb_id = release.imdb_id
                result.status = (
                    "not_checked" if release.imdb_id else "imdb_unavailable"
                )
            db.commit()
    finally:
        db.close()
    rebuild_movie_list_cache()
    return RedirectResponse(
        "/imdb-metadata-review?status=manual",
        status_code=303,
    )


@app.get("/collection-upgrade", response_class=HTMLResponse)
def collection_upgrade(request: Request, q: str = "", status: str = ""):
    db = SessionLocal()
    try:
        statement = select(UpgradeResult).order_by(UpgradeResult.current_release)
        if q:
            statement = statement.where(UpgradeResult.current_release.ilike(f"%{q}%"))
        if status:
            statement = statement.where(UpgradeResult.status == status)
        results = db.scalars(statement).all()
        rows = []
        for result in results:
            candidates = db.scalars(
                select(UpgradeCandidate)
                .where(UpgradeCandidate.upgrade_result_id == result.id)
                .order_by(UpgradeCandidate.release_name)
            ).all()
            rows.append((result, candidates))
        latest_scan = db.scalars(
            select(UpgradeScan).order_by(UpgradeScan.started_at.desc()).limit(1)
        ).first()
        last_successful_scan = db.scalars(
            select(UpgradeScan)
            .where(
                UpgradeScan.status == "completed",
                UpgradeScan.completed_at.is_not(None),
            )
            .order_by(UpgradeScan.completed_at.desc())
            .limit(1)
        ).first()
        progress = db.get(UpgradeProgress, 1)
        return templates.TemplateResponse(
            request=request,
            name="collection_upgrade.html",
            context={
                "rows": rows,
                "q": q,
                "status": status,
                "latest_scan": latest_scan,
                "last_successful_scan": last_successful_scan,
                "progress": progress,
            },
        )
    finally:
        db.close()


@app.get("/api/upgrade-status")
def upgrade_status():
    db = SessionLocal()
    try:
        p = db.get(UpgradeProgress, 1)
        last_successful_scan = db.scalars(
            select(UpgradeScan)
            .where(
                UpgradeScan.status == "completed",
                UpgradeScan.completed_at.is_not(None),
            )
            .order_by(UpgradeScan.completed_at.desc())
            .limit(1)
        ).first()
        return JSONResponse({
            "is_running": bool(p and p.is_running),
            "phase": p.phase if p else "idle",
            "current_release": p.current_release if p else None,
            "processed_count": p.processed_count if p else 0,
            "total_count": p.total_count if p else 0,
            "upgrades_found": p.upgrades_found if p else 0,
            "no_upgrade_count": p.no_upgrade_count if p else 0,
            "imdb_missing_count": p.imdb_missing_count if p else 0,
            "api_error_count": p.api_error_count if p else 0,
            "message": p.message if p else "No upgrade scan has run yet.",
            "can_force_reset": bool(p and p.is_running),
            "last_successful_at": (
                last_successful_scan.completed_at.isoformat()
                if last_successful_scan else None
            ),
        })
    finally:
        db.close()


@app.post("/upgrade/start")
def start_upgrade_scan(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_upgrade_scan)
    return RedirectResponse("/collection-upgrade?started=1", status_code=303)


@app.post("/upgrade/stop")
def stop_upgrade_scan():
    request_upgrade_stop()
    return RedirectResponse("/collection-upgrade?stopping=1", status_code=303)


@app.post("/upgrade/reset")
def reset_upgrade_scan():
    reset = reset_upgrade_scan_state()
    suffix = "reset=1" if reset else "reset_failed=1"
    return RedirectResponse(f"/collection-upgrade?{suffix}", status_code=303)


@app.get("/duplicate-finder", response_class=HTMLResponse)
def duplicate_finder(request: Request, status: str = ""):
    db = SessionLocal()
    try:
        releases = db.scalars(
            select(Release).where(
                Release.is_present.is_(True), Release.ignored.is_(False)
            ).order_by(Release.folder_name)
        ).all()
        groups: dict[str, dict] = {}
        for release in releases:
            parsed = parse_release_name(release.matched_release or release.folder_name)
            if release.imdb_id:
                key = f"imdb:{release.imdb_id}"
                confidence = "confirmed"
                title = " ".join(parsed.title_tokens).title() or release.folder_name
                year = parsed.year
            elif release.movie_title and release.movie_year:
                key = f"title:{release.movie_title.casefold()}|{release.movie_year}"
                confidence = "possible"
                title = release.movie_title.title()
                year = release.movie_year
            else:
                continue
            group = groups.setdefault(key, {"key": key, "title": title, "year": year, "imdb_id": release.imdb_id, "confidence": confidence, "releases": []})
            group["releases"].append({"release": release, "parsed": parsed})
        rows = []
        for key, group in groups.items():
            if len(group["releases"]) < 2:
                continue
            editions = {tuple(sorted(item["parsed"].flags)) for item in group["releases"]}
            group["kind"] = "edition_variant" if len(editions) > 1 and any(editions) else group["confidence"]
            review = db.get(DuplicateReview, key)
            group["review"] = review
            if status and (review.review_status if review else "unreviewed") != status:
                continue
            rows.append(group)
        rows.sort(key=lambda g: ((g["title"] or "").casefold(), g["year"] or ""))
        last_successful_scan = db.scalars(
            select(DuplicateScan)
            .where(
                DuplicateScan.status == "completed",
                DuplicateScan.completed_at.is_not(None),
            )
            .order_by(DuplicateScan.completed_at.desc())
            .limit(1)
        ).first()
        return templates.TemplateResponse(
            request=request,
            name="duplicate_finder.html",
            context={
                "groups": rows,
                "progress": db.get(DuplicateProgress, 1),
                "status": status,
                "last_successful_scan": last_successful_scan,
            },
        )
    finally:
        db.close()


@app.get("/api/duplicate-status")
def duplicate_status():
    db = SessionLocal()
    try:
        p = db.get(DuplicateProgress, 1)
        last_successful_scan = db.scalars(
            select(DuplicateScan)
            .where(
                DuplicateScan.status == "completed",
                DuplicateScan.completed_at.is_not(None),
            )
            .order_by(DuplicateScan.completed_at.desc())
            .limit(1)
        ).first()
        return JSONResponse({
            "is_running": bool(p and p.is_running), "phase": p.phase if p else "idle",
            "current_release": p.current_release if p else None,
            "processed_count": p.processed_count if p else 0, "total_count": p.total_count if p else 0,
            "cached_count": p.cached_count if p else 0, "looked_up_count": p.looked_up_count if p else 0,
            "group_count": p.group_count if p else 0, "error_count": p.error_count if p else 0,
            "message": p.message if p else "No duplicate scan has run yet.",
            "last_successful_at": (
                last_successful_scan.completed_at.isoformat()
                if last_successful_scan else None
            ),
        })
    finally:
        db.close()


@app.post("/duplicate/start")
def start_duplicate_scan(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_duplicate_scan)
    return RedirectResponse("/duplicate-finder?started=1", status_code=303)


@app.post("/duplicate/stop")
def stop_duplicate_scan():
    request_duplicate_stop()
    return RedirectResponse("/duplicate-finder?stopping=1", status_code=303)


@app.post("/duplicate/reset")
def reset_duplicate_scan():
    ok = reset_duplicate_scan_state()
    return RedirectResponse(f"/duplicate-finder?{'reset=1' if ok else 'reset_failed=1'}", status_code=303)


@app.post("/duplicate/{group_key:path}/review")
def review_duplicate(group_key: str, review_status: str = Form(...), comment: str = Form("")):
    db = SessionLocal()
    try:
        row = db.get(DuplicateReview, group_key)
        if row is None:
            row = DuplicateReview(group_key=group_key)
            db.add(row)
        row.review_status = review_status
        row.comment = comment.strip()
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/duplicate-finder", status_code=303)


@app.get("/movie-lists", response_class=HTMLResponse)
def movie_lists_page(
    request: Request,
    list_key: str = "imdb-top-100",
    view: str = "all",
    q: str = "",
):
    db = SessionLocal()
    try:
        lists = db.scalars(select(MovieList).order_by(MovieList.name)).all()
        available_keys = {item.key for item in lists}
        if list_key not in available_keys and lists:
            list_key = lists[0].key

        movie_list = db.get(MovieList, list_key)
        sync = db.get(MovieListSync, list_key)
        cache_state = db.get(MovieListCacheState, 1)

        items = db.scalars(
            select(MovieListItem)
            .where(MovieListItem.list_key == list_key)
            .order_by(MovieListItem.rank)
        ).all()
        item_ids = [item.id for item in items]

        matches = {}
        if item_ids:
            matches = {
                match.item_id: match
                for match in db.scalars(
                    select(MovieListMatch).where(
                        MovieListMatch.item_id.in_(item_ids)
                    )
                ).all()
            }

        release_ids = {
            match.release_id
            for match in matches.values()
            if match.release_id is not None
        }
        releases = {}
        if release_ids:
            releases = {
                release.id: release
                for release in db.scalars(
                    select(Release).where(Release.id.in_(release_ids))
                ).all()
            }

        all_rows = []
        for item in items:
            match = matches.get(item.id)
            release = (
                releases.get(match.release_id)
                if match and match.release_id is not None
                else None
            )
            all_rows.append({
                "item": item,
                "release": release,
                "match_method": match.match_method if match else None,
            })

        total = len(all_rows)
        owned = sum(1 for row in all_rows if row["release"])
        missing = total - owned

        rows = all_rows
        if view == "owned":
            rows = [row for row in rows if row["release"]]
        elif view == "missing":
            rows = [row for row in rows if not row["release"]]
        if q:
            query = q.casefold().strip()
            rows = [
                row for row in rows
                if query in row["item"].title.casefold()
                or query == str(row["item"].year)
            ]

        summary_counts = {
            key: {"total": 0, "owned": 0}
            for key in available_keys
        }
        for item_list_key, release_id in db.execute(
            select(
                MovieListMatch.list_key,
                MovieListMatch.release_id,
            )
        ).all():
            if item_list_key in summary_counts:
                summary_counts[item_list_key]["total"] += 1
                if release_id is not None:
                    summary_counts[item_list_key]["owned"] += 1

        summaries = []
        for available in lists:
            counts = summary_counts.get(
                available.key,
                {"total": 0, "owned": 0},
            )
            summaries.append({
                "list": available,
                "total": counts["total"],
                "owned": counts["owned"],
                "completion": round(
                    counts["owned"] / counts["total"] * 100,
                    1,
                ) if counts["total"] else 0,
            })

        return templates.TemplateResponse(
            request=request,
            name="movie_lists.html",
            context={
                "lists": lists,
                "summaries": summaries,
                "movie_list": movie_list,
                "sync": sync,
                "cache_state": cache_state,
                "rows": rows,
                "total": total,
                "owned": owned,
                "missing": missing,
                "completion": round((owned / total * 100), 1) if total else 0,
                "view": view,
                "q": q,
                "list_key": list_key,
            },
        )
    finally:
        db.close()


@app.post("/movie-lists/{list_key}/reload")
def reload_movie_list(list_key: str):
    import_bundled_list(list_key)
    return RedirectResponse(
        f"/movie-lists?list_key={list_key}&reloaded=1",
        status_code=303,
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    db = SessionLocal()
    try:
        removed_release_count = db.scalar(
            select(func.count())
            .select_from(Release)
            .where(Release.is_present.is_(False))
        ) or 0
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "settings": get_settings(),
                "removed_release_count": removed_release_count,
            },
        )
    finally:
        db.close()


@app.post("/settings/purge-removed")
def purge_removed_inventory(
    confirmation: str = Form(""),
):
    if confirmation.strip().upper() != "PURGE":
        return RedirectResponse(
            "/settings?purge_error=confirmation",
            status_code=303,
        )

    db = SessionLocal()
    try:
        active_progress = (
            db.get(ScanProgress, 1),
            db.get(UpgradeProgress, 1),
            db.get(DuplicateProgress, 1),
        )
        if any(progress and progress.is_running for progress in active_progress):
            return RedirectResponse(
                "/settings?purge_error=scan_running",
                status_code=303,
            )

        removed_ids = list(
            db.scalars(
                select(Release.id).where(Release.is_present.is_(False))
            ).all()
        )
        if not removed_ids:
            return RedirectResponse(
                "/settings?purged=0",
                status_code=303,
            )

        upgrade_result_ids = list(
            db.scalars(
                select(UpgradeResult.id).where(
                    UpgradeResult.release_id.in_(removed_ids)
                )
            ).all()
        )
        if upgrade_result_ids:
            db.execute(
                delete(UpgradeCandidate).where(
                    UpgradeCandidate.upgrade_result_id.in_(upgrade_result_ids)
                )
            )
        db.execute(
            delete(UpgradeResult).where(
                UpgradeResult.release_id.in_(removed_ids)
            )
        )
        db.execute(
            delete(Release).where(Release.id.in_(removed_ids))
        )
        db.commit()
        rebuild_movie_list_cache()

        return RedirectResponse(
            f"/settings?purged={len(removed_ids)}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        logging.exception("Unable to purge removed inventory records")
        return RedirectResponse(
            "/settings?purge_error=failed",
            status_code=303,
        )
    finally:
        db.close()


@app.post("/settings")
def update_settings(
    skip_hidden_system_folders: str | None = Form(None),
    auto_scan_enabled: str | None = Form(None),
    scan_interval_hours: int = Form(24),
    srrdb_delay_seconds: float = Form(1.5),
    timezone_name: str = Form("America/New_York"),
    time_format: str = Form("12h"),
):
    save_settings(
        skip_hidden_system_folders=(
            skip_hidden_system_folders is not None
        ),
        auto_scan_enabled=(auto_scan_enabled is not None),
        scan_interval_hours=scan_interval_hours,
        srrdb_delay_seconds=srrdb_delay_seconds,
        timezone=timezone_name,
        time_format=time_format,
    )
    refresh_scheduler()
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/scan/refresh-library")
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


@app.post("/releases/{release_id}/imdb")
def update_release_imdb(
    release_id: int,
    imdb_id: str = Form(""),
    action: str = Form("save"),
    return_to: str = Form("/releases"),
):
    """Set or clear a persistent manual IMDb override for one release."""
    safe_destination = (
        return_to
        if return_to.startswith(("/releases", "/duplicate-finder", "/collection-upgrade"))
        else "/releases"
    )
    db = SessionLocal()
    error_code = ""
    try:
        release = db.get(Release, release_id)
        if release is None:
            error_code = "not_found"
        elif action == "clear":
            release.imdb_id = release.imdb_srrdb_id
            release.imdb_manual_override = False
            release.imdb_manual_updated_at = datetime.utcnow()
            release.imdb_lookup_status = "found" if release.imdb_id else "not_checked"
            release.imdb_error_message = None
            db.commit()
        else:
            normalized = imdb_id.strip().lower()
            if not re.fullmatch(r"tt\d{7,10}", normalized):
                error_code = "invalid"
            else:
                if not release.imdb_manual_override:
                    release.imdb_srrdb_id = release.imdb_id
                release.imdb_id = normalized
                release.imdb_manual_override = True
                release.imdb_manual_updated_at = datetime.utcnow()
                release.imdb_lookup_status = "manual"
                release.imdb_error_message = None
                db.commit()
    finally:
        db.close()

    if not error_code:
        rebuild_movie_list_cache()

    separator = "&" if "?" in safe_destination else "?"
    if error_code:
        return RedirectResponse(
            f"{safe_destination}{separator}imdb_error={error_code}",
            status_code=303,
        )
    return RedirectResponse(
        f"{safe_destination}{separator}imdb_saved=1",
        status_code=303,
    )


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
