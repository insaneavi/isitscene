from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "iSiTSCENE"
APP_VERSION = os.getenv("APP_VERSION", "0.10.1")
BUILD_DATE = os.getenv("BUILD_DATE", "development")
GIT_COMMIT = os.getenv("GIT_COMMIT", "development")
DATABASE_VERSION = 10
MOVIES_PATH = Path(os.getenv("MOVIES_PATH", "/movies"))
DATA_PATH = Path(os.getenv("DATA_PATH", "/config"))
DATABASE_PATH = DATA_PATH / "isitscene.db"
TZ = os.getenv("TZ", "America/New_York")

SYSTEM_FOLDERS = {
    ".Recycle.Bin",
    "$RECYCLE.BIN",
    "System Volume Information",
    "@eaDir",
    "lost+found",
    ".Trashes",
    ".Trash-1000",
    ".Spotlight-V100",
    ".fseventsd",
}
