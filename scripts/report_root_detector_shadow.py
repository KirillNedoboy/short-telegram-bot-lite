"""Read-only report for coverage and ROOT_DETECTOR_SHADOW_V1 telemetry."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    connection = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    def count(table: str) -> int:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    def distinct_count(predicate: str = "1=1") -> int:
        return int(connection.execute(
            f"SELECT COUNT(DISTINCT symbol) FROM market_coverage_ledger WHERE {predicate}"
        ).fetchone()[0])
    coverage = [dict(row) for row in connection.execute("SELECT scan_status, exclusion_reason, COUNT(*) AS n FROM market_coverage_ledger GROUP BY scan_status, exclusion_reason ORDER BY scan_status, exclusion_reason")]
    candidates = [dict(row) for row in connection.execute("SELECT candidate_id, symbol, first_seen_at, live_root_created, live_root_event_id, candidate_to_root_latency, peak_to_root_latency, outcome_status, outcome_mfe_pct, outcome_mae_pct, outcome_json FROM root_detector_shadow_candidates ORDER BY first_seen_at, id")]
    horizon_summary = {}
    for label in ("15m", "30m", "1h", "4h", "12h", "24h"):
        values = []
        for row in candidates:
            try:
                payload = row.get("outcome_json") or {}
                payload = json.loads(payload) if isinstance(payload, str) else payload
                price = payload.get("horizons", {}).get(label, {}).get("short_return_pct")
                if price is not None:
                    values.append(float(price))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        horizon_summary[label] = {"count": len(values), "mean_short_return_pct": sum(values) / len(values) if values else None}
    latency_values = [float(row["candidate_to_root_latency"]) for row in candidates if row["candidate_to_root_latency"] is not None]
    print(json.dumps({
        "exchange_symbols": distinct_count(),
        "eligible": distinct_count("eligible = 1"),
        "scheduled": distinct_count("scheduled = 1"),
        "scanned": distinct_count("scanned = 1"),
        "exclusion_reasons": {str(row["exclusion_reason"]): row["n"] for row in coverage if row["exclusion_reason"]},
        "scheduled_scanned": coverage,
        "shadow_candidates": candidates,
        "candidate_count": len(candidates),
        "live_root_linked": sum(1 for row in candidates if row["live_root_created"]),
        "without_root": sum(1 for row in candidates if not row["live_root_created"]),
        "latency_seconds": {"count": len(latency_values), "min": min(latency_values) if latency_values else None, "max": max(latency_values) if latency_values else None},
        "outcome_maturity": {status: sum(1 for row in candidates if row["outcome_status"] == status) for status in {row["outcome_status"] for row in candidates}},
        "horizon_summary": horizon_summary,
    }, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
