"""Observability-only market coverage lifecycle helpers."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


def universe_fingerprint(symbols: Iterable[str]) -> str:
    """Return an order-independent SHA-256 fingerprint of unique symbols."""
    normalized = sorted({str(symbol).upper() for symbol in symbols if symbol})
    payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ScanUniverseTelemetry:
    exchange_symbols: tuple[str, ...]
    eligible_symbols: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]
    observed_at: datetime

    @property
    def exchange_fingerprint(self) -> str:
        return universe_fingerprint(self.exchange_symbols)

    @property
    def eligible_fingerprint(self) -> str:
        return universe_fingerprint(self.eligible_symbols)


def build_coverage_rows(*, rotation_id: str, observed_at: datetime, exchange_symbols: Iterable[str], eligible_symbols: Iterable[str], excluded: Iterable[tuple[str, str]], scheduled_symbols: Iterable[str], symbol_results: Iterable[dict]) -> list[dict]:
    exchange = sorted({str(x).upper() for x in exchange_symbols})
    eligible = {str(x).upper() for x in eligible_symbols}
    scheduled = {str(x).upper() for x in scheduled_symbols}
    exclusions = {str(symbol).upper(): reason for symbol, reason in excluded}
    results = {str(row.get("symbol", "")).upper(): row for row in symbol_results}
    rows = []
    for symbol in exchange:
        row = results.get(symbol, {})
        status = str(row.get("terminal_status") or ("EXCLUDED" if symbol not in eligible else "SCAN_SKIPPED"))
        rows.append({
            "rotation_id": rotation_id, "observed_at": observed_at, "symbol": symbol,
            "exchange_present": True, "eligible": symbol in eligible,
            "exclusion_reason": exclusions.get(symbol), "scheduled": symbol in scheduled,
            "scanned": status in {"SCANNED_OK", "SCAN_FAILED", "SCAN_SKIPPED"},
            "scan_status": status,
            "evidence_json": {"reason_code": row.get("reason_code") or exclusions.get(symbol)},
        })
    return rows


TERMINAL_STATUSES = {"EXCLUDED", "SCANNED_OK", "SCAN_FAILED", "SCAN_SKIPPED"}
ROTATION_STATUSES = {"OPEN", "COMPLETED", "INCOMPLETE", "ABORTED_RESTART", "FAILED"}


def coverage_percent(covered: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(min(100.0, max(0.0, covered * 100.0 / denominator)), 2)
