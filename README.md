# Telegram Short Signal Bot v1.1 Lite

Lightweight live Telegram signal bot for Bybit USDT perpetual contracts.

## What It Does

- scans Bybit `linear` USDT perpetual tickers
- builds a configurable shortlist
- detects pump events
- can persist an **EARLY_PUMP_WATCH** / pre-pullback watch for strong blogger-like moves that are not mature enough to trade
- waits for the first meaningful pullback before any actionable short setup exists
- activates a post-pump short zone
- scores aggressive or confirm signals
- sends Telegram alerts for actionable signals
- stores signals, WATCH candidates, event state, and basic outcomes in SQLite

## Project Layout

```text
short-telegram-bot-lite/
├── app/
├── scripts/
├── tests/
├── .env.example
├── config.yaml
├── requirements.txt
└── README.md
```

## Quick Start

1. Create a virtual environment with Python 3.11+.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in Telegram values.
4. Review `config.yaml`.
5. Run a one-shot pass:

```bash
python scripts/run_once.py
```

6. Run the live loop:

```bash
python scripts/run_live.py
```

7. Recompute outcomes for saved signals:

```bash
python scripts/evaluate_outcomes.py
```

## Configuration

- `config.example.yaml` is the complete safe reference for behavior, thresholds, scheduling, storage, rate limiting, watch candidates, and squeeze-guard settings.
- `.env.example` is the complete reference for Telegram credentials and chat IDs.
- Keep the real `.env` outside Git. Never commit tokens, private keys, chat exports, databases, logs, `.venv`, or generated files.

## Documentation map

- `docs/current_bot_architecture.md` — modules and data flow
- `docs/current_bot_signal_pipeline.md` — event → pullback → short-zone → signal lifecycle
- `docs/current_bot_data_model.md` — SQLite entities and persistence
- `docs/current_bot_score_tier_map.md` — scoring and tier semantics
- `docs/deployment.md` — server/systemd deployment and update procedure
- `docs/llm-handoff.md` — compact onboarding guide for another LLM
- `SECURITY.md` — secret-handling rules
- `CONTRIBUTING.md` — development and quality gates

## Tests

Run the full suite with:

```bash
.venv/bin/pytest -q
```

The current server snapshot passes the full test suite before publication.

## Notes

- The bot never opens orders automatically.
- Optional OI and funding inputs are supported but disabled by default.
- The default database is SQLite, but the repository layer is SQLAlchemy 2.0 friendly for a later PostgreSQL move.
