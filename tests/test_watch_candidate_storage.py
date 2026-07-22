from __future__ import annotations

from math import nan

from sqlalchemy import select

from app.domain import SignalType
from app.storage.db import Database
from app.storage.models import SignalModel, WatchCandidateModel
from app.storage.repository import BotRepository


def test_watch_candidate_with_all_metrics_is_stored_separately(tmp_path, make_event_state, make_signal_decision) -> None:
    database = Database(f"sqlite:///{tmp_path / 'watch-all.db'}")
    database.create_all()
    repository = BotRepository(database)
    state = repository.upsert_event_state(make_event_state())
    decision = make_signal_decision(
        signal_type=SignalType.WATCH,
        actionable=False,
        decision_type="WATCH",
        blockers=["thin_orderbook"],
        data_quality_warnings=[],
    )

    record = repository.save_watch_candidate(decision, state, telegram_sent=False)

    assert record.symbol == "ONTUSDT"
    assert record.actionable is False
    assert record.dist_to_vwap_pct == 13.0
    with database.session() as session:
        assert session.scalars(select(SignalModel)).all() == []
        stored = session.scalars(select(WatchCandidateModel)).one()
        assert stored.signal_type == "Watch"
        assert stored.dist_to_vwap_pct == 13.0


def test_watch_candidate_missing_dist_to_vwap_pct_is_stored_without_integrity_error(tmp_path, make_event_state, make_signal_decision) -> None:
    database = Database(f"sqlite:///{tmp_path / 'watch-missing.db'}")
    database.create_all()
    repository = BotRepository(database)
    state = repository.upsert_event_state(make_event_state())
    decision = make_signal_decision(
        signal_type=SignalType.WATCH,
        actionable=False,
        decision_type="WATCH",
        features_snapshot={
            "pullback_from_high_pct": 3.5,
            "dist_to_vwap_pct": nan,
            "upper_wick_ratio": 0.25,
            "rejection_from_high_pct": 1.6,
            "vol_zscore_30m": 2.0,
            "dist_to_ema20_atr": 4.5,
            "rsi_15m": 78.5,
            "ret_1h": 15.0,
            "ret_4h": 35.0,
            "range_atr_ratio": 2.2,
            "oi_change_15m": None,
            "oi_change_1h": None,
            "funding_rate": None,
            "spread_pct": 0.2,
            "orderbook_depth_usdt_1pct": 20000.0,
            "signal_vwap": 99.0,
        },
        blockers=["thin_orderbook"],
        data_quality_warnings=["derivatives_missing"],
    )

    record = repository.save_watch_candidate(decision, state, telegram_sent=False)

    assert record.dist_to_vwap_pct is None
    with database.session() as session:
        assert session.scalars(select(SignalModel)).all() == []
        stored = session.scalars(select(WatchCandidateModel)).one()
        assert stored.dist_to_vwap_pct is None
        assert stored.actionable is False


def test_actionable_signal_still_persists_dist_to_vwap_pct(tmp_path, make_event_state, make_signal_decision) -> None:
    database = Database(f"sqlite:///{tmp_path / 'signal.db'}")
    database.create_all()
    repository = BotRepository(database)
    state = repository.upsert_event_state(make_event_state())

    record = repository.save_signal(make_signal_decision(), state, telegram_sent=True)

    assert record.signal_type == "Aggressive"
    assert record.dist_to_vwap_pct == 13.0
    with database.session() as session:
        stored = session.scalars(select(SignalModel)).one()
        assert stored.dist_to_vwap_pct == 13.0
        assert session.scalars(select(WatchCandidateModel)).all() == []
