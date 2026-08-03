# iSiTSCENE v0.15.1

iSiTSCENE inventories immediate movie-release folders and checks whether each
folder name exactly matches a release registered in SRRDB.

## New in v0.15.1

- Fixes Pixar, Disney Animated Classics, and Studio Ghibli failing to import on upgraded SQLite databases.
- Stores a blank compatibility value when a bundled title intentionally uses title/year fallback instead of an IMDb ID.
- Automatically retries bundled lists whose previous import status is failed.
- Existing DC Animated Movie Universe, MCU, and other cached lists remain unchanged.
- No database schema migration is required.

## New in v0.12.0

- Adds Pixar Feature Films with 31 titles through Toy Story 5.
- Adds Disney Animated Classics with 63 Walt Disney Animation Studios canon titles through Zootopia 2.
- Adds the Marvel Cinematic Universe with 37 theatrical films through The Fantastic Four: First Steps.
- Adds the official 15-film DC Animated Movie Universe continuity.
- Refreshes the bundled Studio Ghibli snapshot date.
- Each collection uses the existing owned/missing filters, completion cards, source date, version, and local cache timestamp.
- New lists import automatically at application startup.
- No database schema migration is required.

## New in v0.12.0

- Replaces fragile IMDb webpage scraping with bundled, versioned movie lists.
- Includes IMDb Top 100, IMDb Top 250, Academy Award Best Picture Winners, AFI 100 Years…100 Movies, and Studio Ghibli Feature Films.
- Imports all bundled lists automatically on first launch.
- Adds independent Reload Bundled List controls.
- Displays both the list data date and the local SQLite cache timestamp.
- Adds overview cards showing owned count and completion percentage for every list.
- Matches by IMDb ID first and title/year as a fallback where a bundled entry lacks an IMDb ID.
- Keeps manual IMDb overrides authoritative during ownership matching.
- Advances the automatic SQLite schema to version 13.

## New in v0.12.0

- Fixes IMDb Top 100 synchronization after IMDb removed the page structure previously expected by the importer.
- Uses IMDb's official Top 250 chart and caches its first 100 ranked movies.
- Adds JSON-LD, embedded JSON, and rendered HTML parsing fallbacks.
- Sends browser-like request headers for improved compatibility.
- Replaces the local cache only after all 100 records are parsed successfully.
- Preserves the previous cache when IMDb is unavailable or changes its page again.
- No database migration is required.

## New in v0.12.0

- Adds **Movie Lists** with an IMDb Top 100 collection-completion view.
- Uses a manual Sync button and caches the list in SQLite.
- Shows the cache date and never performs unnecessary automatic list refreshes.
- Matches owned movies through exact cached IMDb IDs.
- Shows All, Owned, and Missing views plus completion statistics.
- Keeps the previous cache intact if a synchronization fails.

## New in v0.12.0

- Adds **Duplicate Finder** for different releases of the same movie.
- Stores IMDb IDs directly on release records and reuses them in later scans.
- Queries SRRDB only for new, changed, previously failed, or uncached releases.
- Groups confirmed duplicates by IMDb ID and possible duplicates by normalized title and year.
- Displays resolution, source, codec, release group, and edition-related tags.
- Adds **Keep Both** and **Cleanup Needed** review states.
- Includes independent Start, Stop, force-reset, live progress, and interrupted-scan recovery.
- Updates Collection Upgrade to use the same shared IMDb cache.
- Displays application version, build date, and Git commit in the web interface.
- Exposes build information through `GET /api/version`.
- Performs automatic SQLite schema migration; no manual database work is required.

## New in v0.8.3.2

- Fixes the remaining FastAPI route collision for Refresh Library Changes.
- Moves the refresh action out of the dynamic Collection Review namespace to `POST /scan/refresh-library`.
- Keeps the button on Collection Review while treating the operation as a scan action.
- Retains live refresh-status polling and automatic Collection Review reload.
- No database changes.

## New in v0.8.3.1

- Attempted to fix the Collection Review refresh route collision by renaming the endpoint.
- Added live refresh-status polling and automatic Collection Review reload.

## New in v0.8.3

- Adds a Refresh Library Changes button to Collection Review.
- Re-inventories only top-level movie-folder names.
- Marks disappeared folders as Removed.
- Adds newly discovered or renamed folders as new release records.
- Verifies only newly discovered folders against SRRDB.
- Leaves all unchanged releases untouched.
- Uses the existing scan lock and progress tracking to prevent overlapping
  full scans and library refreshes.
- No database changes.

## New in v0.8.2

- Fixed Stage 2 candidate searches returning no results.
- Removed the SRRDB `group:` search keyword currently rejected by the API.
- Candidate searches now use title terms and score release metadata locally.
- Fixed Blu-ray.com title-extraction regex escaping.
- Releases without a year now stop before technical tags such as 1080p,
  REPACK, BluRay, x264, and similar metadata.
- No database changes.

## New in v0.8.1

- Redesigned the Releases page as a compact inventory list.
- Expanded the page to use nearly the full browser width.
- Reduced ordinary release rows to approximately 36 pixels high.
- Added a fixed-height table with internal vertical and horizontal scrolling.
- Added a sticky table header so column names remain visible.
- Added column sizing, ellipsis handling, hover feedback, and compact badges.
- No database or verification behavior changes.

## New in v0.8

- Adds Stage 2 advisory SRRDB candidate matching.
- Exact folder-name equality remains the only path to Verified status.
- Unverified releases can display a likely SRRDB candidate, score, likely
  difference, direct link, and candidate-check timestamp.
- Candidate matching compares title, group, resolution, source, codec,
  release flags, and year.
- Candidates below the confidence threshold are not displayed.
- Adds Collection Review filters for Candidate Found and No Candidate.
- Adds dashboard counts for candidate results.
- Existing SQLite databases are upgraded automatically.

## New in v0.7

- Adds a Blu-ray.com search button to each Collection Review item.
- Extracts the movie title using everything before the first four-digit year.
- Converts scene-style dots and underscores into readable search text.
- Opens the Blu-ray.com search in a new browser tab.
- Adds quick-comment buttons:
  - No Physical Blu-ray Release
  - Requested Movie
  - Personal Favorite
  - Unable to Locate Scene Release
  - Other
- Other clears and focuses the comment field for custom text.

## New in v0.6

- Adds a dedicated Collection Review page.
- Shows only present, unverified releases.
- Adds personal review decisions: Pending, Keep, Replace, and Ignored.
- Adds a persistent comment field for each reviewed release.
- Tracks the date and time each review was last saved.
- Adds dashboard review counts and direct links into the queue.
- Automatically upgrades existing databases without deleting scan history.

## New in v0.5

- Separates inventory state from verification state.
- Inventory is shown as Present or Removed.
- Verification is shown as Pending, Verified, or Unverified.
- Not Found, API Error, timeout, and malformed SRRDB responses display as Unverified.
- The detailed technical reason remains visible beneath the release name.
- Releases can be filtered independently by inventory and verification state.
- Existing v0.4 databases are upgraded automatically.

## v0.4.3 Releases search and branding fix

- Fixes the Releases page template variable mismatch.
- Releases can now be displayed and searched as soon as they enter inventory.
- Adds the official iSiTSCENE icon to the repository.
- Adds a browser favicon and header icon.
- Adds the GitHub-hosted icon URL to the Unraid template.

## v0.4.2 scanner recovery and controls

- Recovers stale running state after a Docker or application restart.
- Adds separate Start Scan and Stop Scan controls.
- Cancels the active SRRDB HTTP request when Stop Scan is pressed.
- Uses explicit network timeouts so an unresponsive release cannot block forever.
- Saves completed verification results when a scan is stopped.
- Leaves unfinished releases pending for the next scan.

## v0.4.1 search availability fix

- Inventory and SRRDB verification now run as separate phases.
- The inventory is committed before slow verification begins.
- Releases become searchable during an active scan.
- Large inventories are committed in batches of 250 folders.
- Scan progress clearly identifies inventory and verification phases.

## New in v0.4

- Live scan progress on the dashboard
- Current release being checked
- Processed and total counters
- Live verified, not-found, API-error, and skipped counts
- Automatic progress refresh every two seconds
- Automatic dashboard refresh when a scan completes
- Recent verification-results table

## v0.3.1 bug fix

- Fixes the scanner crash caused by calling `should_scan_folder()` without the current hidden/system-folder setting.
- Preserves all v0.3 web settings and Docker behavior.

## New in v0.3

Application settings are now stored in SQLite and editable from the Web UI:

- Skip hidden/system folders
- Enable or disable automatic scans
- Scan interval
- SRRDB request delay

Open:

```text
http://YOUR-UNRAID-IP:8080/settings
```

Changes apply immediately and do not require recreating the Docker container.

## Docker configuration

Only these Docker settings are required:

| Type | Host / Value | Container / Key |
|---|---|---|
| Port | `8080` | `8080` |
| Path | `/mnt/user/movies` | `/movies` (read-only) |
| Path | `/mnt/user/appdata/isitscene` | `/config` (read/write) |
| Variable | `America/New_York` | `TZ` |

The following old Docker variables are no longer used and can be removed:

```text
SCAN_INTERVAL_HOURS
SRRDB_DELAY_SECONDS
SKIP_HIDDEN_SYSTEM_FOLDERS
```

Existing values in those variables will be ignored by v0.3.

## Updating the repository

Copy this ZIP's contents over your existing local repository, keeping the
existing `.git` folder. Then run:

```powershell
git add .
git commit -m "Add web based application settings"
git push
```

After GitHub Actions succeeds, update the container in Unraid.
