# Changelog

## v0.8.3.1

- Fixed FastAPI interpreting `refresh` as a numeric Collection Review release ID.
- Changed the refresh endpoint to `/collection-review/refresh-library-changes`.
- Added live status text and automatic Collection Review reload after refresh completion.
- No database changes.

## v0.8.3

- Added Refresh Library Changes to Collection Review.
- Added lightweight top-level inventory refresh.
- Added targeted verification for newly discovered folders only.
- Renamed folders are represented as an old Removed record and a new release.
- Reused scan locking, stopping, progress, and scan-history behavior.
- No database changes.


## v0.8.2

- Fixed Stage 2 candidate searches by removing the rejected `group:` keyword.
- Retained group, resolution, source, codec, flags, and year as local scoring signals.
- Fixed Blu-ray.com search-title regex escaping.
- Added technical-tag fallback parsing for releases without a year.
- No database changes.


## v0.8.1

- Made the Releases page nearly full-width.
- Reduced row height and whitespace for a clean inventory-list view.
- Added an internally scrollable table with visible scrollbars.
- Added a sticky header and more practical fixed column widths.
- Added compact badges, truncation, hover states, and mobile adjustments.
- No database changes.


## v0.8

- Added advisory Stage 2 SRRDB candidate search.
- Kept exact verification rules unchanged.
- Added candidate scoring and likely-difference explanations.
- Added candidate fields and automatic SQLite migration.
- Added candidate display, SRRDB links, filters, and dashboard counts.
- Candidate API failures remain non-fatal and never verify a release.


## v0.7

- Added automatic title extraction from release folder names.
- Added per-release Blu-ray.com search links.
- Added one-click comment presets and custom Other notes.
- Added responsive styling for the new controls.


## v0.6

- Added the Collection Review workflow.
- Added review status, comments, and last-reviewed timestamps.
- Limited the review queue to present, unverified releases.
- Added review-status filtering and release-name search.
- Added dashboard counts for Pending, Keep, and Replace Later.
- Added automatic SQLite schema migration for review fields.


## v0.5

- Split inventory and verification state.
- Added Present and Removed inventory labels.
- Added Pending, Verified, and Unverified verification labels.
- Consolidated not-found and API failures under Unverified.
- Preserved detailed SRRDB failure reasons.
- Added independent inventory and verification filters.
- Added automatic database migration from v0.4.x.


## v0.4.3

- Fixed the Releases page context variable so results render correctly.
- Restored release filtering and searching during active verification.
- Added official application icon assets.
- Added favicon and header branding.
- Added an Unraid template Icon URL.


## v0.4.2

- Added stale scan recovery during application startup.
- Added Start Scan and Stop Scan dashboard actions.
- Added cooperative cancellation during inventory, HTTP requests, and delays.
- Added explicit SRRDB connect/read/write/pool timeouts.
- Added stopped and interrupted scan states.


## v0.4.1

- Split scanning into inventory and verification phases.
- Made discovered folders searchable before SRRDB verification completes.
- Added batch commits during inventory for large archives.
- Added clearer inventory and verification progress messages.


## v0.4

- Added persistent scan progress tracking.
- Added `/api/scan-status`.
- Added dashboard progress polling.
- Added current release and live counters.
- Added recent verification results.
- Disabled the Scan button while a scan is active.

## v0.3.1

- Fixed hidden/system-folder setting argument during scans.
