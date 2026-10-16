"""Read-only discovery feeds, persisted independently of inventory."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
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
_PROGRESS = {"running": False, "phase": "idle", "completed": 0, "total": 3, "percent": 0, "started_at": None, "finished_at": None, "logs": []}

def _log(message):
    _PROGRESS["logs"].append({"time": datetime.now(timezone.utc).strftime("%H:%M:%S"), "message": str(message)})
    _PROGRESS["logs"] = _PROGRESS["logs"][-120:]
    log.info("What's New: %s", message)

def _step(message):
    _PROGRESS["completed"] += 1
    _PROGRESS["percent"] = round(100 * _PROGRESS["completed"] / _PROGRESS["total"])
    _log(message)


EXCLUDED_RELEASE_TERMS = ("MULTI", "GERMAN", "DUAL", "SUBFRENCH", "ITALIAN", "JAPANESE", "FRENCH", "SPANISH", "LATIN")

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
    if any(term in upper for term in EXCLUDED_RELEASE_TERMS):
        return False
    tokens = re.split(r"[._\s-]+", name.casefold())
    required = CATEGORIES[category]
    return all(required[k] in tokens for k in ("resolution", "source", "codec"))


def _fetch_srrdb(client, category):
    found, seen = [], set()
    for page in range(1, 81):
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
        if len(found) >= 100:
            break
    found.sort(key=lambda item: item["date"], reverse=True)
    return found[:100]


def _parse_calendar_page(html, cutoff, today):
    """Parse date-header rows and the movie rows that follow them."""
    soup = BeautifulSoup(html, "html.parser")
    results = {}
    current_date = None
    date_re = re.compile(
        r"\\b(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\\s+\\d{1,2},?\\s+20\\d{2}\\b"
    )
    for row in soup.select("tr"):
        # Date rows are distinct from film rows; preserve the last date heading.
        row_text = row.get_text(" ", strip=True)
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        date_match = date_re.fullmatch(row_text)
        if date_match:
            current_date = datetime.strptime(
                date_match.group(0).replace(",", ""), "%B %d %Y"
            ).date()
            continue
        if current_date is None or not cutoff <= current_date <= today:
            continue
        # The first cell contains the film title, followed by descriptive text.
        first = cells[0]
        link = first.select_one('a[href*="/release/"], a[href*="/title/"]')
        if not link:
            continue
        title = link.get_text(" ", strip=True)
        if not title:
            continue
        description = first.get_text(" ", strip=True)
        if re.search(r"re-release|anniversary|special screening|remaster", description, re.I):
            continue
        href = link.get("href", "")
        if not (href.startswith("/") or href.startswith("https://www.boxofficemojo.com/")):
            continue
        scale_text = cells[-1].get_text(" ", strip=True)
        scale = "Wide" if scale_text == "Wide" else ("Limited" if scale_text == "Limited" else "")
        key = (title.casefold(), current_date.isoformat())
        results[key] = {
            "title": title,
            "date": current_date.isoformat(),
            "scale": scale,
            "url": ("https://www.boxofficemojo.com" + href if href.startswith("/") else href),
        }
    return results


def _fetch_movies(client):
    """Read Box Office Mojo monthly domestic calendar, using dated heading rows."""
    today = date.today()
    cutoff = today - timedelta(days=365)
    cursor = date(cutoff.year, cutoff.month, 1)
    results = {}
    failures = []
    while cursor <= today:
        url = f"https://www.boxofficemojo.com/calendar/{cursor.isoformat()}/"
        _PROGRESS["phase"] = f"Box Office Mojo {cursor:%Y-%m}"
        _log(f"Fetching calendar {cursor:%Y-%m}")
        try:
            response = client.get(url)
            response.raise_for_status()
            _log(f"{cursor:%Y-%m}: HTTP {response.status_code}; {len(response.text)} HTML characters; {response.text.lower().count('<tr')} table rows")
            parsed = _parse_calendar_page(response.text, cutoff, today)
            _log(f"{cursor:%Y-%m}: {len(parsed)} movie rows parsed")
            results.update(parsed)
            if not parsed:
                failures.append(f"{cursor:%Y-%m}: no rows recognized")
        except (httpx.HTTPError, ValueError) as exc:
            failures.append(f"{cursor:%Y-%m}: {exc}")
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    if not results:
        raise ValueError(
            "No movies parsed from Box Office Mojo calendar. "
            + "; ".join(failures[:3])
        )
    if failures:
        log.warning("Partial Box Office Mojo import: %s", "; ".join(failures))
    return sorted(results.values(), key=lambda item: item["date"], reverse=True)

def refresh():
    global _running, ERROR
    if not _lock.acquire(blocking=False):
        return
    _running = True
    ERROR = None
    _PROGRESS.update({"running": True, "phase": "Starting", "completed": 0, "total": 3, "percent": 0,
                      "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None, "logs": []})
    _log("Feed refresh started")
    try:
        cache = read_cache()
        errors = []
        with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "iSiTSCENE/0.17.2"}) as client:
            for key in CATEGORIES:
                _PROGRESS["phase"] = f"SRRDB {CATEGORIES[key]['label']}"
                _log(f"Searching SRRDB {CATEGORIES[key]['label']} (target: 100)")
                try:
                    results = _fetch_srrdb(client, key)
                    _log(f"SRRDB {key}: {len(results)} qualifying releases")
                    if results:
                        cache.setdefault("srrdb", {})[key] = results
                    else:
                        errors.append(f"SRRDB {key}: no qualifying results; prior cache retained.")
                except Exception as exc:
                    log.warning("What's New SRRDB %s refresh failed: %s", key, exc)
                    errors.append(f"SRRDB {key}: {exc}")
                    _log(f"ERROR SRRDB {key}: {exc}")
                finally:
                    _step(f"SRRDB {key} finished")
            _PROGRESS["phase"] = "Box Office Mojo"
            try:
                movies = _fetch_movies(client)
                if movies:
                    cache["movies"] = movies
                    _log(f"Box Office Mojo: {len(movies)} movies")
                else:
                    errors.append("Box Office Mojo: no release rows could be parsed; previous cache retained.")
            except Exception as exc:
                log.warning("Box Office Mojo refresh failed: %s", exc)
                errors.append(f"Box Office Mojo: {exc}")
                _log(f"ERROR Box Office Mojo: {exc}")
            finally:
                _step("Box Office Mojo finished")
        cache["updated_at"] = datetime.now(timezone.utc).isoformat()
        cache["errors"] = errors
        _save(cache)
        ERROR = "; ".join(errors) if errors else None
    finally:
        _running = False
        _PROGRESS["running"] = False
        _PROGRESS["phase"] = "Completed" if not ERROR else "Completed with errors"
        _PROGRESS["finished_at"] = datetime.now(timezone.utc).isoformat()
        _log(_PROGRESS["phase"])
        _lock.release()

def start_refresh():
    if _running or _lock.locked():
        return False
    threading.Thread(target=refresh, name="whats-new-refresh", daemon=True).start()
    return True


def status():
    return {**_PROGRESS, "error": ERROR}
