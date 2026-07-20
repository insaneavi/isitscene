from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
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
    verification_status: Mapped[str] = mapped_column(
        String,
        default="pending",
        index=True,
    )
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    matched_release: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ignored: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Personal collection-review workflow for present, unverified releases.
    review_status: Mapped[str] = mapped_column(
        String,
        default="pending",
        index=True,
    )
    review_comment: Mapped[str] = mapped_column(Text, default="")
    last_reviewed: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # Advisory Stage 2 SRRDB candidate. This never changes verification.
    candidate_release: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
    )
    candidate_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    candidate_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    candidate_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    candidate_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )


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
    unverified: Mapped[int] = mapped_column(Integer, default=0)
    not_found: Mapped[int] = mapped_column(Integer, default=0)
    api_errors: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScanProgress(Base):
    __tablename__ = "scan_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    phase: Mapped[str] = mapped_column(String, default="idle")
    current_release: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    verified_count: Mapped[int] = mapped_column(Integer, default=0)
    unverified_count: Mapped[int] = mapped_column(Integer, default=0)
    not_found_count: Mapped[int] = mapped_column(Integer, default=0)
    api_error_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class UpgradeScan(Base):
    __tablename__ = "upgrade_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")
    eligible_count: Mapped[int] = mapped_column(Integer, default=0)
    checked_count: Mapped[int] = mapped_column(Integer, default=0)
    upgrades_found: Mapped[int] = mapped_column(Integer, default=0)
    no_upgrade_count: Mapped[int] = mapped_column(Integer, default=0)
    imdb_missing_count: Mapped[int] = mapped_column(Integer, default=0)
    api_error_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class UpgradeResult(Base):
    __tablename__ = "upgrade_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    release_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    current_release: Mapped[str] = mapped_column(String, index=True)
    imdb_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default="not_checked", index=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class UpgradeCandidate(Base):
    __tablename__ = "upgrade_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upgrade_result_id: Mapped[int] = mapped_column(Integer, index=True)
    release_name: Mapped[str] = mapped_column(String, index=True)
    srrdb_url: Mapped[str] = mapped_column(Text)


class UpgradeProgress(Base):
    __tablename__ = "upgrade_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    phase: Mapped[str] = mapped_column(String, default="idle")
    current_release: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    upgrades_found: Mapped[int] = mapped_column(Integer, default=0)
    no_upgrade_count: Mapped[int] = mapped_column(Integer, default=0)
    imdb_missing_count: Mapped[int] = mapped_column(Integer, default=0)
    api_error_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


def _add_column_if_missing(
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return

    columns = {
        column["name"]
        for column in inspector.get_columns(table_name)
    }

    if column_name not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN {column_name} {definition}"
                )
            )


def _migrate_schema() -> None:
    _add_column_if_missing(
        "scan_runs",
        "skipped_folders",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        "scan_runs",
        "unverified",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        "scan_progress",
        "unverified_count",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        "releases",
        "verification_status",
        "VARCHAR NOT NULL DEFAULT 'pending'",
    )
    _add_column_if_missing(
        "releases",
        "review_status",
        "VARCHAR NOT NULL DEFAULT 'pending'",
    )
    _add_column_if_missing(
        "releases",
        "review_comment",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        "releases",
        "last_reviewed",
        "DATETIME",
    )
    _add_column_if_missing(
        "releases",
        "candidate_release",
        "VARCHAR",
    )
    _add_column_if_missing(
        "releases",
        "candidate_url",
        "TEXT",
    )
    _add_column_if_missing(
        "releases",
        "candidate_score",
        "INTEGER",
    )
    _add_column_if_missing(
        "releases",
        "candidate_reason",
        "TEXT",
    )
    _add_column_if_missing(
        "releases",
        "candidate_checked_at",
        "DATETIME",
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE releases
                SET verification_status =
                    CASE
                        WHEN status = 'verified' THEN 'verified'
                        WHEN status = 'pending' THEN 'pending'
                        WHEN status IN ('not_found', 'api_error') THEN 'unverified'
                        WHEN status = 'missing' THEN
                            CASE
                                WHEN last_checked IS NULL THEN 'pending'
                                ELSE 'unverified'
                            END
                        ELSE 'pending'
                    END
                WHERE verification_status IS NULL
                   OR verification_status = ''
                   OR verification_status = 'pending'
                """
            )
        )


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_schema()

    db = SessionLocal()
    try:
        changed = False
        if db.get(ScanProgress, 1) is None:
            db.add(ScanProgress(id=1))
            changed = True
        if db.get(UpgradeProgress, 1) is None:
            db.add(UpgradeProgress(id=1))
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()
