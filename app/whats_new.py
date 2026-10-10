"""Read-only discovery feeds, persisted independently of inventory."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from .config import DATA_PATH

log = logging.getLogger(__name__)
CACHE = DATA_PATH / "whats_new_cache.json"
_lock = threading.Lock()
_running = False
ERROR = None

CATEGORIES = {
    "uhd": {"label": "UHD BluRay 2160p x265", "resolution": "2160p", "source": "uhd", "codec": "x265"},
    "bluray": {"label": "BluRay 1080p x264", "resolution": "1080p", "source": "bluray", "codec": "x264"},
}

def read_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"updated_at": None, "srrdb": {}, "movies": [], "errors": []}

def _save(data):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    temp = CACHE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, CACHE)

def _qualifies(name, category):
    upper = name.upper()
    if "MULTI" in upper or "GERMAN" in upper:
        return False
    tokens = re.split(r"[._\\s-]+", name.casefold())
    required = CATEGORIES[category]
    return all(required[k] in tokens for k in ("resolution", "source", "codec"))

def _fetch_srrdb(client, category):
    found = []
    seen = set()
    # Search API returns paginated results. Keep paging until 50 qualify.
    for page in range(1, 21):
        path = f"https://api.srrdb.com/v1/search/r:{CATEGORIES[category]['resolution']}/order:date-desc/page:{page}"
        response = client.get(path)
        response.raise_for_status()
        payload = response.json()
        entries = payload.get("results", []) if isinstance(payload, dict) else payload
        if not isinstance(entries, list) or not entries:
            break
        for item in entries:
            if not isinstance(item, dict):
                continue
            name = str(item.get("release") or item.get("name") or "")
            if not name or name in seen or not _qualifies(name, category):
                continue
            seen.add(name)
            found.append({
                "name": name,
                "date": str(item.get("date") or ""),
                "url": "https://www.srrdb.com/release/details/" + quote(name, safe=""),
            })
        if len(found) >= 50:
            break
    found.sort(key=lambda r: r["date"], reverse=True)
    return found[:50]

def _fetch_movies(client):
    """Read weekly domestic calendar pages for the rolling previous year.

    This is best-effort: Box Office Mojo does not publish a supported calendar API.
    """
    today = date.today()
    cutoff = today - timedelta(days=365)
    results = {}
    # Weekly calendar dates (include boundary weeks).
    cursor = cutoff - timedelta(days=7)
    while cursor <= today:
        url = "https://www.boxofficemojo.com/calendar/" + cursor.isoformat() + "/"
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        # The site uses dated section headers followed by a release table.
        for row in soup.select("tr"):
            link = row.select_one('a[href*="/release/"], a[href*="/title/"]')
            if link is None:
                continue
            title = link.get_text(" ", strip=True)
            if not title:
                continue
            cells = [c.get_text(" ", strip=True) for c in row.select("td")]
            text = " ".join(cells)
            date_match = re.search(r"\\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{1,2},?\\s+20\\d{2}\\b", text)
            if date_match:
                try:
                    released = datetime.strptime(date_match.group(0).replace(",", ""), "%B %d %Y").date()
                except ValueError:
                    try:
                        released = datetime.strptime(date_match.group(0).replace(",", ""), "%b %d %Y").date()
                    except ValueError:
                        continue
            else:
                # Never invent a release date when the page omits it.
                continue
            if not cutoff <= released <= today:
                continue
            if re.search(r"re.release|anniversary|special screening", title, re.I):
                continue
            key = (title.casefold(), released.isoformat())
            results[key] = {
                "title": title,
                "date": released.isoformat(),
                "scale": "Wide" if re.search(r"\\bWide\\b", text) else ("Limited" if re.search(r"\\bLimited\\b", text) else ""),
                "url": "https://www.boxofficemojo.com" + link.get("href", "") if link.get("href", "").startswith("/") else link.get("href", ""),
            }
        cursor += timedelta(days=7)
    return sorted(results.values(), key=lambda m: m["date"], reverse=True)

def refresh():
    global _running, ERROR
    if not _lock.acquire(blocking=False):
        return
    _running = True
    ERROR = None
    try:
        cache = read_cache()
        errors = []
        with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "iSiTSCENE/0.17.0"}) as client:
            for key in CATEGORIES:
                try:
                    cache.setdefault("srrdb", {})[key] = _fetch_srrdb(client, key)
                except Exception as exc:
                    log.warning("What's New SRRDB %s refresh failed: %s", key, exc)
                    errors.append(f"SRRDB {key}: {exc}")
            try:
                movies = _fetch_movies(client)
                if movies:
                    cache["movies"] = movies
                else:
                    errors.append("Box Office Mojo: no release rows could be parsed; previous cache retained.")
            except Exception as exc:
                log.warning("Box Office Mojo refresh failed: %s", exc)
                errors.append(f"Box Office Mojo: {exc}")
        cache["updated_at"] = datetime.now(timezone.utc).isoformat()
        cache["errors"] = errors
        _save(cache)
        ERROR = "; ".join(errors) if errors else None
    finally:
        _running = False
        _lock.release()

def status():
    return {"running": _running, "error": ERROR}
