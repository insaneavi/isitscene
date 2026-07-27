from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

from sqlalchemy import delete, select

from .database import (
    MovieListCacheState,
    MovieListItem,
    MovieListMatch,
    Release,
    SessionLocal,
)
from .srrdb import parse_release_name

log = logging.getLogger(__name__)

_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19\d{2}|20\d{2}|21\d{2})(?!\d)")
_TECH_PATTERN = re.compile(
    r"(?i)(?:^|[._\s-])"
    r"(?:480[pi]|576[pi]|720p|1080[pi]|2160p|4k|"
    r"bluray|blu-ray|uhd|web-dl|webrip|hdtv|dvdrip|"
    r"bdrip|brrip|remux|repack|proper|x264|x265|"
    r"h264|h265|hevc|xvid|av1)"
    r"(?=$|[._\s-])"
)


def title_from_release_name(folder_name: str) -> str:
    name = folder_name.strip()
    boundaries = []
    year_match = _YEAR_PATTERN.search(name)
    tech_match = _TECH_PATTERN.search(name)
    if year_match:
        boundaries.append(year_match.start())
    if tech_match:
        boundaries.append(tech_match.start())
    if boundaries:
        name = name[:min(boundaries)]
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" -")


def normalize_title(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"['’]", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def rebuild_movie_list_cache() -> None:
    """Rebuild all list ownership matches in one transaction."""
    db = SessionLocal()
    try:
        releases = db.scalars(
            select(Release).where(Release.is_present.is_(True))
        ).all()
        items = db.scalars(
            select(MovieListItem).order_by(
                MovieListItem.list_key,
                MovieListItem.rank,
            )
        ).all()

        by_imdb: dict[str, Release] = {}
        by_title_year: dict[tuple[str, str], Release] = {}
        title_groups: dict[str, list[Release]] = defaultdict(list)

        for release in releases:
            if release.imdb_id:
                by_imdb.setdefault(release.imdb_id, release)

            raw_title = release.movie_title or title_from_release_name(
                release.matched_release or release.folder_name
            )
            normalized = normalize_title(raw_title)
            if normalized:
                title_groups[normalized].append(release)

            year = release.movie_year
            if not year:
                parsed = parse_release_name(
                    release.matched_release or release.folder_name
                )
                year = parsed.year
            if normalized and year:
                by_title_year.setdefault((normalized, str(year)), release)

        unique_titles = {
            title: grouped[0]
            for title, grouped in title_groups.items()
            if len(grouped) == 1
        }

        normalized_release_titles = list(unique_titles.items())

        db.execute(delete(MovieListMatch))
        owned_count = 0
        now = datetime.utcnow()

        for item in items:
            release = None
            method = None

            if item.imdb_id:
                release = by_imdb.get(item.imdb_id)
                if release:
                    method = "IMDb"

            normalized_item = normalize_title(item.title)

            if release is None:
                release = by_title_year.get(
                    (normalized_item, str(item.year))
                )
                if release:
                    method = "Title/year"

            if release is None:
                release = unique_titles.get(normalized_item)
                if release:
                    method = "Unique title"

            if release is None and normalized_item:
                best_score = 0.0
                second_score = 0.0
                best_release = None
                for release_title, candidate in normalized_release_titles:
                    score = SequenceMatcher(
                        None,
                        normalized_item,
                        release_title,
                    ).ratio()
                    if score > best_score:
                        second_score = best_score
                        best_score = score
                        best_release = candidate
                    elif score > second_score:
                        second_score = score
                if (
                    best_release is not None
                    and best_score >= 0.98
                    and best_score > second_score
                ):
                    release = best_release
                    method = "Close title"

            if release:
                owned_count += 1

            db.add(
                MovieListMatch(
                    item_id=item.id,
                    list_key=item.list_key,
                    release_id=release.id if release else None,
                    match_method=method,
                    matched_at=now,
                )
            )

        state = db.get(MovieListCacheState, 1)
        if state is None:
            state = MovieListCacheState(id=1)
            db.add(state)
        state.rebuilt_at = now
        state.item_count = len(items)
        state.owned_count = owned_count
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Unable to rebuild Movie List match cache")
        raise
    finally:
        db.close()
