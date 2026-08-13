"""Non-invasive ROOT_DETECTOR_SHADOW_V1 calculations."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


@dataclass(frozen=True, slots=True)
class ShadowCandidateInput:
    symbol: str
    first_seen_at: datetime
    rotation_id: str
    price: float
    event_high: float
    high_time: datetime
    pump_5m: float
    pump_15m: float
    pump_1h: float
    pump_4h: float
    volume_ratio: float | None
    volume_z: float | None
    oi_5m: float | None = None
    oi_15m: float | None = None
    oi_1h: float | None = None
    distance_from_high: float | None = None
    conditions: dict[str, str] | None = None


def should_create_shadow_candidate(candidate: ShadowCandidateInput) -> bool:
    """Broad observer trigger; it never participates in live detector decisions."""
    return candidate.pump_5m >= 2.0 or candidate.pump_15m >= 4.0 or candidate.pump_1h >= 7.0 or candidate.pump_4h >= 10.0


def candidate_episode_key(candidate: ShadowCandidateInput) -> str:
    bucket = int(candidate.first_seen_at.timestamp() // (15 * 60))
    raw = f"ROOT_DETECTOR_SHADOW_V1|{candidate.symbol.upper()}|{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def evaluate_shadow_outcome(*, observed_at: datetime, entry_price: float, event_high: float | None, frame_5m: pd.DataFrame, market_asof: datetime) -> dict[str, Any]:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    observed = _utc(observed_at)
    asof = _utc(market_asof)
    rows = frame_5m.copy()
    if "timestamp" not in rows.columns and rows.index.name == "timestamp":
        rows = rows.reset_index()
    rows["timestamp"] = pd.to_datetime(rows["timestamp"], utc=True)
    rows = rows[(rows["timestamp"] > observed) & (rows["timestamp"] <= asof)]
    rows = rows.reset_index(drop=True).sort_values("timestamp")
    horizons = {}
    for label, minutes in (("15m", 15), ("30m", 30), ("1h", 60), ("4h", 240), ("12h", 720), ("24h", 1440)):
        target = observed + timedelta(minutes=minutes)
        match = rows[rows["timestamp"] >= target]
        horizons[label] = {"price": None if match.empty else float(match.iloc[0]["close"]), "short_return_pct": None if match.empty else (entry_price - float(match.iloc[0]["close"])) / entry_price * 100}
    if rows.empty:
        return {"horizons": horizons, "mfe_pct": None, "mae_pct": None, "new_high_after_candidate": None, "outcome_status": "CENSORED", "coverage_end_at": None}
    mfe = (entry_price - rows["low"].min()) / entry_price * 100
    mae = (rows["high"].max() - entry_price) / entry_price * 100
    mature = all(value["price"] is not None for value in horizons.values())
    return {"horizons": horizons, "mfe_pct": float(mfe), "mae_pct": float(mae), "new_high_after_candidate": None if event_high is None else bool(rows["high"].max() > event_high), "outcome_status": "MATURE" if mature else "CENSORED", "coverage_end_at": rows.iloc[-1]["timestamp"].to_pydatetime()}


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
