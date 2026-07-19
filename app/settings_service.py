from __future__ import annotations

from dataclasses import dataclass

from .database import AppSetting, SessionLocal

DEFAULTS = {
    "skip_hidden_system_folders": "true",
    "auto_scan_enabled": "true",
    "scan_interval_hours": "24",
    "srrdb_delay_seconds": "1.5",
}


@dataclass(frozen=True)
class RuntimeSettings:
    skip_hidden_system_folders: bool
    auto_scan_enabled: bool
    scan_interval_hours: int
    srrdb_delay_seconds: float


def _to_bool(value: str, default: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def ensure_defaults() -> None:
    db = SessionLocal()
    try:
        for key, value in DEFAULTS.items():
            if db.get(AppSetting, key) is None:
                db.add(AppSetting(key=key, value=value))
        db.commit()
    finally:
        db.close()


def get_settings() -> RuntimeSettings:
    ensure_defaults()
    db = SessionLocal()
    try:
        values = {item.key: item.value for item in db.query(AppSetting).all()}
    finally:
        db.close()

    return RuntimeSettings(
        skip_hidden_system_folders=_to_bool(
            values.get("skip_hidden_system_folders", "true"), True
        ),
        auto_scan_enabled=_to_bool(
            values.get("auto_scan_enabled", "true"), True
        ),
        scan_interval_hours=max(
            1, int(values.get("scan_interval_hours", "24"))
        ),
        srrdb_delay_seconds=max(
            0.0, float(values.get("srrdb_delay_seconds", "1.5"))
        ),
    )


def save_settings(
    *,
    skip_hidden_system_folders: bool,
    auto_scan_enabled: bool,
    scan_interval_hours: int,
    srrdb_delay_seconds: float,
) -> None:
    updates = {
        "skip_hidden_system_folders": str(skip_hidden_system_folders).lower(),
        "auto_scan_enabled": str(auto_scan_enabled).lower(),
        "scan_interval_hours": str(max(1, scan_interval_hours)),
        "srrdb_delay_seconds": str(max(0.0, srrdb_delay_seconds)),
    }

    db = SessionLocal()
    try:
        for key, value in updates.items():
            item = db.get(AppSetting, key)
            if item is None:
                db.add(AppSetting(key=key, value=value))
            else:
                item.value = value
        db.commit()
    finally:
        db.close()
