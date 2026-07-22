from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.market.coverage import coverage_percent, universe_fingerprint
from app.storage.db import Database
from app.storage.models import MarketScanRotationModel, MarketScanSymbolResultModel
from app.storage.repository import BotRepository


def test_universe_fingerprint_is_order_independent_and_unique():
    assert universe_fingerprint(["bUSDT", "AUSDT", "AUSDT"]) == universe_fingerprint(["ausdt", "BUSDT"])
    assert len(universe_fingerprint(["AUSDT"])) == 64


def _record(repo, eligible, batch, results):
    now = datetime.now(timezone.utc)
    return repo.record_market_scan_cycle(
        cycle_started_at=now - timedelta(seconds=1),
        cycle_completed_at=now,
        exchange_symbols=eligible + ["EXCLUDEDUSDT"],
        eligible_symbols=eligible,
        excluded=[("EXCLUDEDUSDT", "LIQUIDITY_FILTER")],
        scheduled_symbols=batch,
        symbol_results=results,
    )


def test_batch_100_of_500_is_not_full_rotation(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'coverage.sqlite'}")
    db.create_all()
    repo = BotRepository(db)
    repo.set_runtime_metadata(runtime_instance_id="runtime-1", config_fingerprint="c" * 64)
    eligible = [f"S{i}USDT" for i in range(500)]
    result = _record(repo, eligible, eligible[:100], [{"symbol": s, "terminal_status": "SCANNED_OK", "reason_code": "SCANNED_OK"} for s in eligible[:100]])
    assert result["status"] == "OPEN"
    assert result["eligible_coverage_pct"] == 20.0
    assert result["scheduled_unique_symbols"] == 100


def test_five_batches_complete_rotation_and_failed_is_counted_once(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'coverage.sqlite'}")
    db.create_all()
    repo = BotRepository(db)
    repo.set_runtime_metadata(runtime_instance_id="runtime-1", config_fingerprint="c" * 64)
    eligible = [f"S{i}USDT" for i in range(500)]
    last = None
    for index in range(5):
        batch = eligible[index * 100:(index + 1) * 100]
        status = "SCAN_FAILED" if index == 4 else "SCANNED_OK"
        last = _record(repo, eligible, batch, [{"symbol": s, "terminal_status": status, "reason_code": status} for s in batch])
    assert last["status"] == "COMPLETED"
    assert last["eligible_coverage_pct"] == 100.0
    assert last["failed_unique_symbols"] == 100
    assert last["scheduled_unique_symbols"] == 500
    with db.session() as session:
        assert session.query(MarketScanSymbolResultModel).count() == 501
        rotation = session.query(MarketScanRotationModel).one()
        assert rotation.eligible_coverage_pct <= 100.0


def test_coverage_percent_is_bounded():
    assert coverage_percent(600, 500) == 100.0
    assert coverage_percent(0, 0) is None
