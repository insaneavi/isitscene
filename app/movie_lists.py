from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from .database import MovieList, MovieListItem, MovieListSync, SessionLocal

log = logging.getLogger(__name__)
BUNDLED_LIST_PATH = Path(__file__).parent / "data" / "movie_lists"


def bundled_list_keys() -> list[str]:
    return sorted(path.stem for path in BUNDLED_LIST_PATH.glob("*.json"))


def _validate(payload: dict) -> None:
    required = {
        "key", "name", "source_name", "source_url", "list_version",
        "data_updated_at", "item_count", "items",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"Bundled list is missing: {', '.join(sorted(missing))}")

    items = payload["items"]
    if not isinstance(items, list) or len(items) != int(payload["item_count"]):
        raise ValueError("Bundled list item count is invalid.")

    ranks: set[int] = set()
    for item in items:
        rank = int(item["rank"])
        if rank in ranks:
            raise ValueError("Bundled list contains duplicate ranks.")
        ranks.add(rank)
        if not str(item.get("title", "")).strip():
            raise ValueError("Bundled list contains an empty title.")
        imdb_id = item.get("imdb_id")
        if imdb_id and not re.fullmatch(r"tt\d{7,10}", imdb_id):
            raise ValueError(f"Invalid IMDb ID: {imdb_id}")


def import_bundled_list(list_key: str) -> None:
    path = BUNDLED_LIST_PATH / f"{list_key}.json"
    if not path.exists():
        raise ValueError("Unknown bundled movie list.")

    db = SessionLocal()
    try:
        sync = db.get(MovieListSync, list_key)
        if sync is None:
            sync = MovieListSync(list_key=list_key)
            db.add(sync)
        sync.status = "running"
        sync.started_at = datetime.utcnow()
        sync.error_message = None
        db.commit()

        payload = json.loads(path.read_text(encoding="utf-8"))
        _validate(payload)
        now = datetime.utcnow()

        movie_list = db.get(MovieList, list_key)
        if movie_list is None:
            movie_list = MovieList(
                key=list_key,
                name=payload["name"],
                source_url=payload["source_url"],
            )
            db.add(movie_list)

        movie_list.name = payload["name"]
        movie_list.description = payload.get("description", "")
        movie_list.source_url = payload["source_url"]
        movie_list.source_name = payload["source_name"]
        movie_list.list_version = payload["list_version"]
        movie_list.data_updated_at = datetime.fromisoformat(payload["data_updated_at"])
        movie_list.updated_at = now

        db.execute(
            delete(MovieListItem).where(MovieListItem.list_key == list_key)
        )
        for item in payload["items"]:
            db.add(
                MovieListItem(
                    list_key=list_key,
                    rank=int(item["rank"]),
                    imdb_id=item.get("imdb_id") or "",
                    title=item["title"].strip(),
                    year=int(item["year"]),
                    rating=item.get("rating"),
                )
            )

        sync.status = "completed"
        sync.completed_at = now
        sync.item_count = len(payload["items"])
        sync.error_message = None
        db.commit()
    except Exception as exc:
        db.rollback()
        sync = db.get(MovieListSync, list_key)
        if sync is None:
            sync = MovieListSync(list_key=list_key)
            db.add(sync)
        sync.status = "failed"
        sync.completed_at = datetime.utcnow()
        sync.error_message = str(exc)
        db.commit()
        log.exception("Unable to import bundled movie list %s", list_key)
    finally:
        db.close()


def ensure_bundled_lists() -> None:
    db = SessionLocal()
    try:
        existing = set(db.scalars(select(MovieList.key)).all())
        failed = set(
            db.scalars(
                select(MovieListSync.list_key).where(
                    MovieListSync.status == "failed"
                )
            ).all()
        )
    finally:
        db.close()

    for key in bundled_list_keys():
        if key not in existing or key in failed:
            import_bundled_list(key)
