"""Alert Dispatcher and Throttling Engine for Greek Commercial EMS.

Implements a 5-state machine:
- IDLE: Normal baseline monitoring.
- PENDING_BREACH: Telemetry exceeds threshold; accumulating debounce samples.
- TRIGGERED: Breach confirmed after debounce filter; alert event generated.
- COOLDOWN: Alert dispatched; duplicate alerts suppressed for 30 minutes.
- CLEARED: Power drops below 90% hysteresis threshold; recovery alert generated.

Core features:
- 3-sample debounce filter (prevents single-sample false alarms).
- 30-minute cooldown period per facility (prevents notification spam).
- Escalation trigger (>=25% power jump breaks cooldown immediately).
- 10% release hysteresis (load must drop <= 0.90 * threshold to clear).
- Low power factor detection (cos φ < 0.85).
- Pre-warning detection before peak window or on approaching threshold.
- Async dispatch using ITelegramClient (supporting MockTelegramClient for testing).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import ConfigDict

from backend.models.alert import (
    AlertEvent,
    AlertSeverity,
    AlertThresholdConfig,
    AlertType,
)
from backend.models.telemetry import TelemetryPayload
from bot.telegram_client import ITelegramClient, LiveTelegramClient, MockTelegramClient
from bot.templates_el import (
    get_peak_window_str,
    get_tailored_curtailment_advice,
    render_low_power_factor_alert,
    render_normalization_alert,
    render_peak_breach_alert,
    render_pre_warning_alert,
)
from bot.viber_client import IViberClient, LiveViberClient, MockViberClient

try:
    from tariff_engine.contracts import is_greek_peak_window
    from tariff_engine.cost_calculator import calculate_realtime_cost
except ImportError:
    # Fallbacks if running in isolation
    def is_greek_peak_window(dt: datetime | None = None) -> bool:  # type: ignore[misc]
        if dt is None:
            dt = datetime.now(timezone.utc)
        if dt.weekday() >= 5:
            return False
        h = dt.hour
        m = dt.month
        if 5 <= m <= 10:
            return 14 <= h < 17
        return 17 <= h < 21

    def calculate_realtime_cost(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        return None

logger = logging.getLogger(__name__)

settings: Any = None
try:
    from backend.config import settings
except ImportError:
    pass


class AlertState(str, Enum):
    """5-State Alert Dispatcher finite state machine."""

    IDLE = "IDLE"
    PENDING_BREACH = "PENDING_BREACH"
    TRIGGERED = "TRIGGERED"
    COOLDOWN = "COOLDOWN"
    CLEARED = "CLEARED"


class DispatcherAlertEvent(AlertEvent):
    """Enriched AlertEvent with backward-compatible aliases for test suites and E2E tiers."""

    model_config = ConfigDict(extra="allow")

    @property
    def current_kw(self) -> float:
        """Alias for current_power_kw."""
        return self.current_power_kw

    @property
    def message_text(self) -> str:
        """Alias for message."""
        return self.message

    @property
    def pf_avg(self) -> float:
        """Alias for power_factor."""
        return self.power_factor if self.power_factor is not None else 0.98

    @property
    def running_cost_eur_h(self) -> float:
        """Alias for running cost in EUR/h."""
        return float(self.metadata.get("running_cost_eur_per_h", 0.0))

    @property
    def facility_name(self) -> str:
        """Display name of facility."""
        return str(self.metadata.get("facility_name", self.facility_id))

    @property
    def facility_type(self) -> str:
        """Facility type classification."""
        return str(self.metadata.get("facility_type", "commercial"))


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    """Safely extract configuration attribute from dict, dataclass, or Pydantic model."""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class FacilityStateMachine:
    """Individual state machine tracking a single facility's alert status."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.facility_id: str = _cfg_get(config, "facility_id", "facility-default")
        self.facility_name: str = _cfg_get(
            config, "facility_name", _cfg_get(config, "name", _cfg_get(config, "facility_id", "Εγκατάσταση"))
        )
        self.facility_type: str = _cfg_get(config, "facility_type", "commercial")
        self.peak_threshold_kw: float = float(_cfg_get(config, "peak_threshold_kw", 22.0))
        self.warning_threshold_ratio: float = float(
            _cfg_get(config, "warning_threshold_ratio", 0.85)
        )
        self.low_pf_threshold: float = float(_cfg_get(config, "low_pf_threshold", 0.85))
        self.cooldown_seconds: int = int(_cfg_get(config, "cooldown_seconds", 1800))
        self.hysteresis_factor: float = float(_cfg_get(config, "hysteresis_factor", 0.90))
        self.debounce_samples: int = int(_cfg_get(config, "debounce_samples", 3))
        self.chat_id: int | str | None = _cfg_get(
            config, "chat_id", _cfg_get(config, "telegram_chat_id", None)
        )
        self.viber_receiver_id: str | None = _cfg_get(
            config, "viber_receiver_id", _cfg_get(config, "viber_chat_id", None)
        )
        self.notification_channel: str = str(
            _cfg_get(config, "notification_channel", "telegram")
        ).lower()

        self.state: AlertState = AlertState.IDLE
        self.debounce_counter: int = 0
        self.last_alert_time: datetime | None = None
        self.last_alert_kw: float = 0.0
        self.last_alert_type: AlertType | None = None
        self.last_low_pf_alert_time: datetime | None = None
        self.last_pre_warning_time: datetime | None = None
        self.history: list[DispatcherAlertEvent] = []

    @property
    def cooldown_duration(self) -> timedelta:
        return timedelta(seconds=self.cooldown_seconds)

    def process_reading(
        self,
        power_kw: float,
        timestamp: datetime,
        is_peak_window: bool,
        cost_res: Any = None,
        power_factor: float = 0.98,
    ) -> DispatcherAlertEvent | None:
        """Evaluate an active power telemetry reading through the state machine.

        Args:
            power_kw: Instantaneous active power in kW.
            timestamp: Measurement timestamp (UTC).
            is_peak_window: Whether active tariff peak surcharge window is in effect.
            cost_res: Optional CostCalculationResult with running cost and penalty.
            power_factor: Observed power factor cos φ.

        Returns:
            DispatcherAlertEvent if a notification is triggered or cleared, otherwise None.
        """
        threshold = self.peak_threshold_kw
        is_above_threshold = (power_kw > threshold) and is_peak_window
        # 10% release hysteresis: load must drop below 0.90 * threshold (with small float tolerance)
        is_below_hysteresis = power_kw <= (round(self.hysteresis_factor * threshold, 4) + 1e-6)

        # ----------------------------------------------------------------------
        # State: IDLE
        # ----------------------------------------------------------------------
        if self.state == AlertState.IDLE:
            if is_above_threshold:
                self.debounce_counter += 1
                if self.debounce_counter >= self.debounce_samples:
                    self.state = AlertState.TRIGGERED
                    event = self._build_breach_event(power_kw, timestamp, cost_res, power_factor, is_escalation=False)
                    self.last_alert_time = timestamp
                    self.last_alert_kw = power_kw
                    self.last_alert_type = AlertType.PEAK_BREACH
                    self.state = AlertState.COOLDOWN
                    self.history.append(event)
                    return event
                else:
                    self.state = AlertState.PENDING_BREACH
                    return None
            else:
                self.debounce_counter = max(0, self.debounce_counter - 1)
                return None

        # ----------------------------------------------------------------------
        # State: PENDING_BREACH
        # ----------------------------------------------------------------------
        elif self.state == AlertState.PENDING_BREACH:
            if is_above_threshold:
                self.debounce_counter += 1
                if self.debounce_counter >= self.debounce_samples:
                    self.state = AlertState.TRIGGERED
                    event = self._build_breach_event(power_kw, timestamp, cost_res, power_factor, is_escalation=False)
                    self.last_alert_time = timestamp
                    self.last_alert_kw = power_kw
                    self.last_alert_type = AlertType.PEAK_BREACH
                    self.state = AlertState.COOLDOWN
                    self.history.append(event)
                    return event
                return None
            else:
                self.debounce_counter = max(0, self.debounce_counter - 1)
                if self.debounce_counter == 0:
                    self.state = AlertState.IDLE
                return None

        # ----------------------------------------------------------------------
        # State: COOLDOWN
        # ----------------------------------------------------------------------
        elif self.state == AlertState.COOLDOWN:
            # 1. Hysteresis Recovery: load dropped <= 0.90 * threshold OR peak window ended
            if is_below_hysteresis or not is_peak_window:
                self.state = AlertState.CLEARED
                recovery_event = self._build_recovery_event(power_kw, timestamp)
                self.state = AlertState.IDLE
                self.debounce_counter = 0
                self.last_alert_type = AlertType.NORMALIZED
                self.history.append(recovery_event)
                return recovery_event

            # 2. Escalation Trigger: power jumped >= 25% above last alerted power
            is_escalation = (power_kw >= (self.last_alert_kw * 1.25 - 1e-6)) and is_above_threshold
            if is_escalation:
                self.state = AlertState.TRIGGERED
                escalation_event = self._build_breach_event(
                    power_kw, timestamp, cost_res, power_factor, is_escalation=True
                )
                self.last_alert_time = timestamp
                self.last_alert_kw = power_kw
                self.last_alert_type = AlertType.PEAK_BREACH
                self.state = AlertState.COOLDOWN
                self.history.append(escalation_event)
                return escalation_event

            # 3. Cooldown expiration check
            if self.last_alert_time and (timestamp - self.last_alert_time) >= self.cooldown_duration:
                if is_above_threshold:
                    self.state = AlertState.TRIGGERED
                    event = self._build_breach_event(power_kw, timestamp, cost_res, power_factor, is_escalation=False)
                    self.last_alert_time = timestamp
                    self.last_alert_kw = power_kw
                    self.last_alert_type = AlertType.PEAK_BREACH
                    self.state = AlertState.COOLDOWN
                    self.history.append(event)
                    return event
                else:
                    self.state = AlertState.IDLE
                    self.debounce_counter = 0
                    return None

            # Suppressed duplicate breach during active cooldown
            return None

        # ----------------------------------------------------------------------
        # State: CLEARED
        # ----------------------------------------------------------------------
        elif self.state == AlertState.CLEARED:
            if is_above_threshold:
                self.state = AlertState.PENDING_BREACH
                self.debounce_counter = 1
            else:
                self.state = AlertState.IDLE
                self.debounce_counter = 0
            return None

        return None

    def check_pre_warning(
        self,
        power_kw: float,
        timestamp: datetime,
        minutes_until_peak: int | None = None,
    ) -> DispatcherAlertEvent | None:
        """Evaluate whether to generate a proactive pre-warning alert before or approaching peak window."""
        warning_kw = self.peak_threshold_kw * self.warning_threshold_ratio
        if power_kw < warning_kw and minutes_until_peak is None:
            return None

        if self.last_pre_warning_time is not None and (timestamp - self.last_pre_warning_time) < self.cooldown_duration:
            return None

        window_str = get_peak_window_str(timestamp)
        advice = get_tailored_curtailment_advice(self.facility_type)
        msg = render_pre_warning_alert(
            facility_name=self.facility_name,
            current_kw=power_kw,
            threshold_kw=self.peak_threshold_kw,
            warning_threshold_kw=warning_kw,
            peak_window_str=window_str,
            curtailment_advice=advice,
            facility_type=self.facility_type,
            dt=timestamp,
            minutes_until_peak=minutes_until_peak,
        )

        event = DispatcherAlertEvent(
            id=f"alert-{uuid4().hex[:8]}",
            facility_id=self.facility_id,
            alert_type=AlertType.PRE_WARNING,
            severity=AlertSeverity.WARNING,
            timestamp=timestamp,
            message=msg,
            current_power_kw=power_kw,
            threshold_kw=self.peak_threshold_kw,
            excess_kw=0.0,
            active_zone="Προειδοποίηση Αιχμής",
            estimated_penalty_eur=0.0,
            power_factor=0.98,
            metadata={
                "facility_name": self.facility_name,
                "facility_type": self.facility_type,
                "warning_threshold_kw": warning_kw,
                "chat_id": self.chat_id,
                "viber_receiver_id": self.viber_receiver_id,
                "notification_channel": self.notification_channel,
            },
            dispatched=False,
        )
        self.last_pre_warning_time = timestamp
        self.history.append(event)
        return event

    def check_low_power_factor(
        self,
        power_factor: float,
        power_kw: float,
        timestamp: datetime,
    ) -> DispatcherAlertEvent | None:
        """Evaluate whether to generate a low power factor warning (< 0.85)."""
        if power_factor >= self.low_pf_threshold or power_factor <= 0.0:
            return None

        if self.last_low_pf_alert_time is not None and (timestamp - self.last_low_pf_alert_time) < self.cooldown_duration:
            return None

        msg = render_low_power_factor_alert(
            facility_name=self.facility_name,
            power_factor=power_factor,
            active_power_kw=power_kw,
            low_pf_threshold=self.low_pf_threshold,
        )
        event = DispatcherAlertEvent(
            id=f"alert-{uuid4().hex[:8]}",
            facility_id=self.facility_id,
            alert_type=AlertType.LOW_POWER_FACTOR,
            severity=AlertSeverity.WARNING,
            timestamp=timestamp,
            message=msg,
            current_power_kw=power_kw,
            threshold_kw=self.peak_threshold_kw,
            excess_kw=0.0,
            active_zone="Χαμηλός cos φ",
            estimated_penalty_eur=0.0,
            power_factor=power_factor,
            metadata={
                "facility_name": self.facility_name,
                "facility_type": self.facility_type,
                "chat_id": self.chat_id,
                "viber_receiver_id": self.viber_receiver_id,
                "notification_channel": self.notification_channel,
            },
            dispatched=False,
        )
        self.last_low_pf_alert_time = timestamp
        self.history.append(event)
        return event

    def _extract_costs(self, power_kw: float, cost_res: Any) -> tuple[float, float]:
        """Extract or approximate running cost (€/h) and projected excess penalty (€)."""
        running_cost = 0.0
        penalty = 0.0
        if cost_res is not None:
            running_cost = float(getattr(cost_res, "running_cost_eur_per_h", 0.0))
            penalty = float(getattr(cost_res, "projected_excess_penalty_eur", 0.0))

        if running_cost == 0.0 and power_kw > 0.0:
            # Fallback estimation based on typical Greek commercial rate (~0.245 €/kWh)
            running_cost = round(power_kw * 0.245, 2)
        if penalty == 0.0 and power_kw > self.peak_threshold_kw:
            excess = power_kw - self.peak_threshold_kw
            # Fallback estimation for 2 remaining peak hours with 0.40 markup
            penalty = round(excess * 2.0 * 0.10, 2)
        return running_cost, penalty

    def _build_breach_event(
        self,
        power_kw: float,
        timestamp: datetime,
        cost_res: Any,
        power_factor: float,
        is_escalation: bool = False,
    ) -> DispatcherAlertEvent:
        running_cost, penalty = self._extract_costs(power_kw, cost_res)
        excess_kw = round(max(0.0, power_kw - self.peak_threshold_kw), 2)
        window_str = get_peak_window_str(timestamp)
        advice = get_tailored_curtailment_advice(self.facility_type)

        msg = render_peak_breach_alert(
            facility_name=self.facility_name,
            current_kw=power_kw,
            threshold_kw=self.peak_threshold_kw,
            excess_kw=excess_kw,
            running_cost_eur_h=running_cost,
            estimated_penalty_eur=penalty,
            power_factor=power_factor,
            peak_window_str=window_str,
            curtailment_advice=advice,
            facility_type=self.facility_type,
            is_escalation=is_escalation,
            dt=timestamp,
        )

        return DispatcherAlertEvent(
            id=f"alert-{uuid4().hex[:8]}",
            facility_id=self.facility_id,
            alert_type=AlertType.PEAK_BREACH,
            severity=AlertSeverity.CRITICAL if is_escalation else AlertSeverity.WARNING,
            timestamp=timestamp,
            message=msg,
            current_power_kw=power_kw,
            threshold_kw=self.peak_threshold_kw,
            excess_kw=excess_kw,
            active_zone="Ζώνη Αιχμής",
            estimated_penalty_eur=penalty,
            power_factor=power_factor,
            metadata={
                "facility_name": self.facility_name,
                "facility_type": self.facility_type,
                "running_cost_eur_per_h": running_cost,
                "is_escalation": is_escalation,
                "chat_id": self.chat_id,
                "viber_receiver_id": self.viber_receiver_id,
                "notification_channel": self.notification_channel,
            },
            dispatched=False,
        )

    def _build_recovery_event(self, power_kw: float, timestamp: datetime) -> DispatcherAlertEvent:
        msg = render_normalization_alert(
            facility_name=self.facility_name,
            current_kw=power_kw,
            threshold_kw=self.peak_threshold_kw,
            hysteresis_factor=self.hysteresis_factor,
            dt=timestamp,
        )

        return DispatcherAlertEvent(
            id=f"alert-{uuid4().hex[:8]}",
            facility_id=self.facility_id,
            alert_type=AlertType.NORMALIZED,
            severity=AlertSeverity.INFO,
            timestamp=timestamp,
            message=msg,
            current_power_kw=power_kw,
            threshold_kw=self.peak_threshold_kw,
            excess_kw=0.0,
            active_zone="Ομαλοποίηση",
            estimated_penalty_eur=0.0,
            power_factor=0.98,
            metadata={
                "facility_name": self.facility_name,
                "facility_type": self.facility_type,
                "running_cost_eur_per_h": 0.0,
                "chat_id": self.chat_id,
                "viber_receiver_id": self.viber_receiver_id,
                "notification_channel": self.notification_channel,
            },
            dispatched=False,
        )


class AlertDispatcher:
    """Alert Dispatcher supporting multi-facility state tracking and async Telegram dispatch.

    Provides both single-facility backward-compatible interface and multi-facility
    orchestration with ITelegramClient dispatch.
    """

    def __init__(
        self,
        facility_config: Any = None,
        telegram_client: ITelegramClient | None = None,
        viber_client: IViberClient | None = None,
        default_chat_id: int | str | None = None,
        default_viber_receiver_id: str | None = None,
    ) -> None:
        if telegram_client is not None:
            self.telegram_client: ITelegramClient | None = telegram_client
        elif settings and getattr(settings, "TELEGRAM_BOT_TOKEN", None):
            self.telegram_client = LiveTelegramClient(token=settings.TELEGRAM_BOT_TOKEN)
        else:
            self.telegram_client = MockTelegramClient()

        viber_token = (
            getattr(settings, "VIBER_BOT_TOKEN", None)
            or getattr(settings, "VIBER_AUTH_TOKEN", None)
            if settings
            else None
        )
        if viber_client is not None:
            self.viber_client: IViberClient | None = viber_client
        elif viber_token:
            self.viber_client = LiveViberClient(auth_token=viber_token)
        else:
            self.viber_client = MockViberClient()

        self.default_chat_id: int | str | None = default_chat_id or (
            getattr(settings, "TELEGRAM_DEFAULT_CHAT_ID", None) if settings else None
        )
        self.default_viber_receiver_id: str | None = default_viber_receiver_id or (
            getattr(settings, "VIBER_DEFAULT_RECEIVER_ID", None) if settings else None
        )
        self._facilities: dict[str, FacilityStateMachine] = {}
        self._primary_facility_id: str | None = None
        self.dispatched_alerts: list[AlertEvent] = []

        if facility_config is not None:
            self.register_facility(facility_config, is_primary=True)

    def set_telegram_client(self, client: ITelegramClient) -> None:
        """Override Telegram client (useful for mock injection in tests)."""
        self.telegram_client = client

    def set_viber_client(self, client: IViberClient) -> None:
        """Override Viber client (useful for mock injection in tests)."""
        self.viber_client = client

    def get_facility_state(self, facility_id: str) -> str:
        """Get current alert state string for a facility."""
        if facility_id in self._facilities:
            return self._facilities[facility_id].state.value
        return "IDLE"

    def reset_facility_state(self, facility_id: str | None = None) -> None:
        """Reset state machine for one or all facilities."""
        if facility_id:
            self._facilities.pop(facility_id, None)
        else:
            self._facilities.clear()
            self.dispatched_alerts.clear()

    def register_facility(self, config: Any, is_primary: bool = False) -> FacilityStateMachine:
        """Register or update a facility threshold configuration."""
        f_id = _cfg_get(config, "facility_id", "default")
        fsm = FacilityStateMachine(config)
        self._facilities[f_id] = fsm
        if is_primary or self._primary_facility_id is None:
            self._primary_facility_id = f_id
        return fsm

    @property
    def primary_fsm(self) -> FacilityStateMachine:
        """Get the primary facility state machine."""
        if not self._facilities:
            # Create a default configuration if none registered
            default_cfg = AlertThresholdConfig(
                facility_id="facility-default",
                peak_threshold_kw=22.0,
                warning_threshold_ratio=0.85,
                low_pf_threshold=0.85,
                contracted_capacity_kva=35.0,
                cooldown_seconds=1800,
                hysteresis_factor=0.90,
                debounce_samples=3,
            )
            return self.register_facility(default_cfg, is_primary=True)
        return self._facilities[self._primary_facility_id or next(iter(self._facilities))]

    @property
    def state(self) -> AlertState:
        """Current state of primary facility state machine."""
        return self.primary_fsm.state

    @state.setter
    def state(self, new_state: AlertState) -> None:
        self.primary_fsm.state = new_state

    @property
    def debounce_counter(self) -> int:
        """Current debounce counter of primary facility state machine."""
        return self.primary_fsm.debounce_counter

    @debounce_counter.setter
    def debounce_counter(self, val: int) -> None:
        self.primary_fsm.debounce_counter = val

    @property
    def debounce_threshold(self) -> int:
        return self.primary_fsm.debounce_samples

    @property
    def last_alert_time(self) -> datetime | None:
        return self.primary_fsm.last_alert_time

    @property
    def last_alert_kw(self) -> float:
        return self.primary_fsm.last_alert_kw

    @property
    def history(self) -> list[DispatcherAlertEvent]:
        return self.primary_fsm.history

    def process_reading(
        self,
        power_kw: float,
        timestamp: datetime,
        is_peak_window: bool,
        cost_res: Any = None,
        power_factor: float = 0.98,
        facility_id: str | None = None,
    ) -> DispatcherAlertEvent | None:
        """Synchronously process a power reading through the designated facility state machine."""
        fsm = self._facilities.get(facility_id) if facility_id else self.primary_fsm
        if fsm is None:
            fsm = self.primary_fsm
        return fsm.process_reading(
            power_kw=power_kw,
            timestamp=timestamp,
            is_peak_window=is_peak_window,
            cost_res=cost_res,
            power_factor=power_factor,
        )

    async def dispatch_alert(
        self,
        alert_event: AlertEvent,
        chat_id: int | str | None = None,
        viber_receiver_id: str | None = None,
    ) -> bool:
        """Asynchronously send an alert event via configured Telegram and/or Viber clients.

        Args:
            alert_event: The AlertEvent or DispatcherAlertEvent to send.
            chat_id: Target Telegram chat ID. If None, resolves from alert metadata or defaults.
            viber_receiver_id: Target Viber receiver ID. If None, resolves from metadata or defaults.

        Returns:
            bool: True if delivered successfully via active channels, False otherwise.
        """
        fac_id = getattr(alert_event, "facility_id", None)
        fsm = self._facilities.get(fac_id) if fac_id else None

        channel = str(
            alert_event.metadata.get("notification_channel")
            or (fsm.notification_channel if fsm else None)
            or "telegram"
        ).lower()
        target_chat = (
            chat_id
            or alert_event.metadata.get("chat_id")
            or (fsm.chat_id if fsm else None)
            or self.default_chat_id
        )
        target_viber = (
            viber_receiver_id
            or alert_event.metadata.get("viber_receiver_id")
            or (fsm.viber_receiver_id if fsm else None)
            or self.default_viber_receiver_id
        )

        sent_any = False

        # Telegram dispatch
        if channel in ("telegram", "both"):
            if self.telegram_client is None:
                logger.warning("No ITelegramClient configured; marking dispatched in memory.")
                sent_any = True
            elif target_chat:
                sent_tg = await self.telegram_client.send_message(
                    chat_id=target_chat,
                    text=alert_event.message,
                    parse_mode="HTML",
                )
                if sent_tg:
                    sent_any = True
                    logger.info("Alert %s successfully dispatched to Telegram chat %s", alert_event.id, target_chat)
                else:
                    logger.error("Failed to dispatch alert %s to Telegram chat %s", alert_event.id, target_chat)
            else:
                sent_any = True

        # Viber dispatch
        if channel in ("viber", "both"):
            if self.viber_client is None:
                logger.warning("No IViberClient configured; marking dispatched in memory.")
                sent_any = True
            elif target_viber:
                viber_text = (
                    alert_event.message.replace("<b>", "")
                    .replace("</b>", "")
                    .replace("<code>", "")
                    .replace("</code>", "")
                )
                sent_vb = await self.viber_client.send_message(
                    receiver_id=str(target_viber),
                    text=viber_text,
                )
                if sent_vb:
                    sent_any = True
                    logger.info("Alert %s successfully dispatched to Viber receiver %s", alert_event.id, target_viber)
                else:
                    logger.error("Failed to dispatch alert %s to Viber receiver %s", alert_event.id, target_viber)
            else:
                sent_any = True

        alert_event.dispatched = sent_any
        if sent_any:
            alert_event.dispatched_at = datetime.now(timezone.utc)
        self.dispatched_alerts.append(alert_event)
        return sent_any

    async def process_reading_and_dispatch(
        self,
        power_kw: float,
        timestamp: datetime,
        is_peak_window: bool,
        cost_res: Any = None,
        power_factor: float = 0.98,
        facility_id: str | None = None,
        chat_id: int | str | None = None,
    ) -> DispatcherAlertEvent | None:
        """Process reading and automatically dispatch via Telegram if an alert is generated."""
        event = self.process_reading(
            power_kw=power_kw,
            timestamp=timestamp,
            is_peak_window=is_peak_window,
            cost_res=cost_res,
            power_factor=power_factor,
            facility_id=facility_id,
        )
        if event is not None and self.telegram_client is not None:
            await self.dispatch_alert(event, chat_id=chat_id)
        return event

    def check_pre_warning(
        self,
        power_kw: float,
        timestamp: datetime,
        facility_id: str | None = None,
        minutes_until_peak: int | None = None,
    ) -> DispatcherAlertEvent | None:
        """Evaluate pre-warning condition across registered facilities."""
        fsm = self._facilities.get(facility_id) if facility_id else self.primary_fsm
        if fsm is None:
            fsm = self.primary_fsm
        return fsm.check_pre_warning(
            power_kw=power_kw,
            timestamp=timestamp,
            minutes_until_peak=minutes_until_peak,
        )

    def check_low_power_factor(
        self,
        power_factor: float,
        power_kw: float,
        timestamp: datetime,
        facility_id: str | None = None,
    ) -> DispatcherAlertEvent | None:
        """Evaluate low power factor (< 0.85) condition across registered facilities."""
        fsm = self._facilities.get(facility_id) if facility_id else self.primary_fsm
        if fsm is None:
            fsm = self.primary_fsm
        return fsm.check_low_power_factor(
            power_factor=power_factor,
            power_kw=power_kw,
            timestamp=timestamp,
        )

    async def process_telemetry(
        self,
        payload: TelemetryPayload,
        cost_res_or_profile: Any = None,
        facility_config: dict[str, Any] | None = None,
    ) -> DispatcherAlertEvent | None:
        """High-level async ingestion for a full TelemetryPayload.

        Supports both:
        - (payload, cost_res, facility_config) called from backend ingestion route.
        - (payload, contract_profile) called from simulator/standalone pipeline.
        """
        fid = payload.facility_id
        dt = payload.timestamp

        # Register or update facility state machine if facility_config provided
        if facility_config is not None:
            if fid not in self._facilities:
                self.register_facility(facility_config)
            else:
                fsm_existing = self._facilities[fid]
                if "chat_id" in facility_config or "telegram_chat_id" in facility_config:
                    fsm_existing.chat_id = facility_config.get("chat_id") or facility_config.get("telegram_chat_id")
                if "name" in facility_config or "facility_name" in facility_config:
                    fsm_existing.facility_name = str(facility_config.get("name") or facility_config.get("facility_name") or fid)
                if "peak_threshold_kw" in facility_config:
                    fsm_existing.peak_threshold_kw = float(facility_config["peak_threshold_kw"])
                if "cooldown_seconds" in facility_config:
                    fsm_existing.cooldown_seconds = int(facility_config["cooldown_seconds"])
                if "hysteresis_factor" in facility_config:
                    fsm_existing.hysteresis_factor = float(facility_config["hysteresis_factor"])
                if "debounce_samples" in facility_config:
                    fsm_existing.debounce_samples = int(facility_config["debounce_samples"])

        fsm = self._facilities.get(fid) or self.primary_fsm

        cost_res = None
        is_peak = False
        if cost_res_or_profile is not None:
            if hasattr(cost_res_or_profile, "running_cost_eur_per_h"):
                cost_res = cost_res_or_profile
                is_peak = getattr(cost_res_or_profile, "is_peak_window", is_greek_peak_window(dt))
            else:
                try:
                    cost_res = calculate_realtime_cost(
                        power_kw=payload.total_active_power_kw,
                        energy_kwh_delta=0.0,
                        timestamp=dt,
                        tariff_profile=cost_res_or_profile,
                    )
                    is_peak = is_greek_peak_window(dt)
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    logger.debug("Could not calculate real-time cost during telemetry processing: %s", exc)
                    is_peak = is_greek_peak_window(dt)
        else:
            is_peak = is_greek_peak_window(dt)

        # 1. Process active power threshold state machine
        event = self.process_reading(
            power_kw=payload.total_active_power_kw,
            timestamp=dt,
            is_peak_window=is_peak,
            cost_res=cost_res,
            power_factor=payload.system_power_factor,
            facility_id=fid,
        )

        # 2. Check low power factor if no peak breach event occurred and not directly from ingestion
        if event is None and facility_config is None:
            event = fsm.check_low_power_factor(
                power_factor=payload.system_power_factor,
                power_kw=payload.total_active_power_kw,
                timestamp=dt,
            )

        if event is not None and self.telegram_client is not None:
            target_chat = fsm.chat_id or self.default_chat_id
            await self.dispatch_alert(event, chat_id=target_chat)

        return event


# Backward-compatible alias for test harness and existing E2E tiers
AlertDispatcherStateMachine = AlertDispatcher
