# Current Bot Architecture

## Scope

This document maps the current production-oriented short signal bot without changing runtime behavior. All observations are based on the local repository snapshot and the local SQLite snapshot at `data/bot.sqlite`.

## Project Tree

```text
short-telegram-bot-lite/
├── app/
│   ├── config.py
│   ├── domain.py
│   ├── logger.py
│   ├── main.py
│   ├── events/
│   ├── features/
│   ├── infra/
│   ├── market/
│   ├── notifications/
│   ├── outcomes/
│   ├── signals/
│   └── storage/
├── data/
│   ├── bot.sqlite
│   └── export_source_manifest.json
├── docs/
├── reports/
├── scripts/
├── tests/
├── .env
├── .env.example
├── config.yaml
├── README.md
└── requirements.txt
```

## Architecture Summary

The bot is a single-process asynchronous poller. It does not auto-execute trades. It pulls Bybit market data over REST, computes features from recent 1m candles, detects pump events, advances them through a pullback state machine, opens a short zone, evaluates a scored setup, sends a Telegram message, stores the signal to SQLite, and later updates post-signal outcomes.

Main orchestration sits in `app/main.py` inside `ShortSignalBot`. The code is modular, but strategy state and persistence are tightly coupled around a single symbol-level `event_states` table and a shared SQLite database.

## Entrypoints

| File | Role |
| --- | --- |
| `scripts/run_live.py` | Live infinite loop. Calls `ShortSignalBot.run_forever()`. |
| `scripts/run_once.py` | One-cycle smoke/debug entrypoint. Calls `ShortSignalBot.run_cycle()`. |
| `scripts/evaluate_outcomes.py` | Manual/offline outcome refresh entrypoint. Calls `ShortSignalBot.update_outcomes()`. |
| `app/main.py` | Runtime composition root. Wires config, DB, Bybit, scanner, notifier, strategy pipeline, and outcome tracker. |

## Runtime Flow

1. `ShortSignalBot.from_files()` loads YAML plus `.env`, configures logging, and builds the runtime.
2. `startup()` initializes Telegram and verifies DB write health via a heartbeat write.
3. Each cycle:
   - load active event states from DB
   - fetch Bybit tickers and instruments
   - filter/rank the symbol universe into a shortlist
   - union shortlist symbols with already-active symbols
   - fetch recent 1m candles for that union
   - process each symbol through the event/signal pipeline
   - persist any event-state updates
   - send Telegram and persist any emitted signal
   - refresh due outcomes for already-saved signals
4. `run_forever()` sleeps `scan_interval_sec` between cycles.

## Config Surfaces

### Runtime Config

`config.yaml` is the main configuration surface. It contains:

- polling and shortlist settings
- event thresholds
- pullback thresholds
- short-zone parameters
- core filter thresholds
- score-related bonus thresholds
- DB URL
- timezone
- derivatives toggle
- request scheduler settings
- Telegram chat IDs

### Environment Overrides

`.env.example` shows three Telegram variables:

- `TELEGRAM_TOKEN`
- `SIGNAL_CHAT_ID`
- `ALERTS_CHAT_ID`

`app/config.py` also supports `.env` overrides for:

- `DB_URL`
- `TIMEZONE`

### Constants / Defaults Inside Code

| File | Important defaults |
| --- | --- |
| `app/config.py` | Default values for all strategy and runtime thresholds. |
| `app/storage/db.py` | SQLite defaults: `journal_mode=WAL`, `busy_timeout=5000ms`. |
| `app/infra/request_scheduler.py` | Retry up to 3 attempts with exponential backoff. |
| `app/notifications/throttling.py` | Error-alert dedup TTL from config. |

## Key Directories and Modules

### `app/`

| File | Role in system |
| --- | --- |
| `app/main.py` | Orchestrator for the full live cycle. |
| `app/config.py` | Loads and validates runtime settings. |
| `app/domain.py` | Shared DTOs and enums for events, features, signals, and outcomes. |
| `app/logger.py` | Process-wide logging setup. |
| `app/events/pump_detector.py` | Detects a pump and creates an `EventState`. |
| `app/events/pullback_tracker.py` | Advances event lifecycle, handles maturity and expiry. |
| `app/events/short_zone.py` | Builds the current short zone from event range or ATR. |
| `app/events/state_store.py` | Small repository adapter for event state load/save. |
| `app/features/builder.py` | Builds live feature snapshots from recent candles and optional derivatives. |
| `app/features/*.py` | Low-level math helpers: ATR, EMA, RSI, returns, candle shape, volume z-score, VWAP. |
| `app/market/bybit_client.py` | Async wrapper over the sync `pybit` HTTP client. |
| `app/market/scanner.py` | Fetches market snapshots, filters shortlist, fetches symbol candles and optional derivatives. |
| `app/market/shortlist.py` | Universe filtering and shortlist ranking. |
| `app/market/candles.py` | Converts raw Bybit klines into pandas frames and resamples them. |
| `app/signals/filters.py` | Hard post-pullback filters. |
| `app/signals/scoring.py` | Score calculation and score breakdown. |
| `app/signals/risk_flags.py` | Penalty points and breakout-risk detection. |
| `app/signals/engine.py` | Final admission logic, signal type selection, A/B/C grade assignment. |
| `app/signals/formatter.py` | Renders Telegram message text. |
| `app/notifications/telegram.py` | Telegram sender for signals and operational alerts. |
| `app/notifications/throttling.py` | Dedupes repeated operational alerts. |
| `app/outcomes/tracker.py` | Finds signals missing outcomes and refreshes them from fresh Bybit candles. |
| `app/outcomes/evaluator.py` | Computes post-signal short-side metrics. |
| `app/storage/db.py` | SQLAlchemy engine/session bootstrap and DB heartbeat. |
| `app/storage/models.py` | ORM schema for `signals`, `signal_outcomes`, `event_states`. |
| `app/storage/repository.py` | DTO-oriented persistence API. |
| `app/infra/cache.py` | In-memory TTL cache used for shortlist history and alert dedup. |
| `app/infra/health.py` | Runtime counters. |
| `app/infra/rate_limiter.py` | Min-delay limiter with jitter. |
| `app/infra/request_scheduler.py` | Concurrency gate plus retry/backoff for external requests. |

### `scripts/`

| File | Role in system |
| --- | --- |
| `scripts/run_live.py` | Live process entrypoint. |
| `scripts/run_once.py` | One-cycle manual run. |
| `scripts/evaluate_outcomes.py` | Manual outcome recomputation entrypoint. |
| `scripts/export_signal_stats.py` | Read-only export of persisted signal/outcome/event data for offline analysis. |
| `scripts/deploy_paramiko.py` | Remote deploy helper. Not part of live strategy logic. |
| `scripts/restart_unit_paramiko.py` | Remote systemd restart helper. Not part of live strategy logic. |

### `data/`

| File | Role in system |
| --- | --- |
| `data/bot.sqlite` | Live SQLite snapshot with signals, event states, outcomes, and DB heartbeat. |
| `data/export_source_manifest.json` | Metadata from a remote production snapshot. Indicates probable VPS DB paths and systemd unit names. |

## Service / Deployment Artifacts

No local `systemd`, Docker, docker-compose, or supervisor config files were found in this repository snapshot.

Operational clues exist in:

- `scripts/deploy_paramiko.py`
- `scripts/restart_unit_paramiko.py`
- `data/export_source_manifest.json`

These indicate:

- remote app root likely `/opt/short-telegram-bot-lite`
- remote DB likely `/opt/krntrade/data/bot.sqlite`
- observed unit names include `short-telegram-bot-lite.service` and `short-signal-bot-live.service`

This is evidence of external service management, but the actual service definitions are not present in-repo.

## Keyword Map

### Signal

- signal DTOs: `app/domain.py`
- signal admission: `app/signals/engine.py`
- signal formatting: `app/signals/formatter.py`
- signal persistence: `app/storage/repository.py`
- signal runtime path: `app/main.py`

### Score / Grade / Tier

- score logic: `app/signals/scoring.py`
- penalty/risk logic: `app/signals/risk_flags.py`
- hard filters before score admission: `app/signals/filters.py`
- grade assignment: `app/signals/engine.py`
- persisted score/grade: `app/storage/models.py`, `app/storage/repository.py`
- `tier` keyword: not found in runtime code; current implementation uses `grade`

### Telegram

- client: `app/notifications/telegram.py`
- message payload formatting: `app/signals/formatter.py`
- send call: `app/main.py`
- persisted send result: `signals.telegram_sent`

### Bybit

- REST client wrapper: `app/market/bybit_client.py`
- scan orchestration: `app/market/scanner.py`
- symbol frame fetch and outcome backfill fetch: `app/main.py`, `app/outcomes/tracker.py`

### Database / SQLite / Outcome

- DB bootstrap: `app/storage/db.py`
- schema: `app/storage/models.py`
- repository layer: `app/storage/repository.py`
- event state storage: `app/events/state_store.py`
- outcome updater: `app/outcomes/tracker.py`
- outcome evaluation: `app/outcomes/evaluator.py`

### Cooldown / Dedup

- no explicit signal cooldown module or table found
- signal dedup is implicit through `event_states.signal_id` plus `SIGNAL_SENT` state in `app/events/pullback_tracker.py` and `app/main.py`
- a signaled event is force-expired 15 minutes later in `PullbackTracker.advance()`
- alert dedup exists only for operational error messages in `app/notifications/throttling.py`

### Universe / Symbols / Whitelist / Blacklist

- universe filter: `app/market/shortlist.py`
- shortlist ranking: `app/market/shortlist.py`
- config-controlled exclusion list: `config.yaml`, `app/config.py`
- no dedicated whitelist/blacklist storage or module found beyond `exclude_symbols` and `exclude_btc_eth`

## Operational Behavior

### Polling

- scan cadence: `scan_interval_sec` from `config.yaml`
- current default: 60 seconds

### External Requests

- Bybit data source: REST via `pybit`
- request concurrency cap: `max_request_concurrency`
- min delay with jitter: `AsyncRateLimiter`
- retries: 3 attempts with exponential backoff up to 4 seconds

### Logging

- console logging only via `logging.basicConfig(...)`
- no local file handler found

### Error Handling

- per-symbol failures are isolated and do not abort the cycle
- cycle-level failures emit a throttled Telegram operational alert
- DB health is write-checked before startup and each cycle

### Restart Behavior

- state survives restarts through SQLite
- active symbols are restored from `event_states`
- shortlist history cache does not survive restart
- in-memory alert dedup cache does not survive restart

## Main Architectural Constraints

1. `event_states` is keyed only by `symbol`, so only one tracked event namespace exists per symbol.
2. `signals` and `signal_outcomes` have no `bot_id`, `strategy_id`, or namespace column.
3. Outcome refresh scans recent signals without any bot partitioning.
4. Exact Telegram payload text is not stored, only `telegram_sent`.
5. Raw market data is not persisted, so deep replay/comparison depends on live re-fetch or exported snapshots.
