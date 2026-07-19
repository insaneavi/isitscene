# iSiTSCENE v0.2

This version separates configuration, database, scanning, SRRDB access, and web routes into individual modules.

## Hidden/system folder toggle

Set the Unraid environment variable:

```text
SKIP_HIDDEN_SYSTEM_FOLDERS=true
```

With `true`, the scanner skips all dot-prefixed folders and common system folders such as `.Recycle.Bin`, `$RECYCLE.BIN`, `System Volume Information`, `@eaDir`, and `lost+found`.

Set it to `false` to include every immediate subfolder in the scan.

## Upgrade from the first build

Extract the ZIP and copy all contents into your existing local repository folder. Allow Windows to replace existing files. The ZIP contains no `.git` directory, so your Git history remains intact.

Then run:

```powershell
git status
git add .
git commit -m "Refactor app and add hidden folder toggle"
git push
```

After GitHub Actions finishes, update the container in Unraid and add:

```text
Name: Skip Hidden/System Folders
Key: SKIP_HIDDEN_SYSTEM_FOLDERS
Value: true
```
