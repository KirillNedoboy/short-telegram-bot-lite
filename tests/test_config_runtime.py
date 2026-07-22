from pathlib import Path

import pytest

from app.config import AppConfig
from app.config import load_config


def test_corrected_config_defaults_pass_validation() -> None:
    config = AppConfig()

    assert config.min_24h_volume == 5_000_000
    assert config.shortlist_size == 100
    assert config.pullback_min_pct == 2.4
    assert config.pullback_max_pct == 8.0
    assert config.pullback_hold_range_floor_pct == 0.55
    assert config.short_zone_range_low_pct == 0.70
    assert config.short_zone_range_high_pct == 0.92
    assert config.short_zone_atr_low_mult == 0.3
    assert config.short_zone_atr_high_mult == 1.5
    assert config.dist_to_vwap_min == 7.5
    assert config.upper_wick_min == 0.15
    assert config.vol_zscore_min == 0.8
    assert config.signal_expiry_minutes == 90
    assert config.max_signal_age_minutes == 90
    assert config.max_spread_pct == 0.30
    assert config.max_slippage_pct == 0.35
    assert config.min_orderbook_depth_usdt_1pct == 30_000
    assert config.min_orderbook_depth_usdt_2pct == 60_000
    assert config.cancel_on_new_event_high is True
    assert config.cancel_on_volume_breakout is True
    assert config.request_min_delay_ms == 350


def test_relaxed_config_file_passes_validation() -> None:
    config = load_config(config_path=Path("config.yaml"), env_path=Path(".env.missing"))

    assert config.shortlist_size == 100
    assert config.vol_zscore_min == 0.8
    assert config.upper_wick_min == 0.15
    assert config.pullback_min_pct == 2.4
    assert config.short_zone_range_low_pct == 0.70
    assert config.dist_to_vwap_min == 7.5
    assert config.signal_expiry_minutes == 90


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pullback_min_pct", -0.1),
        ("short_zone_range_low_pct", -0.1),
        ("short_zone_atr_low_mult", -0.1),
        ("max_spread_pct", -0.1),
    ],
)
def test_negative_percent_like_values_fail_validation(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        AppConfig(**{field: value})


def test_reversed_atr_bounds_fail_validation() -> None:
    with pytest.raises(ValueError):
        AppConfig(short_zone_atr_low_mult=1.5, short_zone_atr_high_mult=0.3)


def test_reversed_pullback_bounds_fail_validation() -> None:
    with pytest.raises(ValueError):
        AppConfig(pullback_min_pct=9.0, pullback_max_pct=8.0)


def test_reversed_request_jitter_bounds_fail_validation() -> None:
    with pytest.raises(ValueError):
        AppConfig(request_jitter_min_ms=500, request_jitter_max_ms=100)


def test_partial_telegram_credentials_fail_validation_without_secret_values() -> None:
    token = "123456:secret-token"
    with pytest.raises(ValueError) as exc_info:
        AppConfig(telegram_token=None, signal_chat_id="-100123")

    message = str(exc_info.value)
    assert "telegram_token" in message
    assert "signal_chat_id" in message
    assert token not in message


def test_env_db_url_is_source_of_truth(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text("db_url: sqlite:///./from-config.sqlite\n", encoding="utf-8")
    env_path.write_text("DB_URL=sqlite:////opt/krntrade/data/bot.sqlite\n", encoding="utf-8")

    config = load_config(config_path=config_path, env_path=env_path)

    assert config.db_url == "sqlite:////opt/krntrade/data/bot.sqlite"


def test_request_timeout_env_override_is_loaded(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text("request_timeout_sec: 20\n", encoding="utf-8")
    env_path.write_text("REQUEST_TIMEOUT_SEC=35\n", encoding="utf-8")

    config = load_config(config_path=config_path, env_path=env_path)

    assert config.request_timeout_sec == 35
