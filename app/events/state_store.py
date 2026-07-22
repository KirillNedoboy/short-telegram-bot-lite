"""Repository-backed state store."""

from __future__ import annotations

from app.domain import EventState
from app.storage.repository import BotRepository


class EventStateStore:
    """Small adapter around the repository for event-state access."""

    def __init__(self, repository: BotRepository) -> None:
        self._repository = repository

    def load_active(self) -> dict[str, EventState]:
        """Return active event states keyed by symbol."""

        return {state.symbol: state for state in self._repository.list_active_event_states()}

    def load(self, symbol: str) -> EventState | None:
        """Load a single symbol state."""

        return self._repository.get_event_state(symbol)

    def save(self, state: EventState) -> EventState:
        """Persist a state update."""

        return self._repository.upsert_event_state(state)

    def expire(self, symbol: str) -> EventState | None:
        """Mark a symbol state as expired."""

        return self._repository.expire_symbol(symbol)
