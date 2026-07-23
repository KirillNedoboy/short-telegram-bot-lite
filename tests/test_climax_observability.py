from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domain import EventState
from app.main import ShortSignalBot, _decision_delta
from app.storage.db import Database
from app.storage.repository import BotRepository


def test_fast_monitor_selection_is_round_robin_and_bounded():
    bot = object.__new__(ShortSignalBot)
    bot._fast_monitor_cursor = 0
    bot._config = SimpleNamespace(climax_max_active_symbols=2)
    keys = [(f"S{i}USDT", f"event-{i}") for i in range(5)]

    assert bot._select_fast_monitor_keys(keys) == keys[:2]
    assert bot._select_fast_monitor_keys(keys) == keys[2:4]
    assert bot._select_fast_monitor_keys(keys) == [keys[4], keys[0]]


def test_climax_candidate_ttl_is_not_refreshed_and_expired_event_is_not_added():
    class Events:
        def __init__(self):
            self.rows = []

        def record_climax_monitor_event(self, **kwargs):
            self.rows.append(kwargs)

    bot = object.__new__(ShortSignalBot)
    bot._active_climax_pool = {}
    bot._fast_monitor_poll_sequence = 0
    bot._config = SimpleNamespace(climax_candidate_ttl_minutes=30)
    bot._repository = Events()
    now = datetime.now(timezone.utc)
    state = EventState(symbol="AKEUSDT", event_id="event-1", event_high_time=now - timedelta(minutes=2))

    bot._track_climax_candidate(state, now)
    first_added = bot._active_climax_pool[("AKEUSDT", "event-1")].candidate_added_at
    bot._track_climax_candidate(state, now + timedelta(minutes=5))
    assert bot._active_climax_pool[("AKEUSDT", "event-1")].candidate_added_at == first_added

    expired = EventState(symbol="OLDUSDT", event_id="old-event", event_high_time=now - timedelta(minutes=31))
    bot._track_climax_candidate(expired, now)
    assert ("OLDUSDT", "old-event") not in bot._active_climax_pool
    assert any(row["reason"] == "ttl_expired_before_pool_add" for row in bot._repository.rows)


def test_climax_telemetry_tables_and_records_are_append_only(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'telemetry.sqlite'}")
    database.create_all()
    repository = BotRepository(database)
    repository.set_runtime_metadata(
        runtime_instance_id="runtime-test-1",
        config_fingerprint="a" * 64,
        model_version="climax-v1",
    )
    now = datetime.now(timezone.utc)

    repository.record_climax_evaluation(
        evaluation_time=now,
        symbol="AKEUSDT",
        strategy="CLIMAX_EXHAUSTION",
        subtype_candidate="LOW_VOLUME_EXTENSION_FAILURE",
        model_version="climax-v1",
        event_id="ake-event-1",
        event_high=0.0019,
        event_high_time=now,
        event_detected_at=now,
        candidate_added_at=now,
        candidate_age_sec=3.0,
        fast_monitor=True,
        poll_sequence=7,
        frame_asof=now,
        candles_asof=now,
        oi_asof=now,
        orderbook_asof=now,
        score=70,
        grade="B",
        actionable=False,
        admission_passed=False,
        veto_reasons=["climax_liquidity_block"],
        passed_conditions=["volume_windows_equal"],
        data_quality=[],
        liquidity={"depth_1pct_usdt": 0},
        oi={"status": "OK"},
        features={"event_high": 0.0019},
        lifecycle_state="REJECTED",
        live_decision="REJECTED",
        live_veto_reasons=["climax_liquidity_block"],
        shadow_decision="ACTIONABLE",
        shadow_veto_reasons=[],
        decision_delta="LIVE_REJECTED_SHADOW_ACTIONABLE",
        shadow_hypothetical_entry_price=0.0018,
        shadow_hypothetical_grade="B",
        shadow_hypothetical_score=70,
        shadow_removed_vetoes=["climax_liquidity_block"],
    )
    repository.record_climax_evaluation(
        evaluation_time=now,
        symbol="AKEUSDT",
        strategy="CLIMAX_EXHAUSTION",
        subtype_candidate="LOW_VOLUME_EXTENSION_FAILURE",
        model_version="climax-v1",
        event_id="ake-event-1",
        event_high=0.0019,
        event_high_time=now,
        event_detected_at=now,
        candidate_added_at=now,
        candidate_age_sec=4.0,
        fast_monitor=False,
        poll_sequence=None,
        frame_asof=now,
        candles_asof=now,
        oi_asof=None,
        orderbook_asof=None,
        score=70,
        grade="B",
        actionable=True,
        admission_passed=True,
        veto_reasons=[],
        passed_conditions=["failed_retest_confirmed"],
        data_quality=[],
        liquidity={"available": False},
        oi={"status": "MISSING"},
        features={"event_high": 0.0019},
        lifecycle_state="DELIVERY_RECHECK",
        telegram_eligible=True,
    )
    repository.upsert_shadow_root_event(
        root_event_id="ake-event-1",
        symbol="AKEUSDT",
        event_started_at=now,
        event_base_price=0.0017,
        peak_high=0.0019,
        peak_high_time=now,
        initial_extension_pct=11.7,
        initial_extension_source="event_base_to_peak",
        observed_at=now,
    )
    repository.upsert_shadow_entry_attempt(
        attempt_id="ake-event-1:r1:a1",
        root_event_id="ake-event-1",
        observed_at=now,
        local_retest_high=0.00188,
        breakdown_level=0.00189,
        attempt_state="BREAKDOWN_PENDING",
    )
    repository.record_climax_monitor_event(
        created_at=now,
        symbol="AKEUSDT",
        event_id="ake-event-1",
        event_high_time=now,
        action="poll_complete",
        reason=None,
        pool_size=1,
        poll_sequence=7,
        worker_id="climax-fast-monitor",
    )
    repository.update_fast_monitor_heartbeat(
        checked_at=now,
        pool_size=1,
        poll_sequence=7,
        last_poll_at=now,
        last_complete_at=now,
    )

    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("select count(*) from climax_evaluations").scalar_one() == 2
        assert connection.exec_driver_sql("select count(*) from climax_monitor_events").scalar_one() == 1
        heartbeat = connection.exec_driver_sql(
            "select fast_monitor_pool_size, fast_monitor_poll_sequence from runtime_heartbeats where id=1"
        ).one()
        assert tuple(heartbeat) == (1, 7)
        telemetry = connection.exec_driver_sql(
            "select runtime_instance_id, model_version, observed_at, market_asof, evaluation_completed_at from climax_evaluations order by id desc limit 1"
        ).one()
        assert telemetry[0:2] == ("runtime-test-1", "climax-v1")
        assert telemetry[2] is not None
        assert telemetry[3] is not None
        assert telemetry[4] is not None
        event_telemetry = connection.exec_driver_sql(
            "select root_event_id, event_revision, observed_at, market_asof from climax_monitor_events"
        ).one()
        assert tuple(event_telemetry[:2]) == ("ake-event-1", 1)
        assert event_telemetry[2] is not None
        assert event_telemetry[3] is not None
        assert connection.exec_driver_sql("select count(*) from runtime_heartbeat_history").scalar_one() == 1
        assert connection.exec_driver_sql("select count(*) from climax_root_events").scalar_one() == 1
        assert connection.exec_driver_sql("select count(*) from climax_entry_attempts").scalar_one() == 1
        shadow = connection.exec_driver_sql(
            "select live_decision, shadow_decision, decision_delta, shadow_hypothetical_grade from climax_evaluations order by id limit 1"
        ).one()
        assert tuple(shadow) == ("REJECTED", "ACTIONABLE", "LIVE_REJECTED_SHADOW_ACTIONABLE", "B")
        root = connection.exec_driver_sql(
            "select root_event_id, peak_revision, initial_extension_pct from climax_root_events"
        ).one()
        assert tuple(root) == ("ake-event-1", 1, 11.7)
        assert connection.exec_driver_sql("select attempt_state from climax_entry_attempts").scalar_one() == "BREAKDOWN_PENDING"
        assert connection.exec_driver_sql("select runtime_instance_id, config_fingerprint from runtime_heartbeats where id=1").one() == ("runtime-test-1", "a" * 64)
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"


def test_volume_observation_ledger_keeps_below_threshold_observation(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'volume-observations.sqlite'}")
    database.create_all()
    repository = BotRepository(database)
    now = datetime.now(timezone.utc)

    repository.record_volume_climax_observation(
        observed_at=now,
        market_asof=now,
        symbol="LOWUSDT",
        event_id="low-event",
        root_event_id="low-event",
        event_revision=1,
        runtime_instance_id="runtime-1",
        model_version="climax-v1",
        subtype="VOLUME_CLIMAX_UNWIND",
        stage="OBSERVED",
        score=55,
        grade="C",
        veto_reasons=["score_below_actionable_threshold"],
        data_quality=[],
        metadata={"ret_5m": 4.0},
        source_evaluation_id=None,
        attempt_id=None,
    )

    with database.engine.connect() as connection:
        row = connection.exec_driver_sql(
            "select symbol, stage, score, metadata_json from volume_climax_observations"
        ).one()
        assert row[0:3] == ("LOWUSDT", "OBSERVED", 55)
        assert '\"ret_5m\": 4.0' in row[3]


def test_live_shadow_decision_delta_uses_complete_classification_labels():
    actionable = SimpleNamespace(actionable=True, grade="B")
    rejected = SimpleNamespace(actionable=False, grade="C")

    assert _decision_delta(actionable, actionable) == "BOTH_ACTIONABLE"
    assert _decision_delta(rejected, rejected) == "BOTH_REJECTED"
    assert _decision_delta(rejected, actionable) == "LIVE_REJECTED_SHADOW_ACTIONABLE"
    assert _decision_delta(actionable, rejected) == "LIVE_ACTIONABLE_SHADOW_REJECTED"


def test_attempt_terminal_transition_is_append_only_and_idempotent(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'attempt-events.sqlite'}")
    database.create_all()
    repository = BotRepository(database)
    now = datetime.now(timezone.utc)
    repository.upsert_shadow_entry_attempt(
        attempt_id="root-1:r1:a1",
        root_event_id="root-1",
        observed_at=now,
        local_retest_high=1.2,
        breakdown_level=1.1,
        attempt_state="BREAKDOWN_PENDING",
        confirmation_expires_at=now + timedelta(minutes=30),
    )
    repository.transition_shadow_entry_attempt(
        attempt_id="root-1:r1:a1",
        root_event_id="root-1",
        event_revision=1,
        evaluation_id=42,
        new_state="SHADOW_ACTIONABLE",
        reason="shadow_actionable",
        observed_at=now + timedelta(minutes=1),
        market_asof=now + timedelta(minutes=1),
        runtime_instance_id="runtime-1",
        model_version="climax-v1",
    )
    repository.transition_shadow_entry_attempt(
        attempt_id="root-1:r1:a1",
        root_event_id="root-1",
        event_revision=1,
        evaluation_id=42,
        new_state="SHADOW_ACTIONABLE",
        reason="shadow_actionable",
        observed_at=now + timedelta(minutes=2),
        market_asof=now + timedelta(minutes=2),
        runtime_instance_id="runtime-1",
        model_version="climax-v1",
    )
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("select attempt_state, attempt_close_reason from climax_entry_attempts").one() == ("SHADOW_ACTIONABLE", "shadow_actionable")
        assert connection.exec_driver_sql("select count(*) from climax_entry_attempt_events where attempt_id='root-1:r1:a1'").scalar_one() == 3
        assert connection.exec_driver_sql("select count(*) from climax_entry_attempt_events where event_type='attempt_closed'").scalar_one() == 1


def test_attempt_limit_is_enforced_in_database(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'attempt-limit.sqlite'}")
    database.create_all()
    repository = BotRepository(database)
    now = datetime.now(timezone.utc)
    for ordinal in (1, 2, 3):
        assert repository.upsert_shadow_entry_attempt(
            attempt_id=f"root-limit:r1:a{ordinal}",
            root_event_id="root-limit",
            observed_at=now,
            local_retest_high=1.0,
            breakdown_level=0.99,
            attempt_state="BREAKDOWN_PENDING",
            max_attempts_per_root_event=3,
        ) is True

    assert repository.upsert_shadow_entry_attempt(
        attempt_id="root-limit:r1:a4",
        root_event_id="root-limit",
        observed_at=now,
        local_retest_high=1.0,
        breakdown_level=0.99,
        attempt_state="BREAKDOWN_PENDING",
        max_attempts_per_root_event=3,
    ) is False
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("select count(*) from climax_entry_attempts where root_event_id='root-limit'").scalar_one() == 3
        assert connection.exec_driver_sql("select event_type from climax_entry_attempt_events where event_type='attempt_limit_reached'").scalar_one() == "attempt_limit_reached"


def test_attempt_limit_is_serialized_for_concurrent_sqlite_admission(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    database = Database(f"sqlite:///{tmp_path / 'attempt-concurrency.sqlite'}")
    database.create_all()
    repository = BotRepository(database)
    now = datetime.now(timezone.utc)

    def create_attempt(ordinal):
        return repository.upsert_shadow_entry_attempt(
            attempt_id=f"root-concurrent:r1:a{ordinal}",
            root_event_id="root-concurrent",
            observed_at=now,
            local_retest_high=1.0,
            breakdown_level=0.99,
            attempt_state="BREAKDOWN_PENDING",
            max_attempts_per_root_event=1,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create_attempt, range(8)))

    assert sum(results) == 1
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("select count(*) from climax_entry_attempts where root_event_id='root-concurrent'").scalar_one() == 1


def test_evaluation_attempt_correlation_and_missing_event_telemetry(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'correlation.sqlite'}")
    database.create_all()
    repository = BotRepository(database)
    now = datetime.now(timezone.utc)
    repository.record_climax_evaluation(
        evaluation_time=now,
        symbol="AKEUSDT",
        strategy="CLIMAX_EXHAUSTION",
        subtype_candidate="LOW_VOLUME_EXTENSION_FAILURE",
        model_version="climax-v1",
        event_id="root-1",
        event_high=1.2,
        event_high_time=now,
        event_detected_at=now,
        candidate_added_at=now,
        candidate_age_sec=1,
        fast_monitor=True,
        poll_sequence=1,
        frame_asof=now,
        candles_asof=now,
        oi_asof=None,
        orderbook_asof=None,
        score=70,
        grade="B",
        actionable=False,
        admission_passed=False,
        veto_reasons=["rejection_missing"],
        passed_conditions=[],
        data_quality=[],
        liquidity={},
        oi={},
        features={},
        lifecycle_state="REJECTED",
        runtime_instance_id="runtime-1",
        root_event_id="root-1",
        event_revision=1,
        attempt_id="root-1:r1:a1",
        observed_at=now,
        market_asof=now,
    )
    repository.record_attempt_correlation_missing(
        root_event_id="root-2",
        event_revision=1,
        attempt_id=None,
        evaluation_id=None,
        observed_at=now,
        market_asof=now,
        runtime_instance_id="runtime-1",
        model_version="climax-v1",
        details={"reason": "pool_metadata_absent"},
    )
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("select attempt_id from climax_evaluations").scalar_one() == "root-1:r1:a1"
        assert connection.exec_driver_sql("select event_type from climax_entry_attempt_events where root_event_id='root-2'").scalar_one() == "attempt_correlation_missing"
