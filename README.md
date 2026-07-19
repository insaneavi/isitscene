# iSiTSCENE v0.3

iSiTSCENE inventories immediate movie-release folders and checks whether each
folder name exactly matches a release registered in SRRDB.

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
