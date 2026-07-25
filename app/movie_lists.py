from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import httpx

from .database import MovieList, MovieListItem, MovieListSync, SessionLocal

log = logging.getLogger(__name__)
IMDB_TOP_100_URL = "https://www.imdb.com/search/title/?count=100&groups=top_100&sort=user_rating,desc"


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "titleText", "originalText"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
            if isinstance(candidate, dict) and isinstance(candidate.get("text"), str):
                return candidate["text"]
    return None


def _extract_titles(html: str) -> list[dict[str, Any]]:
    start = html.find('<script id="__NEXT_DATA__" type="application/json">')
    if start < 0:
        start = html.find('<script id="__NEXT_DATA__"')
    if start < 0:
        raise ValueError("IMDb response did not include expected page data.")
    start = html.find(">", start) + 1
    end = html.find("</script>", start)
    data = json.loads(html[start:end])

    found: dict[str, dict[str, Any]] = {}
    for obj in _walk(data):
        imdb_id = obj.get("id") or obj.get("titleId")
        if not isinstance(imdb_id, str) or not imdb_id.startswith("tt"):
            continue
        title = _text(obj.get("titleText")) or _text(obj.get("originalTitleText")) or _text(obj.get("title"))
        year_value = obj.get("releaseYear") or obj.get("year")
        if isinstance(year_value, dict):
            year_value = year_value.get("year")
        if not title or not isinstance(year_value, int):
            continue
        rank = obj.get("currentRank") or obj.get("rank") or obj.get("position")
        if isinstance(rank, dict):
            rank = rank.get("rank")
        rating = obj.get("ratingsSummary")
        rating_value = rating.get("aggregateRating") if isinstance(rating, dict) else None
        existing = found.get(imdb_id)
        candidate = {
            "imdb_id": imdb_id,
            "title": title,
            "year": year_value,
            "rank": rank if isinstance(rank, int) else None,
            "rating": float(rating_value) if isinstance(rating_value, (int, float)) else None,
        }
        if existing is None or (candidate["rank"] and not existing["rank"]):
            found[imdb_id] = candidate

    rows = list(found.values())
    ranked = [row for row in rows if row["rank"] and 1 <= row["rank"] <= 100]
    if len(ranked) >= 90:
        return sorted(ranked, key=lambda row: row["rank"])[:100]

    # The advanced-search response is already rating-sorted. Preserve first unique titles.
    if len(rows) >= 90:
        rows = rows[:100]
        for index, row in enumerate(rows, 1):
            row["rank"] = index
        return rows
    raise ValueError(f"IMDb returned only {len(rows)} usable titles; cache was not replaced.")


def sync_imdb_top_100() -> None:
    db = SessionLocal()
    sync = db.get(MovieListSync, "imdb-top-100")
    if sync is None:
        sync = MovieListSync(list_key="imdb-top-100")
        db.add(sync)
    sync.status = "running"
    sync.started_at = datetime.utcnow()
    sync.error_message = None
    db.commit()
    db.close()

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; iSiTSCENE/0.10.3; personal collection manager)",
            "Accept-Language": "en-US,en;q=0.9",
        }
        with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
            response = client.get(IMDB_TOP_100_URL)
            response.raise_for_status()
        titles = _extract_titles(response.text)

        db = SessionLocal()
        movie_list = db.get(MovieList, "imdb-top-100")
        if movie_list is None:
            movie_list = MovieList(
                key="imdb-top-100",
                name="IMDb Top 100 Movies",
                description="The 100 highest-rated movies in IMDb's Top 100 search group.",
                source_url=IMDB_TOP_100_URL,
            )
            db.add(movie_list)
        movie_list.updated_at = datetime.utcnow()
        db.query(MovieListItem).filter(MovieListItem.list_key == movie_list.key).delete()
        for row in titles:
            db.add(MovieListItem(list_key=movie_list.key, **row))
        sync = db.get(MovieListSync, movie_list.key)
        sync.status = "completed"
        sync.completed_at = datetime.utcnow()
        sync.item_count = len(titles)
        sync.error_message = None
        db.commit()
        db.close()
    except Exception as exc:
        log.exception("IMDb Top 100 synchronization failed")
        db = SessionLocal()
        sync = db.get(MovieListSync, "imdb-top-100")
        if sync is None:
            sync = MovieListSync(list_key="imdb-top-100")
            db.add(sync)
        sync.status = "failed"
        sync.completed_at = datetime.utcnow()
        sync.error_message = str(exc)
        db.commit()
        db.close()
