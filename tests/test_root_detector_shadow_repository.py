from datetime import datetime, timezone

from app.storage.db import Database
from app.storage.models import RootDetectorShadowCandidateModel, SignalModel, TelegramDeliveryOutboxModel
from app.storage.repository import BotRepository


def _candidate():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "candidate_id": "candidate-1", "symbol": "AAAUSDT", "first_seen_at": now, "rotation_id": "rotation-1",
        "price": 110.0, "event_high": 110.0, "high_time": now, "pump_5m": 3.0, "pump_15m": 6.0,
        "pump_1h": 9.0, "pump_4h": 12.0, "volume_ratio": None, "volume_z": 1.0,
        "oi_5m": None, "oi_15m": None, "oi_1h": None, "distance_from_high": 0.0,
        "conditions_json": {"pump_5m": "PASS"}, "live_root_created": False,
        "code_version": "test", "config_hash": "c" * 64, "runtime_instance_id": "runtime", "runtime_started_at": now,
        "observations_json": [], "outcome_json": {},
    }


def test_shadow_candidate_dedupe_linkage_and_outcome_do_not_create_signal_or_outbox(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'shadow.sqlite'}")
    db.create_all()
    repo = BotRepository(db)
    assert repo.record_root_detector_shadow_candidate(_candidate()) is True
    assert repo.record_root_detector_shadow_candidate(_candidate()) is False
    assert repo.append_root_detector_shadow_observation("candidate-1", {"price": 111}) is True
    assert repo.link_root_detector_shadow_candidate("candidate-1", root_event_id="root-1", root_created_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc), peak_time=datetime(2026, 1, 1, tzinfo=timezone.utc)) is True
    assert repo.mature_root_detector_shadow_candidate("candidate-1", {"outcome_status": "CENSORED", "mfe_pct": 2.0, "mae_pct": 1.0, "horizons": {"15m": {"price": None}}}) is True
    with db.session() as session:
        row = session.query(RootDetectorShadowCandidateModel).one()
        assert row.live_root_created is True
        assert row.candidate_to_root_latency == 60.0
        assert session.query(SignalModel).count() == 0
        assert session.query(TelegramDeliveryOutboxModel).count() == 0
