from app.market.bybit_client import BybitClient


class _FakeHTTP:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


def test_bybit_client_passes_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _build_http(**kwargs):
        captured.update(kwargs)
        return _FakeHTTP(**kwargs)

    monkeypatch.setattr("app.market.bybit_client.HTTP", _build_http)

    BybitClient(scheduler=object(), timeout=27)

    assert captured["timeout"] == 27
    assert captured["testnet"] is False
