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
    tokens = re.split(r"[._\s-]+", name.casefold())
    required = CATEGORIES[category]
    return all(required[k] in tokens for k in ("resolution", "source", "codec"))


def _fetch_srrdb(client, category):
    found, seen = [], set()
    for page in range(1, 31):
        # SRRDB supports free-text search segments and order/page modifiers.
        query = CATEGORIES[category]["resolution"]
        url = f"https://api.srrdb.com/v1/search/{query}/order:date-desc/page:{page}"
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
        entries = payload.get("results", []) if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise ValueError("SRRDB returned an unexpected search response")
        if not entries:
            break
        for item in entries:
            if not isinstance(item, dict):
                continue
            name = str(item.get("release") or item.get("name") or "")
            if not name or name.casefold() in seen or not _qualifies(name, category):
                continue
            seen.add(name.casefold())
            found.append({"name": name, "date": str(item.get("date") or ""),
                          "url": "https://www.srrdb.com/release/details/" + quote(name, safe="")})
        if len(found) >= 50:
            break
    found.sort(key=lambda item: item["date"], reverse=True)
    return found[:50]


def _fetch_movies(client):
    """Best-effort parsing of Box Office Mojo's monthly domestic calendar."""
    today = date.today()
    cutoff = today - timedelta(days=365)
    cursor = date(cutoff.year, cutoff.month, 1)
    results = {}
    while cursor <= today:
        url = f"https://www.boxofficemojo.com/calendar/{cursor.isoformat()}/"
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        current_date = None
        for row in soup.select("tr"):
            row_text = row.get_text(" ", strip=True)
            match = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+20\d{2}\b", row_text)
            if match:
                try:
                    current_date = datetime.strptime(match.group(0).replace(",", ""), "%B %d %Y").date()
                except ValueError:
                    current_date = None
            if current_date is None or not cutoff <= current_date <= today:
                continue
            link = row.select_one('a[href*="/release/"], a[href*="/title/"]')
            if not link:
                continue
            title = link.get_text(" ", strip=True)
            if not title or re.search(r"re-release|anniversary|special screening", row_text, re.I):
                continue
            href = link.get("href", "")
            if not href.startswith("/") and not href.startswith("https://"):
                continue
            key = (title.casefold(), current_date.isoformat())
            results[key] = {
                "title": title, "date": current_date.isoformat(),
                "scale": "Wide" if re.search(r"\bWide\b", row_text) else ("Limited" if re.search(r"\bLimited\b", row_text) else ""),
                "url": "https://www.boxofficemojo.com" + href if href.startswith("/") else href,
            }
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return sorted(results.values(), key=lambda item: item["date"], reverse=True)

def refresh():
    global _running, ERROR
    if not _lock.acquire(blocking=False):
        return
    _running = True
    ERROR = None
    try:
        cache = read_cache()
        errors = []
        with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "iSiTSCENE/0.17.1"}) as client:
            for key in CATEGORIES:
                try:
                    results = _fetch_srrdb(client, key)
                    if results:
                        cache.setdefault("srrdb", {})[key] = results
                    else:
                        errors.append(f"SRRDB {key}: no qualifying results; prior cache retained.")
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
