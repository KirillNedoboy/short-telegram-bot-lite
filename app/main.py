"""Application runtime for the short signal bot."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.config import AppConfig, load_config
from app.domain import EventState, EventStatus, SignalDecision, SignalType, SymbolFeatures
from app.events.pump_detector import PumpDetector
from app.events.pullback_tracker import PullbackTracker
from app.events.short_zone import ShortZoneBuilder
from app.events.state_store import EventStateStore
from app.features.builder import FeatureBuilder
from app.infra.health import ServiceHealth
from app.infra.request_scheduler import RequestScheduler
from app.logger import configure_logging
from app.market.bybit_client import BybitClient
from app.market.scanner import MarketScanner
from app.notifications.telegram import TelegramNotifier
from app.notifications.throttling import ErrorThrottler
from app.outcomes.tracker import OutcomeTracker
from app.signals.climax import ClimaxEvaluation, advance_volume_climax_lifecycle, evaluate_climax, evaluate_climax_shadow, volume_climax_attempt_id
from app.signals.engine import SignalEngine
from app.signals.formatter import format_signal_message
from app.storage.db import Database
from app.storage.repository import BotRepository


def _public_config_fingerprint(config: AppConfig) -> str:
    """Hash only non-secret, behavior-relevant config values."""
    values = config.model_dump(mode="json")
    excluded = ("token", "secret", "password", "api_key", "apikey", "private_key", "credential", "webhook")
    public = {
        key: value
        for key, value in values.items()
        if not any(part in key.lower() for part in excluded) and key not in {"db_url", "signal_chat_id", "alerts_chat_id"}
    }
    encoded = json.dumps(public, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _decision_delta(live: ClimaxEvaluation, shadow: ClimaxEvaluation | None) -> str | None:
    if shadow is None:
        return None
    if live.actionable and shadow.actionable:
        return "GRADE_CHANGED" if live.grade != shadow.grade else "UNCHANGED_ACTIONABLE"
    if not live.actionable and not shadow.actionable:
        return "UNCHANGED_REJECTED"
    if not live.actionable and shadow.actionable:
        return "LIVE_REJECTED_SHADOW_ACTIONABLE"
    return "LIVE_ACTIONABLE_SHADOW_REJECTED"


@dataclass(slots=True)
class _ClimaxCandidate:
    symbol: str
    event_id: str
    event_high_time: datetime | None
    candidate_added_at: datetime
    pool_added_at: datetime


class ShortSignalBot:
    """Single-process runtime that scans, scores, and sends signals."""

    def __init__(
        self,
        config: AppConfig,
        repository: BotRepository | None = None,
        scanner: MarketScanner | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._config = config
        self._runtime_instance_id = uuid.uuid4().hex
        self._config_fingerprint = _public_config_fingerprint(config)
        self._logger = logging.getLogger(self.__class__.__name__)
        self._watch_sent_in_cycle = 0
        self._active_climax_pool: dict[tuple[str, str], _ClimaxCandidate] = {}
        self._fast_monitor_cursor = 0
        self._fast_monitor_poll_sequence = 0
        self._fast_monitor_task: asyncio.Task[None] | None = None
        self._fast_monitor_running = False

        if repository is None:
            database = Database(config.db_url)
            database.create_all()
            repository = BotRepository(database)
        self._repository = repository
        set_runtime_metadata = getattr(self._repository, "set_runtime_metadata", None)
        if set_runtime_metadata is not None:
            set_runtime_metadata(
                runtime_instance_id=self._runtime_instance_id,
                config_fingerprint=self._config_fingerprint,
                model_version="climax-v1",
            )

        if scanner is None:
            scheduler = RequestScheduler(
                max_concurrency=config.max_request_concurrency,
                min_delay_ms=config.request_min_delay_ms,
                jitter_min_ms=config.request_jitter_min_ms,
                jitter_max_ms=config.request_jitter_max_ms,
            )
            client = BybitClient(
                scheduler=scheduler,
                timeout=config.request_timeout_sec,
            )
            scanner = MarketScanner(client=client, config=config)
        self._scanner = scanner
        self._client = scanner.client

        self._notifier = notifier or TelegramNotifier(
            token=config.telegram_token,
            signal_chat_id=config.signal_chat_id,
            alerts_chat_id=config.alerts_chat_id,
        )
        self._state_store = EventStateStore(repository)
        self._feature_builder = FeatureBuilder()
        self._pump_detector = PumpDetector(config)
        self._pullback_tracker = PullbackTracker(config)
        self._zone_builder = ShortZoneBuilder(config)
        self._signal_engine = SignalEngine(config)
        self._outcome_tracker = OutcomeTracker(self._client, repository)
        self._error_throttler = ErrorThrottler(config.error_alert_ttl_sec)
        self._health = ServiceHealth()
        self._storage_health_last_ok: str | None = None
        if not config.derivatives_enabled:
            self._logger.warning("Derivatives confirmation unavailable; derivatives_enabled=false.")

    @classmethod
    def from_files(
        cls,
        config_path: str | Path = "config.yaml",
        env_path: str | Path = ".env",
    ) -> "ShortSignalBot":
        configure_logging()
        config = load_config(config_path=config_path, env_path=env_path)
        return cls(config=config)

    async def __aenter__(self) -> "ShortSignalBot":
        await self.startup()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.shutdown()

    async def startup(self) -> None:
        await self._notifier.start()
        await self._ensure_storage_healthy("startup")
        if self._config.climax_short_enabled and self._config.climax_fast_monitor_enabled:
            self._fast_monitor_running = True
            self._fast_monitor_task = asyncio.create_task(self._run_fast_monitor(), name="climax-fast-monitor")
            self._logger.info("Climax strategies registered | fast_monitor=enabled poll=%ss max_symbols=%s", self._config.climax_fast_poll_sec, self._config.climax_max_active_symbols)

    async def shutdown(self) -> None:
        self._fast_monitor_running = False
        if self._fast_monitor_task:
            self._fast_monitor_task.cancel()
            await asyncio.gather(self._fast_monitor_task, return_exceptions=True)
            self._fast_monitor_task = None
        await self._notifier.close()

    async def run_cycle(self) -> list[SignalDecision]:
        self._health.on_cycle_start()
        cycle_started_at = datetime.now(timezone.utc)
        self._watch_sent_in_cycle = 0
        decisions: list[SignalDecision] = []
        symbol_results: list[dict[str, object]] = []
        try:
            if not await self._ensure_storage_healthy("cycle"):
                return []
            active_states = self._state_store.load_active()
            snapshots = await self._scanner.fetch_market_snapshots()
            shortlist = self._scanner.shortlist(snapshots)
            symbols = [snapshot.symbol for snapshot in shortlist]
            seen_symbols = set(symbols)
            for active_symbol in active_states:
                if active_symbol in seen_symbols:
                    continue
                symbols.append(active_symbol)
                seen_symbols.add(active_symbol)
            frames = await self._scanner.fetch_symbol_frames(symbols)

            for symbol in symbols:
                frame = frames.get(symbol)
                if frame is None or frame.empty:
                    symbol_results.append({"symbol": symbol, "terminal_status": "SCAN_FAILED", "reason_code": "MARKET_DATA_INCOMPLETE"})
                    continue
                state = active_states.get(symbol)
                try:
                    decision, updated_state = await self._process_symbol(symbol, frame, state)
                except Exception as exc:
                    symbol_results.append({"symbol": symbol, "terminal_status": "SCAN_FAILED", "reason_code": "SCAN_EXCEPTION", "details": {"error_code": type(exc).__name__}})
                    await self._handle_error(f"symbol:{symbol}", exc)
                    continue
                symbol_results.append({"symbol": symbol, "terminal_status": "SCANNED_OK", "reason_code": "SCANNED_OK"})
                if updated_state is not None:
                    self._state_store.save(updated_state)
                if decision is not None:
                    decisions.append(decision)
                    if decision.actionable:
                        self._health.on_signal()

            updated_outcomes = await self.update_outcomes()
            universe = getattr(self._scanner, "last_universe_telemetry", None)
            record_coverage = getattr(self._repository, "record_market_scan_cycle", None)
            if universe is not None and record_coverage is not None:
                record_coverage(
                    cycle_started_at=cycle_started_at,
                    cycle_completed_at=datetime.now(timezone.utc),
                    exchange_symbols=list(universe.exchange_symbols),
                    eligible_symbols=list(universe.eligible_symbols),
                    excluded=list(universe.excluded),
                    scheduled_symbols=list(symbols),
                    symbol_results=symbol_results,
                    candidate_symbols=len(decisions),
                    evaluated_symbols=len(symbol_results),
                )
            self._logger.info(
                "Cycle complete | shortlist=%s symbols=%s signals=%s outcomes=%s",
                len(shortlist),
                len(symbols),
                len([d for d in decisions if d.actionable]),
                updated_outcomes,
            )
            return decisions
        except Exception as exc:
            await self._handle_error("cycle", exc)
            return []
        finally:
            self._health.on_cycle_finish()

    async def run_forever(self) -> None:
        while True:
            await self.run_cycle()
            await asyncio.sleep(self._config.scan_interval_sec)

    async def _run_fast_monitor(self) -> None:
        """Bounded, fair active-candidate monitor; never scans the full universe."""
        while self._fast_monitor_running:
            try:
                await asyncio.sleep(self._config.climax_fast_poll_sec)
                poll_started = datetime.now(timezone.utc)
                self._fast_monitor_poll_sequence += 1
                poll_sequence = self._fast_monitor_poll_sequence
                keys = list(self._active_climax_pool)
                self._repository.update_fast_monitor_heartbeat(
                    checked_at=poll_started,
                    pool_size=len(keys),
                    poll_sequence=poll_sequence,
                    last_poll_at=poll_started,
                )
                if not keys:
                    completed = datetime.now(timezone.utc)
                    self._repository.update_fast_monitor_heartbeat(
                        checked_at=completed,
                        pool_size=0,
                        poll_sequence=poll_sequence,
                        last_complete_at=completed,
                    )
                    self._repository.record_climax_monitor_event(
                        created_at=completed,
                        symbol="__FAST_MONITOR__",
                        event_id=f"poll:{poll_sequence}",
                        event_high_time=None,
                        action="poll_complete",
                        reason=None,
                        pool_size=0,
                        poll_sequence=poll_sequence,
                        worker_id="climax-fast-monitor",
                        details={"selected_count": 0, "terminal": True},
                    )
                    self._logger.info(
                        "Climax fast-monitor poll_complete | seq=%s pool=0",
                        poll_sequence,
                    )
                    continue
                selected = self._select_fast_monitor_keys(keys)
                self._logger.info(
                    "Climax fast-monitor poll_start | seq=%s pool=%s selected=%s",
                    poll_sequence, len(keys), len(selected),
                )
                for symbol, event_id in selected:
                    candidate = self._active_climax_pool.get((symbol, event_id))
                    if candidate is None:
                        continue
                    self._repository.record_climax_monitor_event(
                        created_at=poll_started,
                        symbol=symbol,
                        event_id=event_id,
                        event_high_time=candidate.event_high_time,
                        action="poll_start",
                        reason=None,
                        pool_size=len(self._active_climax_pool),
                        poll_sequence=poll_sequence,
                        worker_id="climax-fast-monitor",
                    )
                frames = await self._scanner.fetch_symbol_frames([symbol for symbol, _ in selected])
                for symbol, event_id in selected:
                    frame = frames.get(symbol)
                    candidate = self._active_climax_pool.get((symbol, event_id))
                    if candidate is None:
                        continue
                    if not self._fast_monitor_running or frame is None or frame.empty:
                        self._repository.record_climax_monitor_event(
                            created_at=datetime.now(timezone.utc), symbol=symbol, event_id=event_id,
                            event_high_time=candidate.event_high_time, action="poll_skip",
                            reason="empty_frame_or_shutdown", pool_size=len(self._active_climax_pool),
                            poll_sequence=poll_sequence, worker_id="climax-fast-monitor",
                        )
                        continue
                    state = self._state_store.load(symbol)
                    if state is None or state.event_id != event_id:
                        self._active_climax_pool.pop((symbol, event_id), None)
                        self._repository.record_climax_monitor_event(
                            created_at=datetime.now(timezone.utc), symbol=symbol, event_id=event_id,
                            event_high_time=candidate.event_high_time, action="candidate_removed",
                            reason="event_state_missing_or_replaced", pool_size=len(self._active_climax_pool),
                            poll_sequence=poll_sequence, worker_id="climax-fast-monitor",
                        )
                        continue
                    await self._evaluate_and_send_climax(symbol, frame, state, fast_monitor=True, poll_sequence=poll_sequence)
                now = datetime.now(timezone.utc)
                for key, candidate in list(self._active_climax_pool.items()):
                    if now - candidate.candidate_added_at >= timedelta(minutes=self._config.climax_candidate_ttl_minutes):
                        self._active_climax_pool.pop(key, None)
                        self._repository.record_climax_monitor_event(
                            created_at=now, symbol=candidate.symbol, event_id=candidate.event_id,
                            event_high_time=candidate.event_high_time, action="candidate_expired",
                            reason="ttl", pool_size=len(self._active_climax_pool),
                            poll_sequence=poll_sequence, worker_id="climax-fast-monitor",
                        )
                completed = datetime.now(timezone.utc)
                self._repository.update_fast_monitor_heartbeat(
                    checked_at=completed,
                    pool_size=len(self._active_climax_pool),
                    poll_sequence=poll_sequence,
                    last_complete_at=completed,
                )
                self._repository.record_climax_monitor_event(
                    created_at=completed,
                    symbol="__FAST_MONITOR__",
                    event_id=f"poll:{poll_sequence}",
                    event_high_time=None,
                    action="poll_complete",
                    reason=None,
                    pool_size=len(self._active_climax_pool),
                    poll_sequence=poll_sequence,
                    worker_id="climax-fast-monitor",
                    details={"selected_count": len(selected), "terminal": True},
                )
                self._logger.info(
                    "Climax fast-monitor poll_complete | seq=%s pool=%s",
                    poll_sequence, len(self._active_climax_pool),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = datetime.now(timezone.utc)
                self._repository.update_fast_monitor_heartbeat(
                    checked_at=now,
                    pool_size=len(self._active_climax_pool),
                    poll_sequence=self._fast_monitor_poll_sequence,
                    last_error=str(exc),
                )
                self._repository.record_climax_monitor_event(
                    created_at=now,
                    symbol="__FAST_MONITOR__",
                    event_id=f"poll:{self._fast_monitor_poll_sequence}",
                    event_high_time=None,
                    action="poll_skip",
                    reason="poll_exception",
                    pool_size=len(self._active_climax_pool),
                    poll_sequence=self._fast_monitor_poll_sequence,
                    worker_id="climax-fast-monitor",
                    details={"error_code": type(exc).__name__, "terminal": True},
                )
                self._logger.exception("Climax fast-monitor error: %s", exc)

    def _select_fast_monitor_keys(self, keys: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Return the next bounded slice and advance the fairness cursor."""
        start = self._fast_monitor_cursor % len(keys)
        ordered = keys[start:] + keys[:start]
        selected = ordered[: self._config.climax_max_active_symbols]
        self._fast_monitor_cursor = (start + len(selected)) % len(keys)
        return selected

    def _prepare_shadow_root_event(self, state: EventState, observed_at: datetime) -> int:
        """Freeze initial pump evidence and persist the shadow root identity."""
        if not getattr(self._config, "climax_root_event_tracking_enabled", False):
            return int((state.event_features_snapshot or {}).get("root_event_revision") or 1)
        snapshot = state.event_features_snapshot
        frozen = snapshot.get("initial_extension_pct")
        source = snapshot.get("initial_extension_source")
        if frozen is None:
            base = state.event_base_price
            high = state.event_high
            if base and high and base > 0 and high >= base:
                frozen = (high - base) / base * 100.0
                source = "event_base_to_peak"
            elif snapshot.get("initial_ret_5m") is not None:
                frozen = snapshot.get("initial_ret_5m")
                source = "event_snapshot_ret_5m"
            if frozen is not None:
                snapshot["initial_extension_pct"] = float(frozen)
                snapshot["initial_extension_source"] = source or "event_snapshot"
        revision, persisted_frozen = self._repository.upsert_shadow_root_event(
            root_event_id=state.event_id,
            symbol=state.symbol,
            event_started_at=state.event_start_time,
            event_base_price=state.event_base_price,
            peak_high=state.event_high,
            peak_high_time=state.event_high_time,
            initial_extension_pct=float(frozen) if frozen is not None else None,
            initial_extension_source=source,
            observed_at=observed_at,
        )
        snapshot["root_event_id"] = state.event_id
        snapshot["root_event_revision"] = revision
        if snapshot.get("initial_extension_pct") is None and persisted_frozen is not None:
            snapshot["initial_extension_pct"] = float(persisted_frozen)
        return revision

    def _track_climax_candidate(self, state: EventState, now: datetime) -> None:
        """Track one event identity once; do not refresh TTL on every full scan."""
        if not state.event_id:
            return
        event_revision = self._prepare_shadow_root_event(state, now)
        key = (state.symbol, state.event_id)
        for old_key in list(self._active_climax_pool):
            if old_key[0] == state.symbol and old_key != key:
                old = self._active_climax_pool.pop(old_key)
                if self._config.climax_root_event_tracking_enabled:
                    self._repository.close_open_shadow_attempts_for_root(
                        root_event_id=old.event_id,
                        new_state="ROOT_REPLACED",
                        reason="root_replaced_by_new_event",
                        observed_at=now,
                        runtime_instance_id=self._runtime_instance_id,
                        model_version="climax-v1",
                    )
                self._repository.record_climax_monitor_event(
                    created_at=now, symbol=old.symbol, event_id=old.event_id,
                    event_high_time=old.event_high_time, action="candidate_removed",
                    reason="replaced_by_new_event", pool_size=len(self._active_climax_pool),
                    poll_sequence=self._fast_monitor_poll_sequence, worker_id="full-scan",
                )
        if key in self._active_climax_pool:
            return
        added_at = state.event_high_time or now
        if now - added_at >= timedelta(minutes=self._config.climax_candidate_ttl_minutes):
            self._repository.record_climax_monitor_event(
                created_at=now, symbol=state.symbol, event_id=state.event_id,
                event_high_time=state.event_high_time, action="candidate_rejected",
                reason="ttl_expired_before_pool_add", pool_size=len(self._active_climax_pool),
                poll_sequence=self._fast_monitor_poll_sequence, worker_id="full-scan",
            )
            return
        self._active_climax_pool[key] = _ClimaxCandidate(
            symbol=state.symbol,
            event_id=state.event_id,
            event_high_time=state.event_high_time,
            candidate_added_at=added_at,
            pool_added_at=now,
        )

        self._repository.record_climax_monitor_event(
            created_at=now, symbol=state.symbol, event_id=state.event_id,
            event_high_time=state.event_high_time, action="pool_add",
            reason="new_event_id", pool_size=len(self._active_climax_pool),
            poll_sequence=self._fast_monitor_poll_sequence, worker_id="full-scan",
            root_event_id=state.event_id, event_revision=event_revision,
            observed_at=now, pool_added_at=now,
        )

    def _remove_climax_candidate(self, symbol: str, event_id: str, *, reason: str) -> None:
        candidate = self._active_climax_pool.pop((symbol, event_id), None)
        if candidate is not None:
            self._repository.record_climax_monitor_event(
                created_at=datetime.now(timezone.utc), symbol=symbol, event_id=event_id,
                event_high_time=candidate.event_high_time, action="candidate_removed",
                reason=reason, pool_size=len(self._active_climax_pool),
                poll_sequence=self._fast_monitor_poll_sequence, worker_id="runtime",
            )

    async def update_outcomes(self, now: datetime | None = None) -> int:
        return await self._outcome_tracker.update_due_outcomes(now=now)

    async def _evaluate_and_send_climax(
        self,
        symbol: str,
        frame_1m: pd.DataFrame,
        state: EventState,
        *,
        features: SymbolFeatures | None = None,
        fast_monitor: bool = False,
        poll_sequence: int | None = None,
    ) -> SignalDecision | None:
        """Evaluate, dedupe, persist, and deliver one climax signal."""
        if not self._config.climax_short_enabled or state.signal_id is not None:
            return None
        if features is None:
            derivatives = await self._scanner.fetch_optional_derivatives(symbol)
            liquidity = await self._fetch_optional_liquidity(symbol, float(frame_1m["close"].iloc[-1]))
            features = self._feature_builder.build(symbol, frame_1m, state=state, derivatives=derivatives, liquidity=liquidity)
        evaluation: ClimaxEvaluation = evaluate_climax(state, features, frame_1m, self._config)
        shadow_evaluation: ClimaxEvaluation | None = None
        if self._config.low_volume_frozen_initial_extension_enabled and self._config.low_volume_frozen_initial_extension_shadow_only:
            shadow_evaluation = evaluate_climax_shadow(state, features, frame_1m, self._config)
        evaluated_at = datetime.now(timezone.utc)
        candidate = self._active_climax_pool.get((symbol, state.event_id))
        candidate_added_at = candidate.candidate_added_at if candidate else None
        candidate_age_sec = ((evaluated_at - candidate_added_at).total_seconds() if candidate_added_at else None)
        root_event_id = str((state.event_features_snapshot or {}).get("root_event_id") or state.event_id)
        event_revision = int((state.event_features_snapshot or {}).get("root_event_revision") or 1)
        lifecycle_shadow = None
        lifecycle_attempt_id: str | None = None
        shadow_attempt_id: str | None = None
        lifecycle_metadata = evaluation.metadata.get("volume_climax_metadata") or evaluation.metadata
        is_volume_climax_candidate = bool(evaluation.metadata.get("volume_climax_candidate")) or (
            evaluation.subtype == "VOLUME_CLIMAX_UNWIND"
            or evaluation.metadata.get("strategy_subtype") == "VOLUME_CLIMAX_UNWIND"
        )
        if self._config.volume_climax_lifecycle_shadow_enabled and is_volume_climax_candidate:
            snapshot = dict(state.event_features_snapshot or {})
            current_high = float(lifecycle_metadata.get("event_high") or features.price)
            prior_high = float(snapshot.get("volume_climax_latest_high") or current_high)
            root_created_at = state.event_start_time or state.event_high_time or features.asof
            latest_high_at = _parse_optional_datetime(snapshot.get("volume_climax_latest_high_at")) or state.event_high_time or root_created_at
            confirmation_started_at = _parse_optional_datetime(snapshot.get("volume_climax_confirmation_started_at")) or latest_high_at
            last_observed_at = _parse_optional_datetime(snapshot.get("volume_climax_last_observed_at")) or root_created_at
            prior_revision = int(snapshot.get("volume_climax_event_revision") or 1)
            lifecycle_shadow = advance_volume_climax_lifecycle(
                root_created_at=root_created_at,
                latest_high=prior_high,
                latest_high_at=latest_high_at,
                confirmation_started_at=confirmation_started_at,
                last_observed_at=last_observed_at,
                event_revision=prior_revision,
                current_high=current_high,
                observed_at=features.asof,
                closed_candles_after_high=int(lifecycle_metadata.get("closed_candles_after_high") or 0),
                min_closed_candles_after_high=self._config.volume_climax_min_closed_candles_after_high,
                max_lifetime_minutes=self._config.volume_climax_max_lifetime_minutes,
                confirmation_window_minutes=self._config.volume_climax_confirmation_window_minutes,
                price_acceleration_resumed=features.ret_5m > 0,
                active_short_squeeze=(features.oi_change_pct is not None and features.oi_change_pct < 0 and features.ret_5m > 0),
                oi_continuation=features.oi_change_pct is None or features.oi_change_pct >= 0,
                rejection_ok=float(lifecycle_metadata.get("rejection_pct") or 0.0) >= self._config.volume_climax_min_rejection_pct,
                liquidity_ok=features.liquidity_available and not bool(lifecycle_metadata.get("liquidity_warning")),
                entry_distance_ok=float(lifecycle_metadata.get("entry_distance_below_high_pct") or 999.0) <= self._config.volume_climax_max_entry_distance_below_high_pct,
            )
            db_revision, _ = self._repository.upsert_shadow_root_event(
                root_event_id=root_event_id,
                symbol=symbol,
                event_started_at=root_created_at,
                event_base_price=state.event_base_price,
                peak_high=current_high,
                peak_high_time=features.asof,
                initial_extension_pct=features.ret_15m,
                initial_extension_source="volume_climax",
                observed_at=features.asof,
            )
            lifecycle_shadow.event_revision = db_revision
            event_revision = db_revision
            lifecycle_attempt_id = volume_climax_attempt_id(root_event_id, db_revision)
            shadow_attempt_id = lifecycle_attempt_id
            snapshot.update({
                "volume_climax_latest_high": lifecycle_shadow.latest_high,
                "volume_climax_latest_high_at": lifecycle_shadow.latest_high_at.isoformat(),
                "volume_climax_confirmation_started_at": lifecycle_shadow.confirmation_started_at.isoformat(),
                "volume_climax_last_observed_at": lifecycle_shadow.last_observed_at.isoformat(),
                "volume_climax_event_revision": lifecycle_shadow.event_revision,
                "volume_climax_lifecycle_state": lifecycle_shadow.state,
                "volume_climax_lifecycle_vetoes": lifecycle_shadow.veto_reasons,
            })
            state.event_features_snapshot = snapshot
            self._state_store.save(state)
            attempt_state = "EXPIRED" if lifecycle_shadow.expired else "SHADOW_ACTIONABLE" if lifecycle_shadow.state == "FALLBACK_READY" else "RETEST_IN_PROGRESS"
            self._repository.upsert_shadow_entry_attempt(
                attempt_id=lifecycle_attempt_id,
                root_event_id=root_event_id,
                observed_at=features.asof,
                local_retest_high=lifecycle_shadow.latest_high,
                breakdown_level=float(lifecycle_metadata.get("breakout_reference") or lifecycle_shadow.latest_high * 0.995),
                attempt_state=attempt_state,
                attempt_trigger="volume_climax_lifecycle",
                confirmation_expires_at=lifecycle_shadow.confirmation_started_at + timedelta(minutes=self._config.volume_climax_confirmation_window_minutes),
                close_reason=lifecycle_shadow.veto_reasons[0] if lifecycle_shadow.expired and lifecycle_shadow.veto_reasons else None,
                event_revision=lifecycle_shadow.event_revision,
                runtime_instance_id=self._runtime_instance_id,
                model_version="climax-lifecycle-v1-shadow",
            )
            if attempt_state == "SHADOW_ACTIONABLE":
                self._repository.transition_shadow_entry_attempt(
                    attempt_id=lifecycle_attempt_id,
                    root_event_id=root_event_id,
                    event_revision=lifecycle_shadow.event_revision,
                    evaluation_id=None,
                    new_state="SHADOW_ACTIONABLE",
                    reason="fallback_conditions_met",
                    observed_at=features.asof,
                    market_asof=features.asof,
                    runtime_instance_id=self._runtime_instance_id,
                    model_version="climax-lifecycle-v1-shadow",
                    details={"veto_reasons": lifecycle_shadow.veto_reasons},
                )
        if self._config.climax_root_event_tracking_enabled and lifecycle_shadow is None:
            if shadow_evaluation is not None and shadow_evaluation.metadata.get("post_high_retest_high") is not None:
                shadow_data = shadow_evaluation.metadata
                shadow_attempt_id = f"{root_event_id}:r{event_revision}:a1"
                if shadow_evaluation.actionable:
                    attempt_state = "BREAKDOWN_PENDING"
                elif shadow_data.get("failed_retest_confirmed"):
                    attempt_state = "BREAKDOWN_PENDING"
                else:
                    attempt_state = "RETEST_IN_PROGRESS"
                self._repository.upsert_shadow_entry_attempt(
                    attempt_id=shadow_attempt_id,
                    root_event_id=root_event_id,
                    event_revision=event_revision,
                    observed_at=evaluated_at,
                    local_retest_high=shadow_data.get("post_high_retest_high"),
                    breakdown_level=shadow_data.get("breakout_reference"),
                    attempt_state=attempt_state,
                    confirmation_expires_at=evaluated_at + timedelta(minutes=self._config.climax_entry_attempt_ttl_minutes),
                    runtime_instance_id=self._runtime_instance_id,
                    model_version=str(shadow_data.get("model_version", "climax-v1")),
                )
            else:
                shadow_attempt_id = self._repository.get_open_shadow_attempt_id(root_event_id=root_event_id)
                if shadow_attempt_id:
                    self._repository.record_attempt_reused_after_restart(
                        attempt_id=shadow_attempt_id,
                        root_event_id=root_event_id,
                        event_revision=event_revision,
                        observed_at=evaluated_at,
                        runtime_instance_id=self._runtime_instance_id,
                        model_version="climax-v1",
                    )
        passed_conditions = [key for key, value in evaluation.metadata.items() if isinstance(value, bool) and value]
        lifecycle_shadow_decision = lifecycle_shadow.state if lifecycle_shadow is not None else None
        lifecycle_shadow_vetoes = lifecycle_shadow.veto_reasons if lifecycle_shadow is not None else None
        lifecycle_decision_delta = (
            "LIVE_REJECTED_SHADOW_FALLBACK_READY"
            if lifecycle_shadow is not None and not evaluation.actionable and lifecycle_shadow.state == "FALLBACK_READY"
            else None
        )
        evaluation_features = asdict(features)
        evaluation_features["climax_evaluation"] = {
            "selected_subtype": evaluation.metadata.get("strategy_subtype"),
            "selected_score": evaluation.score,
            "selected_grade": evaluation.grade,
            "selected_veto_reasons": list(evaluation.veto_reasons),
            "volume_climax_observed": bool(evaluation.metadata.get("volume_climax_observed")),
            "volume_climax_candidate": bool(evaluation.metadata.get("volume_climax_candidate")),
            "volume_climax_metadata": evaluation.metadata.get("volume_climax_metadata"),
        }
        evaluation_id = self._repository.record_climax_evaluation(
            evaluation_time=evaluated_at,
            symbol=symbol,
            strategy="CLIMAX_EXHAUSTION",
            subtype_candidate=evaluation.metadata.get("strategy_subtype"),
            model_version=str(evaluation.metadata.get("model_version", "climax-v1")),
            event_id=state.event_id,
            event_high=evaluation.metadata.get("event_high"),
            event_high_time=state.event_high_time,
            event_detected_at=state.event_start_time,
            candidate_added_at=candidate_added_at,
            candidate_age_sec=candidate_age_sec,
            fast_monitor=fast_monitor,
            poll_sequence=poll_sequence,
            frame_asof=features.asof,
            candles_asof=features.asof,
            oi_asof=features.asof if features.oi_change_pct is not None else None,
            orderbook_asof=features.asof if features.liquidity_available else None,
            score=evaluation.score,
            grade=evaluation.grade,
            actionable=evaluation.actionable,
            admission_passed=not evaluation.veto_reasons,
            veto_reasons=evaluation.veto_reasons,
            passed_conditions=passed_conditions,
            data_quality=evaluation.data_quality,
            liquidity={
                "available": features.liquidity_available,
                "spread_pct": features.spread_pct,
                "slippage_pct": features.slippage_pct,
                "depth_1pct_usdt": features.orderbook_depth_usdt_1pct,
                "depth_2pct_usdt": features.orderbook_depth_usdt_2pct,
            },
            oi={
                "status": features.derivatives_status,
                "change_pct": features.oi_change_pct,
                "reasons": features.derivatives_reasons,
            },
            features=evaluation_features,
            lifecycle_state="ACTIONABLE" if evaluation.actionable else "REJECTED",
            telegram_eligible=evaluation.actionable,
            runtime_instance_id=self._runtime_instance_id,
            root_event_id=root_event_id,
            event_revision=event_revision,
            attempt_id=shadow_attempt_id,
            observed_at=evaluated_at,
            market_asof=features.asof,
            pool_added_at=candidate.pool_added_at if candidate else None,
            event_age_sec=((evaluated_at - state.event_high_time).total_seconds() if state.event_high_time else None),
            pool_age_sec=((evaluated_at - candidate.pool_added_at).total_seconds() if candidate else None),
            evaluation_completed_at=evaluated_at,
            live_decision="ACTIONABLE" if evaluation.actionable else "REJECTED",
            live_veto_reasons=evaluation.veto_reasons,
            shadow_decision=lifecycle_shadow_decision or (("ACTIONABLE" if shadow_evaluation and shadow_evaluation.actionable else "REJECTED") if shadow_evaluation else None),
            shadow_veto_reasons=lifecycle_shadow_vetoes if lifecycle_shadow is not None else (shadow_evaluation.veto_reasons if shadow_evaluation else None),
            decision_delta=lifecycle_decision_delta or _decision_delta(evaluation, shadow_evaluation),
            shadow_hypothetical_entry_price=(features.price if shadow_evaluation and shadow_evaluation.actionable else None),
            shadow_hypothetical_grade=shadow_evaluation.grade if shadow_evaluation else None,
            shadow_hypothetical_score=shadow_evaluation.score if shadow_evaluation else None,
            shadow_removed_vetoes=(sorted(set(evaluation.veto_reasons) - set(shadow_evaluation.veto_reasons)) if shadow_evaluation else None),
        )
        if self._config.climax_root_event_tracking_enabled and lifecycle_shadow is None:
            lifecycle_model_version = str((shadow_evaluation.metadata if shadow_evaluation else evaluation.metadata).get("model_version", "climax-v1"))
            if shadow_attempt_id is None:
                self._repository.record_attempt_correlation_missing(
                    root_event_id=root_event_id,
                    event_revision=event_revision,
                    attempt_id=None,
                    evaluation_id=evaluation_id,
                    observed_at=evaluated_at,
                    market_asof=features.asof,
                    runtime_instance_id=self._runtime_instance_id,
                    model_version=lifecycle_model_version,
                    details={"event_id": state.event_id, "fast_monitor": fast_monitor},
                )
            elif shadow_evaluation is not None and shadow_evaluation.actionable:
                self._repository.transition_shadow_entry_attempt(
                    attempt_id=shadow_attempt_id,
                    root_event_id=root_event_id,
                    event_revision=event_revision,
                    evaluation_id=evaluation_id,
                    new_state="SHADOW_ACTIONABLE",
                    reason="shadow_actionable",
                    observed_at=evaluated_at,
                    market_asof=features.asof,
                    runtime_instance_id=self._runtime_instance_id,
                    model_version=lifecycle_model_version,
                )
            else:
                self._repository.expire_shadow_attempt_if_due(
                    attempt_id=shadow_attempt_id,
                    root_event_id=root_event_id,
                    event_revision=event_revision,
                    evaluation_id=evaluation_id,
                    observed_at=evaluated_at,
                    market_asof=features.asof,
                    runtime_instance_id=self._runtime_instance_id,
                    model_version=lifecycle_model_version,
                )
        if not evaluation.actionable:
            return None
        subtype = evaluation.subtype or ""
        model_version = str(evaluation.metadata.get("model_version", "climax-v1"))
        if self._repository.has_signal_for_event(symbol, state.event_id, subtype, model_version):
            self._remove_climax_candidate(symbol, state.event_id, reason="duplicate_signal")
            return None
        high = float(evaluation.metadata.get("event_high") or features.price)
        decision = SignalDecision(
            symbol=symbol,
            event_id=state.event_id,
            signal_type=SignalType.CONFIRM,
            grade=evaluation.grade,
            score=evaluation.score,
            market_price=features.price,
            short_zone_low=high * 0.97,
            short_zone_high=high,
            signal_time=features.asof,
            reasons=["Climax exhaustion confirmed", "Failed continuation below event high"],
            risk_flags=["Повышенный риск ликвидности"] if evaluation.metadata.get("liquidity_warning") else [],
            features_snapshot=asdict(features),
            score_breakdown={"climax_v1": float(evaluation.score)},
            decision_type="SIGNAL",
            actionable=True,
            lifecycle_state="CLIMAX_SIGNAL_SENT",
            strategy_type="CLIMAX_EXHAUSTION",
            strategy_subtype=subtype,
            model_version=model_version,
            strategy_metadata={**evaluation.metadata, "veto_reasons": evaluation.veto_reasons, "fast_monitor": fast_monitor},
        )
        if decision.strategy_subtype == "LOW_VOLUME_EXTENSION_FAILURE":
            event_high = float(decision.strategy_metadata.get("event_high") or 0.0)
            stored_distance = float(decision.strategy_metadata.get("entry_distance_below_high_pct") or 0.0)
            computed_distance = ((event_high - decision.market_price) / event_high * 100) if event_high else 999.0
            if abs(computed_distance - stored_distance) > 0.15:
                self._logger.warning("Climax delivery veto: inconsistent_event_high_metadata symbol=%s", symbol)
                return None
            if hasattr(self._scanner, "fetch_symbol_frames"):
                fresh_frames = await self._scanner.fetch_symbol_frames([symbol])
                fresh_frame = fresh_frames.get(symbol) if fresh_frames else None
                if fresh_frame is not None and not fresh_frame.empty:
                    fresh_derivatives = await self._scanner.fetch_optional_derivatives(symbol)
                    fresh_liquidity = await self._fetch_optional_liquidity(symbol, float(fresh_frame["close"].iloc[-1]))
                    fresh_features = self._feature_builder.build(symbol, fresh_frame, state=state, derivatives=fresh_derivatives, liquidity=fresh_liquidity)
                    fresh_eval = evaluate_climax(state, fresh_features, fresh_frame, self._config)
                    self._repository.record_climax_evaluation(
                        evaluation_time=datetime.now(timezone.utc),
                        symbol=symbol,
                        strategy="CLIMAX_EXHAUSTION",
                        subtype_candidate=fresh_eval.metadata.get("strategy_subtype"),
                        model_version=str(fresh_eval.metadata.get("model_version", "climax-v1")),
                        event_id=state.event_id,
                        event_high=fresh_eval.metadata.get("event_high"),
                        event_high_time=state.event_high_time,
                        event_detected_at=state.event_start_time,
                        candidate_added_at=candidate_added_at,
                        candidate_age_sec=candidate_age_sec,
                        fast_monitor=fast_monitor,
                        poll_sequence=poll_sequence,
                        frame_asof=fresh_features.asof,
                        candles_asof=fresh_features.asof,
                        oi_asof=fresh_features.asof if fresh_features.oi_change_pct is not None else None,
                        orderbook_asof=fresh_features.asof if fresh_features.liquidity_available else None,
                        score=fresh_eval.score,
                        grade=fresh_eval.grade,
                        actionable=fresh_eval.actionable,
                        admission_passed=not fresh_eval.veto_reasons,
                        veto_reasons=fresh_eval.veto_reasons,
                        passed_conditions=[],
                        data_quality=fresh_eval.data_quality,
                        liquidity={
                            "available": fresh_features.liquidity_available,
                            "spread_pct": fresh_features.spread_pct,
                            "slippage_pct": fresh_features.slippage_pct,
                            "depth_1pct_usdt": fresh_features.orderbook_depth_usdt_1pct,
                            "depth_2pct_usdt": fresh_features.orderbook_depth_usdt_2pct,
                        },
                        oi={"status": fresh_features.derivatives_status, "change_pct": fresh_features.oi_change_pct},
                        features=asdict(fresh_features),
                        lifecycle_state="DELIVERY_RECHECK",
                        telegram_eligible=fresh_eval.actionable,
                        runtime_instance_id=self._runtime_instance_id,
                        root_event_id=root_event_id,
                        event_revision=event_revision,
                        attempt_id=shadow_attempt_id,
                        observed_at=fresh_features.asof,
                        market_asof=fresh_features.asof,
                    )
                    if not fresh_eval.actionable or fresh_eval.subtype != "LOW_VOLUME_EXTENSION_FAILURE":
                        self._logger.info("Climax delivery veto: fresh_admission_failed symbol=%s reasons=%s", symbol, fresh_eval.veto_reasons)
                        return None
        telegram_sent = await self._notifier.send_signal(format_signal_message(decision, self._config.timezone))
        record = self._repository.save_signal(decision, state, telegram_sent)
        state = self._pullback_tracker.mark_signal_sent(state, signal_id=record.id, when=features.asof)
        self._state_store.save(state)
        self._remove_climax_candidate(symbol, state.event_id, reason="signal_created")
        return decision

    async def _process_symbol(
        self,
        symbol: str,
        frame_1m: pd.DataFrame,
        state: EventState | None,
    ) -> tuple[SignalDecision | None, EventState | None]:
        derivatives = await self._scanner.fetch_optional_derivatives(symbol)
        features = self._feature_builder.build(symbol, frame_1m, state=state, derivatives=derivatives)
        now = features.asof

        if state is None or state.state in {EventStatus.IDLE, EventStatus.EXPIRED}:
            new_state = self._pump_detector.build_event(symbol, frame_1m, features, now)
            if new_state is None:
                return None, new_state
            if self._config.climax_short_enabled:
                self._track_climax_candidate(new_state, now)
                liquidity = await self._fetch_optional_liquidity(symbol, features.price)
                features = self._feature_builder.build(symbol, frame_1m, state=new_state, derivatives=derivatives, liquidity=liquidity)
                climax_decision = await self._evaluate_and_send_climax(symbol, frame_1m, new_state, features=features)
                if climax_decision is not None:
                    return climax_decision, new_state
            early_watch = await self._maybe_emit_early_pump_watch(new_state, features, now)
            return early_watch, new_state

        replacement_event = self._pump_detector.build_event(symbol, frame_1m, features, now)
        if (
            replacement_event is not None
            and state.signal_id is None
            and state.event_high is not None
            and features.last_high > state.event_high
        ):
            state = replacement_event
            if self._config.climax_short_enabled:
                self._track_climax_candidate(state, now)
            early_watch = await self._maybe_emit_early_pump_watch(state, features, now)
            return early_watch, state

        if self._config.climax_short_enabled:
            self._track_climax_candidate(state, now)
            liquidity = await self._fetch_optional_liquidity(symbol, features.price)
            features = self._feature_builder.build(symbol, frame_1m, state=state, derivatives=derivatives, liquidity=liquidity)
            climax_decision = await self._evaluate_and_send_climax(symbol, frame_1m, state, features=features)
            if climax_decision is not None:
                return climax_decision, state

        if self._should_cancel_after_high_break(state, features, now):
            state.state = EventStatus.EXPIRED
            state.expires_at = now
            state.updated_at = now
            return None, state

        state = self._pullback_tracker.advance(state, features, now)
        if state.state == EventStatus.EXPIRED:
            return None, state

        zone = self._zone_builder.build(state, features)
        if zone is None:
            return None, state

        state.zone_low = zone.low
        state.zone_high = zone.high
        liquidity = await self._fetch_optional_liquidity(symbol, features.price)
        features = self._feature_builder.build(
            symbol,
            frame_1m,
            state=state,
            derivatives=derivatives,
            liquidity=liquidity,
        )

        if zone.low <= features.price <= zone.high and state.state == EventStatus.PULLBACK_OBSERVED:
            state.state = EventStatus.SHORT_ZONE_ACTIVE

        evaluation = self._signal_engine.analyze(state, features, zone, now)
        self._repository.record_reject_stat(
            symbol=symbol,
            timeframe=state.trigger_window or "15m",
            decision_type=evaluation.decision.decision_type if evaluation.decision else "REJECT",
            score=evaluation.score,
            reasons=evaluation.reject_reasons,
            blockers=evaluation.blockers,
            risk_flags=evaluation.risk_flags,
            close_to_watch=evaluation.close_to_watch,
            squeeze_risk_level=evaluation.squeeze_risk_level,
            derivatives_status=features.derivatives_status,
            derivatives_reasons=features.derivatives_reasons,
            data_quality_warnings=evaluation.data_quality_warnings,
            logged_at=now,
        )
        decision = evaluation.decision
        if decision is None or state.signal_id is not None:
            return None, state

        if decision.signal_type == SignalType.WATCH:
            watch_type = _watch_delivery_type(decision)
            if _watch_already_emitted(state, watch_type=watch_type):
                return None, state
            telegram_sent = False
            if self._config.send_watch_to_telegram and self._watch_sent_in_cycle < self._config.watch_max_per_cycle:
                telegram_sent = await self._notifier.send_signal(format_signal_message(decision, self._config.timezone))
                if telegram_sent:
                    self._watch_sent_in_cycle += 1
            _mark_watch_emitted(state, watch_type=watch_type)
            self._repository.save_watch_candidate(decision, state, telegram_sent)
            return decision, state

        telegram_sent = await self._notifier.send_signal(format_signal_message(decision, self._config.timezone))
        record = self._repository.save_signal(decision, state, telegram_sent)
        state = self._pullback_tracker.mark_signal_sent(state, signal_id=record.id, when=now)
        return decision, state

    async def _maybe_emit_early_pump_watch(
        self,
        state: EventState,
        features: SymbolFeatures,
        now: datetime,
    ) -> SignalDecision | None:
        if not self._config.enable_watch_candidates:
            return None
        if state.state != EventStatus.PUMP_DETECTED:
            return None
        if _early_watch_already_emitted(state, watch_type="EARLY_PUMP_WATCH"):
            return None

        score = _early_watch_score(features, self._config)
        if score < self._config.watch_min_score:
            return None

        blockers = ["early_pump_not_mature", "no_pullback_observed", "no_short_zone_active"]
        reject_reasons = [*blockers, "not_actionable"]
        risk_flags: list[str] = [
            "Observed before pullback maturity.",
            "Not actionable until pullback and short-zone confirmation.",
        ]
        if features.vol_zscore_30m < self._config.vol_zscore_min and features.vol_zscore_30m >= _early_watch_volume_threshold(self._config):
            risk_flags.append("Volume z-score is near the actionable threshold but still below it.")

        decision = SignalDecision(
            symbol=features.symbol,
            event_id=state.event_id,
            signal_type=SignalType.WATCH,
            grade=_grade_from_score(score),
            score=score,
            market_price=features.price,
            short_zone_low=state.zone_low or features.price,
            short_zone_high=state.zone_high or features.price,
            signal_time=now,
            reasons=[
                f"Dist to VWAP: +{features.dist_to_vwap_pct:.1f}%",
                f"Dist to EMA20 ATR: {features.dist_to_ema20_atr:.2f}",
                f"Volume z-score 30m: {features.vol_zscore_30m:.2f}",
                f"Range/ATR ratio: {features.range_atr_ratio:.2f}",
                f"Early pump trigger window: {state.trigger_window or '15m'}",
            ],
            risk_flags=risk_flags,
            features_snapshot={**asdict(features), "signal_vwap": features.vwap},
            score_breakdown={
                "momentum_confirms": _early_watch_momentum_confirms(features, self._config),
                "stretch_confirms": _early_watch_stretch_confirms(features, self._config),
                "mode": "early_pump_watch",
            },
            decision_type="EARLY_PUMP_WATCH",
            actionable=False,
            lifecycle_state=state.state.value,
            blockers=blockers,
            squeeze_risk_score=0,
            squeeze_risk_level="LOW",
            squeeze_risk_reasons=[],
            squeeze_guard_action="WATCH_ONLY",
            data_quality_warnings=[],
        )
        _mark_early_watch_emitted(state, watch_type="EARLY_PUMP_WATCH")
        self._repository.record_reject_stat(
            symbol=features.symbol,
            timeframe=state.trigger_window or "15m",
            decision_type=decision.decision_type,
            score=decision.score,
            reasons=reject_reasons,
            blockers=blockers,
            risk_flags=risk_flags,
            close_to_watch=True,
            squeeze_risk_level="LOW",
            derivatives_status=features.derivatives_status,
            derivatives_reasons=features.derivatives_reasons,
            data_quality_warnings=[],
            logged_at=now,
        )
        telegram_sent = False
        if self._config.send_watch_to_telegram and self._watch_sent_in_cycle < self._config.watch_max_per_cycle:
            telegram_sent = await self._notifier.send_signal(format_signal_message(decision, self._config.timezone))
            if telegram_sent:
                self._watch_sent_in_cycle += 1
        self._repository.save_watch_candidate(decision, state, telegram_sent)
        return decision

    async def _fetch_optional_liquidity(self, symbol: str, price: float) -> dict[str, object]:
        fetcher = getattr(self._scanner, "fetch_optional_liquidity", None)
        if fetcher is None:
            return {}
        return await fetcher(symbol, price)

    def _should_cancel_after_high_break(self, state: EventState, features: SymbolFeatures, now: datetime) -> bool:
        if state.event_high is None or features.last_high <= state.event_high:
            return False
        if state.event_high_time and now >= state.event_high_time + timedelta(minutes=self._config.max_signal_age_minutes):
            return True
        if self._config.cancel_on_new_event_high and state.state == EventStatus.SHORT_ZONE_ACTIVE:
            return True
        return (
            self._config.cancel_on_volume_breakout
            and state.state in {EventStatus.PULLBACK_OBSERVED, EventStatus.SHORT_ZONE_ACTIVE}
            and features.vol_zscore_30m >= self._config.vol_zscore_min
        )

    async def _handle_error(self, key: str, exc: Exception) -> None:
        self._health.on_error()
        self._logger.exception("Runtime error in %s: %s", key, exc)
        if self._error_throttler.should_send(key):
            await self._notifier.send_alert(f"Short signal bot error in {key}: {exc!r}")

    async def _ensure_storage_healthy(self, context: str) -> bool:
        try:
            health = self._repository.check_storage_health()
        except Exception as exc:
            await self._handle_error(f"db-heartbeat:{context}", exc)
            return False

        checked_at = str(health.get("checked_at"))
        if checked_at != self._storage_health_last_ok:
            journal_mode = health.get("journal_mode")
            busy_timeout = health.get("busy_timeout")
            self._logger.info(
                "DB heartbeat OK | context=%s db_url=%s journal_mode=%s busy_timeout=%sms checked_at=%s",
                context,
                self._repository.db_url,
                journal_mode,
                busy_timeout,
                checked_at,
            )
            self._storage_health_last_ok = checked_at
        return True


def _early_watch_volume_threshold(config: AppConfig) -> float:
    return min(config.vol_zscore_min * 0.85, 0.70)


def _early_watch_stretch_confirms(features: SymbolFeatures, config: AppConfig) -> int:
    return sum(
        [
            features.dist_to_vwap_pct >= config.event_dist_to_vwap_min,
            features.dist_to_ema20_atr >= config.event_dist_to_ema20_atr_min,
            features.vol_zscore_30m >= _early_watch_volume_threshold(config),
            features.range_atr_ratio >= config.range_atr_bonus_level,
        ]
    )


def _early_watch_momentum_confirms(features: SymbolFeatures, config: AppConfig) -> int:
    return sum(
        [
            features.ret_15m >= config.event_ret_15m_min,
            features.ret_1h >= config.event_ret_1h_min,
            features.ret_4h >= config.event_ret_4h_min,
        ]
    )


def _early_watch_score(features: SymbolFeatures, config: AppConfig) -> int:
    stretch_confirms = _early_watch_stretch_confirms(features, config)
    momentum_confirms = _early_watch_momentum_confirms(features, config)
    return min(100, 45 + (stretch_confirms * 4) + (momentum_confirms * 3))


def _early_watch_already_emitted(state: EventState, watch_type: str) -> bool:
    return _watch_already_emitted(state, watch_type)


def _mark_early_watch_emitted(state: EventState, watch_type: str) -> None:
    _mark_watch_emitted(state, watch_type)


def _watch_already_emitted(state: EventState, watch_type: str) -> bool:
    snapshot = state.event_features_snapshot or {}
    emitted = snapshot.get("emitted_watch_types") or {}
    return emitted.get(watch_type) == state.event_id


def _mark_watch_emitted(state: EventState, watch_type: str) -> None:
    snapshot = dict(state.event_features_snapshot or {})
    emitted = dict(snapshot.get("emitted_watch_types") or {})
    emitted[watch_type] = state.event_id
    snapshot["emitted_watch_types"] = emitted
    state.event_features_snapshot = snapshot


def _watch_delivery_type(decision: SignalDecision) -> str:
    if decision.decision_type == "EARLY_PUMP_WATCH":
        return "EARLY_PUMP_WATCH"
    if decision.grade == "B":
        return "WATCH_B_BLOCKED"
    return "WATCH_C_STRONG_MOVE"


def _grade_from_score(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    return "C"
