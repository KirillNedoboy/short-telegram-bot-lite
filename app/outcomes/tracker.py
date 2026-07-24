"""Repository-driven outcome updater."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.market.bybit_client import BybitClient
from app.market.candles import klines_to_frame, normalize_utc
from app.outcomes.evaluator import OutcomeEvaluator
from app.outcomes.strategy_observations import evaluate_strategy_observation
from app.storage.repository import BotRepository


logger = logging.getLogger(__name__)


class OutcomeTracker:
    """Update saved signals and climax observations with paper outcomes."""

    def __init__(self, client: BybitClient, repository: BotRepository) -> None:
        self._client = client
        self._repository = repository
        self._evaluator = OutcomeEvaluator()

    async def update_due_outcomes(self, now: datetime | None = None) -> int:
        """Refresh saved signal and strategy-observation outcomes."""

        now = now or datetime.now(timezone.utc)
        updated = await self._update_signal_outcomes(now)
        updated += await self._update_strategy_observation_outcomes(now)
        return updated

    async def _update_signal_outcomes(self, now: datetime) -> int:
        pending = self._repository.list_signals_missing_outcomes(now=now)
        updated = 0
        for signal in pending:
            start_ms = int((signal.signal_time - timedelta(minutes=5)).timestamp() * 1000)
            end_ms = int(min(now, signal.signal_time + timedelta(hours=4, minutes=5)).timestamp() * 1000)
            raw = await self._client.fetch_klines(
                signal.symbol,
                "1",
                limit=500,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            frame = klines_to_frame(raw)
            outcome = self._evaluator.evaluate(signal, frame, now=now)
            if outcome is None:
                continue
            self._repository.upsert_signal_outcome(outcome)
            updated += 1
        return updated

    async def _update_strategy_observation_outcomes(self, now: datetime) -> int:
        list_due = getattr(self._repository, "list_strategy_observations_due_outcomes", None)
        update_outcome = getattr(self._repository, "update_strategy_observation_outcome", None)
        if list_due is None or update_outcome is None:
            return 0

        pending = list_due(limit=25)
        updated = 0
        for observation in pending:
            observed_at = normalize_utc(observation["observed_at"])
            start_ms = int(observed_at.timestamp() * 1000)
            end_ms = int(min(now, observed_at + timedelta(minutes=15)).timestamp() * 1000)
            try:
                raw = await self._client.fetch_klines(
                    observation["symbol"],
                    "1",
                    limit=100,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
                frame = klines_to_frame(raw)
                if observation["market_price"] is None:
                    outcome = {
                        "data_status": "unknown",
                        "horizons": {},
                        "mfe_pct": None,
                        "mae_pct": None,
                        "time_to_mfe_minutes": None,
                        "time_to_mae_minutes": None,
                        "new_high_after_observation": None,
                        "observed_candles": 0,
                        "coverage_end": None,
                    }
                else:
                    outcome = evaluate_strategy_observation(
                        observed_at=observed_at,
                        entry_price=observation["market_price"],
                        event_high=observation["event_high"],
                        frame_1m=frame,
                        now=now,
                    )
                if update_outcome(observation["observation_id"], outcome, updated_at=now):
                    updated += 1
            except Exception:
                logger.exception(
                    "strategy observation outcome update failed observation_id=%s symbol=%s",
                    observation.get("observation_id"),
                    observation.get("symbol"),
                )
        return updated
