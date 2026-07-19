from __future__ import annotations

import os
from pathlib import Path


def get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


APP_NAME = "iSiTSCENE"
MOVIES_PATH = Path(os.getenv("MOVIES_PATH", "/movies"))
DATA_PATH = Path(os.getenv("DATA_PATH", "/config"))
DATABASE_PATH = DATA_PATH / "isitscene.db"
SCAN_INTERVAL_HOURS = int(os.getenv("SCAN_INTERVAL_HOURS", "24"))
SRRDB_DELAY_SECONDS = float(os.getenv("SRRDB_DELAY_SECONDS", "1.5"))
TZ = os.getenv("TZ", "America/New_York")

SKIP_HIDDEN_SYSTEM_FOLDERS = get_bool_env(
    "SKIP_HIDDEN_SYSTEM_FOLDERS",
    True,
)

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
