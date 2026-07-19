from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DATA_PATH, DATABASE_PATH

DATA_PATH.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DATABASE_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Release(Base):
    __tablename__ = "releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folder_name: Mapped[str] = mapped_column(String, unique=True, index=True)
    normalized_name: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    matched_release: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ignored: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")
    folders_found: Mapped[int] = mapped_column(Integer, default=0)
    skipped_folders: Mapped[int] = mapped_column(Integer, default=0)
    new_folders: Mapped[int] = mapped_column(Integer, default=0)
    missing_folders: Mapped[int] = mapped_column(Integer, default=0)
    exact_matches: Mapped[int] = mapped_column(Integer, default=0)
    not_found: Mapped[int] = mapped_column(Integer, default=0)
    api_errors: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


def _migrate_scan_runs() -> None:
    inspector = inspect(engine)
    if "scan_runs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("scan_runs")}
    if "skipped_folders" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE scan_runs "
                    "ADD COLUMN skipped_folders INTEGER NOT NULL DEFAULT 0"
                )
            )


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_scan_runs()
