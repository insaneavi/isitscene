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
    imdb_ids: dict[str, str] = {}
    title_years: set[tuple[str, int]] = set()

    for item in items:
        rank = int(item["rank"])
        if rank in ranks:
            raise ValueError("Bundled list contains duplicate ranks.")
        ranks.add(rank)

        title = str(item.get("title", "")).strip()
        if not title:
            raise ValueError("Bundled list contains an empty title.")

        year = int(item["year"])
        title_year = (title.casefold(), year)
        if title_year in title_years:
            raise ValueError(
                f"Bundled list contains duplicate title/year: "
                f"{title} ({year})"
            )
        title_years.add(title_year)

        aliases = item.get("aliases", [])
        if not isinstance(aliases, list):
            raise ValueError(f"Aliases for {title} must be a list.")
        seen_aliases: set[str] = set()
        for alias in aliases:
            alias_text = str(alias).strip()
            normalized_alias = alias_text.casefold()
            if not alias_text:
                raise ValueError(f"Bundled list contains an empty alias for {title}.")
            if normalized_alias == title.casefold():
                raise ValueError(f"Primary title repeated as alias: {title}.")
            if normalized_alias in seen_aliases:
                raise ValueError(f"Duplicate alias for {title}: {alias_text}.")
            seen_aliases.add(normalized_alias)

        imdb_id = item.get("imdb_id")
        if imdb_id:
            if not re.fullmatch(r"tt\d{7,10}", imdb_id):
                raise ValueError(f"Invalid IMDb ID: {imdb_id}")
            previous_title = imdb_ids.get(imdb_id)
            if previous_title:
                raise ValueError(
                    f"Bundled list assigns IMDb ID {imdb_id} to both "
                    f"{previous_title} and {title}."
                )
            imdb_ids[imdb_id] = title


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
                    aliases_json=json.dumps(item.get("aliases", [])),
                    rating=item.get("rating"),
                )
            )

        sync.status = "completed"
        sync.completed_at = now
        sync.item_count = len(payload["items"])
        sync.error_message = None
        db.commit()

        from .movie_list_cache import rebuild_movie_list_cache
        rebuild_movie_list_cache()
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
        existing = {
            movie_list.key: movie_list.list_version
            for movie_list in db.scalars(select(MovieList)).all()
        }
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
        path = BUNDLED_LIST_PATH / f"{key}.json"
        try:
            bundled_version = str(
                json.loads(path.read_text(encoding="utf-8")).get(
                    "list_version", ""
                )
            )
        except Exception:
            log.exception("Unable to inspect bundled movie list %s", key)
            continue

        if (
            key not in existing
            or key in failed
            or existing.get(key, "") != bundled_version
        ):
            import_bundled_list(key)
