# Current Bot Signal Pipeline

## End-to-End Flow

This is the effective signal path from live market data to Telegram and later to stored outcomes.

1. **Load runtime state**
   - File: `app/main.py`
   - Function: `ShortSignalBot.run_cycle()`
   - Active symbol states are loaded from SQLite via `EventStateStore.load_active()` and `BotRepository.list_active_event_states()`.

2. **Fetch Bybit market universe**
   - Files: `app/market/scanner.py`, `app/market/bybit_client.py`
   - Functions:
     - `MarketScanner.fetch_market_snapshots()`
     - `BybitClient.fetch_tickers()`
     - `BybitClient.fetch_instruments()`
   - Source is Bybit linear USDT perpetual REST data.

3. **Filter and shortlist the universe**
   - File: `app/market/shortlist.py`
   - Functions:
     - `filter_universe()`
     - `build_shortlist()`
   - Filters:
     - `min_24h_volume`
     - `exclude_symbols`
     - `exclude_btc_eth`
   - Ranking:
     - top daily movers
     - top scan-to-scan velocity movers
     - union of both, then sort by turnover

4. **Build working symbol set**
   - File: `app/main.py`
   - Function: `ShortSignalBot.run_cycle()`
   - Runtime union is:
     - current shortlist symbols
     - any symbols with active event states already in DB
   - This keeps existing events alive even if a symbol falls out of the shortlist.

5. **Fetch recent 1m candles for working symbols**
   - Files: `app/market/scanner.py`, `app/market/bybit_client.py`, `app/market/candles.py`
   - Functions:
     - `MarketScanner.fetch_symbol_frames()`
     - `BybitClient.fetch_klines()`
     - `klines_to_frame()`
   - Default depth: `deep_scan_kline_limit` from config, currently 300 candles.

6. **Fetch optional derivatives inputs**
   - Files: `app/main.py`, `app/market/scanner.py`
   - Functions:
     - `ShortSignalBot._process_symbol()`
     - `MarketScanner.fetch_optional_derivatives()`
   - Data:
     - open interest history
     - funding history
   - Disabled by default through `derivatives_enabled: false`.

7. **Compute live features**
   - File: `app/features/builder.py`
   - Function: `FeatureBuilder.build()`
   - Inputs:
     - symbol
     - 1m candle frame
     - current event state, if any
     - optional derivatives payload
   - Features include:
     - returns: 5m, 15m, 1h, 4h
     - VWAP and distance to VWAP
     - EMA20 and ATR-based stretch
     - RSI 15m
     - wick/body/rejection candle stats
     - volume z-scores
     - event-relative fields
     - short-zone membership flag
     - breakout / failed retest / continuation body flags

8. **Detect a fresh pump event**
   - File: `app/events/pump_detector.py`
   - Functions:
     - `PumpDetector.qualifies()`
     - `PumpDetector.build_event()`
   - Trigger windows:
     - `15m` if `ret_15m >= event_ret_15m_min`
     - else `1h` if `ret_1h >= event_ret_1h_min`
     - else `4h` if `ret_4h >= event_ret_4h_min`
   - Additional stretch requirement:
     - at least one of VWAP stretch, EMA20 ATR stretch, volume z-score, or range/ATR expansion must be strong enough
   - Output:
     - `EventState(state=PUMP_DETECTED, trigger_window=..., event_id=...)`

9. **Persist optional EARLY_PUMP_WATCH before pullback maturity**
   - File: `app/main.py`
   - Function: `ShortSignalBot._maybe_emit_early_pump_watch()`
   - Purpose:
     - preserve blogger-like early pump cases in the watch/audit layer
     - do **not** create an actionable trade signal
   - Requirements:
     - symbol already passed universe + deep-scan entry
     - state is only `PUMP_DETECTED`
     - watch mode is enabled
     - early watch score reaches `watch_min_score`
   - Recorded reasons:
     - `early_pump_not_mature`
     - `no_pullback_observed`
     - `no_short_zone_active`
     - `not_actionable`
   - Delivery behavior:
     - saved to watch/reject/audit storage
     - follows `send_watch_to_telegram`; when `false`, nothing is sent to Telegram

10. **Track pullback maturity**
   - File: `app/events/pullback_tracker.py`
   - Function: `PullbackTracker.advance()`
   - Logic:
     - update event high if no pullback has been seen and price makes a higher high
     - compute pullback depth from event high
     - require pullback inside `[pullback_min_pct, pullback_max_pct]`
     - require hold above VWAP stretch floor `pullback_hold_vwap_min`
     - require price above event-range floor `pullback_hold_range_floor_pct`
   - Output state:
     - `PULLBACK_OBSERVED` once maturity conditions pass

11. **Expire dead events**
   - File: `app/events/pullback_tracker.py`
   - Function: `PullbackTracker._should_expire()`
   - Expiry conditions:
     - current time passed `expires_at`
     - price falls below a kill price at 35% of the event range off the base
     - after a signal is sent, force-expire 15 minutes later

11. **Build short zone**
   - File: `app/events/short_zone.py`
   - Function: `ShortZoneBuilder.build()`
   - Modes:
     - `event_range` using configurable percentages of the event range
     - `atr_from_high` using ATR offsets from event high
   - Result is persisted back to event state as `zone_low` and `zone_high`.

12. **Refresh features with zone-aware state**
   - File: `app/main.py`
   - Function: `ShortSignalBot._process_symbol()`
   - After writing zone bounds into state, features are rebuilt so `inside_short_zone_flag` and event-relative fields reflect the updated zone.

13. **Activate short-zone state**
   - File: `app/main.py`
   - Function: `ShortSignalBot._process_symbol()`
   - If price is inside the zone and the state is already `PULLBACK_OBSERVED`, the state becomes `SHORT_ZONE_ACTIVE`.

14. **Apply hard admission gates**
   - File: `app/signals/engine.py`
   - Function: `SignalEngine.evaluate()`
   - Gating conditions before a signal exists:
     - state must be `PULLBACK_OBSERVED` or `SHORT_ZONE_ACTIVE`
     - live price must be inside the short zone
     - all core filters must pass
     - final score must be at least 50

15. **Apply core filters**
   - File: `app/signals/filters.py`
   - Function: `evaluate_core_filters()`
   - Hard filters:
     - `dist_to_vwap_pct >= dist_to_vwap_min`
     - rejection candle quality via upper wick or rejection percentage
     - `vol_zscore_30m >= vol_zscore_min`

16. **Score the setup**
   - Files: `app/signals/scoring.py`, `app/signals/risk_flags.py`
   - Functions:
     - `score_setup()`
     - `evaluate_risk_flags()`
   - Scoring buckets:
     - stretch
     - exhaustion
     - volume
     - event quality
     - pullback maturity
     - zone quality
     - derivatives bonus
   - Penalties and breakout-risk flags are subtracted here.

17. **Assign signal type and A/B/C grade**
   - File: `app/signals/engine.py`
   - Logic:
     - `Aggressive` if `score >= 75`, rejection is strong, and `breakout_risk` is false
     - otherwise `Confirm`
     - grade:
       - `A` for `score >= 80`
       - `B` for `score >= 65`
       - `C` otherwise
   - Important:
     - current code has `grade`, not a separate `tier`
     - `trigger_window`, `signal_type`, and `grade` are different concepts

18. **Format Telegram payload**
   - File: `app/signals/formatter.py`
   - Function: `format_signal_message()`
   - Inputs:
     - symbol
     - signal type
     - signal time localized by config timezone
     - short zone
     - grade
     - score
     - reasons
     - translated risk flags

19. **Send Telegram**
   - File: `app/notifications/telegram.py`
   - Function: `TelegramNotifier.send_signal()`
   - Call site:
     - `app/main.py`, `ShortSignalBot._process_symbol()`
   - Important ordering:
     - Telegram send happens before DB signal insert
     - only a boolean send result is persisted, not the exact payload text

20. **Persist signal**
   - File: `app/storage/repository.py`
   - Function: `BotRepository.save_signal()`
   - Stored data includes:
     - signal metadata
     - event anchor values
     - selected feature columns
     - full feature snapshot in `context_json`
     - reasons, risk flags, score breakdown in `context_json`
     - `telegram_sent`

21. **Mark the symbol as already signaled**
   - Files: `app/events/pullback_tracker.py`, `app/main.py`
   - Functions:
     - `PullbackTracker.mark_signal_sent()`
     - `ShortSignalBot._process_symbol()`
   - State changes:
     - state becomes `SIGNAL_SENT`
     - `signal_id` is stored on the event state
     - future duplicate signals for the same tracked event are suppressed

22. **Persist updated event state**
   - File: `app/events/state_store.py`
   - Function: `EventStateStore.save()`
   - Underlying persistence:
     - `BotRepository.upsert_event_state()`

23. **Update outcomes later**
   - Files: `app/main.py`, `app/outcomes/tracker.py`, `app/outcomes/evaluator.py`
   - Functions:
     - `ShortSignalBot.update_outcomes()`
     - `OutcomeTracker.update_due_outcomes()`
     - `OutcomeEvaluator.evaluate()`
   - Flow:
     - select recent signals still missing a complete 4h outcome
     - refetch post-signal 1m candles from Bybit
     - compute `price_after_15m`, `price_after_1h`, `price_after_4h`, `mfe_pct`, `mae_pct`, `reached_vwap`, `time_to_vwap_minutes`
     - upsert into `signal_outcomes`

## Stage-to-Owner Map

| Stage | Functions / classes | Files |
| --- | --- | --- |
| Universe fetch | `MarketScanner.fetch_market_snapshots`, `BybitClient.fetch_tickers`, `BybitClient.fetch_instruments` | `app/market/scanner.py`, `app/market/bybit_client.py` |
| Universe filter / shortlist | `filter_universe`, `build_shortlist` | `app/market/shortlist.py` |
| Candle fetch | `MarketScanner.fetch_symbol_frames`, `BybitClient.fetch_klines`, `klines_to_frame` | `app/market/scanner.py`, `app/market/bybit_client.py`, `app/market/candles.py` |
| Feature engineering | `FeatureBuilder.build` | `app/features/builder.py` |
| Pump detection | `PumpDetector.qualifies`, `PumpDetector.build_event` | `app/events/pump_detector.py` |
| Pullback maturity | `PullbackTracker.advance` | `app/events/pullback_tracker.py` |
| Short-zone calculation | `ShortZoneBuilder.build` | `app/events/short_zone.py` |
| Core filters | `evaluate_core_filters` | `app/signals/filters.py` |
| Score and penalties | `score_setup`, `evaluate_risk_flags` | `app/signals/scoring.py`, `app/signals/risk_flags.py` |
| Signal type / grade | `SignalEngine.evaluate`, `_grade_from_score` | `app/signals/engine.py` |
| Telegram payload | `format_signal_message` | `app/signals/formatter.py` |
| Telegram delivery | `TelegramNotifier.send_signal` | `app/notifications/telegram.py` |
| Signal persistence | `BotRepository.save_signal` | `app/storage/repository.py` |
| Event-state dedup | `PullbackTracker.mark_signal_sent`, `BotRepository.upsert_event_state` | `app/events/pullback_tracker.py`, `app/storage/repository.py` |
| Outcome selection | `BotRepository.list_signals_missing_outcomes` | `app/storage/repository.py` |
| Outcome evaluation | `OutcomeEvaluator.evaluate` | `app/outcomes/evaluator.py` |
| Outcome persistence | `BotRepository.upsert_signal_outcome` | `app/storage/repository.py` |

## Important Pipeline Behaviors

### Market data origin

- real-time source: Bybit REST
- local persistence of raw market data: not found

### Dedup / cooldown behavior

- no standalone cooldown engine exists
- duplicate signal suppression is event-state-driven:
  - if `state.signal_id` is already set, `_process_symbol()` will not emit another decision
  - signaled events are expired 15 minutes later

### Signal-type vs trigger-window vs grade

- `trigger_window`: pump detection horizon (`15m`, `1h`, `4h`)
- `signal_type`: final style (`Aggressive`, `Confirm`)
- `grade`: score bucket (`A`, `B`, `C`)

These are not interchangeable, and they are decided in different layers.

### Persisted artifacts by stage

- event detection snapshot: `event_states.event_features_snapshot`
- signal snapshot and reasoning: `signals.context_json`
- later outcome metrics: `signal_outcomes`

### Missing pipeline artifacts

- no persisted Telegram payload table
- no persisted raw candle store
- no persisted score-only audit table
- no persisted per-signal cooldown/dedup table
