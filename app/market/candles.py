"""Helpers for turning Bybit klines into OHLCV data frames."""

from __future__ import annotations

import pandas as pd


OHLCV_COLUMNS = [
    "start_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
]


def klines_to_frame(raw_klines: list[list[str]]) -> pd.DataFrame:
    """Convert Bybit kline payloads into an ascending OHLCV frame."""

    if not raw_klines:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    frame = pd.DataFrame(raw_klines, columns=OHLCV_COLUMNS)
    numeric_columns = [column for column in OHLCV_COLUMNS if column != "start_ms"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["start_ms"] = pd.to_numeric(frame["start_ms"], errors="coerce").astype("int64")
    frame["timestamp"] = pd.to_datetime(frame["start_ms"], unit="ms", utc=True)
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame = frame.set_index("timestamp", drop=False)
    return frame


def resample_ohlcv(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate 1m candles into higher timeframes."""

    if frame.empty:
        return frame.copy()

    aggregated = frame.resample(rule, label="right", closed="right").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "turnover": "sum",
        }
    )
    aggregated = aggregated.dropna(subset=["open", "high", "low", "close"])
    aggregated["timestamp"] = aggregated.index
    return aggregated
