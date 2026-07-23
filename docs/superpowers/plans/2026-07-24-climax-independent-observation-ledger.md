# Independent Climax Observation Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist one immutable research observation for every enabled `CLIMAX_EXHAUSTION` evaluator branch and evaluator phase without changing live selection or delivery.

**Architecture:** `evaluate_climax_bundle()` exposes the unchanged selected live evaluation plus every enabled branch evaluation. A new pure observation module canonicalizes bounded non-secret evidence and calculates fingerprints. `strategy_observations` is an additive append-only table with a unique idempotency key; the runtime records branch evidence before existing signal side effects and treats telemetry failures as non-fatal operational errors.

**Tech Stack:** Python 3.12, pandas, Pydantic configuration, SQLAlchemy 2.0, SQLite, pytest.

---

### Task 1: Preserve every evaluator branch in a pure bundle

**Files:**
- Modify: `app/signals/climax.py`
- Modify: `tests/test_climax_engine.py`

- [ ] **Step 1: Write failing branch-bundle tests**

```python
def test_bundle_keeps_volume_branch_when_low_volume_is_selected(make_event_state, make_features, make_frame):
    bundle = evaluate_climax_bundle(state, features, frame, config)
    assert bundle.selected.subtype == "LOW_VOLUME_EXTENSION_FAILURE"
    assert set(bundle.branch_evaluations) == {
        "VOLUME_CLIMAX_UNWIND", "LOW_VOLUME_EXTENSION_FAILURE"
    }
    assert "oi_missing_for_volume_climax" in bundle.branch_evaluations["VOLUME_CLIMAX_UNWIND"].veto_reasons

def test_bundle_selected_result_matches_legacy_evaluator(make_event_state, make_features, make_frame):
    assert evaluate_climax_bundle(state, features, frame, config).selected == evaluate_climax(state, features, frame, config)
```

- [ ] **Step 2: Verify RED**

Run: `./.venv/Scripts/python.exe -m pytest -q tests/test_climax_engine.py -k bundle --tb=short`

Expected: import failure for `evaluate_climax_bundle`.

- [ ] **Step 3: Implement the side-effect-free bundle**

```python
@dataclass(slots=True)
class ClimaxEvaluationBundle:
    selected: ClimaxEvaluation
    branch_evaluations: dict[str, ClimaxEvaluation]

def evaluate_climax_bundle(
    state: EventState, features: SymbolFeatures, frame: pd.DataFrame, config: Any,
    *, frozen_initial_extension_pct: float | None = None,
    current_ret5_gate_enabled: bool = True, strict_closed_candles: bool = False,
) -> ClimaxEvaluationBundle:
    base = _common_metadata(
        state, features, frame,
        confirmation_window_minutes=getattr(config, "volume_climax_confirmation_window_minutes", 3),
        strict_closed_candles=strict_closed_candles,
    )
    branches = {"VOLUME_CLIMAX_UNWIND": _volume_climax(base, state, features, config)}
    branches["LOW_VOLUME_EXTENSION_FAILURE"] = _low_volume(
        base, state, features, config,
        frozen_initial_extension_pct=frozen_initial_extension_pct,
        current_ret5_gate_enabled=current_ret5_gate_enabled,
    )
    return ClimaxEvaluationBundle(selected=_select_climax(branches, base), branch_evaluations=branches)

def evaluate_climax(state: EventState, features: SymbolFeatures, frame: pd.DataFrame, config: Any, **kwargs: Any) -> ClimaxEvaluation:
    return evaluate_climax_bundle(state, features, frame, config, **kwargs).selected
```

The implementation must retain the existing `with_volume_context` behavior on `selected` and not mutate admission conditions.

- [ ] **Step 4: Verify GREEN and existing regressions**

Run: `./.venv/Scripts/python.exe -m pytest -q tests/test_climax_engine.py tests/test_low_volume_regression.py --tb=short`

Expected: PASS; existing `ESPORTSUSDT` and AKE assertions remain unchanged.

### Task 2: Add deterministic observation evidence and persistence

**Files:**
- Create: `app/observability/__init__.py`
- Create: `app/observability/strategy_observations.py`
- Modify: `app/storage/models.py`
- Modify: `app/storage/repository.py`
- Create: `tests/test_strategy_observations.py`

- [ ] **Step 1: Write failing evidence and repository tests**

```python
def test_same_observation_is_duplicate_across_runtime_restarts(tmp_path):
    first = repository.record_strategy_observation(observation)
    second = repository.record_strategy_observation(replace(observation, runtime_instance_id="new-run"))
    assert first.status is ObservationWriteStatus.INSERTED
    assert second.status is ObservationWriteStatus.DUPLICATE

def test_nonfinite_or_oversized_snapshot_is_deterministic_and_bounded():
    assert build_observation_evidence(payload).snapshot_json["snapshot_truncated"] is True
    assert len(serialized) <= 32 * 1024
```

- [ ] **Step 2: Verify RED**

Run: `./.venv/Scripts/python.exe -m pytest -q tests/test_strategy_observations.py --tb=short`

Expected: import failure for the observation module and table.

- [ ] **Step 3: Implement pure observation types and canonical evidence**

```python
class ObservationWriteStatus(StrEnum):
    INSERTED = "INSERTED"
    DUPLICATE = "DUPLICATE"
    FAILED = "FAILED"

@dataclass(frozen=True, slots=True)
class StrategyObservation:
    observation_id: str
    idempotency_key: str
    run_id: str
    strategy_family: str
    strategy: str
    evaluation_phase: str
    symbol: str
    root_event_id: str | None
    event_revision: int | None
    observed_at: datetime
    exchange_time: datetime | None
    market_asof: datetime | None

def build_observation_evidence(snapshot: Mapping[str, Any]) -> ObservationEvidence:
    canonical = canonical_json(snapshot)
    return ObservationEvidence(input_fingerprint=sha256(canonical), snapshot_json=bounded_snapshot(canonical))
```

Reject NaN/infinity before hashing. Store a valid truncation marker with the full fingerprint when the canonical snapshot exceeds 32 KiB. Add `StrategyObservationModel` with the approved fields and indexes. The repository uses `INSERT ... ON CONFLICT DO NOTHING` and returns an explicit result without raising into callers.

- [ ] **Step 4: Verify GREEN**

Run: `./.venv/Scripts/python.exe -m pytest -q tests/test_strategy_observations.py --tb=short`

Expected: PASS for inserts, duplicates, deterministic fingerprints, bounded snapshots, indexes, and `integrity_check`.

### Task 3: Instrument initial and recheck climax phases

**Files:**
- Modify: `app/main.py`
- Modify: `app/storage/repository.py`
- Modify: `tests/test_runtime_flow.py`
- Modify: `tests/test_climax_observability.py`

- [ ] **Step 1: Write failing runtime integration tests**

```python
def test_initial_bundle_writes_one_row_per_enabled_branch_before_delivery(tmp_path, make_event_state, make_features, monkeypatch):
    decision = asyncio.run(bot._evaluate_and_send_climax("ONTUSDT", frame, state, features=features))
    assert rows == [("VOLUME_CLIMAX_UNWIND", "INITIAL"), ("LOW_VOLUME_EXTENSION_FAILURE", "INITIAL")]
    assert decision is not None

def test_low_volume_recheck_writes_new_branch_rows_before_veto(tmp_path, make_event_state, make_features, monkeypatch):
    assert phases == ["INITIAL", "PRE_DELIVERY_RECHECK"]
    assert signal_count == 0

def test_ledger_failure_alerts_without_changing_delivery(tmp_path, make_event_state, make_features, monkeypatch):
    assert signal_count == 1
    assert notifier.alerts == ["strategy_observation_ledger_write_failed"]
```

- [ ] **Step 2: Verify RED**

Run: `./.venv/Scripts/python.exe -m pytest -q tests/test_runtime_flow.py tests/test_climax_observability.py -k 'observation or recheck' --tb=short`

Expected: FAIL because no strategy-observation rows or failure path exist.

- [ ] **Step 3: Implement branch trace and ledger writer integration**

```python
bundle = evaluate_climax_bundle(state, features, frame_1m, self._config)
evaluation = bundle.selected
branch_evaluation_ids = self._record_climax_branch_evaluations(bundle, phase="INITIAL", state=state, features=features)
write_results = self._record_strategy_observations(bundle, branch_evaluation_ids, phase="INITIAL", state=state, features=features)
await self._report_observation_write_failures(write_results)
```

Use one generated `run_id` per bundle invocation. Map lifecycle states only to `shadow_decision`; map branch evaluator state only to `live_decision`. Record `PRE_DELIVERY_RECHECK` through the same helper before the existing LOW_VOLUME veto. Keep `signal_id=None`, do not update old rows, and preserve the current selected decision path byte-for-byte outside added telemetry calls.

On `FAILED`, increment a dedicated `ServiceHealth` counter, log safe strategy/root identifiers, and send one rate-limited operational alert through the existing `ErrorThrottler`. Never create signal-chat traffic, outbox rows, or state changes from telemetry failure.

- [ ] **Step 4: Verify GREEN**

Run: `./.venv/Scripts/python.exe -m pytest -q tests/test_runtime_flow.py tests/test_climax_observability.py tests/test_strategy_observations.py --tb=short`

Expected: PASS; initial/recheck ledger rows exist, below-threshold branches persist, and error handling is non-fatal.

### Task 4: Document, verify invariants, and review the patch

**Files:**
- Modify: `docs/current_bot_data_model.md`
- Modify: `docs/current_bot_signal_pipeline.md`
- Modify: `docs/llm-handoff.md`
- Modify: `tests/test_config_runtime.py` only if a strategy-fingerprint helper needs a direct regression test

- [ ] **Step 1: Document the additive contract**

Document `strategy_observations`, its branch/phase denominator meaning, idempotency, nullable links, bounded snapshot behavior, and non-fatal failure reporting. Explicitly say that `BASELINE_PULLBACK` is not instrumented yet and live delivery remains unchanged.

- [ ] **Step 2: Run focused invariant suite**

Run: `./.venv/Scripts/python.exe -m pytest -q tests/test_climax_engine.py tests/test_low_volume_regression.py tests/test_climax_lifecycle.py tests/test_runtime_flow.py tests/test_climax_observability.py tests/test_strategy_observations.py tests/test_live_delivery_policy.py --tb=short`

Expected: PASS with existing delivery, grade, and lifecycle fixtures unchanged.

- [ ] **Step 3: Run full verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --tb=short
.\.venv\Scripts\python.exe -m compileall -q app tests research scripts
git diff --check
git diff --stat
rg -n "place_order|create_order|submit_order|wallet|private_key|api_secret" app scripts
```

Expected: full suite passes; compile succeeds; no whitespace error; no new order API.

- [ ] **Step 4: Commit the completed package**

```powershell
git add app tests docs
git commit -m "feat: add climax observation ledger"
```

Do not push, deploy, restart, or alter VPS state in this package.
