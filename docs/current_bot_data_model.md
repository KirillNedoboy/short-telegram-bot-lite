# Current Bot Data Model

## Storage Summary

Real storage is SQLite, configured by default as `sqlite:///./data/bot.sqlite` and normalized to an absolute path at runtime by `app/storage/db.py`.

Observed tables in the local snapshot:

- `signals`
- `signal_outcomes`
- `event_states`
- `__db_heartbeat`

Observed row counts in the local snapshot:

- `signals`: 152
- `signal_outcomes`: 152
- `event_states`: 55
- `__db_heartbeat`: 1

## ER-Style Entity Map

```text
signals (1) ---- (0..1) signal_outcomes

event_states -- stores per-symbol runtime state
event_states.signal_id -> signals.id is a soft link only
event_states.event_id and signals.event_id duplicate event identity for audit and recovery
```

Important:

- only `signal_outcomes.signal_id -> signals.id` is a real foreign key
- `event_states.signal_id` is not a foreign key
- `event_states` is keyed by `symbol`, not by event or bot namespace

## Table Inventory

### `signals`

Purpose:

- canonical persisted signal record
- stores final label, score, event anchors, and full feature/context snapshot

Primary key:

- `id` integer autoincrement

Indexes:

- `ix_signals_symbol`
- `ix_signals_signal_time`
- `ix_signals_event_id`

Key fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Signal primary key. |
| `symbol` | yes | Bybit symbol. |
| `signal_time` | yes | UTC signal timestamp. |
| `signal_type` | yes | `Aggressive` or `Confirm`. |
| `grade` | yes | `A`, `B`, `C`. |
| `score` | yes | Final admitted score. |
| `market_price` | yes | Price at signal time. |
| `short_zone_low` | yes | Lower bound of active short zone. |
| `short_zone_high` | yes | Upper bound of active short zone. |
| `event_id` | yes | Event identity string. |
| `event_high` | yes | Event high anchor. |
| `event_base_price` | yes | Event base anchor. |
| `event_range_pct` | yes | Event percent range. |
| `pullback_from_high_pct` | yes | Pullback depth at signal. |
| `dist_to_vwap_pct` | yes | Stretch from VWAP at signal. |
| `upper_wick_ratio` | yes | Latest wick strength metric. |
| `rejection_from_high_pct` | yes | Latest rejection metric. |
| `vol_zscore_30m` | yes | Volume anomaly metric. |
| `dist_to_ema20_atr` | yes | EMA stretch in ATR units. |
| `rsi_15m` | yes | RSI snapshot. |
| `ret_1h` | yes | 1h return snapshot. |
| `ret_4h` | yes | 4h return snapshot. |
| `range_atr_ratio` | yes | Range expansion metric. |
| `oi_change_15m` | nullable | Optional derivatives field. |
| `oi_change_1h` | nullable | Optional derivatives field. |
| `funding_rate` | nullable | Optional derivatives field. |
| `context_json` | yes | Full feature snapshot plus reasons, risk flags, core filters, score breakdown. |
| `telegram_sent` | yes | Boolean send result only. |
| `created_at` | yes | Insert timestamp. |

Write timing:

- inserted once at signal emission by `BotRepository.save_signal()`
- not updated later by current runtime

### `signal_outcomes`

Purpose:

- post-signal evaluation store

Primary key:

- `signal_id`

Foreign key:

- `signal_id -> signals.id ON DELETE CASCADE`

Key fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `signal_id` | yes | One-to-one link to `signals.id`. |
| `price_after_15m` | nullable | First close at or after signal time + 15m. |
| `price_after_1h` | nullable | First close at or after signal time + 1h. |
| `price_after_4h` | nullable | First close at or after signal time + 4h. |
| `mfe_pct` | nullable | Best short-side move after signal. |
| `mae_pct` | nullable | Worst adverse move after signal. |
| `reached_vwap` | nullable | Whether future lows tagged signal VWAP. |
| `time_to_vwap_minutes` | nullable | Minutes until first VWAP tag. |
| `tp1_hit` | nullable | Schema field exists but current evaluator does not populate it. |
| `stopped_virtual` | nullable | Schema field exists but current evaluator does not populate it. |
| `updated_at` | yes | Last outcome upsert time. |

Write timing:

- inserted or updated later by `BotRepository.upsert_signal_outcome()`
- driven by `OutcomeTracker.update_due_outcomes()`

### `event_states`

Purpose:

- per-symbol state machine storage for pump/pullback/zone/signal lifecycle

Primary key:

- `symbol`

Indexes:

- `ix_event_states_event_id`
- `ix_event_states_state`

Key fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `symbol` | yes | One row per symbol namespace. |
| `event_id` | yes | Current tracked event identifier. |
| `state` | yes | Lifecycle state. |
| `event_start_time` | nullable | Event base timestamp. |
| `event_high` | nullable | Event high price. |
| `event_high_time` | nullable | Event high timestamp. |
| `event_base_price` | nullable | Event base price. |
| `event_range_pct` | nullable | Event range. |
| `event_features_snapshot` | yes | Detection-time feature snapshot. |
| `trigger_window` | nullable | `15m`, `1h`, or `4h`. |
| `pullback_detected_at` | nullable | Pullback maturity timestamp. |
| `pullback_depth_pct` | nullable | Deepest pullback seen. |
| `pullback_low_price` | nullable | Lowest pullback price seen. |
| `zone_low` | nullable | Persisted active zone low. |
| `zone_high` | nullable | Persisted active zone high. |
| `signal_sent_at` | nullable | Signal timestamp for this event. |
| `signal_id` | nullable | Soft link to saved signal. |
| `expires_at` | nullable | Event expiry boundary. |
| `updated_at` | yes | Last state update time. |
| `notes` | nullable | Schema field exists but current runtime does not use it. |

Write timing:

- created on pump detection
- updated during pullback/zone progression
- updated again when signal is sent
- updated on expiry

### `__db_heartbeat`

Purpose:

- DB write-health probe

Fields:

- `id`
- `checked_at`

Write timing:

- updated on startup and every cycle through `Database.write_heartbeat()`

## Data Written at Signal Time

When a signal is emitted, current runtime writes:

1. `signals` row
2. `event_states.signal_id`
3. `event_states.signal_sent_at`
4. `event_states.state = SIGNAL_SENT`

The signal row includes:

- final score
- grade
- signal type
- live feature snapshot
- risk flags
- reasons
- score breakdown
- core-filter results

## Data Updated Later

Later updates happen in:

- `signal_outcomes`
- `event_states` as events continue or expire
- `__db_heartbeat`

The `signals` row itself is append-only in current behavior.

## Where Tier / Score / Features / Raw Payload Live

### Grade / Score

- `signals.grade`
- `signals.score`

### Score breakdown

- `signals.context_json.score_breakdown`

### Core filter results

- `signals.context_json.core_filters`

### Reasons and risk flags

- `signals.context_json.reasons`
- `signals.context_json.risk_flags`

### Feature snapshot

- signal-time feature snapshot: `signals.context_json`
- event-detection snapshot: `event_states.event_features_snapshot`

### Telegram payload

- exact message text is **not** persisted
- only `signals.telegram_sent` is stored

## Denormalization Notes

Current schema intentionally denormalizes important signal context into `signals`:

- duplicated event anchors
- duplicated selected feature columns
- duplicated full JSON context snapshot

This is useful for analytics because it avoids needing joins for common score/feature analysis. It also means a second bot can compare outputs if it preserves the same shape.

## Entities Not Found as Dedicated Storage

The following were **not** found as standalone persisted entities:

- Telegram delivery log table
- raw market snapshot table
- raw candle store
- score audit table
- symbol universe table
- whitelist table
- blacklist table
- cooldown state table
- dedup state table separate from `event_states`
- service metadata table beyond `__db_heartbeat`

## Critical Fields for a Second Bot

If a second bot needs side-by-side comparison, these fields are the most important to preserve or namespace:

- bot or strategy identity: currently missing and should be added in a future isolated design
- `symbol`
- `signal_time`
- `event_id`
- `trigger_window`
- `signal_type`
- `grade`
- `score`
- `short_zone_low`
- `short_zone_high`
- `event_high`
- `event_base_price`
- `event_range_pct`
- `context_json`
- `telegram_sent`
- outcome horizon fields in `signal_outcomes`

## Storage Risks for Parallel Bots

1. `event_states` primary key is `symbol`, so two bots sharing the same DB will overwrite each other's live state.
2. `signals` has no `bot_id` or `strategy_id`, so analytics and outcomes cannot distinguish bot origin cleanly.
3. `signal_outcomes` is keyed only by `signal_id`, which assumes one global signal namespace.
4. outcome tracking queries recent signals globally, without bot partitioning.
5. exact Telegram payload is not stored, so message-level baseline vs experiment comparisons are incomplete.
