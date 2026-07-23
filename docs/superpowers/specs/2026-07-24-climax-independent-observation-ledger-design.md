# Independent Climax Observation Ledger — Design

## Goal

Add an append-only `strategy_observations` ledger that records every factual evaluation of each enabled `CLIMAX_EXHAUSTION` branch. The first writer covers `VOLUME_CLIMAX_UNWIND` and `LOW_VOLUME_EXTENSION_FAILURE` only. The schema is intentionally reusable for `BASELINE_PULLBACK` in a later package.

The ledger is research telemetry. It must not change evaluator selection, admission, score, grade, thresholds, event state, Telegram, outbox, delivery gates, or autoexecution.

## Existing boundaries

`climax_evaluations` remains the detailed evaluator trace: scores, grades, vetoes, OI, liquidity, features, and Telegram eligibility. The new ledger is the stable denominator contract across strategies. It is not a replacement for `climax_evaluations`.

The current evaluator calculates both climax branches but returns one selected `ClimaxEvaluation`. That loses the unselected branch from the research denominator. The package introduces a side-effect-free `ClimaxEvaluationBundle`:

```python
@dataclass(frozen=True, slots=True)
class ClimaxEvaluationBundle:
    selected: ClimaxEvaluation
    branch_evaluations: dict[str, ClimaxEvaluation]
```

`selected` preserves the current selection exactly. The runtime uses it for all existing live behavior. The writer consumes every item in `branch_evaluations`.

## Observation contract

Each `strategy_observations` row has these groups of fields:

| Group | Fields |
| --- | --- |
| Identity | `observation_id`, `idempotency_key`, `run_id`, `runtime_instance_id` |
| Strategy | `strategy_family`, `strategy`, `evaluation_phase`, `symbol` |
| Correlation | `root_event_id`, `event_revision`, `attempt_id`, `evaluation_id`, nullable `signal_id` |
| Time | `observed_at`, nullable `exchange_time`, `market_asof` |
| Decisions | `live_decision`, `shadow_decision`, `score`, `blockers_json`, `warnings_json` |
| Evidence | `market_price`, `event_high`, `model_version`, `config_hash`, `input_fingerprint`, `input_snapshot_json` |

`observation_id` is a generated immutable row identifier. `run_id` identifies one evaluator invocation and is shared by its branch rows. It is diagnostic metadata only.

`evaluation_id`, `attempt_id`, and `signal_id` are nullable links. The first package leaves `signal_id` null because the ledger is written before `save_signal()`. It never updates an inserted row after Telegram delivery. Signals are correlated later through root/event identity, strategy, and model version.

## Independent axes

The ledger does not mix live and shadow states in one stage enum.

```text
evaluation_phase: INITIAL | PRE_DELIVERY_RECHECK | EVENT_EXPIRED

live_decision: OBSERVED | CANDIDATE | ACTIONABLE | BLOCKED | EXPIRED

shadow_decision: NOT_EVALUATED | WATCHING | FALLBACK_READY | EXPIRED | REJECTED
```

`evaluation_phase` describes which actual evaluator call produced the evidence. `live_decision` describes that branch's independent evaluator result, not whether it was selected for live delivery. `shadow_decision` describes the lifecycle/shadow result independently.

## Writer timing

For an initial climax evaluation:

```text
build features
→ evaluate all enabled climax branches
→ calculate lifecycle/shadow decisions
→ write detailed evaluator traces and one ledger row per branch
→ continue with unchanged selected live evaluation
→ save signal / outbox / Telegram only when existing logic allows it
```

The LOW_VOLUME delivery safety recheck creates a separate evaluator bundle. It writes one ledger row per enabled branch with `evaluation_phase=PRE_DELIVERY_RECHECK` before the existing recheck veto can cancel delivery. An event expiration path that evaluates an existing branch uses `EVENT_EXPIRED`.

One market snapshot can therefore produce two rows, one for `VOLUME_CLIMAX_UNWIND` and one for `LOW_VOLUME_EXTENSION_FAILURE`. A below-threshold score, missing OI, liquidity block, stale candidate, continued pump, or rejected branch still produces a row.

## Idempotency and append-only semantics

The repository calculates an immutable key from canonical input:

```text
SHA-256(
  strategy_family + strategy + symbol + root_event_id + event_revision
  + evaluation_phase + market_asof + input_fingerprint + model_version + config_hash
)
```

It deliberately excludes `observation_id`, `observed_at`, `runtime_instance_id`, and random `run_id`. A restart therefore cannot duplicate the same market observation.

The database enforces `UNIQUE(idempotency_key)` and the repository uses `INSERT ... ON CONFLICT DO NOTHING`. The writer returns one explicit result: `INSERTED`, `DUPLICATE`, or `FAILED`. It never updates or deletes a ledger row.

Required indexes are:

```text
(strategy_family, strategy, observed_at)
(symbol, observed_at)
(root_event_id, event_revision)
(live_decision, observed_at)
(shadow_decision, observed_at)
evaluation_id
attempt_id
UNIQUE(idempotency_key)
```

The maximum write rate is bounded by enabled branches times actual evaluator phases: normally two rows for `INITIAL`, plus at most two rows for LOW_VOLUME's `PRE_DELIVERY_RECHECK`.

## Deterministic evidence

`input_fingerprint` is SHA-256 over canonical JSON. Canonicalization sorts keys, converts timestamps to UTC ISO-8601, rejects NaN and infinity, and uses one documented normalized float representation. Values that could expose secrets or operations are excluded: Telegram tokens and IDs, database URLs, logging settings, and other non-strategy runtime configuration.

`config_hash` is a separate fingerprint over strategy-affecting configuration only. It excludes Telegram, database, logging, scheduler, and other operational settings. The current runtime heartbeat fingerprint remains unchanged; the ledger gets its own strategy fingerprint.

`exchange_time` stays `NULL` when Bybit did not provide it. Local receipt time must never be substituted. Strict candle-close interpretation remains out of scope for this package.

`input_snapshot_json` stores a whitelisted canonical snapshot. Its serialized representation is capped at 32 KiB. If it would exceed the cap, the stored value is a valid compact JSON object containing `snapshot_truncated=true`, the full payload fingerprint, and omitted field names; it is never silently sliced into invalid JSON.

## Failure behavior

The ledger is best-effort telemetry. A repository failure:

```text
raises no exception into the scanner
→ returns FAILED
→ emits structured error logging with safe identifiers
→ increments an in-memory failure counter
→ triggers the existing rate-limited operational alert path
→ leaves the live evaluator and delivery path unchanged
```

The alert is operational-only; no signal-chat message, signal row, outbox row, or event-state mutation is created by a ledger failure.

## Verification requirements

Tests must prove all of the following:

1. A single snapshot records independent VOLUME and LOW_VOLUME rows even when only one branch is selected.
2. A score below `climax_min_signal_score`, missing OI, and a liquidity block still create branch rows with the correct blockers/warnings.
3. A LOW_VOLUME fresh delivery recheck writes `PRE_DELIVERY_RECHECK` rows and can record a later block without changing the initial row.
4. Repeating the same canonical observation returns `DUPLICATE` and leaves row count unchanged, including after a changed runtime/run identifier.
5. A changed `market_asof`, branch, phase, strategy config, or canonical input creates a new row.
6. Oversized evidence is valid bounded JSON and its full fingerprint remains stable.
7. Insert failure returns `FAILED`, logs safely, increments the counter, rate-limits the operational alert, and does not alter selected evaluation, signal/outbox behavior, or event state.
8. Existing VOLUME B/70, LOW_VOLUME safety, WATCH, Grade C, live delivery gate, and no-order regressions remain unchanged.

## Explicit exclusions

This package does not instrument `BASELINE_PULLBACK`, add raw candle storage, change candle-close semantics, expand market coverage, attach signals after delivery, promote lifecycle V2, change systemd, or add order execution.
