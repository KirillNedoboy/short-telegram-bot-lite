# Short Telegram Bot — Project Brief for LLM Handoff

## 1. Mission

This project is a **read-only short-signal and market-observation bot** for Bybit USDT perpetual contracts.

It:

1. scans a bounded market universe;
2. detects strong upward moves / pump-like events;
3. waits for pullback and short-zone conditions;
4. evaluates short setups using hard filters and a score;
5. persists signals, WATCH observations, lifecycle state, and later outcomes in SQLite;
6. may send operator alerts to Telegram when the configured delivery gate allows it.

**It does not place orders, sign transactions, manage wallets, or execute trades automatically.**

Safety invariant:

```text
AUTO-EXECUTION = OFF
```

Do not add order placement or weaken admission gates without a separate explicit design and review decision.

## 2. Product vocabulary

| Term | Meaning |
| --- | --- |
| Event | A detected upward/pump-like market episode tracked by symbol and event identity. |
| Pullback | A measured retracement after the event high that may establish short readiness. |
| Short zone | Price interval derived from the event range or ATR where a short setup can be evaluated. |
| Signal | An actionable evaluated setup that passes live hard filters and score threshold. |
| WATCH | Non-actionable observation/audit record. It is not a trade instruction. |
| Grade | Signal score bucket (`A`, `B`, or `C`); not the same as strategy subtype or trigger window. |
| Trigger window | Pump-detection horizon (`15m`, `1h`, or `4h`). |
| Strategy subtype | The specialized setup family, for example `VOLUME_CLIMAX_UNWIND`. |
| Shadow lifecycle | Evidence-only lifecycle persisted for research; it cannot create a live signal or Telegram delivery by itself. |

## 3. Current strategy contours

### 3.1 Core post-pump short strategy — live evaluator

Main path:

```text
market shortlist
→ deep 1m candles
→ pump event
→ pullback maturity
→ short-zone activation
→ hard filters
→ score / risk flags
→ Aggressive or Confirm signal
```

Relevant modules:

- `app/events/pump_detector.py`
- `app/events/pullback_tracker.py`
- `app/events/short_zone.py`
- `app/signals/filters.py`
- `app/signals/scoring.py`
- `app/signals/risk_flags.py`
- `app/signals/engine.py`

Live signal admission requires the event to be mature enough, the current price to be in the short zone, core filters to pass, and the final score to meet the configured threshold. A `SignalDecision` is persisted to the `signals` table only after it is actionable.

Signal style is separate from strategy subtype:

- `Aggressive`: stronger score/rejection and no breakout risk;
- `Confirm`: the normal confirming style;
- grade `A/B/C`: score bucket, not a permission to bypass hard vetoes.

### 3.2 EARLY_PUMP_WATCH — non-actionable observation

`EARLY_PUMP_WATCH` preserves strong early pump cases before pullback maturity. It is useful for later analysis and operator awareness, but it is **not an actionable short signal**.

Properties:

- stored as a WATCH/audit candidate;
- deduped by tracked event/watch identity;
- controlled by `enable_watch_candidates`;
- Telegram delivery is separately controlled by `send_watch_to_telegram`;
- default behavior must remain silent when WATCH delivery is disabled;
- never routes through the actionable `signals` admission path.

Typical reasons include:

```text
early_pump_not_mature
no_pullback_observed
no_short_zone_active
not_actionable
```

### 3.3 VOLUME_CLIMAX_UNWIND — specialized lifecycle / shadow contour

This contour observes volume-climax exhaustion and unwind behavior around a pump event. It has its own evaluation metadata and a lifecycle with root events, revisions, attempts, confirmation windows, and terminal transitions.

Current safety boundary:

```text
lifecycle = shadow-only
FALLBACK_READY = research state, not a production gate
VOLUME_CLIMAX_UNWIND = do not reinterpret as permission to execute
```

The shadow lifecycle may persist:

```text
CLIMAX_WATCHING
→ confirmation / retest attempt
→ SHADOW_ACTIONABLE or FALLBACK_READY evidence
→ terminal close / expiry
```

Shadow lifecycle rows do not call the Telegram sender, do not create actionable `signals`, and do not modify live admission.

Relevant code:

- `app/signals/climax.py`
- `app/main.py` lifecycle orchestration
- `app/storage/models.py`
- `app/storage/repository.py`

### 3.4 LOW_VOLUME_EXTENSION_FAILURE — specialized live evaluator branch

This subtype evaluates a low-volume extension/failure pattern. It remains subject to the same final delivery and fresh-admission checks as other actionable signals. It must not bypass liquidity, breakout, freshness, or score gates.

Relevant implementation is in `app/signals/climax.py` and the specialized branch in `app/main.py`.

## 4. End-to-end runtime flow

```text
Bybit REST tickers/instruments
→ universe filters and shortlist
→ recent 1m candles
→ features (returns, VWAP, EMA, ATR, RSI, volume, candle shape)
→ event detection
→ pullback tracking / expiry
→ short-zone calculation
→ live evaluator and/or shadow evaluator
→ durable SQLite records
→ gated Telegram delivery for eligible actionable decisions
→ later outcome refresh
```

The main composition root is `app/main.py` (`ShortSignalBot`).

Entrypoints:

- `scripts/run_live.py` — long-running process;
- `scripts/run_once.py` — one-cycle smoke/debug run;
- `scripts/evaluate_outcomes.py` — refresh saved signal outcomes.

## 5. Persistence and delivery contract

Default database:

```text
data/bot.sqlite
```

Important persistence families:

- event state and event snapshots;
- actionable `signals`;
- WATCH candidates;
- root/revision/attempt lifecycle rows;
- append-only lifecycle events;
- outcome rows;
- monitor heartbeat/observability records.

For actionable signal and enabled WATCH delivery, the durable ordering is:

```text
1. render and save the immutable source row with telegram_sent = False
2. save a matching telegram_delivery_outbox row in the same transaction
3. claim the outbox row with a lease
4. attempt Telegram delivery
5. atomically mark outbox SENT and source telegram_sent = True
   or persist RETRY/DEAD with bounded backoff
```

The outbox is new-only. Historical `telegram_sent = False` rows are not automatically enqueued because their exact payload and delivery intent cannot be reconstructed safely. Delivery is at-least-once, not exactly-once: a crash after Telegram accepts a message but before the local `SENT` commit can produce a duplicate on retry. `telegram_sent` alone is a source-row result flag, not a delivery queue.

SQLite lifecycle terminal transitions use `BEGIN IMMEDIATE` around read-check-update-event sequences. Startup reconciliation runs after storage health verification and reports expired rows, newly detected orphans, duplicate terminal-event groups, and explicit reconciliation failure.

## 6. Safety boundaries for future LLMs

Before changing code, preserve these invariants unless the operator explicitly authorizes a behavior change:

- no automatic orders or wallet actions;
- no live admission changes from shadow states;
- `FALLBACK_READY` remains research-only;
- unknown/partial liquidity fails closed at actionable boundaries;
- shadow telemetry failure must not silently fabricate lifecycle state;
- Telegram is not sent for shadow-only actionable/attempt-limit/ledger-failure events;
- do not claim profitability, edge, hit-rate, expectancy, PnL, or drawdown improvement from unit tests or lifecycle evidence;
- do not claim runtime activation from a pushed Git commit;
- do not claim cross-database locking from SQLite tests;
- never store or print secrets, tokens, private keys, or live `.env` values.

## 7. Verification workflow

For code changes:

```bash
cd /opt/short-telegram-bot-lite
.venv/bin/python -m pytest -q
python3 -m compileall -q app tests research
git diff --check
```

For lifecycle changes, also run focused observability tests and repeated concurrency probes. For deployment, separate these facts:

1. **code proof** — tests, compile, schema/integrity checks;
2. **publication proof** — local HEAD equals `origin/main`;
3. **runtime proof** — fresh systemd PID, runtime instance ID, DB rows and logs;
4. **research proof** — raw inputs, labels, costs and time-split OOS replay.

A green test suite proves only code behavior. It does not prove trading performance.

## 8. Current deployment state

The canonical checkout is:

```text
/opt/short-telegram-bot-lite
```

The systemd service is intentionally controlled separately from Git publication. Always inspect the live unit with `systemctl cat` and `systemctl show` before restart. A controlled restart must include a verified backup outside the repository, a fresh non-secret `runtime_instance_id`, DB integrity/WAL checks, and a fresh journal window.

## 9. Recommended reading order

1. `README.md`
2. `docs/project-brief.md` — this file
3. `docs/llm-handoff.md`
4. `docs/current_bot_architecture.md`
5. `docs/current_bot_signal_pipeline.md`
6. `docs/current_bot_data_model.md`
7. `docs/current_bot_score_tier_map.md`
8. `docs/deployment.md`
9. relevant tests under `tests/`

When documentation and executable code disagree, inspect the code and tests first, then update the documentation rather than guessing.
