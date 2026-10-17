"""Metadata-only timestamp backfill, independent of SRRDB verification."""
from datetime import datetime, timezone
from threading import Lock, Thread
from sqlalchemy import select
from .config import MOVIES_PATH
from .database import SessionLocal, Release

_lock = Lock()
_state = {'running': False, 'processed': 0, 'total': 0, 'updated': 0, 'missing': 0, 'error': None}

def status():
    return dict(_state)

def _run():
    try:
        if not MOVIES_PATH.is_dir():
            raise RuntimeError(f'Movie folder path is unavailable: {MOVIES_PATH}')
        with SessionLocal() as db:
            releases = db.scalars(select(Release).where(Release.is_present.is_(True))).all()
            _state['total'] = len(releases)
            for idx, release in enumerate(releases, 1):
                try:
                    # Resolve a direct child only, never traverse movie files.
                    folder = MOVIES_PATH / release.folder_name
                    if folder.is_dir():
                        release.folder_modified_at = datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc).replace(tzinfo=None)
                        _state['updated'] += 1
                    else:
                        _state['missing'] += 1
                except OSError:
                    _state['missing'] += 1
                _state['processed'] = idx
                if idx % 200 == 0:
                    db.commit()
            db.commit()
    except Exception as exc:
        _state['error'] = str(exc)
    finally:
        _state['running'] = False
        _lock.release()

def start():
    if not _lock.acquire(blocking=False):
        return False
    _state.update(running=True, processed=0, total=0, updated=0, missing=0, error=None)
    Thread(target=_run, daemon=True, name='folder-date-backfill').start()
    return True
