from datetime import datetime, timezone
from datetime import timedelta

from app.domain import SignalOutcome
from app.storage.db import Database
from app.storage.repository import BotRepository


def test_repository_round_trip(tmp_path, make_event_state, make_signal_decision) -> None:
    db_path = tmp_path / "bot.db"
    database = Database(f"sqlite:///{db_path}")
    database.create_all()
    repository = BotRepository(database)

    saved_state = repository.upsert_event_state(
        make_event_state(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    )
    active = repository.list_active_event_states()
    record = repository.save_signal(make_signal_decision(), saved_state, telegram_sent=True)
    outcome = repository.upsert_signal_outcome(
        SignalOutcome(
            signal_id=record.id,
            price_after_15m=103.0,
            price_after_1h=101.0,
            price_after_4h=99.0,
            mfe_pct=5.0,
            mae_pct=1.0,
            reached_vwap=True,
            time_to_vwap_minutes=45,
            tp1_hit=None,
            stopped_virtual=None,
            updated_at=datetime.now(timezone.utc),
        )
    )

    assert saved_state.symbol == "ONTUSDT"
    assert len(active) == 1
    assert record.id > 0
    assert repository.list_signals_missing_outcomes() == []
    assert outcome.price_after_4h == 99.0
