# Deployment and operations

This document describes the current server deployment without including server secrets.

## Local/server layout

- Project path: `/opt/short-telegram-bot-lite`
- Python runtime: `/opt/short-telegram-bot-lite/.venv/bin/python`
- Live entrypoint: `scripts/run_live.py`
- One-shot entrypoint: `scripts/run_once.py`
- Default state database: `data/bot.sqlite`
- Environment file: `/opt/short-telegram-bot-lite/.env` (never commit)
- systemd example: `deploy/short-telegram-bot-lite.service.example`

## Install

```bash
cd /opt/short-telegram-bot-lite
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Edit .env and config.yaml with local values.
```

## Run manually

```bash
.venv/bin/python scripts/run_once.py
.venv/bin/python scripts/run_live.py
```

## systemd

```bash
sudo cp deploy/short-telegram-bot-lite.service.example /etc/systemd/system/short-telegram-bot-lite.service
sudo systemctl daemon-reload
sudo systemctl enable --now short-telegram-bot-lite.service
systemctl status --no-pager short-telegram-bot-lite.service
journalctl -u short-telegram-bot-lite.service -n 100 --no-pager
```

## Safe update sequence

```bash
cd /opt/short-telegram-bot-lite
git pull --ff-only origin main
.venv/bin/pytest -q
sudo systemctl restart short-telegram-bot-lite.service
systemctl is-active --quiet short-telegram-bot-lite.service
```

## Configuration precedence

- `config.yaml` contains non-secret behavior and scanner settings.
- `.env` contains deployment credentials and chat identifiers.
- Runtime code reads configuration through `app/config.py`.

Do not copy the live `.env` into GitHub. For a new deployment, use `.env.example` as the complete variable reference and fill in values locally.
