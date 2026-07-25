from __future__ import annotations

import html as html_module
import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx

from .database import MovieList, MovieListItem, MovieListSync, SessionLocal

log = logging.getLogger(__name__)
IMDB_TOP_CHART_URL = "https://www.imdb.com/chart/top/"
LIST_KEY = "imdb-top-100"


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
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("text", "titleText", "originalText", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
            if isinstance(candidate, dict):
                nested = candidate.get("text")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return None


def _year(value: Any) -> int | None:
    if isinstance(value, int):
        return value if 1800 <= value <= 2200 else None
    if isinstance(value, str):
        match = re.search(r"(?:18|19|20|21)\d{2}", value)
        return int(match.group(0)) if match else None
    if isinstance(value, dict):
        for key in ("year", "releaseYear", "datePublished"):
            parsed = _year(value.get(key))
            if parsed:
                return parsed
    return None


def _rating(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ("aggregateRating", "ratingValue", "value"):
            parsed = _rating(value.get(key))
            if parsed is not None:
                return parsed
    return None


def _imdb_id(value: Any) -> str | None:
    if isinstance(value, str):
        match = re.search(r"tt\d{7,10}", value)
        return match.group(0) if match else None
    return None


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        imdb_id = _imdb_id(row.get("imdb_id"))
        title = _text(row.get("title"))
        year = _year(row.get("year"))
        rank = row.get("rank")
        try:
            rank = int(rank)
        except (TypeError, ValueError):
            rank = None
        if not imdb_id or not title or not year:
            continue
        candidate = {
            "imdb_id": imdb_id,
            "title": html_module.unescape(title),
            "year": year,
            "rank": rank,
            "rating": _rating(row.get("rating")),
        }
        existing = unique.get(imdb_id)
        if existing is None or (candidate["rank"] and not existing["rank"]):
            unique[imdb_id] = candidate

    ordered = list(unique.values())
    ranked = [r for r in ordered if isinstance(r["rank"], int) and 1 <= r["rank"] <= 250]
    if len(ranked) >= 100:
        return sorted(ranked, key=lambda r: r["rank"])[:100]

    if len(ordered) >= 100:
        for index, row in enumerate(ordered[:100], 1):
            row["rank"] = index
        return ordered[:100]

    return []


def _extract_json_ld(document: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(document):
        try:
            data = json.loads(html_module.unescape(match.group(1)).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for obj in _walk(data):
            if not isinstance(obj, dict):
                continue
            imdb_id = _imdb_id(obj.get("url")) or _imdb_id(obj.get("@id"))
            title = _text(obj.get("name"))
            year = _year(obj.get("datePublished"))
            rating = _rating(obj.get("aggregateRating"))
            position = obj.get("position")

            # ItemList entries commonly wrap the movie under item.
            item = obj.get("item")
            if isinstance(item, dict):
                imdb_id = imdb_id or _imdb_id(item.get("url")) or _imdb_id(item.get("@id"))
                title = title or _text(item.get("name"))
                year = year or _year(item.get("datePublished"))
                rating = rating if rating is not None else _rating(item.get("aggregateRating"))

            if imdb_id and title:
                rows.append({
                    "imdb_id": imdb_id,
                    "title": title,
                    "year": year,
                    "rank": position,
                    "rating": rating,
                })
    return _normalize_rows(rows)


def _extract_embedded_json(document: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    script_pattern = re.compile(
        r'<script[^>]*(?:id=["\']__NEXT_DATA__["\']|type=["\']application/json["\'])[^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in script_pattern.finditer(document):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in _walk(data):
            if not isinstance(obj, dict):
                continue
            imdb_id = _imdb_id(obj.get("id")) or _imdb_id(obj.get("titleId"))
            if not imdb_id:
                continue
            title = (
                _text(obj.get("titleText"))
                or _text(obj.get("originalTitleText"))
                or _text(obj.get("title"))
                or _text(obj.get("name"))
            )
            year = _year(obj.get("releaseYear")) or _year(obj.get("year"))
            rank = obj.get("currentRank") or obj.get("rank") or obj.get("position")
            if isinstance(rank, dict):
                rank = rank.get("rank") or rank.get("currentRank")
            rating = _rating(obj.get("ratingsSummary")) or _rating(obj.get("aggregateRating"))
            if title:
                rows.append({
                    "imdb_id": imdb_id,
                    "title": title,
                    "year": year,
                    "rank": rank,
                    "rating": rating,
                })
    return _normalize_rows(rows)


def _extract_html_cards(document: str) -> list[dict[str, Any]]:
    """Last-resort parser for chart cards and links in rendered HTML."""
    rows: list[dict[str, Any]] = []
    link_pattern = re.compile(
        r'href=["\']/title/(?P<id>tt\d{7,10})/[^"\']*["\'][^>]*>(?P<body>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    seen: set[str] = set()
    for match in link_pattern.finditer(document):
        imdb_id = match.group("id")
        if imdb_id in seen:
            continue
        body = re.sub(r"<[^>]+>", " ", match.group("body"))
        body = html_module.unescape(re.sub(r"\s+", " ", body)).strip()
        title = re.sub(r"^\s*\d+\.\s*", "", body).strip()
        if not title or len(title) > 300:
            continue
        context = document[max(0, match.start() - 1200): min(len(document), match.end() + 2200)]
        rank_match = re.search(r"(?:#|>\s*)(\d{1,3})(?:\.|<)", context)
        year_match = re.search(r"(?<!\d)((?:18|19|20|21)\d{2})(?!\d)", context)
        rating_match = re.search(r"(?:aggregateRating|ratingValue)[^0-9]{0,40}(10(?:\.0)?|[0-9](?:\.[0-9])?)", context)
        rows.append({
            "imdb_id": imdb_id,
            "title": title,
            "year": int(year_match.group(1)) if year_match else None,
            "rank": int(rank_match.group(1)) if rank_match else None,
            "rating": float(rating_match.group(1)) if rating_match else None,
        })
        seen.add(imdb_id)
    return _normalize_rows(rows)


def _extract_titles(document: str) -> list[dict[str, Any]]:
    for parser in (_extract_json_ld, _extract_embedded_json, _extract_html_cards):
        rows = parser(document)
        if len(rows) == 100:
            return rows
    raise ValueError(
        "IMDb returned a page, but fewer than 100 ranked movie records could be parsed. "
        "The existing local cache was preserved."
    )


def sync_imdb_top_100() -> None:
    db = SessionLocal()
    try:
        sync = db.get(MovieListSync, LIST_KEY)
        if sync is None:
            sync = MovieListSync(list_key=LIST_KEY)
            db.add(sync)
        sync.status = "running"
        sync.started_at = datetime.utcnow()
        sync.error_message = None
        db.commit()
    finally:
        db.close()

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
            response = client.get(IMDB_TOP_CHART_URL)
            response.raise_for_status()
        titles = _extract_titles(response.text)

        db = SessionLocal()
        try:
            movie_list = db.get(MovieList, LIST_KEY)
            if movie_list is None:
                movie_list = MovieList(
                    key=LIST_KEY,
                    name="IMDb Top 100 Movies",
                    description="The first 100 ranked titles from IMDb's official Top 250 movie chart.",
                    source_url=IMDB_TOP_CHART_URL,
                )
                db.add(movie_list)
            movie_list.name = "IMDb Top 100 Movies"
            movie_list.description = "The first 100 ranked titles from IMDb's official Top 250 movie chart."
            movie_list.source_url = IMDB_TOP_CHART_URL
            movie_list.updated_at = datetime.utcnow()

            # Replace the cache only after a complete 100-title response is parsed.
            db.query(MovieListItem).filter(MovieListItem.list_key == LIST_KEY).delete()
            for row in titles:
                db.add(MovieListItem(list_key=LIST_KEY, **row))

            sync = db.get(MovieListSync, LIST_KEY)
            sync.status = "completed"
            sync.completed_at = datetime.utcnow()
            sync.item_count = len(titles)
            sync.error_message = None
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        log.exception("IMDb Top 100 synchronization failed")
        db = SessionLocal()
        try:
            sync = db.get(MovieListSync, LIST_KEY)
            if sync is None:
                sync = MovieListSync(list_key=LIST_KEY)
                db.add(sync)
            sync.status = "failed"
            sync.completed_at = datetime.utcnow()
            sync.error_message = str(exc)
            db.commit()
        finally:
            db.close()
