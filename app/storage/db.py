"""SQLAlchemy database bootstrap."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
import logging

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.storage.models import Base

DEFAULT_SQLITE_JOURNAL_MODE = "WAL"
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 5_000

logger = logging.getLogger(__name__)


class Database:
    """Thin wrapper around a SQLAlchemy engine and session factory."""

    def __init__(self, db_url: str) -> None:
        connect_args: dict[str, object] = {}
        db_url = _normalize_sqlite_url(db_url)
        self.db_url = db_url
        self.is_sqlite = db_url.startswith("sqlite:///")
        if self.is_sqlite:
            connect_args["check_same_thread"] = False
        self.engine = create_engine(db_url, future=True, connect_args=connect_args)
        if self.is_sqlite:
            _configure_sqlite_engine(
                self.engine,
                journal_mode=DEFAULT_SQLITE_JOURNAL_MODE,
                busy_timeout_ms=DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
            )
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        if self.is_sqlite:
            self._ensure_sqlite_schema()

    def _ensure_sqlite_schema(self) -> None:
        inspector = inspect(self.engine)
        columns_by_table = {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in inspector.get_table_names()
        }
        additions: dict[str, list[tuple[str, str]]] = {
            "signals": [
                ("strategy_type", "TEXT"),
                ("strategy_subtype", "TEXT"),
                ("model_version", "TEXT"),
            ],
            "signal_outcomes": [
                ("risk_adjusted_status", "TEXT"),
                ("squeeze_extension_pct", "FLOAT"),
                ("is_clean_short", "BOOLEAN"),
                ("is_squeeze_before_tp", "BOOLEAN"),
            ],
            "strategy_observations": [
                ("outcome_status", "TEXT"),
                ("outcome_json", "JSON"),
                ("outcome_mfe_pct", "FLOAT"),
                ("outcome_mae_pct", "FLOAT"),
                ("outcome_time_to_mfe_minutes", "FLOAT"),
                ("outcome_time_to_mae_minutes", "FLOAT"),
                ("outcome_new_high_after_observation", "BOOLEAN"),
                ("outcome_updated_at", "TIMESTAMP"),
            ],
            "climax_evaluations": [
                ("runtime_instance_id", "TEXT"),
                ("root_event_id", "TEXT"),
                ("event_revision", "INTEGER"),
                ("attempt_id", "TEXT"),
                ("observed_at", "TIMESTAMP"),
                ("market_asof", "TIMESTAMP"),
                ("pool_added_at", "TIMESTAMP"),
                ("event_age_sec", "FLOAT"),
                ("pool_age_sec", "FLOAT"),
                ("evaluation_completed_at", "TIMESTAMP"),
                ("live_decision", "TEXT"),
                ("live_veto_reasons_json", "TEXT"),
                ("shadow_decision", "TEXT"),
                ("shadow_veto_reasons_json", "TEXT"),
                ("decision_delta", "TEXT"),
                ("shadow_hypothetical_entry_price", "FLOAT"),
                ("shadow_hypothetical_grade", "TEXT"),
                ("shadow_hypothetical_score", "INTEGER"),
                ("shadow_removed_vetoes_json", "TEXT"),
            ],
            "climax_monitor_events": [
                ("runtime_instance_id", "TEXT"),
                ("root_event_id", "TEXT"),
                ("event_revision", "INTEGER"),
                ("attempt_id", "TEXT"),
                ("observed_at", "TIMESTAMP"),
                ("market_asof", "TIMESTAMP"),
                ("pool_added_at", "TIMESTAMP"),
                ("event_age_sec", "FLOAT"),
                ("pool_age_sec", "FLOAT"),
            ],
            "runtime_heartbeats": [
                ("runtime_instance_id", "TEXT"),
                ("model_version", "TEXT"),
                ("config_fingerprint", "TEXT"),
            ],
            "reject_stats": [
                ("derivatives_status", "TEXT"),
                ("derivatives_reasons_json", "JSON"),
                ("data_quality_warnings_json", "JSON"),
            ],
        }
        with self.engine.begin() as connection:
            for table_name, columns in additions.items():
                existing = columns_by_table.get(table_name, set())
                for column_name, ddl_type in columns:
                    if column_name in existing:
                        continue
                    connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_type}")
            if "signals" in columns_by_table:
                index_names = {index["name"] for index in inspect(self.engine).get_indexes("signals")}
                constraint_names = {constraint["name"] for constraint in inspect(self.engine).get_unique_constraints("signals")}
                duplicate = connection.exec_driver_sql(
                    "SELECT 1 FROM signals "
                    "WHERE strategy_subtype IS NOT NULL AND model_version IS NOT NULL "
                    "GROUP BY symbol, event_id, strategy_subtype, model_version HAVING COUNT(*) > 1 LIMIT 1"
                ).first()
                if duplicate:
                    logger.error("Skipping enriched signal identity index: existing duplicate rows require explicit repair")
                elif "uq_signal_enriched_identity" not in index_names and "uq_signal_enriched_identity" not in constraint_names:
                    connection.exec_driver_sql(
                        "CREATE UNIQUE INDEX uq_signal_enriched_identity "
                        "ON signals(symbol, event_id, strategy_subtype, model_version)"
                    )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_sqlite_pragmas(self) -> dict[str, int | str] | None:
        """Return the effective SQLite pragma values for the current engine."""

        if not self.is_sqlite:
            return None
        with self.engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
        return {
            "journal_mode": str(journal_mode),
            "busy_timeout": int(busy_timeout),
        }

    def write_heartbeat(self) -> dict[str, int | str]:
        """Perform a lightweight write probe against the configured database."""

        checked_at = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS __db_heartbeat (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    checked_at TEXT NOT NULL
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO __db_heartbeat (id, checked_at)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET checked_at = excluded.checked_at
                """,
                (checked_at,),
            )
            stored_value = connection.exec_driver_sql(
                "SELECT checked_at FROM __db_heartbeat WHERE id = 1"
            ).scalar_one()

        pragmas = self.get_sqlite_pragmas() or {}
        return {
            "db_url": self.db_url,
            "checked_at": str(stored_value),
            **pragmas,
        }


def _normalize_sqlite_url(db_url: str) -> str:
    """Resolve SQLite file URLs to absolute writable filesystem paths."""

    url = make_url(db_url)
    if url.get_backend_name() != "sqlite":
        return db_url

    database = url.database
    if not database or database == ":memory:":
        return db_url

    db_path = Path(database).expanduser()
    if not db_path.is_absolute():
        db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    normalized = url.set(database=str(db_path))
    return normalized.render_as_string(hide_password=False)


def _configure_sqlite_engine(engine: object, journal_mode: str, busy_timeout_ms: int) -> None:
    """Attach connect-time SQLite pragmas for long-running service behavior."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA journal_mode={journal_mode}")
        cursor.fetchone()
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cursor.close()
