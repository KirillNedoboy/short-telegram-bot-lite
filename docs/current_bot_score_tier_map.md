# Current Bot Score / Tier / Grade Map

## Executive Summary

The current implementation does not have a separate `tier` object in runtime code. It has:

- `trigger_window`: event horizon chosen by pump detection (`15m`, `1h`, `4h`)
- `signal_type`: final label (`Aggressive`, `Confirm`)
- `grade`: A/B/C score bucket

`grade` is a direct function of the final score. Real signal admission happens earlier and is stricter than grade assignment.

## Admission Logic

Signal emission requires all of the following:

1. event state is `PULLBACK_OBSERVED` or `SHORT_ZONE_ACTIVE`
2. current price is inside the short zone
3. all core filters pass
4. final score is at least 50
5. final grade is actionable (`A` or `B`)
6. current event state has not already been linked to a `signal_id`

This means:

- score affects both admission and labeling
- `grade` affects final Telegram admission; `C` is observation-only and is not emitted as an actionable signal
- a high raw score can still fail earlier if core filters, state, or zone conditions do not pass

## Feature / Filter / Modifier Map

| Type | Item | Where calculated | Effect |
| --- | --- | --- | --- |
| Event trigger | `ret_15m`, `ret_1h`, `ret_4h` | `app/features/builder.py` | Used by `PumpDetector` to pick `trigger_window`. |
| Event trigger | event stretch check | `app/events/pump_detector.py` | Requires at least one of VWAP stretch, EMA20 ATR stretch, volume z-score, or range/ATR expansion. |
| Pullback gate | `pullback_from_high_pct` | `app/features/builder.py`, `app/events/pullback_tracker.py` | Must sit inside configured pullback band for maturity. |
| Pullback gate | `dist_to_vwap_pct` | `app/features/builder.py`, `app/events/pullback_tracker.py` | Must stay above `pullback_hold_vwap_min` during pullback maturity. |
| Pullback gate | price vs event-range floor | `app/events/pullback_tracker.py` | Blocks pullback maturity if pullback is too deep into the range floor. |
| Short-zone gate | zone bounds | `app/events/short_zone.py` | Signal engine only evaluates when live price is inside zone. |
| Core filter | `dist_to_vwap` | `app/signals/filters.py` | Hard block if below `dist_to_vwap_min`. |
| Core filter | `rejection` | `app/signals/filters.py` | Hard block unless upper wick or rejection percent is strong enough. |
| Core filter | `volume` | `app/signals/filters.py` | Hard block if `vol_zscore_30m` is too low. |
| Core filter | `pullback` | `app/signals/filters.py` | Hard block if no pullback is present or it is outside the configured band. |
| Score bucket | `stretch` | `app/signals/scoring.py` | Adds points for VWAP stretch and EMA20 ATR stretch. |
| Score bucket | `exhaustion` | `app/signals/scoring.py` | Adds points for wick, rejection, weak close in range, failed retest. |
| Score bucket | `volume` | `app/signals/scoring.py` | Adds points for 30m volume anomaly and 15m range expansion vs ATR. |
| Score bucket | `event_quality` | `app/signals/scoring.py` | Adds points for strong pump returns and event range size. |
| Score bucket | `pullback_maturity` | `app/signals/scoring.py` | Rewards ideal pullback depth and extra VWAP stretch. |
| Score bucket | `zone_quality` | `app/signals/scoring.py` | Rewards price sitting near the ideal position inside the zone. |
| Score bucket | derivatives bonus | `app/signals/scoring.py` | Optional positive bonus from OI/funding when derivatives inputs are enabled. |
| Penalty | shallow pullback | `app/signals/risk_flags.py` | Minus 8 points when pullback is less than 2%. |
| Penalty | too close to event high | `app/signals/risk_flags.py` | Minus 8 points when pullback is less than 0.5%. |
| Penalty | weak rejection candle | `app/signals/risk_flags.py` | Minus 8 points if wick and rejection are both weak. |
| Penalty | thin VWAP buffer | `app/signals/risk_flags.py` | Minus 6 points if stretch is only barely above minimum. |
| Penalty | recent high breakout | `app/signals/risk_flags.py` | Minus 12 points and sets `breakout_risk=True`. |
| Penalty | continuation body too large | `app/signals/risk_flags.py` | Minus 10 points and sets `breakout_risk=True`. |
| Penalty | failed retest not confirmed | `app/signals/risk_flags.py` | Minus 4 points. |
| Penalty / veto | liquidity unavailable or weak | `app/signals/risk_flags.py` | Missing orderbook data downgrades quality; bad spread, slippage, or depth sets `breakout_risk=True`. |
| Type label | strong rejection + no breakout risk | `app/signals/engine.py` | Decides `Aggressive` vs `Confirm`. |
| Grade label | `_grade_from_score()` | `app/signals/engine.py` | Converts final score into `A/B/C`; `C` is suppressed before Telegram. |

## Threshold Table

### Event Detection Thresholds

| Setting | Default | Used in |
| --- | --- | --- |
| `event_ret_15m_min` | 6.0 | `PumpDetector.qualifies()` |
| `event_ret_1h_min` | 8.0 | `PumpDetector.qualifies()` |
| `event_ret_4h_min` | 20.0 | `PumpDetector.qualifies()` |
| `event_dist_to_vwap_min` | 6.0 | event stretch check |
| `event_dist_to_ema20_atr_min` | 2.0 | event stretch check |
| `vol_zscore_min` | 0.8 | event stretch check and core filter |
| `range_atr_bonus_level` | 1.3 | event stretch check and score |

### Pullback Maturity Thresholds

| Setting | Default | Used in |
| --- | --- | --- |
| `pullback_min_pct` | 2.4 | `PullbackTracker.advance()` |
| `pullback_max_pct` | 8.0 | `PullbackTracker.advance()` |
| `pullback_hold_vwap_min` | 5.5 | `PullbackTracker.advance()` |
| `pullback_hold_range_floor_pct` | 0.55 | `PullbackTracker.advance()` |

### Short-Zone Thresholds

| Setting | Default | Used in |
| --- | --- | --- |
| `short_zone_mode` | `event_range` | `ShortZoneBuilder.build()` |
| `short_zone_range_low_pct` | 0.70 | event-range zone |
| `short_zone_range_high_pct` | 0.92 | event-range zone |
| `short_zone_atr_low_mult` | 0.3 | ATR-from-high zone |
| `short_zone_atr_high_mult` | 1.5 | ATR-from-high zone |

### Core Filter Thresholds

| Setting | Default | Used in |
| --- | --- | --- |
| `dist_to_vwap_min` | 7.5 | `evaluate_core_filters()` |
| `upper_wick_min` | 0.18 | `evaluate_core_filters()` and risk flags |
| `rejection_min` | 0.8 | `evaluate_core_filters()` and risk flags |
| `vol_zscore_min` | 0.8 | `evaluate_core_filters()` |

### Liquidity Thresholds

| Setting | Default | Used in |
| --- | --- | --- |
| `max_spread_pct` | 0.30 | `evaluate_risk_flags()` |
| `max_slippage_pct` | 0.35 | `evaluate_risk_flags()` |
| `min_orderbook_depth_usdt_1pct` | 30000 | `evaluate_risk_flags()` |
| `min_orderbook_depth_usdt_2pct` | 60000 | `evaluate_risk_flags()` |

### Signal Type / Grade Thresholds

| Rule | Threshold | Used in |
| --- | --- | --- |
| minimum admitted score | `score >= 50` | `SignalEngine.evaluate()` |
| aggressive type score floor | `score >= 75` | `SignalEngine.evaluate()` |
| aggressive extra rejection rule | `upper_wick_ratio >= 0.18` or `rejection_from_high_pct >= 1.2` | `SignalEngine.evaluate()` |
| aggressive breakout veto | `breakout_risk == False` | `SignalEngine.evaluate()` |
| grade A | `score >= 80` | `_grade_from_score()` |
| grade B | `score >= 65` | `_grade_from_score()` |
| grade C | `50 <= score < 65` in practice | `_grade_from_score()` then suppressed before Telegram |

## Explicit A / B / C Rules

`app/signals/engine.py` implements:

```text
if score >= 80 -> grade A
elif score >= 65 -> grade B
else -> grade C
```

Because the same engine also blocks `score < 50` and suppresses `C`, emitted actionable Telegram signals can only be:

- `A`: 80-100
- `B`: 65-79

`C`: 50-64 remains an internal observation bucket and is not sent as a trading signal.

## Signal Type Rules

Signal type is not the same as grade.

Current rule:

- `Aggressive` if:
  - `score >= 75`
  - rejection is strong
  - `breakout_risk` is false
- otherwise `Confirm`

Implications:

- you can have a high score but still get `Confirm`
- `Aggressive` is a stricter subset of strong signals
- `Aggressive` is not tied to `trigger_window`

## Hidden Blockers Even with Good Score

These conditions can stop or suppress a signal even if the raw setup looks strong:

1. state is not mature enough yet
   - not `PULLBACK_OBSERVED` or `SHORT_ZONE_ACTIVE`
2. price is not inside the short zone
3. one or more core filters are false
4. risk penalties drag the final score below 50
5. the current event already has `signal_id` set
6. the event expired by time or kill-price logic
7. the event got replaced by a newer higher-high event before signaling
8. the setup is stale, breaks the event/local high after short-zone activation, or breaks the high on elevated volume
9. orderbook spread, estimated slippage, or depth violates configured liquidity limits

## Branching by Confirm / Aggressive / 15m / 1h / 4h

### `Confirm` vs `Aggressive`

- branch point exists only in `SignalEngine.evaluate()`
- branch depends on final score, rejection strength, and breakout-risk veto

### `15m` / `1h` / `4h`

- branch point exists in `PumpDetector.qualifies()`
- it chooses the first horizon whose return threshold is met, in priority order:
  - 15m first
  - then 1h
  - then 4h
- this choice affects:
  - `trigger_window`
  - event window size used to build base/high
  - `event_id`
  - persisted `trigger_window`

Important:

- there is no separate score formula by `trigger_window`
- there is no separate filter set by `trigger_window`
- there is no separate grade mapping by `trigger_window`

## Observed Data Snapshot

From the local `data/bot.sqlite` snapshot:

- signal score range: 50 to 80
- observed grades: `A=1`, `B=20`, `C=131`
- observed signal types: `Aggressive=2`, `Confirm=150`

This matches the code path where `Aggressive` is much harder to reach than basic admission.
