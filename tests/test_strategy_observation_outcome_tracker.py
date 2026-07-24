from datetime import datetime, timezone

import asyncio

from app.market.candles import klines_to_frame
from app.outcomes.tracker import OutcomeTracker


UTC = timezone.utc


class _Client:
    def __init__(self, raw_klines):
        self.raw_klines = raw_klines
        self.calls = []

    async def fetch_klines(self, symbol, interval, *, limit, start_ms, end_ms):
        self.calls.append((symbol, interval, start_ms, end_ms))
        return self.raw_klines


class _Repository:
    def __init__(self, rows):
        self.rows = rows
        self.updated = []

    def list_signals_missing_outcomes(self, *, now):
        return []

    def list_strategy_observations_due_outcomes(self, *, limit):
        return self.rows[:limit]

    def update_strategy_observation_outcome(self, observation_id, outcome, *, updated_at):
        self.updated.append((observation_id, outcome, updated_at))
        self.rows = [row for row in self.rows if row["observation_id"] != observation_id]
        return True


def test_outcome_tracker_updates_strategy_observations_without_signal_side_effects(make_frame) -> None:
    start = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    frame = make_frame([100.0] * 16, start=start)
    raw = [
        [str(int(row["start_ms"])), str(row["open"]), str(row["high"]), str(row["low"]), str(row["close"]), str(row["volume"]), str(row["turnover"])]
        for _, row in frame.iterrows()
    ]
    client = _Client(raw)
    repository = _Repository(
        [
            {
                "observation_id": "obs-1",
                "symbol": "TESTUSDT",
                "observed_at": start,
                "market_price": 100.0,
                "event_high": 110.0,
            }
        ]
    )

    updated = asyncio.run(OutcomeTracker(client, repository).update_due_outcomes(now=start.replace(hour=13)))

    assert updated == 1
    assert len(client.calls) == 1
    assert repository.updated[0][0] == "obs-1"
    assert repository.updated[0][1]["data_status"] == "complete"
