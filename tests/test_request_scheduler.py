from app.infra.request_scheduler import RequestScheduler


def test_request_scheduler_defaults_to_bybit_safe_min_delay() -> None:
    scheduler = RequestScheduler(max_concurrency=4)

    assert scheduler.min_delay_ms == 350
