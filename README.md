# iSiTSCENE v0.8.2

iSiTSCENE inventories immediate movie-release folders and checks whether each
folder name exactly matches a release registered in SRRDB.

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
