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


TERMINAL_STATUSES = {"EXCLUDED", "SCANNED_OK", "SCAN_FAILED", "SCAN_SKIPPED"}
ROTATION_STATUSES = {"OPEN", "COMPLETED", "INCOMPLETE", "ABORTED_RESTART", "FAILED"}


def coverage_percent(covered: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(min(100.0, max(0.0, covered * 100.0 / denominator)), 2)
