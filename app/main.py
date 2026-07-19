import asyncio, logging, os, threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

APP_NAME = 'iSiTSCENE'
MOVIES_PATH = Path(os.getenv('MOVIES_PATH', '/movies'))
DATA_PATH = Path(os.getenv('DATA_PATH', '/config'))
DATA_PATH.mkdir(parents=True, exist_ok=True)
SCAN_INTERVAL_HOURS = int(os.getenv('SCAN_INTERVAL_HOURS', '24'))
SRRDB_DELAY_SECONDS = float(os.getenv('SRRDB_DELAY_SECONDS', '1.5'))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(APP_NAME)
engine = create_engine(f"sqlite:///{DATA_PATH / 'isitscene.db'}", connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
scan_lock = threading.Lock()
scheduler = BackgroundScheduler()

class Base(DeclarativeBase): pass

class Release(Base):
    __tablename__ = 'releases'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folder_name: Mapped[str] = mapped_column(String, unique=True, index=True)
    status: Mapped[str] = mapped_column(String, default='pending', index=True)
    matched_release: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default='')
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

class ScanRun(Base):
    __tablename__ = 'scan_runs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default='running')
    folders_found: Mapped[int] = mapped_column(Integer, default=0)
    new_folders: Mapped[int] = mapped_column(Integer, default=0)
    missing_folders: Mapped[int] = mapped_column(Integer, default=0)
    exact_matches: Mapped[int] = mapped_column(Integer, default=0)
    not_found: Mapped[int] = mapped_column(Integer, default=0)
    api_errors: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

async def check_srrdb(name: str):
    url = f"https://api.srrdb.com/v1/details/{quote(name, safe='')}"
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={'User-Agent':'iSiTSCENE/0.1'}) as client:
            r = await client.get(url)
        await asyncio.sleep(SRRDB_DELAY_SECONDS)
        if r.status_code == 404:
            return 'not_found', None, None
        r.raise_for_status()
        data = r.json()
        candidates = []
        if isinstance(data, dict):
            for key in ('release','releaseName','name'):
                if isinstance(data.get(key), str): candidates.append(data[key])
            if isinstance(data.get('results'), list):
                candidates += [x.get('release') for x in data['results'] if isinstance(x, dict) and isinstance(x.get('release'), str)]
        exact = next((x for x in candidates if x.casefold() == name.casefold()), None)
        return ('verified', exact, None) if exact else ('api_error', None, 'Unrecognized SRRDB response schema')
    except Exception as exc:
        return 'api_error', None, str(exc)

async def scan_async():
    if not scan_lock.acquire(blocking=False):
        return
    db = SessionLocal(); run = ScanRun(); db.add(run); db.commit()
    try:
        if not MOVIES_PATH.is_dir(): raise RuntimeError(f'Movie path unavailable: {MOVIES_PATH}')
        names = sorted(x.name for x in MOVIES_PATH.iterdir() if x.is_dir())
        now = datetime.utcnow(); current = set(names)
        existing = db.scalars(select(Release)).all(); by_name = {x.folder_name:x for x in existing}
        new_count = missing_count = 0
        for name in names:
            row = by_name.get(name)
            if row is None:
                row = Release(folder_name=name, first_seen=now, last_seen=now, is_present=True, status='pending')
                db.add(row); new_count += 1
            else:
                row.last_seen = now; row.is_present = True
        for row in existing:
            if row.folder_name not in current and row.is_present:
                row.is_present = False; row.status = 'missing'; missing_count += 1
        db.commit()
        pending = db.scalars(select(Release).where(Release.is_present.is_(True), Release.ignored.is_(False), Release.status.in_(['pending','api_error'])).order_by(Release.folder_name)).all()
        for row in pending:
            row.status, row.matched_release, row.error_message = await check_srrdb(row.folder_name)
            row.last_checked = datetime.utcnow(); db.commit()
        present = db.scalars(select(Release).where(Release.is_present.is_(True), Release.ignored.is_(False))).all()
        run.completed_at=datetime.utcnow(); run.status='completed'; run.folders_found=len(names); run.new_folders=new_count; run.missing_folders=missing_count
        run.exact_matches=sum(x.status=='verified' for x in present); run.not_found=sum(x.status=='not_found' for x in present); run.api_errors=sum(x.status=='api_error' for x in present)
        db.commit()
    except Exception as exc:
        log.exception('Scan failed'); run.completed_at=datetime.utcnow(); run.status='failed'; run.error_message=str(exc); db.commit()
    finally:
        db.close(); scan_lock.release()

def run_scan(): asyncio.run(scan_async())

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    scheduler.add_job(run_scan, 'interval', hours=SCAN_INTERVAL_HOURS, id='daily', replace_existing=True, max_instances=1)
    scheduler.start(); yield; scheduler.shutdown(wait=False)

app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount('/static', StaticFiles(directory='app/static'), name='static')
templates = Jinja2Templates(directory='app/templates')

@app.get('/health')
def health(): return {'status':'ok','application':APP_NAME}

@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    db=SessionLocal()
    try:
        present=Release.is_present.is_(True)
        counts={k:(db.scalar(select(func.count()).select_from(Release).where(*conds)) or 0) for k,conds in {
            'total':(present,), 'verified':(present,Release.status=='verified'), 'not_found':(present,Release.status=='not_found'),
            'pending':(present,Release.status=='pending'), 'api_error':(present,Release.status=='api_error'), 'missing':(Release.is_present.is_(False),)}.items()}
        latest=db.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(1)).first()
        return templates.TemplateResponse(request=request,name='dashboard.html',context={'counts':counts,'latest':latest})
    finally: db.close()

@app.get('/releases', response_class=HTMLResponse)
def releases(request: Request, q: str='', status: str=''):
    db=SessionLocal()
    try:
        stmt=select(Release).order_by(Release.folder_name)
        if q: stmt=stmt.where(Release.folder_name.ilike(f'%{q}%'))
        if status: stmt=stmt.where(Release.status==status)
        return templates.TemplateResponse(request=request,name='releases.html',context={'items':db.scalars(stmt).all(),'q':q,'status':status})
    finally: db.close()

@app.post('/scan')
def scan(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scan); return RedirectResponse('/',303)
