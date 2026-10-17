"""Metadata-only collection size measurement. No file content is opened."""
from pathlib import Path
from threading import Lock, Thread
import os
from sqlalchemy import select
from .config import MOVIES_PATH
from .database import SessionLocal, Release
_lock = Lock()
_state = dict(running=False, processed=0, total=0, measured=0, errors=0, error=None)
def status(): return dict(_state)
def _size(folder: Path):
    total = 0
    # Do not follow symlinked directories or files. No hard-link deduplication.
    stack = [folder]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.is_symlink(): continue
                if entry.is_dir(follow_symlinks=False): stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False): total += entry.stat(follow_symlinks=False).st_size
    return total
def _run():
    try:
        if not MOVIES_PATH.is_dir(): raise RuntimeError(f'Movie path unavailable: {MOVIES_PATH}')
        with SessionLocal() as db:
            releases = db.scalars(select(Release).where(Release.is_present.is_(True))).all()
            _state['total'] = len(releases)
            for idx, release in enumerate(releases, 1):
                folder = MOVIES_PATH / release.folder_name
                try:
                    if folder.is_dir() and not folder.is_symlink():
                        release.folder_size_bytes = _size(folder)
                        _state['measured'] += 1
                    else: _state['errors'] += 1
                except OSError: _state['errors'] += 1
                _state['processed'] = idx
                if idx % 50 == 0: db.commit()
            db.commit()
    except Exception as exc: _state['error'] = str(exc)
    finally:
        _state['running'] = False
        _lock.release()
def start():
    if not _lock.acquire(blocking=False): return False
    _state.update(running=True, processed=0, total=0, measured=0, errors=0, error=None)
    Thread(target=_run, daemon=True, name='collection-size-worker').start()
    return True
