# Changelog

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
