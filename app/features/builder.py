"""Feature engineering on top of recent 1m candles."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pandas as pd

from app.domain import EventState, SymbolFeatures
from app.features.atr import atr
from app.features.candle_stats import candle_shape
from app.features.ema import ema
from app.features.returns import pct_return
from app.features.rsi import rsi
from app.features.volume import rolling_zscore
from app.features.vwap import cumulative_vwap
from app.market.candles import resample_ohlcv


class FeatureBuilder:
    """Transform kline data into bot-ready feature DTOs."""

    def build(
        self,
        symbol: str,
        frame_1m: pd.DataFrame,
        state: EventState | None = None,
        derivatives: Mapping[str, Any] | None = None,
        liquidity: Mapping[str, Any] | None = None,
    ) -> SymbolFeatures:
        """Build the current feature snapshot for one symbol."""

        if frame_1m.empty:
            raise ValueError(f"Cannot build features for empty frame: {symbol}")

        frame_5m = resample_ohlcv(frame_1m, "5min")
        frame_15m = resample_ohlcv(frame_1m, "15min")
        latest_1m = frame_1m.iloc[-1]
        latest_5m = frame_5m.iloc[-1]
        latest_15m = frame_15m.iloc[-1]

        vwap_series = cumulative_vwap(frame_1m)
        ema20_series = ema(frame_15m["close"], span=20)
        atr14_series = atr(frame_15m["high"], frame_15m["low"], frame_15m["close"], period=14)
        rsi_15m_series = rsi(frame_15m["close"], period=14)

        rolling_30m = frame_1m["volume"].rolling(30, min_periods=10).sum()
        rolling_1h = frame_1m["volume"].rolling(60, min_periods=20).sum()
        vol_zscore_30m = rolling_zscore(rolling_30m.fillna(0), window=30).iloc[-1]
        vol_zscore_1h = rolling_zscore(rolling_1h.fillna(0), window=24).iloc[-1]

        candle_metrics = candle_shape(
            float(latest_5m["open"]),
            float(latest_5m["high"]),
            float(latest_5m["low"]),
            float(latest_5m["close"]),
        )

        price = float(latest_1m["close"])
        current_vwap = float(vwap_series.iloc[-1])
        current_ema20 = float(ema20_series.iloc[-1])
        current_atr14 = float(atr14_series.iloc[-1]) if not pd.isna(atr14_series.iloc[-1]) else 0.0
        latest_range_atr_ratio = (
            (float(latest_15m["high"]) - float(latest_15m["low"])) / current_atr14
            if current_atr14 > 0
            else 0.0
        )

        ret_5m = pct_return(price, float(frame_1m["close"].iloc[-6])) if len(frame_1m) > 5 else 0.0
        ret_15m = pct_return(price, float(frame_1m["close"].iloc[-16])) if len(frame_1m) > 15 else 0.0
        ret_1h = pct_return(price, float(frame_1m["close"].iloc[-61])) if len(frame_1m) > 60 else 0.0
        ret_4h = pct_return(price, float(frame_1m["close"].iloc[-241])) if len(frame_1m) > 240 else 0.0

        derivatives_metrics = _extract_derivatives(derivatives)
        liquidity_metrics = _extract_liquidity(liquidity)
        event_range_pct = state.event_range_pct if state else None
        pullback_pct = None
        distance_to_high = None
        inside_zone = False
        if state and state.event_high and state.event_base_price:
            distance_to_high = ((state.event_high - price) / state.event_high) * 100 if state.event_high else None
            pullback_pct = ((state.event_high - price) / state.event_high) * 100 if state.event_high else None
            inside_zone = (
                state.zone_low is not None
                and state.zone_high is not None
                and state.zone_low <= price <= state.zone_high
            )

        recent_high_breakout = False
        if len(frame_5m) >= 3:
            recent_high_breakout = float(frame_5m["high"].iloc[-1]) > float(frame_5m["high"].iloc[-3:-1].max())

        latest_body_atr_ratio = (
            abs(float(latest_5m["close"]) - float(latest_5m["open"])) / current_atr14
            if current_atr14 > 0
            else 0.0
        )
        latest_failed_retest = (
            candle_metrics["upper_wick_ratio"] >= 0.12
            and candle_metrics["rejection_from_high_pct"] >= 0.5
            and float(latest_5m["close"]) < float(latest_5m["open"])
        )

        return SymbolFeatures(
            symbol=symbol,
            asof=_frame_timestamp(latest_1m["timestamp"]),
            price=price,
            ret_5m=ret_5m,
            ret_15m=ret_15m,
            ret_1h=ret_1h,
            ret_4h=ret_4h,
            vwap=current_vwap,
            dist_to_vwap_pct=pct_return(price, current_vwap),
            ema20=current_ema20,
            dist_to_ema20_pct=pct_return(price, current_ema20),
            dist_to_ema20_atr=((price - current_ema20) / current_atr14) if current_atr14 else 0.0,
            rsi_15m=float(rsi_15m_series.iloc[-1]) if not pd.isna(rsi_15m_series.iloc[-1]) else 50.0,
            upper_wick_ratio=candle_metrics["upper_wick_ratio"],
            lower_wick_ratio=candle_metrics["lower_wick_ratio"],
            body_pct=candle_metrics["body_pct"],
            rejection_from_high_pct=candle_metrics["rejection_from_high_pct"],
            close_position_in_range=candle_metrics["close_position_in_range"],
            vol_zscore_30m=float(vol_zscore_30m) if not pd.isna(vol_zscore_30m) else 0.0,
            vol_zscore_1h=float(vol_zscore_1h) if not pd.isna(vol_zscore_1h) else 0.0,
            atr_14=current_atr14,
            range_atr_ratio=latest_range_atr_ratio,
            oi_change_15m=derivatives_metrics["oi_change_15m"],
            oi_change_1h=derivatives_metrics["oi_change_1h"],
            funding_rate=derivatives_metrics["funding_rate"],
            open_interest=derivatives_metrics["open_interest"],
            oi_change_pct=derivatives_metrics["oi_change_pct"],
            derivatives_status=derivatives_metrics["derivatives_status"],
            derivatives_reasons=derivatives_metrics["derivatives_reasons"],
            data_quality_warnings=derivatives_metrics["data_quality_warnings"],
            event_range_pct=event_range_pct,
            pullback_from_high_pct=pullback_pct,
            distance_to_event_high_pct=distance_to_high,
            inside_short_zone_flag=inside_zone,
            recent_high_breakout=recent_high_breakout,
            latest_body_atr_ratio=latest_body_atr_ratio,
            latest_failed_retest=latest_failed_retest,
            last_high=float(latest_5m["high"]),
            last_low=float(latest_5m["low"]),
            last_close=float(latest_5m["close"]),
            current_volume=float(latest_1m["volume"]),
            spread_pct=liquidity_metrics["spread_pct"],
            slippage_pct=liquidity_metrics["slippage_pct"],
            orderbook_depth_usdt_1pct=liquidity_metrics["orderbook_depth_usdt_1pct"],
            orderbook_depth_usdt_2pct=liquidity_metrics["orderbook_depth_usdt_2pct"],
            liquidity_available=liquidity_metrics["liquidity_available"],
        )


def _frame_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return pd.Timestamp(value, tz="UTC").to_pydatetime()


def _extract_derivatives(derivatives: Mapping[str, Any] | None) -> dict[str, Any]:
    if not derivatives:
        return {
            "oi_change_15m": None,
            "oi_change_1h": None,
            "funding_rate": None,
            "open_interest": None,
            "oi_change_pct": None,
            "derivatives_status": None,
            "derivatives_reasons": [],
            "data_quality_warnings": [],
        }

    oi_history = derivatives.get("open_interest") or []
    funding_history = derivatives.get("funding") or []

    def _pct_change(rows: list[dict[str, Any]], count: int) -> float | None:
        if len(rows) <= count:
            return None
        latest = float(rows[0].get("openInterest") or rows[0].get("openInterestValue") or 0.0)
        prior = float(rows[count].get("openInterest") or rows[count].get("openInterestValue") or 0.0)
        if prior == 0:
            return None
        return round(((latest / prior) - 1) * 100, 6)

    funding_rate = None
    if funding_history:
        funding_rate = float(funding_history[0].get("fundingRate") or 0.0)

    open_interest = None
    if oi_history:
        open_interest = float(oi_history[0].get("openInterest") or oi_history[0].get("openInterestValue") or 0.0)

    derivatives_status = derivatives.get("derivatives_status")
    derivatives_reasons = [str(item) for item in (derivatives.get("derivatives_reasons") or [])]
    data_quality_warnings = [str(item) for item in (derivatives.get("data_quality_warnings") or [])]

    return {
        "oi_change_15m": _pct_change(oi_history, 1),
        "oi_change_1h": _pct_change(oi_history, 4),
        "funding_rate": funding_rate,
        "open_interest": open_interest,
        "oi_change_pct": _pct_change(oi_history, 1),
        "derivatives_status": str(derivatives_status) if derivatives_status is not None else None,
        "derivatives_reasons": derivatives_reasons,
        "data_quality_warnings": data_quality_warnings,
    }


def _extract_liquidity(liquidity: Mapping[str, Any] | None) -> dict[str, Any]:
    if not liquidity:
        return {
            "spread_pct": None,
            "slippage_pct": None,
            "orderbook_depth_usdt_1pct": None,
            "orderbook_depth_usdt_2pct": None,
            "liquidity_available": False,
        }
    return {
        "spread_pct": liquidity.get("spread_pct"),
        "slippage_pct": liquidity.get("slippage_pct"),
        "orderbook_depth_usdt_1pct": liquidity.get("orderbook_depth_usdt_1pct"),
        "orderbook_depth_usdt_2pct": liquidity.get("orderbook_depth_usdt_2pct"),
        "liquidity_available": True,
    }
