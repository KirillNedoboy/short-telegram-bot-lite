# Technical Strategy Audit

Дата аудита: 2026-07-26
Аудируемый commit: `bce93f0` (`feat: track strategy observation outcomes`)
Проверка кода: `203 passed`

## Область и правила доказательности

Этот документ описывает кодовую базу на `bce93f0`. Утверждения ниже основаны прежде всего на Python-коде и тестах; README и старые документы используются только для фиксации расхождений. Для каждого факта указаны конкретные символы источника и, где есть исполняемый контракт, тест.

Предыдущее состояние (`a76eacb / 190 passed`), старые сообщения о `114 tests` и старые оценки числа сигналов не смешиваются с текущим HEAD. GitHub `main` и production checkout были read-only проверены на `bce93f0`; это фиксирует версионную базу, но не является доказательством качества или прибыльности production-данных. Техническая работоспособность live-бота и доказательство положительного математического ожидания -- разные вопросы.

## 1. Архитектура

| Слой | Ответственность | Код / тесты |
| --- | --- | --- |
| Config/runtime | Pydantic-конфигурация, YAML и `.env`, стратегия-фингерпринт | `app/config.py:AppConfig`, `app/main.py:_strategy_config_fingerprint`; `tests/test_config_runtime.py` |
| Market | Bybit linear USDT snapshots, klines, OI/funding, orderbook, shortlist | `app/market/bybit_client.py:BybitClient`, `app/market/scanner.py:MarketScanner`, `app/market/shortlist.py`; `tests/test_bybit_client.py`, `tests/test_market_scanner_derivatives.py`, `tests/test_shortlist.py` |
| Event state | Persistent event, pump, pullback, short-zone state | `app/events/pump_detector.py:PumpDetector`, `app/events/pullback_tracker.py:PullbackTracker`, `app/events/short_zone.py:ShortZoneBuilder`, `app/events/state_store.py:EventStateStore`; `tests/test_state_pipeline.py`, `tests/test_runtime_flow.py` |
| Features | Live price/returns plus closed-candle structure, derivatives and liquidity | `app/features/builder.py:FeatureBuilder.build`, `app/market/candles.py`; `tests/test_feature_builder.py`, `tests/test_closed_candles.py` |
| Signal engine | Baseline filters, score, grade, squeeze/risk vetoes | `app/signals/engine.py:SignalEngine.analyze`, `app/signals/filters.py`, `app/signals/scoring.py`, `app/signals/risk_flags.py`, `app/signals/squeeze_guard.py`; `tests/test_signal_engine.py`, `tests/test_signal_filters.py`, `tests/test_squeeze_guard.py` |
| Climax | Independent live V1 branches and shadow lifecycle | `app/signals/climax.py:evaluate_climax_bundle`, `app/main.py:ShortSignalBot._evaluate_and_send_climax`; `tests/test_climax_engine.py`, `tests/test_climax_lifecycle.py`, `tests/test_climax_observability.py` |
| Persistence | SQLite/SQLAlchemy signals, outcomes, event state, ledgers, outbox | `app/storage/db.py:Database`, `app/storage/models.py`, `app/storage/repository.py:BotRepository`; `tests/test_storage_db.py`, `tests/test_repository.py`, `tests/test_strategy_observations.py` |
| Delivery | Durable Telegram intent, bounded leases/retries, operational alerts | `app/notifications/telegram.py:TelegramNotifier`, `app/storage/repository.py:claim_due_deliveries`, `app/main.py:_deliver_outbox_item`; `tests/test_watch_candidate_storage.py`, `tests/test_runtime_flow.py` |
| Outcomes/research | Refresh saved signal outcomes and deterministic replay fixtures | `app/outcomes/tracker.py:OutcomeTracker.update_due_outcomes`, `app/outcomes/evaluator.py`, `research/climax_replay.py`; `tests/test_outcome_evaluator.py`, `tests/test_climax_replay.py` |

The composition root is `app/main.py:ShortSignalBot`. It initializes the database and heartbeat, drains the outbox, scans the market, processes symbols, refreshes outcomes, and records scan telemetry in `run_cycle()` (`app/main.py:297`). There is no order-placement method in `app/` or `scripts/`; the repository is a signal bot, not an execution engine. The public Bybit client exposes market endpoints only (`app/market/bybit_client.py:BybitClient`).

## 2. Signal pipeline

1. `MarketScanner.fetch_market_snapshots()` fetches tickers and instruments, keeps trading USDT instruments, and applies minimum turnover/exclusion filters (`app/market/scanner.py:MarketScanner.fetch_market_snapshots`, `app/market/shortlist.py:filter_universe`).
2. `build_shortlist()` combines 24h price rank and scan-to-scan velocity, then caps by turnover (`app/market/shortlist.py:build_shortlist`). Active event symbols are appended to the scan set in `ShortSignalBot.run_cycle()` (`app/main.py:297`).
3. `fetch_symbol_frames()` obtains recent 1m klines and normalizes them to ascending OHLCV (`app/market/scanner.py:MarketScanner.fetch_symbol_frames`, `app/market/candles.py:klines_to_frame`).
4. `_process_symbol()` captures one aware UTC receipt time, fetches optional derivatives, and calls `FeatureBuilder.build()` (`app/main.py:1209`, `app/features/builder.py:FeatureBuilder.build`).
5. `PumpDetector.qualifies()` requires one configured return window plus at least one stretch/volume/range trigger; `build_event()` creates `PUMP_DETECTED` state from closed 1m rows (`app/events/pump_detector.py:PumpDetector`).
6. `PullbackTracker.advance()` tracks pullback depth and promotes to `PULLBACK_OBSERVED`; `ShortZoneBuilder.build()` derives the event-range or ATR zone (`app/events/pullback_tracker.py:PullbackTracker.advance`, `app/events/short_zone.py:ShortZoneBuilder.build`).
7. Baseline `SignalEngine.analyze()` requires mature state, current price in zone, all core filters, score/admission, public grade, liquidity and squeeze/breakout conditions (`app/signals/engine.py:SignalEngine.analyze`).
8. The three live delivery families are selected by `live_delivery_enabled()` (`app/signals/delivery_policy.py:live_delivery_enabled`): `BASELINE_PULLBACK`, `VOLUME_CLIMAX_UNWIND`, and `LOW_VOLUME_EXTENSION_FAILURE`. The delivery mapping is covered by `tests/test_live_delivery_policy.py:test_live_delivery_policy_maps_only_known_actionable_strategies`.
9. Climax evaluation always computes the enabled branches independently and retains the highest actionable/vetoed selection (`app/signals/climax.py:evaluate_climax_bundle`). `ShortSignalBot._record_strategy_observations()` writes both branch rows before existing signal admission (`app/main.py:1083`); low-score and blocked branch behavior is covered by `tests/test_climax_engine.py:test_low_score_volume_observation_is_retained` and `tests/test_runtime_flow.py:test_climax_initial_evaluation_records_every_enabled_branch`.
10. A live decision is saved to `signals` and its immutable delivery payload is inserted into `telegram_delivery_outbox` in one transaction (`app/storage/repository.py:BotRepository.save_signal`). Telegram is attempted only after persistence (`app/main.py:_send_new_delivery`).
11. `OutcomeTracker` refreshes recent saved non-WATCH signals until `price_after_4h` is present and separately refreshes due climax observations (`outcome_status != complete`) with closed 1m market data through the 15-minute horizon (`app/outcomes/tracker.py:OutcomeTracker.update_due_outcomes`, `_update_strategy_observation_outcomes`, `app/storage/repository.py:BotRepository.list_strategy_observations_due_outcomes`).

## 3. Candle and feature semantics

`normalize_utc()` treats a naïve SQLite datetime as UTC; `closed_1m_rows()` accepts a row only when `candle_open + 1 minute <= market_asof`; `complete_5m_ohlcv()` requires all five consecutive 1m rows in a bucket (`app/market/candles.py:normalize_utc`, `closed_1m_rows`, `complete_5m_ohlcv`). Contracts: `tests/test_closed_candles.py:test_closed_1m_rows_exclude_the_candle_still_forming`, `test_complete_5m_ohlcv_requires_all_underlying_closed_rows`, `test_normalize_utc_treats_naive_sqlite_datetime_as_utc`.

| Feature group | Fields | Availability and use | Source / test |
| --- | --- | --- | --- |
| Receipt/live price | `asof`, `market_asof`, `price`, `ret_5m`, `ret_15m`, `ret_1h`, `ret_4h` | Current snapshot; used for live admission and returns | `app/features/builder.py:FeatureBuilder.build`; `tests/test_feature_builder.py` |
| Trend/stretch | `vwap`, `dist_to_vwap_pct`, `ema20`, `dist_to_ema20_pct`, `dist_to_ema20_atr`, `rsi_15m`, `atr_14`, `range_atr_ratio` | Derived from current 1m/15m frames; used by pump, filters and score | `app/features/builder.py:FeatureBuilder.build`, `app/features/vwap.py`, `ema.py`, `rsi.py`, `atr.py`; `tests/test_feature_builder.py` |
| Candle structure | `upper_wick_ratio`, `lower_wick_ratio`, `body_pct`, `rejection_from_high_pct`, `close_position_in_range` | Latest complete 5m candle only | `app/features/builder.py:latest_structural`, `app/market/candles.py:complete_5m_ohlcv`; `tests/test_closed_candles.py:test_feature_builder_keeps_partial_5m_structure_out_of_rejection_and_breakout` |
| Volume | `vol_zscore_30m`, `vol_zscore_1h`, `current_volume` | Rolling 1m volume; live current volume remains available | `app/features/builder.py:FeatureBuilder.build`; `app/features/volume.py` |
| Confirmed high/state | `last_high`, `last_high_time`, `last_low`, `last_close`, `last_structural_close_time`, pullback/zone flags | `last_high` is the highest confirmed closed 1m high in the available frame; structural values use complete 5m data | `app/features/builder.py:FeatureBuilder.build`, `app/events/pullback_tracker.py:reset_after_confirmed_high`; `tests/test_closed_candles.py:test_feature_builder_uses_highest_closed_1m_candle_and_ignores_forming_high`, `tests/test_state_pipeline.py:test_pullback_tracker_resets_stale_pullback_after_confirmed_new_high` |
| Derivatives | `oi_change_15m`, `oi_change_1h`, `oi_change_pct`, `funding_rate`, `open_interest`, status/reasons | Optional and disabled by default; missing OI/funding becomes data-quality context or a climax veto | `app/market/scanner.py:fetch_optional_derivatives`, `app/features/builder.py:_extract_derivatives`, `app/signals/climax.py:_volume_climax`; `tests/test_market_scanner_derivatives.py`, `tests/test_climax_engine.py:test_m1_only_divergence_no_actionable` |
| Liquidity | spread, estimated slippage, depth at 1%/2%, `liquidity_available` | Current orderbook snapshot; baseline and climax risk gates | `app/market/scanner.py:_orderbook_liquidity`, `app/features/builder.py:_extract_liquidity`, `app/signals/risk_flags.py:evaluate_risk_flags`; `tests/test_liquidity_builder.py`, `tests/test_signal_engine.py:test_signal_engine_blocks_missing_liquidity` |
| Event context | event range, pullback distance, distance to high, zone membership, recent breakout | Persistent event plus current price | `app/domain.py:SymbolFeatures`, `app/features/builder.py:FeatureBuilder.build` |

The implementation intentionally mixes live and confirmed data: price, returns, VWAP and zone checks use the current snapshot, while rejection, failed retest, breakout and microstructure inputs use complete closed 5m structure (`app/features/builder.py:FeatureBuilder.build`, `app/signals/climax.py:_common_metadata`). The baseline high used for state transitions is the maximum of closed 1m highs; a forming 1m candle cannot trigger the reset (`app/features/builder.py:FeatureBuilder.build`, `app/events/pullback_tracker.py:reset_after_confirmed_high`). This is a defined design choice, not proof that every outcome timestamp is execution-safe.

## 4. Hard filters and admission

### Baseline

| Gate | Rule | Evidence |
| --- | --- | --- |
| Event state/age | State is `PULLBACK_OBSERVED` or `SHORT_ZONE_ACTIVE`; event and signal expiry are enforced | `app/signals/engine.py:SignalEngine.analyze`; `tests/test_signal_engine.py:test_signal_engine_uses_90_minute_signal_age` |
| Zone | Current price must be inside `ShortZone` | `app/signals/engine.py:SignalEngine.analyze`; `tests/test_signal_engine.py` |
| VWAP distance | `dist_to_vwap_pct >= dist_to_vwap_min` | `app/signals/filters.py:evaluate_core_filters` |
| Rejection | Upper wick or rejection percentage threshold | `app/signals/filters.py:evaluate_core_filters` |
| Volume | `vol_zscore_30m >= vol_zscore_min` | `app/signals/filters.py:evaluate_core_filters`; `tests/test_signal_filters.py` |
| Pullback | `pullback_min_pct <= pullback <= pullback_max_pct` | `app/signals/filters.py:evaluate_core_filters`; `tests/test_state_pipeline.py` |
| Liquidity/squeeze/breakout | Missing/weak orderbook, configured squeeze block, or breakout risk blocks live admission | `app/signals/engine.py:SignalEngine.analyze`, `app/signals/risk_flags.py:evaluate_risk_flags`, `app/signals/squeeze_guard.py:evaluate_squeeze_guard`; `tests/test_signal_engine.py`, `tests/test_squeeze_guard.py` |
| Final admission | All core filters, `score >= 50`, public grade allowed, no action block and no forced WATCH | `app/signals/engine.py:SignalEngine.analyze`; `tests/test_signal_engine.py` |

### Climax V1

`VOLUME_CLIMAX_UNWIND` vetoes missing/invalid OI, price/OI acceleration together, insufficient volume or pump, missing rejection, excessive entry distance and climax liquidity (`app/signals/climax.py:_volume_climax`). `LOW_VOLUME_EXTENSION_FAILURE` additionally requires comparable volume windows, closed-candle count, no new high, low-volume extension, close below breakout reference, lower-high/failed-retest, microstructure break, no OI acceleration/squeeze/resumed acceleration, rejection, entry distance and liquidity (`app/signals/climax.py:_low_volume`). Both branches require score at least `climax_min_signal_score` and grade A/B for `actionable` (`app/signals/climax.py:ClimaxEvaluation.actionable`). Tests: `tests/test_climax_engine.py:test_missing_liquidity_blocks_live_volume_candidate`, `test_low_volume_requires_two_closed_candles_after_high`, `test_low_volume_rejects_unconfirmed_failed_high`.

## 5. Scoring, grades and penalties

Baseline `score_setup()` computes:

| Component | Formula/source |
| --- | --- |
| Stretch | VWAP distance scaled to 10 plus EMA20/ATR distance scaled to 5 |
| Exhaustion | Wick, rejection, close position and failed-retest bonus |
| Volume | Volume z-score and range/ATR |
| Event quality | Maximum of 15m/1h/4h extension scores plus event-range score |
| Pullback maturity | Ideal pullback band plus VWAP-distance score |
| Zone quality | Position inside short zone |
| Derivatives bonus | Positive OI/funding bonuses when available |
| Penalties | `evaluate_risk_flags()` penalties, capped at 40, then squeeze score penalty |

Sources: `app/signals/scoring.py:score_setup`, `app/signals/risk_flags.py:evaluate_risk_flags`, `app/signals/engine.py:SignalEngine.analyze`, `app/signals/squeeze_guard.py:evaluate_squeeze_guard`. Grade mapping is A for `score >= 80`, B for `65..79`, C below 65 (`app/signals/engine.py:_grade_from_score`). Climax score is `min(100, 10 + 15 * passed_conditions)` with branch-specific booleans and grade A/B/C at 85/70 (`app/signals/climax.py:_score`, `_grade`). Regression coverage: `tests/test_signal_engine.py`, `tests/test_climax_engine.py`.

Risk flags include shallow pullback, price near high, weak rejection, thin VWAP buffer, recent high breakout, large continuation body, unconfirmed retest, missing liquidity, spread, slippage and depth (`app/signals/risk_flags.py:evaluate_risk_flags`). Squeeze reasons include negative-funding trap, rising OI with price, shallow pullback, unfailed retest, weak rejection, second-leg pump and liquidity/data-quality warnings (`app/signals/squeeze_guard.py:evaluate_squeeze_guard`).

## 6. Event state machines

### Baseline

`IDLE/EXPIRED -> PUMP_DETECTED -> PULLBACK_OBSERVED -> SHORT_ZONE_ACTIVE -> SIGNAL_SENT`, with expiry from age/kill-price and a return to `PUMP_DETECTED` after a confirmed new 1m high. `PullbackTracker.reset_after_confirmed_high()` preserves `event_id`, base and expiry, updates high/time, clears stale pullback and zone fields, and returns before baseline evaluation (`app/events/pullback_tracker.py:PullbackTracker.advance`, `reset_after_confirmed_high`; `app/main.py:ShortSignalBot._process_symbol`). Tests: `tests/test_state_pipeline.py:test_pullback_tracker_resets_stale_pullback_after_confirmed_new_high`, `test_pullback_tracker_keeps_active_pullback_when_high_is_not_confirmed`, `test_pullback_tracker_requires_a_new_pullback_after_confirmed_high_reset`, and `tests/test_runtime_flow.py:test_confirmed_new_high_after_short_zone_resets_without_delivery`.

### Climax live and lifecycle shadow

Live V1 branches remain independent and selected by score only after veto evaluation (`app/signals/climax.py:evaluate_climax_bundle`). Lifecycle V2 is a separate shadow object: a new high creates a revision and confirmation window; insufficient closed candles, open window, acceleration, squeeze, OI continuation, rejection, liquidity or entry distance keep `CLIMAX_WATCHING`; all gates can produce `FALLBACK_READY`; root lifetime can produce `EXPIRED` (`app/signals/climax.py:advance_volume_climax_lifecycle`). `ShortSignalBot._evaluate_and_send_climax()` persists lifecycle/root/attempt telemetry but does not route shadow state through `live_delivery_enabled()` (`app/main.py:624`). Tests: `tests/test_climax_lifecycle.py` and `tests/test_climax_observability.py`.

## 7. Persistence, ledger and delivery

`Database` configures SQLite WAL and a 5-second busy timeout, creates additive schema objects and columns, and performs a write heartbeat (`app/storage/db.py:Database`). `SignalModel` has an enriched unique identity on `(symbol, event_id, strategy_subtype, model_version)` (`app/storage/models.py:SignalModel`); baseline also checks persistent event state, while climax calls `BotRepository.has_signal_for_event()` (`app/storage/repository.py:BotRepository.has_signal_for_event`).

`strategy_observations` is append-only by convention and protected by a unique idempotency key (`app/storage/models.py:StrategyObservationModel`, `app/storage/repository.py:record_strategy_observation`). Evidence is canonical, bounded to 32 KiB and deterministic; `NaN`, `+Inf` and `-Inf` are normalized to JSON `null` with warning paths, while sensitive token/chat/database/logging keys are excluded (`app/observability/strategy_observations.py:build_observation_evidence`, `_canonicalize`). The key includes strategy, symbol, root/revision, evaluation phase, market_asof, input fingerprint, model and strategy config hash, but excludes `observation_id`, `run_id`, `runtime_instance_id` and local `observed_at` (`app/observability/strategy_observations.py:make_observation_idempotency_key`). Tests: `tests/test_strategy_observations.py:test_same_observation_is_duplicate_across_runtime_restarts`, `test_nested_nonfinite_values_are_saved_as_null`, `test_numpy_nonfinite_values_are_saved_as_null`, `test_evidence_is_deterministic_and_bounded`.

The first ledger writer instruments only enabled `CLIMAX_EXHAUSTION` branches. It writes `INITIAL` and `PRE_DELIVERY_RECHECK` rows before existing selection/delivery, leaves `signal_id=None`, and maps live and shadow decisions independently (`app/main.py:ShortSignalBot._record_strategy_observations`). A failed write is logged, counted in `ServiceHealth`, and rate-limited to an operational alert; the scanner and signal/outbox path continue (`app/main.py:_report_strategy_observation_failures`, `app/infra/health.py:ServiceHealth`, `tests/test_runtime_flow.py:test_failed_observation_write_alerts_without_changing_signal_delivery`). Baseline has no equivalent complete observation denominator.

Each climax observation now has additive outcome storage: status, JSON payload, MFE/MAE, time to favorable/adverse excursion and new-high-after-observation (`app/storage/models.py:StrategyObservationModel`, `app/storage/db.py:Database`, `app/storage/repository.py:update_strategy_observation_outcome`). `evaluate_strategy_observation()` excludes the observation boundary itself, uses only fully closed 1m rows, returns prices/returns at 1m, 3m, 5m and 15m, and marks missing coverage as `incomplete` or `unknown` rather than zero (`app/outcomes/strategy_observations.py:evaluate_strategy_observation`; `tests/test_strategy_observation_outcomes.py`). The tracker processes at most 25 due rows per refresh and this telemetry path has no signal, Telegram, outbox, threshold or admission side effect (`app/outcomes/tracker.py:OutcomeTracker._update_strategy_observation_outcomes`; `tests/test_strategy_observation_outcome_tracker.py:test_outcome_tracker_updates_strategy_observations_without_signal_side_effects`). Baseline observations remain outside this first instrumented scope.

Telegram delivery is durable but at-least-once: source row and outbox intent are transactional, claims use leases, retries are bounded to five attempts, and `SENT` updates source and outbox atomically (`app/storage/models.py:TelegramDeliveryOutboxModel`, `app/storage/repository.py:save_signal`, `claim_due_deliveries`, `mark_delivery_sent`, `mark_delivery_retry`). An acknowledgement lost after Telegram accepts the message can cause a duplicate retry; this is a documented property, not exactly-once delivery (`README.md:23`, `docs/current_bot_data_model.md:255`, `tests/test_watch_candidate_storage.py:test_delivery_lease_expiry_requeues_item`). Historical unsent rows are intentionally not auto-enqueued (`app/storage/repository.py:count_legacy_unsent_signals`, `tests/test_watch_candidate_storage.py:test_legacy_unsent_signals_are_not_auto_enqueued`).

## 8. Outcome calculation

`OutcomeEvaluator.evaluate()` selects `frame_1m[timestamp >= signal_time]`, stores prices at 15m/1h/4h, computes short-side MFE from future lows and MAE from future highs, checks VWAP, TP1 and virtual stop, then classifies `CLEAN_TP`, `DIRTY_TP_HIGH_MAE`, `SQUEEZE_BEFORE_TP`, `SL_OR_BAD` or `NOT_ENTERED` (`app/outcomes/evaluator.py:OutcomeEvaluator.evaluate`, `_classify_risk_adjusted`). `OutcomeTracker.update_due_outcomes()` fetches five minutes before the signal through at most four hours plus five minutes and upserts repeatedly until 4h is available (`app/outcomes/tracker.py:OutcomeTracker.update_due_outcomes`, `app/storage/repository.py:upsert_signal_outcome`). Tests: `tests/test_outcome_evaluator.py` and `tests/test_climax_replay.py`.

The separate `evaluate_strategy_observation()` path is research telemetry for both climax branches, including blocked and low-score rows; it is not a replacement for the saved-signal outcome model. It computes closed-candle MFE/MAE and excursion timing across the available post-observation window, with 1m/3m/5m/15m horizon values, but does not model execution or costs (`app/outcomes/strategy_observations.py:evaluate_strategy_observation`, `app/storage/models.py:StrategyObservationModel`).

The separate `evaluate_hypothetical_short()` replay helper supports 1m/3m/5m/15m horizons, MFE/MAE and new-high detection, but it is not the normal persisted signal outcome model (`app/outcomes/evaluator.py:evaluate_hypothetical_short`; `tests/test_climax_replay.py:test_hypothetical_short_outcomes_include_required_shadow_horizons`).

## 9. Confirmed findings

### F1 -- Normal outcomes do not explicitly model the candle-open/receipt boundary (high)

`OutcomeEvaluator.evaluate()` selects rows by candle-open timestamp (`timestamp >= signal.signal_time`), not by an explicit closed-candle or intra-minute receipt boundary. The normal runtime usually records `signal_time` after the latest candle opened, so the forming row is commonly excluded; that safety depends on timestamp ordering rather than an explicit contract and is not tested for exact-boundary, delayed or replayed signals. A future backtest can therefore include pre-receipt movement if a stored signal time is at or before the candle open (`app/outcomes/evaluator.py:OutcomeEvaluator.evaluate`, `app/main.py:ShortSignalBot._process_symbol`; `tests/test_outcome_evaluator.py` has no boundary regression).

### F2 -- TP and stop chronology is not modeled (high)

`tp1_hit` and `stopped_virtual` are independent `any()` checks over the full future frame. There is no first-hit ordering, same-candle stop/target policy, fill price, or partial-fill model in the persisted evaluator (`app/outcomes/evaluator.py:OutcomeEvaluator.evaluate`). The replay helper has a separate first-hit model, proving the distinction (`research/climax_replay.py`, `tests/test_climax_replay.py:test_confirmed_model_has_paper_outcomes_and_first_hit_ordering`).

### F3 -- Persisted outcomes are not cost-aware trading returns (high)

The persisted signal and strategy-observation outcome schemas store prices, MFE/MAE and timing/classification fields, but no entry/exit fees, funding cash flow, spread/slippage execution adjustment, latency, leverage, liquidation distance or position-size PnL (`app/domain.py:SignalOutcome`, `app/storage/models.py:SignalOutcomeModel`, `StrategyObservationModel`, `app/outcomes/evaluator.py`, `app/outcomes/strategy_observations.py`). `slippage_pct`, spread and funding exist as signal-time features/risk inputs, not as realized outcome costs (`app/features/builder.py:FeatureBuilder`, `app/signals/risk_flags.py`).

### F4 -- Baseline lacks a complete observation denominator (high)

The append-only observation writer is called from the climax bundle path and iterates only `bundle.branch_evaluations`; baseline rejections are recorded as aggregate `reject_stats`, with only selected optional WATCH rows persisted separately (`app/main.py:_record_strategy_observations`, `_process_symbol`, `app/storage/repository.py:record_reject_stat`, `save_watch_candidate`). Therefore the repository cannot yet answer how many baseline candidates were observed before score/admission loss with the same independent-event semantics.

### F5 -- Ledger event-time fields are incomplete for exchange-level replay (medium)

The writer sets `market_asof=features.asof` from local receipt time and explicitly sets `exchange_time=None`; `signal_id` is also `None` because the row is inserted before `save_signal()` (`app/main.py:_record_strategy_observations`). The schema supports these fields (`app/storage/models.py:StrategyObservationModel`), but current writes do not populate exchange time or a direct signal link. Observation outcomes use this stored `observed_at` boundary and only closed 1m rows; they are asynchronous research telemetry, not exchange-timestamped fills.

### F6 -- Telegram delivery is at-least-once, not exactly-once (medium)

The durable outbox correctly preserves intent across failures, but an accepted Telegram request followed by a lost acknowledgement can be retried and duplicated (`app/storage/repository.py:_deliver_outbox_item` and `claim_due_deliveries`, `README.md:23`). This is an operational limitation to include in any delivery-quality analysis.

### F7 -- Documentation has known drift (medium)

`docs/current_bot_data_model.md:125-127` says `tp1_hit` and `stopped_virtual` are not populated, while `OutcomeEvaluator.evaluate()` populates both. The current high-reset behavior is implemented in `PullbackTracker.reset_after_confirmed_high()`, so any older text describing replacement/cancellation of the event must be treated as stale until updated. These are documentation findings, not runtime behavior changes.

## 10. Unknowns and non-findings

- Profitability, win rate, expectancy and drawdown are unknown. `203 passed` validates code contracts, not market performance (`pytest` result; no production claim).
- Strategy-observation outcomes are bounded to the available post-observation 15-minute window; missing future candles are explicit `incomplete`/`unknown` states, and no cost-aware return is persisted.
- The current repository does not provide a complete, immutable historical candle/OI/funding/orderbook corpus for all observed events. The AKE replay fixture explicitly marks trades and orderbook as unavailable (`research/fixtures/akeusdt_2026-07-15.json`, `tests/test_climax_replay.py:test_ake_fixture_is_complete_and_marks_unavailable_microstructure`).
- The effect of each gate, score component and grade is unknown without a time-ordered denominator and holdout data. No threshold should be tuned from aggregate signals alone.
- The exact Telegram receipt delay and hypothetical executable price are not persisted as a normal signal outcome field. `telegram_sent` is a delivery result flag, not a market-time execution timestamp (`app/storage/models.py:SignalModel`, `app/notifications/telegram.py:TelegramNotifier`).
- GitHub `main` and the VPS checkout are aligned at `bce93f0`; this version fact does not assert service health, database counts or profitability.

## 11. Minimum honest backtest requirements

1. Freeze one UTC-aware signal receipt time, exchange/candle timestamps and all signal-time features in an immutable snapshot; reject or explicitly label incomplete/missing OI, funding, orderbook and candle rows (`app/market/candles.py`, `app/observability/strategy_observations.py`).
2. Add a complete denominator for baseline observations, including blocked, low-score, stale and non-selected branches, without changing live admission.
3. Exclude the signal-minute pre-receipt range from outcomes or model the exact intra-minute receipt boundary; define a deterministic same-candle stop/target rule.
4. Define executable short-entry/exit rules, fees, funding, spread, slippage, latency, partial fills, leverage, liquidation distance and missing-data policy. Persist gross and net returns separately.
5. Use a strictly temporal train/validation/test or walk-forward split. Freeze thresholds/config/model per evaluation window and report sensitivity without selecting on the holdout.
6. Report MFE/MAE, first favorable/adverse movement, 1m/3m/5m/15m observation horizons plus 15m/1h/4h signal horizons, grade/strategy/symbol/time buckets, costs and worst/base cases.
7. Require an independent forward/paper period before any strategy or lifecycle promotion. Keep lifecycle V2 shadow-only until the comparison is complete.

## 12. Parameters unsafe to optimize blindly

Without the requirements above, do not tune `event_ret_15m_min`, `pullback_min_pct`, `pullback_max_pct`, `dist_to_vwap_min`, `vol_zscore_min`, rejection/wick thresholds, OI/liquidity gates, score weights, grade thresholds, squeeze actions, climax thresholds, closed-candle counts, lifecycle windows or delivery gates. Their definitions are in `app/config.py:AppConfig`; their consumers are `app/events/pump_detector.py`, `app/signals/filters.py`, `app/signals/scoring.py`, `app/signals/climax.py` and `app/signals/delivery_policy.py`. A parameter change without temporal holdout and cost-aware evaluation would change the research target after observing outcomes.

## 13. GO / NO-GO для profitability research

**Решение: NO-GO для исследования прибыльности на текущем persisted outcome contract.**

Причины подтверждены F1--F5: signal-minute contamination risk, отсутствует first-hit/execution model, нет cost-aware net return, baseline denominator неполон, а exchange-time/receipt linkage недостаточны для строгого replay. Это решение **не означает**, что live signal bot технически неработоспособен: текущие `pytest`-контракты проходят, live admission и delivery gates покрыты тестами, а autoexecution отсутствует. Оно означает только, что текущие данные и outcome semantics ещё не доказывают положительное математическое ожидание после торговых издержек и out-of-sample проверки.

Следующий безопасный пакет: закрыть перечисленные telemetry/outcome gaps отдельными аддитивными изменениями, затем собрать заранее заданный временной holdout. Не менять одновременно thresholds, scoring, delivery и lifecycle promotion.

## Проверка этого аудита

- `.\.venv\Scripts\python.exe -m pytest -q` -> `203 passed in 14.07s` (12 non-blocking NumPy `DeprecationWarning` messages from the existing observation-outcome implementation).
- `.\.venv\Scripts\python.exe -m compileall -q app scripts tests` -> `exit 0`.
- `git diff --check` -> `exit 0`; для нового untracked-файла дополнительно проверен `git diff --no-index --check`, без whitespace findings.
- Изменение scope: только `docs/strategy_audit.md`; deployment и restart не выполняются в этой фазе.
