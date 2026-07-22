# AKEUSDT Climax Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, deterministic AKEUSDT climax-short forensic replay that cannot affect the running baseline bot.

**Architecture:** New `research/` modules consume a checked-in candle/OI fixture and run four pure research models. The replay produces JSON and Markdown reports; unavailable historical microstructure fields remain JSON `null` with a per-field `missing_reason`.

**Tech Stack:** Python 3.12, standard library, pandas, pytest, Bybit public REST only during fixture collection.

---

### Task 1: Fixture contract and source evidence

**Files:**
- Create: `research/fixtures/akeusdt_2026-07-15.json`
- Create: `research/fixtures/akeusdt_2026-07-15.metadata.json`
- Test: `tests/test_climax_replay.py`

- [ ] **Step 1: Write the failing fixture-contract test**

```python
def test_ake_fixture_is_complete_and_marks_unavailable_microstructure() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    assert fixture["symbol"] == "AKEUSDT"
    assert len(fixture["candles_1m"]) == 301
    assert fixture["missing_data"]["trades"]["value"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_climax_replay.py::test_ake_fixture_is_complete_and_marks_unavailable_microstructure`

Expected: FAIL because `research.climax_replay` does not exist.

- [ ] **Step 3: Collect Bybit public candles/OI/funding into immutable fixture files**

Use UTC `2026-07-15T07:30:00Z` through `12:30:00Z`; attach source URLs, retrieval timestamp, and blogger claims under `evidence_source: "user_screenshot_derived"`.

- [ ] **Step 4: Run the contract test**

Run: `.venv/bin/pytest -q tests/test_climax_replay.py::test_ake_fixture_is_complete_and_marks_unavailable_microstructure`

Expected: PASS.

### Task 2: Candle-only and OI research models

**Files:**
- Create: `research/climax_replay.py`
- Create: `tests/test_climax_replay.py`

- [ ] **Step 1: Write failing model assertions**

```python
def test_models_return_deterministic_confirmation_or_explicit_no_confirmation() -> None:
    report = run_replay(load_fixture(FIXTURE_PATH))
    assert set(report["models"]) == {"M1", "M2", "M3", "M4"}
    assert report["models"]["M3"]["status"] == "INSUFFICIENT_DATA"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_climax_replay.py::test_models_return_deterministic_confirmation_or_explicit_no_confirmation`

Expected: FAIL because `run_replay` does not exist.

- [ ] **Step 3: Implement pure replay functions**

Implement candle velocity/acceleration, rollover, new-high, wick/rejection, failed continuation, time-near-high, volume climax, OI divergence, deterministic M1/M2/M4 confirmation, and M3 `INSUFFICIENT_DATA` when trades are absent.

- [ ] **Step 4: Run model tests**

Run: `.venv/bin/pytest -q tests/test_climax_replay.py`

Expected: PASS.

### Task 3: Outcome analysis and reports

**Files:**
- Create: `research/run_akeusdt_replay.py`
- Create: `research/reports/AKEUSDT_2026-07-15_climax_replay.json`
- Create: `research/reports/AKEUSDT_2026-07-15_climax_replay.md`
- Test: `tests/test_climax_replay.py`

- [ ] **Step 1: Write failing report assertions**

```python
def test_report_separates_user_claims_from_server_and_market_evidence() -> None:
    report = run_replay(load_fixture(FIXTURE_PATH))
    assert report["evidence"]["blogger"]["source"] == "user_screenshot_derived"
    assert report["missing_data"]["orderbook"]["value"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest -q tests/test_climax_replay.py::test_report_separates_user_claims_from_server_and_market_evidence`

Expected: FAIL before evidence/report serialization exists.

- [ ] **Step 3: Implement paper-entry outcomes**

Compute MFE/MAE, close outcomes at 5m/15m/30m/1h/4h, and first-hit ordering for favorable 5% vs adverse 3% and favorable 10% vs adverse 5%; produce reports without touching SQLite.

- [ ] **Step 4: Generate reports and rerun all replay tests**

Run: `.venv/bin/python research/run_akeusdt_replay.py && .venv/bin/pytest -q tests/test_climax_replay.py`

Expected: reports are created and all tests pass.

### Task 4: Isolation verification

**Files:**
- Verify only: `app/main.py`, `scripts/run_live.py`, `data/bot.sqlite`, `config.yaml`

- [ ] **Step 1: Record pre/post checksums and service state**

Run: `sha256sum app/main.py scripts/run_live.py data/bot.sqlite config.yaml; systemctl is-active short-telegram-bot-lite.service`

- [ ] **Step 2: Confirm no runtime integration**

Run: `rg -n 'climax_replay|research/' app scripts || true`

Expected: no imports or calls from baseline/runtime.
