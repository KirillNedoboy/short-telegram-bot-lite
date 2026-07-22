"""Rate-limited Bybit REST client."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pybit.unified_trading import HTTP

from app.infra.request_scheduler import RequestScheduler


class BybitClient:
    """Async wrapper around the sync pybit HTTP client."""

    def __init__(
        self,
        scheduler: RequestScheduler,
        testnet: bool = False,
        timeout: int = 20,
    ) -> None:
        self._scheduler = scheduler
        self._client = HTTP(testnet=testnet, timeout=timeout)

    async def fetch_instruments(self) -> list[dict[str, Any]]:
        """Fetch all linear instruments with cursor pagination."""

        instruments: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            response = await self._scheduler.schedule(self._client.get_instruments_info, **params)
            result = response.get("result", {})
            instruments.extend(result.get("list", []))
            cursor = result.get("nextPageCursor") or None
            if not cursor:
                break

        return instruments

    async def fetch_tickers(self) -> list[dict[str, Any]]:
        """Fetch current linear ticker snapshots."""

        response = await self._scheduler.schedule(self._client.get_tickers, category="linear")
        return response.get("result", {}).get("list", [])

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 240,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[list[str]]:
        """Fetch recent klines."""

        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_ms is not None:
            params["start"] = start_ms
        if end_ms is not None:
            params["end"] = end_ms
        response = await self._scheduler.schedule(self._client.get_kline, **params)
        return response.get("result", {}).get("list", [])

    async def fetch_open_interest(self, symbol: str, interval: str = "15min", limit: int = 5) -> list[dict[str, Any]]:
        """Fetch open-interest history for a symbol."""

        response = await self._scheduler.schedule(
            self._client.get_open_interest,
            category="linear",
            symbol=symbol,
            intervalTime=interval,
            limit=limit,
        )
        return response.get("result", {}).get("list", [])

    async def fetch_funding(self, symbol: str, limit: int = 1) -> list[dict[str, Any]]:
        """Fetch funding history for a symbol."""

        response = await self._scheduler.schedule(
            self._client.get_funding_rate_history,
            category="linear",
            symbol=symbol,
            limit=limit,
        )
        return response.get("result", {}).get("list", [])

    async def fetch_orderbook(self, symbol: str, limit: int = 50) -> dict[str, Any]:
        """Fetch the current linear orderbook for a symbol."""

        response = await self._scheduler.schedule(
            self._client.get_orderbook,
            category="linear",
            symbol=symbol,
            limit=limit,
        )
        return response.get("result", {})

    @staticmethod
    def extract_market_time() -> datetime:
        """Return current UTC time for market snapshots."""

        return datetime.now(timezone.utc)
