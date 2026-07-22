from app.config import AppConfig
from app.domain import EventStatus
from app.events.pump_detector import PumpDetector
from app.events.pullback_tracker import PullbackTracker
from app.events.short_zone import ShortZoneBuilder


def test_pullback_tracker_promotes_event_to_pullback_observed(make_event_state, make_features) -> None:
    config = AppConfig()
    tracker = PullbackTracker(config)
    zone_builder = ShortZoneBuilder(config)
    state = make_event_state()
    features = make_features(
        price=111.5,
        pullback_from_high_pct=3.04,
        dist_to_vwap_pct=9.0,
        last_low=111.0,
        last_high=114.0,
    )

    updated = tracker.advance(state, features, features.asof)
    zone = zone_builder.build(updated, features)

    assert updated.state == EventStatus.PULLBACK_OBSERVED
    assert updated.pullback_detected_at == features.asof
    assert zone is not None
    assert zone.low <= features.price <= zone.high


def test_pullback_tracker_waits_for_deeper_pullback_before_promotion(make_event_state, make_features) -> None:
    config = AppConfig()
    tracker = PullbackTracker(config)
    state = make_event_state()
    features = make_features(
        price=112.7,
        pullback_from_high_pct=2.0,
        dist_to_vwap_pct=7.0,
        last_low=112.5,
        last_high=114.0,
    )

    updated = tracker.advance(state, features, features.asof)

    assert config.pullback_min_pct == 2.4
    assert config.pullback_hold_vwap_min == 5.5
    assert config.short_zone_range_low_pct == 0.70
    assert config.short_zone_range_high_pct == 0.92
    assert config.vol_zscore_min == 0.8
    assert updated.state == EventStatus.PUMP_DETECTED
    assert updated.pullback_detected_at is None


def test_pullback_tracker_promotes_conservative_2_4_percent_pullback(make_event_state, make_features) -> None:
    config = AppConfig()
    tracker = PullbackTracker(config)
    zone_builder = ShortZoneBuilder(config)
    state = make_event_state()
    features = make_features(
        price=112.2,
        pullback_from_high_pct=2.43,
        dist_to_vwap_pct=8.0,
        last_low=112.0,
        last_high=114.0,
    )

    updated = tracker.advance(state, features, features.asof)
    zone = zone_builder.build(updated, features)

    assert updated.state == EventStatus.PULLBACK_OBSERVED
    assert updated.pullback_detected_at == features.asof
    assert zone is not None
    assert zone.low == 110.5
    assert zone.low <= features.price <= zone.high


def test_pullback_tracker_expires_stale_signal_after_90_minutes(make_event_state, make_features) -> None:
    config = AppConfig()
    tracker = PullbackTracker(config)
    state = make_event_state(expires_at=make_features().asof)
    features = make_features()

    updated = tracker.advance(state, features, features.asof)

    assert config.signal_expiry_minutes == 90
    assert updated.state == EventStatus.EXPIRED


def test_pump_detector_uses_or_trigger_for_event_windows(make_features) -> None:
    detector = PumpDetector(AppConfig())
    features = make_features(
        ret_15m=1.0,
        ret_1h=9.0,
        ret_4h=1.0,
        dist_to_vwap_pct=7.0,
        dist_to_ema20_atr=0.5,
        vol_zscore_30m=0.2,
        range_atr_ratio=0.2,
    )

    qualifies, trigger_window = detector.qualifies(features)

    assert qualifies is True
    assert trigger_window == "1h"
