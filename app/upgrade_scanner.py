from __future__ import annotations
import asyncio, logging, threading
from datetime import datetime
from sqlalchemy import delete, select
from .database import Release, ScanProgress, SessionLocal, UpgradeCandidate, UpgradeProgress, UpgradeResult, UpgradeScan
from .settings_service import get_settings
from .srrdb import ScanCancelled, find_uhd_upgrades_by_imdb, lookup_imdb_id
logger=logging.getLogger(__name__); _upgrade_lock=threading.Lock(); _stop_event=threading.Event()

def _eligible(name):
    f=name.casefold(); return ("bluray" in f or "blu-ray" in f) and not any(x in f for x in ("web-dl","webrip",".web."," web ")) and not any(x in f for x in ("2160p",".uhd.","ultrahd"))
def _progress(db):
    p=db.get(UpgradeProgress,1)
    if p is None: p=UpgradeProgress(id=1); db.add(p); db.flush()
    return p
def request_upgrade_stop():
    _stop_event.set(); db=SessionLocal()
    try:
        p=_progress(db)
        if p.is_running: p.phase="stopping"; p.message="Stopping Collection Upgrade after the current SRRDB request..."; db.commit()
    finally: db.close()
def recover_interrupted_upgrade_scan():
    db=SessionLocal()
    try:
        p=_progress(db)
        if not p.is_running: return
        now=datetime.utcnow(); p.is_running=False; p.phase="interrupted"; p.current_release=None; p.completed_at=now; p.message="Previous Collection Upgrade scan was interrupted. You can safely start it again."
        for run in db.scalars(select(UpgradeScan).where(UpgradeScan.status=="running")).all(): run.status="interrupted"; run.completed_at=now
        db.commit()
    finally: db.close()
def reset_upgrade_scan_state():
    if not _upgrade_lock.acquire(blocking=False): return False
    try:
        _stop_event.clear(); db=SessionLocal()
        try:
            now=datetime.utcnow(); p=_progress(db); p.is_running=False; p.phase="reset"; p.current_release=None; p.completed_at=now; p.message="Collection Upgrade scan state was reset. You can start a new scan."
            for run in db.scalars(select(UpgradeScan).where(UpgradeScan.status=="running")).all(): run.status="interrupted"; run.completed_at=now
            db.commit(); return True
        finally: db.close()
    finally: _upgrade_lock.release()
async def _run():
    if not _upgrade_lock.acquire(blocking=False): return
    _stop_event.clear(); db=SessionLocal(); run=None
    try:
        normal=db.get(ScanProgress,1)
        if normal and normal.is_running: return
        releases=db.scalars(select(Release).where(Release.is_present.is_(True),Release.verification_status=="verified",Release.ignored.is_(False)).order_by(Release.folder_name)).all()
        eligible=[r for r in releases if _eligible(r.matched_release or r.folder_name)]
        run=UpgradeScan(status="running",eligible_count=len(eligible)); db.add(run); p=_progress(db)
        p.is_running=True; p.phase="checking"; p.current_release=None; p.processed_count=0; p.total_count=len(eligible); p.upgrades_found=0; p.no_upgrade_count=0; p.imdb_missing_count=0; p.api_error_count=0; p.started_at=datetime.utcnow(); p.completed_at=None; p.message="Checking verified Blu-ray releases for strict UHD upgrades..."; db.commit()
        delay=get_settings().srrdb_delay_seconds
        for index,release in enumerate(eligible,1):
            if _stop_event.is_set(): raise ScanCancelled("Upgrade scan stopped by user.")
            current=release.matched_release or release.folder_name; p.current_release=current; p.message="Using cached IMDb ID and searching SRRDB..."; db.commit()
            result=db.scalar(select(UpgradeResult).where(UpgradeResult.release_id==release.id))
            if result is None: result=UpgradeResult(release_id=release.id,current_release=current); db.add(result); db.flush()
            else: result.current_release=current; db.execute(delete(UpgradeCandidate).where(UpgradeCandidate.upgrade_result_id==result.id))
            imdb_id=release.imdb_id if release.imdb_source_release==current else None; error=None
            if not imdb_id and not (release.imdb_lookup_status=="unavailable" and release.imdb_source_release==current):
                imdb_id,error=await lookup_imdb_id(current,delay,_stop_event.is_set); release.imdb_id=imdb_id; release.imdb_source_release=current; release.imdb_checked_at=datetime.utcnow(); release.imdb_error_message=error; release.imdb_lookup_status="api_error" if error else ("found" if imdb_id else "unavailable")
            candidates=[]
            if imdb_id and not error: candidates,error=await find_uhd_upgrades_by_imdb(imdb_id,delay,_stop_event.is_set)
            result.imdb_id=imdb_id; result.checked_at=datetime.utcnow(); result.error_message=error; result.candidate_count=len(candidates)
            if error: result.status="api_error"; p.api_error_count+=1
            elif not imdb_id: result.status="imdb_unavailable"; p.imdb_missing_count+=1
            elif candidates:
                result.status="upgrade_available"; p.upgrades_found+=1
                for c in candidates: db.add(UpgradeCandidate(upgrade_result_id=result.id,release_name=c.release_name,srrdb_url=c.url))
            else: result.status="no_upgrade"; p.no_upgrade_count+=1
            p.processed_count=index; run.checked_count=index; run.upgrades_found=p.upgrades_found; run.no_upgrade_count=p.no_upgrade_count; run.imdb_missing_count=p.imdb_missing_count; run.api_error_count=p.api_error_count; db.commit()
        now=datetime.utcnow(); run.status="completed"; run.completed_at=now; p.is_running=False; p.phase="complete"; p.current_release=None; p.completed_at=now; p.message=f"Upgrade scan complete. {p.upgrades_found} upgrade(s) found."; db.commit()
    except ScanCancelled:
        now=datetime.utcnow();
        if run: run.status="stopped"; run.completed_at=now
        p=_progress(db); p.is_running=False; p.phase="stopped"; p.current_release=None; p.completed_at=now; p.message="Upgrade scan stopped by user."; db.commit()
    except Exception as exc:
        logger.exception("Collection Upgrade scan failed"); now=datetime.utcnow()
        if run: run.status="failed"; run.error_message=str(exc); run.completed_at=now
        p=_progress(db); p.is_running=False; p.phase="failed"; p.current_release=None; p.completed_at=now; p.message=f"Upgrade scan failed: {exc}"; db.commit()
    finally: db.close(); _stop_event.clear(); _upgrade_lock.release()
def run_upgrade_scan(): asyncio.run(_run())
