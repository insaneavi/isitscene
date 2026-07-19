# iSiTSCENE

A small self-hosted application that inventories immediate folders under `/movies` and checks whether each exact folder name is registered in SRRDB.

## Unraid paths

- `/mnt/user/movies` → `/movies` (read-only)
- `/mnt/user/appdata/isitscene` → `/config` (read/write)

## Push this repository

```bash
git init
git branch -M main
git remote add origin https://github.com/insaneavi/isitscene.git
git add .
git commit -m "Initial iSiTSCENE MVP"
git push -u origin main
```

GitHub Actions publishes `ghcr.io/insaneavi/isitscene:latest`.

## Run on Unraid

```bash
docker run -d \
  --name isitscene \
  --restart unless-stopped \
  -p 8080:8080 \
  -e TZ=America/New_York \
  -e SCAN_INTERVAL_HOURS=24 \
  -e SRRDB_DELAY_SECONDS=1.5 \
  -v /mnt/user/movies:/movies:ro \
  -v /mnt/user/appdata/isitscene:/config \
  ghcr.io/insaneavi/isitscene:latest
```

Open `http://192.168.49.5:8080` and click **Scan Now**.

The application does not inspect, rename, delete, or alter movie files. It is not affiliated with SRRDB.
