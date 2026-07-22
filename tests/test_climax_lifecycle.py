from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.signals.climax import advance_volume_climax_lifecycle


UTC = timezone.utc


def test_new_high_creates_revision_without_extending_root_lifetime():
    root_created = datetime(2026, 7, 22, 8, 4, tzinfo=UTC)
    latest_high_at = root_created
    now = root_created + timedelta(minutes=4)

    result = advance_volume_climax_lifecycle(
        root_created_at=root_created,
        latest_high=0.127,
        latest_high_at=latest_high_at,
        confirmation_started_at=root_created,
        last_observed_at=root_created + timedelta(minutes=1),
        event_revision=1,
        current_high=0.13747,
        observed_at=now,
        closed_candles_after_high=0,
        max_lifetime_minutes=15,
        confirmation_window_minutes=3,
    )

    assert result.state == "CLIMAX_WATCHING"
    assert result.event_revision == 2
    assert result.latest_high == 0.13747
    assert result.root_created_at == root_created
    assert result.confirmation_started_at == now
    assert result.expired is False


def test_fallback_requires_two_closed_candles_and_no_new_high():
    root_created = datetime(2026, 7, 22, 8, 4, tzinfo=UTC)
    latest_high_at = root_created + timedelta(minutes=2)
    now = latest_high_at + timedelta(minutes=3)

    result = advance_volume_climax_lifecycle(
        root_created_at=root_created,
        latest_high=0.13747,
        latest_high_at=latest_high_at,
        confirmation_started_at=latest_high_at,
        last_observed_at=latest_high_at,
        event_revision=2,
        current_high=0.13650,
        observed_at=now,
        closed_candles_after_high=2,
        max_lifetime_minutes=15,
        confirmation_window_minutes=3,
        price_acceleration_resumed=False,
        active_short_squeeze=False,
        oi_continuation=False,
        rejection_ok=True,
        liquidity_ok=True,
        entry_distance_ok=True,
    )

    assert result.state == "FALLBACK_READY"
    assert result.expired is False


def test_fallback_is_blocked_while_price_acceleration_resumes():
    root_created = datetime(2026, 7, 22, 8, 4, tzinfo=UTC)
    latest_high_at = root_created + timedelta(minutes=2)
    now = latest_high_at + timedelta(minutes=3)

    result = advance_volume_climax_lifecycle(
        root_created_at=root_created,
        latest_high=0.13747,
        latest_high_at=latest_high_at,
        confirmation_started_at=latest_high_at,
        last_observed_at=latest_high_at,
        event_revision=2,
        current_high=0.13810,
        observed_at=now,
        closed_candles_after_high=2,
        max_lifetime_minutes=15,
        confirmation_window_minutes=3,
        price_acceleration_resumed=True,
        active_short_squeeze=False,
        oi_continuation=False,
        rejection_ok=True,
        liquidity_ok=True,
        entry_distance_ok=True,
    )

    assert result.state == "CLIMAX_WATCHING"
    assert result.event_revision == 3
    assert result.latest_high == 0.13810
    assert result.veto_reasons == []


def test_root_lifetime_expires_even_when_confirmation_window_was_reset():
    root_created = datetime(2026, 7, 22, 8, 4, tzinfo=UTC)
    latest_high_at = root_created + timedelta(minutes=14)
    now = root_created + timedelta(minutes=16)

    result = advance_volume_climax_lifecycle(
        root_created_at=root_created,
        latest_high=0.14000,
        latest_high_at=latest_high_at,
        confirmation_started_at=latest_high_at,
        last_observed_at=latest_high_at,
        event_revision=4,
        current_high=0.14000,
        observed_at=now,
        closed_candles_after_high=0,
        max_lifetime_minutes=15,
        confirmation_window_minutes=3,
    )

    assert result.state == "EXPIRED"
    assert result.expired is True
    assert "root_lifetime_expired" in result.veto_reasons