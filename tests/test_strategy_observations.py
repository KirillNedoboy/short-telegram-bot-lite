from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.observability.strategy_observations import (
    ObservationWriteStatus,
    StrategyObservation,
    build_observation_evidence,
    make_observation_idempotency_key,
)
from app.storage.db import Database
from app.storage.repository import BotRepository


def _observation(*, runtime_instance_id: str = "runtime-1") -> StrategyObservation:
    observed_at = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
    evidence = build_observation_evidence({"ret_5m": 3.0, "reason": "oi_missing"})
    key = make_observation_idempotency_key(
        strategy_family="CLIMAX_EXHAUSTION",
        strategy="VOLUME_CLIMAX_UNWIND",
        symbol="TESTUSDT",
        root_event_id="root-1",
        event_revision=1,
        evaluation_phase="INITIAL",
        market_asof=observed_at,
        input_fingerprint=evidence.input_fingerprint,
        model_version="climax-v1",
        config_hash="a" * 64,
    )
    return StrategyObservation(
        observation_id="observation-1",
        idempotency_key=key,
        run_id="run-1",
        runtime_instance_id=runtime_instance_id,
        strategy_family="CLIMAX_EXHAUSTION",
        strategy="VOLUME_CLIMAX_UNWIND",
        evaluation_phase="INITIAL",
        symbol="TESTUSDT",
        root_event_id="root-1",
        event_revision=1,
        attempt_id=None,
        evaluation_id=None,
        signal_id=None,
        observed_at=observed_at,
        exchange_time=None,
        market_asof=observed_at,
        live_decision="BLOCKED",
        shadow_decision="NOT_EVALUATED",
        score=55,
        blockers=["oi_missing_for_volume_climax"],
        warnings=[],
        market_price=100.0,
        event_high=104.0,
        model_version="climax-v1",
        config_hash="a" * 64,
        input_fingerprint=evidence.input_fingerprint,
        input_snapshot=evidence.snapshot,
    )


def test_same_observation_is_duplicate_across_runtime_restarts(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'strategy-observations.sqlite'}")
    database.create_all()
    repository = BotRepository(database)

    first = repository.record_strategy_observation(_observation())
    second = repository.record_strategy_observation(
        replace(_observation(), observation_id="observation-2", run_id="run-2", runtime_instance_id="runtime-2")
    )

    assert first.status is ObservationWriteStatus.INSERTED
    assert second.status is ObservationWriteStatus.DUPLICATE
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("select count(*) from strategy_observations").scalar_one() == 1
        assert connection.exec_driver_sql("pragma integrity_check").scalar_one() == "ok"


def test_evidence_is_deterministic_bounded_and_rejects_nonfinite_values() -> None:
    evidence = build_observation_evidence({"when": datetime(2026, 7, 24, 15, tzinfo=timezone.utc), "payload": "x" * 40_000})

    assert evidence.snapshot["snapshot_truncated"] is True
    assert evidence.snapshot["full_input_fingerprint"] == evidence.input_fingerprint
    assert len(evidence.snapshot_json.encode("utf-8")) <= 32 * 1024
    with pytest.raises(ValueError, match="non-finite"):
        build_observation_evidence({"price": float("nan")})


def test_strategy_observation_schema_has_research_query_indexes(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'strategy-observation-indexes.sqlite'}")
    database.create_all()

    with database.engine.connect() as connection:
        index_names = {
            row[1]
            for row in connection.exec_driver_sql("pragma index_list('strategy_observations')").all()
        }

    assert "ix_strategy_observations_family_strategy_observed_at" in index_names
