# Changelog

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
