from __future__ import annotations

import asyncio
from datetime import timedelta, datetime, timezone

from sqlalchemy import select

from app.config import AppConfig
from app.domain import EventStatus, ShortZone
from app.main import ShortSignalBot
from app.storage.db import Database
from app.storage.models import RejectStatModel, SignalModel, TelegramDeliveryOutboxModel, WatchCandidateModel
from app.storage.repository import BotRepository


class _FakeClient:
    async def fetch_klines(self, *_args, **_kwargs):
        return []


class _FakeScanner:
    def __init__(self) -> None:
        self.client = _FakeClient()

    async def fetch_optional_derivatives(self, _symbol: str):
        return {}


class _CycleScanner:
    def __init__(self, shortlist_symbols: list[str], frames_by_symbol: dict[str, object]) -> None:
        self.client = _FakeClient()
        self._shortlist_symbols = shortlist_symbols
        self._frames_by_symbol = frames_by_symbol
        self.requested_symbols: list[str] = []

    async def fetch_market_snapshots(self):
        return []

    def shortlist(self, _snapshots):
        return [type("Snapshot", (), {"symbol": symbol})() for symbol in self._shortlist_symbols]

    async def fetch_symbol_frames(self, symbols: list[str]):
        self.requested_symbols = symbols
        return {symbol: self._frames_by_symbol[symbol] for symbol in symbols if symbol in self._frames_by_symbol}

    async def fetch_optional_derivatives(self, _symbol: str):
        return {}


class _FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def send_signal(self, message: str) -> bool:
        self.messages.append(message)
        return True

    async def send_alert(self, message: str) -> bool:
        self.messages.append(message)
        return True


class _ExplodingNotifier(_FakeNotifier):
    async def send_signal(self, _message: str) -> bool:
        raise RuntimeError("telegram transport down")


def test_notifier_failure_leaves_signal_retryable_in_outbox(tmp_path, make_event_state, make_signal_decision) -> None:
    async def _run() -> tuple[object, object]:
        database = Database(f"sqlite:///{tmp_path / 'delivery-failure.db'}")
        database.create_all()
        repository = BotRepository(database)
        state = repository.upsert_event_state(make_event_state())
        signal = repository.save_signal(
            make_signal_decision(), state, telegram_sent=False, delivery_payload="immutable payload"
        )
        bot = ShortSignalBot(
            config=AppConfig(),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=_ExplodingNotifier(),
        )
        sent = await bot._send_new_delivery(entity_type="SIGNAL", entity_id=signal.id)
        with database.session() as session:
            outbox = session.scalars(select(TelegramDeliveryOutboxModel)).one()
            stored_signal = session.get(SignalModel, signal.id)
        return sent, (stored_signal.telegram_sent, outbox.status, outbox.attempt_count, outbox.last_error)

    sent, state = asyncio.run(_run())
    assert sent is False
    assert state[0] is False
    assert state[1:] == ("RETRY", 1, "RuntimeError: telegram transport down")


class _FailingRepository:
    def __init__(self) -> None:
        self.db_url = "sqlite:////opt/krntrade/data/bot.sqlite"

    def check_storage_health(self):
        raise RuntimeError("readonly database")

    def list_active_event_states(self):
        return []


def test_process_symbol_persists_signal_and_suppresses_duplicates(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run() -> tuple[object | None, object | None, object | None]:
        database = Database(f"sqlite:///{tmp_path / 'runtime.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )

        state = make_event_state(state=EventStatus.PULLBACK_OBSERVED)
        repository.upsert_event_state(state)
        features = make_features(asof=state.updated_at)

        bot._pump_detector.build_event = lambda *_args, **_kwargs: None
        bot._feature_builder.build = lambda *_args, **_kwargs: features

        decision, updated_state = await bot._process_symbol("ONTUSDT", object(), state)
        duplicate_decision, duplicate_state = await bot._process_symbol("ONTUSDT", object(), updated_state)
        return decision, updated_state, duplicate_decision, duplicate_state, notifier

    decision, updated_state, duplicate_decision, duplicate_state, notifier = asyncio.run(_run())

    assert decision is not None
    assert updated_state is not None
    assert updated_state.state == EventStatus.SIGNAL_SENT
    assert updated_state.signal_id is not None
    assert duplicate_decision is None
    assert duplicate_state is not None
    assert len(notifier.messages) == 1


def test_storage_heartbeat_failure_sends_alert() -> None:
    async def _run() -> tuple[bool, list[str]]:
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(),
            repository=_FailingRepository(),
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        ok = await bot._ensure_storage_healthy("cycle")
        return ok, notifier.messages

    ok, messages = asyncio.run(_run())

    assert ok is False
    assert len(messages) == 1
    assert "readonly database" in messages[0]


def test_early_pump_is_stored_as_watch_but_not_actionable_signal_when_watch_delivery_disabled(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run():
        database = Database(f"sqlite:///{tmp_path / 'runtime-early-pump-watch.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(enable_watch_candidates=True, send_watch_to_telegram=False, watch_min_score=45),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        features = make_features(
            symbol="EPICUSDT",
            ret_15m=8.2,
            ret_1h=14.0,
            ret_4h=24.0,
            dist_to_vwap_pct=10.5,
            dist_to_ema20_atr=3.2,
            vol_zscore_30m=0.68,
            range_atr_ratio=1.7,
            inside_short_zone_flag=False,
            pullback_from_high_pct=None,
            distance_to_event_high_pct=0.4,
            latest_failed_retest=False,
            liquidity_available=False,
        )

        def _build_event(*_args, **_kwargs):
            return make_event_state(
                symbol="EPICUSDT",
                state=EventStatus.PUMP_DETECTED,
                trigger_window="15m",
                zone_low=None,
                zone_high=None,
            )

        bot._pump_detector.build_event = _build_event
        bot._feature_builder.build = lambda *_args, **_kwargs: features

        decision, updated_state = await bot._process_symbol("EPICUSDT", object(), None)
        return decision, updated_state, notifier, database

    decision, updated_state, notifier, database = asyncio.run(_run())

    assert decision is not None
    assert decision.signal_type.value == "Watch"
    assert decision.decision_type == "EARLY_PUMP_WATCH"
    assert decision.actionable is False
    assert decision.blockers == ["early_pump_not_mature", "no_pullback_observed", "no_short_zone_active"]
    assert updated_state is not None
    assert updated_state.state == EventStatus.PUMP_DETECTED
    assert notifier.messages == []
    with database.session() as session:
        assert session.scalars(select(SignalModel)).all() == []
        stored_watch = session.scalars(select(WatchCandidateModel)).one()
        assert stored_watch.symbol == "EPICUSDT"
        assert stored_watch.signal_type == "Watch"
        assert stored_watch.telegram_sent is False
        assert stored_watch.context_json["decision_type"] == "EARLY_PUMP_WATCH"
        assert stored_watch.context_json["blockers"] == [
            "early_pump_not_mature",
            "no_pullback_observed",
            "no_short_zone_active",
        ]


def test_early_pump_watch_is_deduped_for_same_event_id_across_cycles(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run():
        database = Database(f"sqlite:///{tmp_path / 'runtime-early-pump-watch-dedupe.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(enable_watch_candidates=True, send_watch_to_telegram=False, watch_min_score=45),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        features = make_features(
            symbol="EPICUSDT",
            ret_15m=8.2,
            ret_1h=14.0,
            ret_4h=24.0,
            dist_to_vwap_pct=10.5,
            dist_to_ema20_atr=3.2,
            vol_zscore_30m=0.68,
            range_atr_ratio=1.7,
            inside_short_zone_flag=False,
            pullback_from_high_pct=None,
            distance_to_event_high_pct=0.4,
            latest_failed_retest=False,
            liquidity_available=False,
            last_high=120.0,
        )
        state = make_event_state(
            symbol="EPICUSDT",
            event_id="EPICUSDT:15m:dedupe:1",
            state=EventStatus.PUMP_DETECTED,
            trigger_window="15m",
            zone_low=None,
            zone_high=None,
            event_high=115.0,
            event_features_snapshot={},
        )
        current_state = {"value": state}
        bot._feature_builder.build = lambda *_args, **_kwargs: features
        bot._pump_detector.build_event = lambda *_args, **_kwargs: make_event_state(
            symbol="EPICUSDT",
            event_id="EPICUSDT:15m:dedupe:1",
            state=EventStatus.PUMP_DETECTED,
            trigger_window="15m",
            zone_low=None,
            zone_high=None,
            event_high=115.0,
            event_features_snapshot=dict((current_state["value"].event_features_snapshot or {})),
        )
        first_decision, first_state = await bot._process_symbol("EPICUSDT", object(), state)
        current_state["value"] = first_state
        second_decision, second_state = await bot._process_symbol("EPICUSDT", object(), first_state)
        return first_decision, first_state, second_decision, second_state, notifier, database

    first_decision, first_state, second_decision, second_state, notifier, database = asyncio.run(_run())

    assert first_decision is not None
    assert first_decision.decision_type == "EARLY_PUMP_WATCH"
    assert first_state is not None
    assert second_decision is None
    assert second_state is not None
    assert notifier.messages == []
    with database.session() as session:
        assert len(session.scalars(select(WatchCandidateModel)).all()) == 1
        assert len(session.scalars(select(RejectStatModel)).all()) == 1


def test_run_cycle_always_includes_active_event_symbols_beyond_shortlist(
    tmp_path,
    make_event_state,
) -> None:
    async def _run() -> list[str]:
        database = Database(f"sqlite:///{tmp_path / 'runtime-active-symbols.db'}")
        database.create_all()
        repository = BotRepository(database)
        repository.upsert_event_state(
            make_event_state(
                symbol="BBBUSDT",
                state=EventStatus.PULLBACK_OBSERVED,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        repository.upsert_event_state(
            make_event_state(
                symbol="ZZZUSDT",
                state=EventStatus.PULLBACK_OBSERVED,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        scanner = _CycleScanner(shortlist_symbols=["AAAUSDT", "BBBUSDT"], frames_by_symbol={})
        bot = ShortSignalBot(
            config=AppConfig(shortlist_size=2),
            repository=repository,
            scanner=scanner,
            notifier=_FakeNotifier(),
        )

        await bot.run_cycle()
        return scanner.requested_symbols

    requested_symbols = asyncio.run(_run())

    assert requested_symbols == ["AAAUSDT", "BBBUSDT", "ZZZUSDT"]


def test_run_cycle_preserves_shortlist_rank_order_and_appends_missing_active_symbols_without_duplicates(
    tmp_path,
    make_event_state,
) -> None:
    async def _run() -> list[str]:
        database = Database(f"sqlite:///{tmp_path / 'runtime-active-order.db'}")
        database.create_all()
        repository = BotRepository(database)
        repository.upsert_event_state(
            make_event_state(
                symbol="BBBUSDT",
                state=EventStatus.PULLBACK_OBSERVED,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        repository.upsert_event_state(
            make_event_state(
                symbol="ZZZUSDT",
                state=EventStatus.PULLBACK_OBSERVED,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        scanner = _CycleScanner(shortlist_symbols=["CCCUSDT", "AAAUSDT", "BBBUSDT"], frames_by_symbol={})
        bot = ShortSignalBot(
            config=AppConfig(shortlist_size=3),
            repository=repository,
            scanner=scanner,
            notifier=_FakeNotifier(),
        )

        await bot.run_cycle()
        return scanner.requested_symbols

    requested_symbols = asyncio.run(_run())

    assert requested_symbols == ["CCCUSDT", "AAAUSDT", "BBBUSDT", "ZZZUSDT"]


def test_new_high_after_short_zone_cancels_signal(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run():
        database = Database(f"sqlite:///{tmp_path / 'runtime-new-high.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        state = make_event_state(
            state=EventStatus.SHORT_ZONE_ACTIVE,
            zone_low=110.5,
            zone_high=113.8,
            pullback_detected_at=make_features().asof - timedelta(minutes=5),
        )
        features = make_features(last_high=116.0, price=112.0, inside_short_zone_flag=True)

        bot._pump_detector.build_event = lambda *_args, **_kwargs: None
        bot._feature_builder.build = lambda *_args, **_kwargs: features

        return await bot._process_symbol("ONTUSDT", object(), state), notifier

    (decision, updated_state), notifier = asyncio.run(_run())

    assert decision is None
    assert updated_state is not None
    assert updated_state.state == EventStatus.EXPIRED
    assert notifier.messages == []


def test_volume_breakout_above_event_high_cancels_signal(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run():
        database = Database(f"sqlite:///{tmp_path / 'runtime-volume-breakout.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        state = make_event_state(
            state=EventStatus.PULLBACK_OBSERVED,
            zone_low=110.5,
            zone_high=113.8,
            pullback_detected_at=make_features().asof - timedelta(minutes=5),
        )
        features = make_features(
            last_high=116.0,
            price=112.0,
            inside_short_zone_flag=True,
            vol_zscore_30m=3.0,
        )

        bot._pump_detector.build_event = lambda *_args, **_kwargs: None
        bot._feature_builder.build = lambda *_args, **_kwargs: features

        return await bot._process_symbol("ONTUSDT", object(), state), notifier

    (decision, updated_state), notifier = asyncio.run(_run())

    assert decision is None
    assert updated_state is not None
    assert updated_state.state == EventStatus.EXPIRED
    assert notifier.messages == []


def test_c_grade_signal_is_not_sent_to_telegram(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run():
        database = Database(f"sqlite:///{tmp_path / 'runtime-c-grade.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        state = make_event_state(state=EventStatus.PULLBACK_OBSERVED, zone_low=110.5, zone_high=113.8)
        features = make_features(
            price=112.0,
            inside_short_zone_flag=True,
            ret_15m=6.0,
            ret_1h=8.0,
            ret_4h=20.0,
            dist_to_vwap_pct=8.0,
            dist_to_ema20_atr=2.0,
            upper_wick_ratio=0.18,
            rejection_from_high_pct=0.8,
            close_position_in_range=0.6,
            vol_zscore_30m=1.1,
            range_atr_ratio=1.3,
            pullback_from_high_pct=3.0,
            latest_failed_retest=False,
        )

        bot._pump_detector.build_event = lambda *_args, **_kwargs: None
        bot._feature_builder.build = lambda *_args, **_kwargs: features

        return await bot._process_symbol("ONTUSDT", object(), state), notifier

    (decision, updated_state), notifier = asyncio.run(_run())

    assert decision is None
    assert updated_state is not None
    assert notifier.messages == []


def test_grade_c_watch_is_stored_but_not_sent_when_watch_delivery_disabled(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run():
        database = Database(f"sqlite:///{tmp_path / 'runtime-c-grade-watch.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(enable_watch_candidates=True, send_watch_to_telegram=False, watch_min_score=50),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        state = make_event_state(state=EventStatus.PULLBACK_OBSERVED, zone_low=110.5, zone_high=113.8)
        features = make_features(
            price=112.0,
            inside_short_zone_flag=True,
            ret_15m=7.0,
            ret_1h=9.0,
            ret_4h=21.0,
            dist_to_vwap_pct=13.0,
            dist_to_ema20_atr=1.8,
            upper_wick_ratio=0.16,
            rejection_from_high_pct=0.9,
            close_position_in_range=0.55,
            vol_zscore_30m=1.4,
            range_atr_ratio=1.3,
            pullback_from_high_pct=3.9,
            latest_failed_retest=False,
            funding_rate=-0.0009,
            oi_change_15m=1.2,
            oi_change_1h=0.6,
        )

        bot._pump_detector.build_event = lambda *_args, **_kwargs: None
        bot._feature_builder.build = lambda *_args, **_kwargs: features
        bot._zone_builder.build = lambda *_args, **_kwargs: ShortZone(low=110.5, high=113.8, mode="event_range")

        return await bot._process_symbol("ONTUSDT", object(), state), notifier, database

    (decision, updated_state), notifier, database = asyncio.run(_run())

    assert decision is not None
    assert decision.signal_type.value == "Watch"
    assert decision.grade == "C"
    assert updated_state is not None
    assert updated_state.signal_id is None
    assert notifier.messages == []
    with database.session() as session:
        assert session.scalars(select(SignalModel)).all() == []
        stored_watch = session.scalars(select(WatchCandidateModel)).one()
        assert stored_watch.base_grade == "C"
        assert stored_watch.telegram_sent is False


def test_watch_signal_is_sent_without_short_wording(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run():
        database = Database(f"sqlite:///{tmp_path / 'runtime-watch.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(enable_watch_candidates=True, send_watch_to_telegram=True, watch_min_score=50),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        state = make_event_state(state=EventStatus.PULLBACK_OBSERVED, zone_low=110.5, zone_high=113.8)
        features = make_features(
            price=112.0,
            inside_short_zone_flag=True,
            ret_1h=10.0,
            ret_4h=24.0,
            dist_to_ema20_atr=3.0,
            range_atr_ratio=1.6,
            spread_pct=0.31,
        )

        bot._pump_detector.build_event = lambda *_args, **_kwargs: None
        bot._feature_builder.build = lambda *_args, **_kwargs: features

        return await bot._process_symbol("ONTUSDT", object(), state), notifier, database

    (decision, updated_state), notifier, database = asyncio.run(_run())

    assert decision is not None
    assert updated_state is not None
    assert len(notifier.messages) == 1
    assert "WATCH / НЕ ВХОД" in notifier.messages[0]
    assert "ШОРТ-СИГНАЛ" not in notifier.messages[0]
    with database.session() as session:
        assert session.scalars(select(SignalModel)).all() == []
        stored_watch = session.scalars(select(WatchCandidateModel)).one()
        assert stored_watch.symbol == "ONTUSDT"
        assert stored_watch.telegram_sent is True


def test_watch_signal_is_not_sent_when_telegram_watch_disabled(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run():
        database = Database(f"sqlite:///{tmp_path / 'runtime-watch-disabled.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(enable_watch_candidates=True, send_watch_to_telegram=False, watch_min_score=50),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        state = make_event_state(state=EventStatus.PULLBACK_OBSERVED, zone_low=110.5, zone_high=113.8)
        features = make_features(
            price=112.0,
            inside_short_zone_flag=True,
            ret_1h=10.0,
            ret_4h=24.0,
            dist_to_ema20_atr=3.0,
            range_atr_ratio=1.6,
            spread_pct=0.31,
        )

        bot._pump_detector.build_event = lambda *_args, **_kwargs: None
        bot._feature_builder.build = lambda *_args, **_kwargs: features

        return await bot._process_symbol("ONTUSDT", object(), state), notifier, database

    (decision, updated_state), notifier, database = asyncio.run(_run())

    assert decision is not None
    assert decision.signal_type.value == "Watch"
    assert updated_state is not None
    assert updated_state.signal_id is None
    assert notifier.messages == []
    with database.session() as session:
        assert session.scalars(select(SignalModel)).all() == []
        stored_watch = session.scalars(select(WatchCandidateModel)).one()
        assert stored_watch.telegram_sent is False


def test_warn_only_derivatives_diagnostics_are_persisted_without_sending_watch(
    tmp_path,
    make_event_state,
    make_features,
) -> None:
    async def _run():
        database = Database(f"sqlite:///{tmp_path / 'runtime-derivatives-watch-disabled.db'}")
        database.create_all()
        repository = BotRepository(database)
        notifier = _FakeNotifier()
        bot = ShortSignalBot(
            config=AppConfig(
                derivatives_enabled=True,
                enable_watch_candidates=True,
                enable_squeeze_guard=True,
                squeeze_guard_mode="warn_only",
                send_watch_to_telegram=False,
                watch_min_score=50,
            ),
            repository=repository,
            scanner=_FakeScanner(),
            notifier=notifier,
        )
        state = make_event_state(state=EventStatus.PULLBACK_OBSERVED, zone_low=110.5, zone_high=113.8)
        features = make_features(
            price=112.0,
            inside_short_zone_flag=True,
            ret_1h=10.0,
            ret_4h=24.0,
            dist_to_ema20_atr=3.0,
            range_atr_ratio=1.6,
            spread_pct=0.31,
            funding_rate=-0.02,
            oi_change_15m=12.0,
            oi_change_1h=18.0,
            open_interest=1200.0,
            oi_change_pct=12.0,
            derivatives_status="OK",
            derivatives_reasons=[],
            data_quality_warnings=[],
        )

        bot._pump_detector.build_event = lambda *_args, **_kwargs: None
        bot._feature_builder.build = lambda *_args, **_kwargs: features

        return await bot._process_symbol("ONTUSDT", object(), state), notifier, database

    (decision, updated_state), notifier, database = asyncio.run(_run())

    assert decision is not None
    assert decision.signal_type.value == "Watch"
    assert updated_state is not None
    assert updated_state.signal_id is None
    assert notifier.messages == []
    with database.session() as session:
        stored_watch = session.scalars(select(WatchCandidateModel)).one()
        assert stored_watch.telegram_sent is False
        assert stored_watch.context_json["derivatives_status"] == "OK"
        assert stored_watch.context_json["open_interest"] == 1200.0
