"""Repository-driven outcome updater."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.market.bybit_client import BybitClient
from app.market.candles import klines_to_frame
from app.outcomes.evaluator import OutcomeEvaluator
from app.storage.repository import BotRepository


class OutcomeTracker:
    """Update stored signals with their live outcomes."""

    def __init__(self, client: BybitClient, repository: BotRepository) -> None:
        self._client = client
        self._repository = repository
        self._evaluator = OutcomeEvaluator()

    async def update_due_outcomes(self, now: datetime | None = None) -> int:
        """Refresh outcomes for saved signals that still need data."""

        now = now or datetime.now(timezone.utc)
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
