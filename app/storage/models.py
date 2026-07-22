"""ORM models for the lightweight SQLite schema."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """Return the current UTC timestamp for ORM defaults."""

    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base."""


class SignalModel(Base):
    """Saved Telegram signal or WATCH entry."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    signal_type: Mapped[str] = mapped_column(String(32))
    grade: Mapped[str] = mapped_column(String(1))
    score: Mapped[int] = mapped_column(Integer)
    market_price: Mapped[float] = mapped_column(Float)
    short_zone_low: Mapped[float] = mapped_column(Float)
    short_zone_high: Mapped[float] = mapped_column(Float)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    event_high: Mapped[float] = mapped_column(Float)
    event_base_price: Mapped[float] = mapped_column(Float)
    event_range_pct: Mapped[float] = mapped_column(Float)
    pullback_from_high_pct: Mapped[float] = mapped_column(Float)
    dist_to_vwap_pct: Mapped[float] = mapped_column(Float)
    upper_wick_ratio: Mapped[float] = mapped_column(Float)
    rejection_from_high_pct: Mapped[float] = mapped_column(Float)
    vol_zscore_30m: Mapped[float] = mapped_column(Float)
    dist_to_ema20_atr: Mapped[float] = mapped_column(Float)
    rsi_15m: Mapped[float] = mapped_column(Float)
    ret_1h: Mapped[float] = mapped_column(Float)
    ret_4h: Mapped[float] = mapped_column(Float)
    range_atr_ratio: Mapped[float] = mapped_column(Float)
    oi_change_15m: Mapped[float | None] = mapped_column(Float, nullable=True)
    oi_change_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_json: Mapped[dict] = mapped_column(JSON)
    strategy_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    outcome: Mapped["SignalOutcomeModel | None"] = relationship(back_populates="signal", uselist=False)


class SignalOutcomeModel(Base):
    """Post-signal outcome measurements."""

    __tablename__ = "signal_outcomes"

    signal_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("signals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    price_after_15m: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after_4h: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    reached_vwap: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    time_to_vwap_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tp1_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stopped_virtual: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    risk_adjusted_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    squeeze_extension_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_clean_short: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_squeeze_before_tp: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    signal: Mapped[SignalModel] = relationship(back_populates="outcome")


class WatchCandidateModel(Base):
    """Saved non-actionable watch/near-signal candidate with nullable metrics."""

    __tablename__ = "watch_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(16), index=True)
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    score: Mapped[int] = mapped_column(Integer)
    base_grade: Mapped[str] = mapped_column(String(1))
    signal_type: Mapped[str] = mapped_column(String(32), default="Watch")
    actionable: Mapped[bool] = mapped_column(Boolean, default=False)
    blockers_json: Mapped[list] = mapped_column(JSON, default=list)
    risk_flags_json: Mapped[list] = mapped_column(JSON, default=list)
    squeeze_risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    squeeze_risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    squeeze_risk_reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    data_quality_warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    dist_to_vwap_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper_wick_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    rejection_from_high_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_zscore_30m: Mapped[float | None] = mapped_column(Float, nullable=True)
    pullback_from_event_high_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dist_to_ema20_atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi_15m: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    orderbook_depth_1pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class RejectStatModel(Base):
    """Per-candidate reject/watch journal for analytics."""

    __tablename__ = "reject_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(16), index=True)
    decision_type: Mapped[str] = mapped_column(String(16), index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    blockers_json: Mapped[list] = mapped_column(JSON, default=list)
    risk_flags_json: Mapped[list] = mapped_column(JSON, default=list)
    close_to_watch: Mapped[bool] = mapped_column(Boolean, default=False)
    squeeze_risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    derivatives_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    derivatives_reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    data_quality_warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class EventStateModel(Base):
    """Persistent symbol-level event state."""

    __tablename__ = "event_states"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    event_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_high_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_base_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_range_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_features_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    trigger_window: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pullback_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pullback_depth_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pullback_low_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ClimaxEvaluationModel(Base):
    """Append-only evidence journal for every climax evaluator invocation."""

    __tablename__ = "climax_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    evaluation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    subtype_candidate: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    event_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_high_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidate_added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidate_age_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    fast_monitor: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    poll_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_asof: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candles_asof: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    oi_asof: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    orderbook_asof: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    grade: Mapped[str] = mapped_column(String(1), default="C")
    actionable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    admission_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    veto_reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    passed_conditions_json: Mapped[list] = mapped_column(JSON, default=list)
    data_quality_json: Mapped[list] = mapped_column(JSON, default=list)
    liquidity_json: Mapped[dict] = mapped_column(JSON, default=dict)
    oi_json: Mapped[dict] = mapped_column(JSON, default=dict)
    features_json: Mapped[dict] = mapped_column(JSON, default=dict)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="EVALUATED")
    removal_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    # V3A observability fields. Nullable for legacy rows and backward-compatible migration.
    runtime_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    root_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    market_asof: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pool_added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_age_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    pool_age_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluation_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    live_veto_reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    shadow_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shadow_veto_reasons_json: Mapped[list] = mapped_column(JSON, default=list)
    decision_delta: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    shadow_hypothetical_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    shadow_hypothetical_grade: Mapped[str | None] = mapped_column(String(1), nullable=True)
    shadow_hypothetical_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shadow_removed_vetoes_json: Mapped[list] = mapped_column(JSON, default=list)


class ClimaxRootEventModel(Base):
    """Durable V3C root-event identity; shadow-only until explicitly activated."""

    __tablename__ = "climax_root_events"

    root_event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    subtype_family: Mapped[str] = mapped_column(String(64), default="CLIMAX_EXHAUSTION")
    event_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_base_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_high_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    peak_revision: Mapped[int] = mapped_column(Integer, default=1)
    initial_extension_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    initial_extension_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    initial_extension_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ClimaxEntryAttemptModel(Base):
    """Independent V3C entry-attempt lifecycle, persisted shadow-only."""

    __tablename__ = "climax_entry_attempts"

    attempt_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    root_event_id: Mapped[str] = mapped_column(String(128), index=True)
    attempt_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    attempt_trigger: Mapped[str] = mapped_column(String(64), default="structure_observed")
    local_retest_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    breakdown_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_state: Mapped[str] = mapped_column(String(32), default="WAITING_FOR_RETEST")
    attempt_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_close_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClimaxEntryAttemptEventModel(Base):
    """Append-only V3C attempt lifecycle history; shadow-only and delivery-free."""

    __tablename__ = "climax_entry_attempt_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    root_event_id: Mapped[str] = mapped_column(String(128), index=True)
    event_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    previous_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    market_asof: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    runtime_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)


class ClimaxMonitorEventModel(Base):
    """Append-only lifecycle journal for the bounded fast-monitor pool."""

    __tablename__ = "climax_monitor_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    event_high_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pool_size: Mapped[int] = mapped_column(Integer, default=0)
    poll_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # V3A explicit time/identity semantics. Nullable for legacy lifecycle rows.
    runtime_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    root_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    market_asof: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pool_added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_age_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    pool_age_sec: Mapped[float | None] = mapped_column(Float, nullable=True)


class RuntimeHeartbeatModel(Base):
    """Singleton runtime heartbeat, including fast-monitor liveness."""

    __tablename__ = "runtime_heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    fast_monitor_last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fast_monitor_last_complete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fast_monitor_pool_size: Mapped[int] = mapped_column(Integer, default=0)
    fast_monitor_poll_sequence: Mapped[int] = mapped_column(Integer, default=0)
    fast_monitor_last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    runtime_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    config_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RuntimeHeartbeatHistoryModel(Base):
    """Append-only runtime heartbeat history for V3A operational replay."""

    __tablename__ = "runtime_heartbeat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    runtime_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    main_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poll_sequence: Mapped[int] = mapped_column(Integer, default=0)
    pool_size: Mapped[int] = mapped_column(Integer, default=0)
    last_poll_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    full_scan_running: Mapped[bool] = mapped_column(Boolean, default=False)
    fast_monitor_running: Mapped[bool] = mapped_column(Boolean, default=False)
    event_loop_lag_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    poll_duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketScanRotationModel(Base):
    """Append-only aggregate for one observed eligible-universe rotation."""

    __tablename__ = "market_scan_rotations"

    rotation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    runtime_instance_id: Mapped[str] = mapped_column(String(64), index=True)
    model_version: Mapped[str] = mapped_column(String(32))
    config_fingerprint: Mapped[str] = mapped_column(String(64))
    rotation_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    rotation_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    exchange_universe_size: Mapped[int] = mapped_column(Integer, default=0)
    eligible_universe_size: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_unique_symbols: Mapped[int] = mapped_column(Integer, default=0)
    scanned_ok_unique_symbols: Mapped[int] = mapped_column(Integer, default=0)
    failed_unique_symbols: Mapped[int] = mapped_column(Integer, default=0)
    skipped_unique_symbols: Mapped[int] = mapped_column(Integer, default=0)
    excluded_symbols: Mapped[int] = mapped_column(Integer, default=0)
    exchange_universe_fingerprint: Mapped[str] = mapped_column(String(64))
    eligible_universe_fingerprint: Mapped[str] = mapped_column(String(64))
    scheduled_universe_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scanned_ok_universe_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    eligible_coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    exchange_coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    batch_count: Mapped[int] = mapped_column(Integer, default=0)
    last_batch_sequence: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)


class MarketScanCycleModel(Base):
    """Aggregate for one existing production scan cycle/shortlist."""

    __tablename__ = "market_scan_cycles"

    cycle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rotation_id: Mapped[str] = mapped_column(String(64), index=True)
    runtime_instance_id: Mapped[str] = mapped_column(String(64), index=True)
    cycle_sequence: Mapped[int] = mapped_column(Integer)
    batch_index: Mapped[int] = mapped_column(Integer)
    batch_count_expected: Mapped[int] = mapped_column(Integer)
    cycle_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cycle_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    exchange_universe_size: Mapped[int] = mapped_column(Integer, default=0)
    eligible_universe_size: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_symbols: Mapped[int] = mapped_column(Integer, default=0)
    scanned_ok_symbols: Mapped[int] = mapped_column(Integer, default=0)
    failed_symbols: Mapped[int] = mapped_column(Integer, default=0)
    skipped_symbols: Mapped[int] = mapped_column(Integer, default=0)
    candidate_symbols: Mapped[int] = mapped_column(Integer, default=0)
    evaluated_symbols: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scanned_ok_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)


class MarketScanSymbolResultModel(Base):
    """One terminal symbol result per rotation; bounded by rotation count."""

    __tablename__ = "market_scan_symbol_results"
    __table_args__ = (UniqueConstraint("rotation_id", "symbol", name="uq_market_scan_rotation_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rotation_id: Mapped[str] = mapped_column(String(64), index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    terminal_status: Mapped[str] = mapped_column(String(32), index=True)
    reason_code: Mapped[str] = mapped_column(String(64), index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_instance_id: Mapped[str] = mapped_column(String(64), index=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)

