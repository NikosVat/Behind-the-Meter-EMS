"""
Test harness, validation models, and reference calculation oracles for
Greek Commercial Behind-the-Meter EMS E2E test suites.

Reference standards:
- Greek Law 5068/2023 & Ministerial Decision (ΥΠΕΝ) Green Tariff Regulation
- DEDDIE / ADMIE Commercial Tariff Schedules (Γ21, Γ22, Γ23)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

# --- 1. Physical & Telemetry Pydantic Models (Interface Contract 1) ---

class PhaseReading(BaseModel):
    voltage_v: float = Field(..., ge=0.0, le=350.0, description="Phase-to-Neutral RMS Voltage [V]")
    current_a: float = Field(..., ge=0.0, le=250.0, description="Phase RMS Current [A]")
    active_power_kw: float = Field(..., ge=0.0, le=75.0, description="Active Power [kW]")
    apparent_power_kva: float = Field(..., ge=0.0, le=90.0, description="Apparent Power [kVA]")
    power_factor: float = Field(..., ge=-1.0, le=1.0, description="Phase Power Factor cos phi")

    @model_validator(mode="after")
    def validate_apparent_active_relationship(self):
        # Apparent power must be >= active power (within 0.05 kVA precision)
        if self.apparent_power_kva < self.active_power_kw - 0.05:
            raise ValueError(
                f"apparent_power_kva ({self.apparent_power_kva}) cannot be less than "
                f"active_power_kw ({self.active_power_kw})"
            )
        return self


class TelemetryPayload(BaseModel):
    device_id: str = Field(..., min_length=3, max_length=64)
    facility_id: str = Field(..., min_length=3, max_length=64)
    timestamp: datetime = Field(..., description="UTC timestamp of reading")
    phases: dict[str, PhaseReading] = Field(..., description="L1, L2, L3 phase readings")
    total_active_power_kw: float = Field(..., ge=0.0, le=225.0)
    total_apparent_power_kva: float = Field(..., ge=0.0, le=270.0)
    system_power_factor: float = Field(..., ge=-1.0, le=1.0)
    cumulative_energy_kwh: float = Field(..., ge=0.0)
    grid_frequency_hz: float = Field(default=50.0, ge=45.0, le=55.0)
    wifi_rssi_dbm: int = Field(default=-65, ge=-100, le=0)

    @model_validator(mode="after")
    def validate_three_phase_invariants(self):
        # Must have exactly L1, L2, L3
        required_phases = {"L1", "L2", "L3"}
        if set(self.phases.keys()) != required_phases:
            raise ValueError(f"Telemetry payload must contain exactly phases {required_phases}, got {set(self.phases.keys())}")

        # Invariant: |total_active_power_kw - sum(phases[Li].active_power_kw)| <= 0.05 kW
        sum_active = sum(phase.active_power_kw for phase in self.phases.values())
        if abs(self.total_active_power_kw - sum_active) > 0.051:
            raise ValueError(
                f"Active power invariant violated: total ({self.total_active_power_kw} kW) "
                f"differs from sum of phases ({sum_active:.3f} kW) by more than 0.05 kW"
            )

        # Invariant: total_apparent_power_kva >= total_active_power_kw (within tolerance)
        if self.total_apparent_power_kva < self.total_active_power_kw - 0.05:
            raise ValueError(
                f"Total apparent power ({self.total_apparent_power_kva} kVA) cannot be less than "
                f"total active power ({self.total_active_power_kw} kW)"
            )
        return self


def create_valid_telemetry_payload(
    facility_id: str = "bakery-central-athens",
    device_id: str = "esp32-ems-001",
    p_total_kw: float = 17.90,
    pf: float = 0.98,
    voltage_v: float = 230.0,
    timestamp: datetime | None = None,
    cumulative_energy_kwh: float = 142.50,
) -> TelemetryPayload:
    """Helper factory creating physically consistent TelemetryPayload instances."""
    if timestamp is None:
        timestamp = datetime(2026, 9, 14, 15, 30, 0, tzinfo=timezone.utc)

    # Distribute active power equally among 3 phases
    p_phase = round(p_total_kw / 3.0, 2)
    # Adjust L3 for exact rounding sum
    p_l1 = p_phase
    p_l2 = p_phase
    p_l3 = round(p_total_kw - p_l1 - p_l2, 2)

    s_total = round(p_total_kw / max(abs(pf), 0.001), 2) if pf != 0 else round(p_total_kw * 1.5, 2)
    s_l1 = round(p_l1 / max(abs(pf), 0.001), 2) if pf != 0 else round(p_l1 * 1.5, 2)
    s_l2 = round(p_l2 / max(abs(pf), 0.001), 2) if pf != 0 else round(p_l2 * 1.5, 2)
    s_l3 = round(s_total - s_l1 - s_l2, 2)
    if s_l3 < p_l3:
        s_l3 = p_l3
        s_total = s_l1 + s_l2 + s_l3

    i_l1 = round((s_l1 * 1000.0) / voltage_v, 1)
    i_l2 = round((s_l2 * 1000.0) / voltage_v, 1)
    i_l3 = round((s_l3 * 1000.0) / voltage_v, 1)

    phases = {
        "L1": PhaseReading(voltage_v=voltage_v, current_a=i_l1, active_power_kw=p_l1, apparent_power_kva=s_l1, power_factor=pf),
        "L2": PhaseReading(voltage_v=voltage_v, current_a=i_l2, active_power_kw=p_l2, apparent_power_kva=s_l2, power_factor=pf),
        "L3": PhaseReading(voltage_v=voltage_v, current_a=i_l3, active_power_kw=p_l3, apparent_power_kva=s_l3, power_factor=pf),
    }

    return TelemetryPayload(
        device_id=device_id,
        facility_id=facility_id,
        timestamp=timestamp,
        phases=phases,
        total_active_power_kw=p_total_kw,
        total_apparent_power_kva=s_total,
        system_power_factor=pf,
        cumulative_energy_kwh=cumulative_energy_kwh,
        grid_frequency_hz=50.0,
        wifi_rssi_dbm=-62,
    )


# --- 2. Tariff & Regulatory Mathematics (Interface Contract 2) ---

class TariffZone(str, Enum):
    PEAK = "PEAK"          # Greek Ζώνη Αιχμής
    NORMAL = "NORMAL"      # Greek Κανονική Ζώνη
    OFF_PEAK = "OFF_PEAK"  # Greek Ζώνη Μειωμένης Χρέωσης / Νυχτερινό


@dataclass
class FacilityProfileConfig:
    facility_id: str
    facility_name: str
    facility_type: str  # "bakery", "cold_storage", "hotel"
    contract_code: str  # "G21", "G22", "G23"
    tariff_color: str   # "green", "yellow", "dynamic"
    contracted_kva: float = 35.0
    peak_threshold_kw: float = 22.0
    cooldown_seconds: int = 1800
    telegram_chat_id: int = 999111222


@dataclass(frozen=True)
class CostCalculationResult:
    current_rate_eur_per_kwh: float
    running_cost_eur_per_h: float
    incremental_cost_eur: float
    is_peak_window: bool
    is_excess_breach: bool
    excess_power_kw: float
    projected_excess_penalty_eur: float
    regulated_rate_eur_per_kwh: float = 0.0
    vat_rate: float = 0.06


def is_greek_peak_window(dt: datetime, season: str = "auto") -> bool:
    """
    Evaluates whether given UTC or local timestamp falls within Greek DEDDIE peak tariff hours.
    DEDDIE Schedule:
    - Summer (May 1 to Oct 31):
        Peak: 14:00 to 17:00 (Mon - Fri)
        Off-Peak: 23:00 to 07:00
        Normal: 07:00 to 14:00 & 17:00 to 23:00
    - Winter (Nov 1 to Apr 30):
        Peak: 17:00 to 21:00 (Mon - Fri)
        Off-Peak: 02:00 to 08:00 (or 23:00 to 07:00)
    - Weekends (Saturday=5, Sunday=6): No peak hours.
    """
    # Check weekend
    if dt.weekday() >= 5:
        return False

    month = dt.month
    if season == "auto":
        is_summer = (5 <= month <= 10)
    else:
        is_summer = (season.lower() == "summer")

    hour = dt.hour

    if is_summer:
        # 14:00:00 to 16:59:59 is Peak
        return (14 <= hour < 17)
    else:
        # 17:00:00 to 20:59:59 is Peak
        return (17 <= hour < 21)


def is_greek_offpeak_window(dt: datetime, season: str = "auto") -> bool:
    """Evaluates off-peak / reduced night tariff window."""
    hour = dt.hour
    month = dt.month
    is_summer = (5 <= month <= 10) if season == "auto" else (season.lower() == "summer")
    if is_summer:
        return (hour >= 23 or hour < 7)
    else:
        return (hour >= 23 or hour < 7)  # standard commercial night window


def calculate_green_tariff_fluctuation(
    tea_eur_mwh: float,
    ll_eur_mwh: float = 95.0,
    lu_eur_mwh: float = 115.0,
    alpha: float = 1.15,
    beta: float = 0.0,
) -> float:
    """
    Computes Green Tariff Fluctuation Mechanism MD(M) per Law 5068/2023 & MD ΥΠΕΝ.
    Returns fluctuation MD in €/kWh.
    """
    tea_kwh = tea_eur_mwh / 1000.0
    ll_kwh = ll_eur_mwh / 1000.0
    lu_kwh = lu_eur_mwh / 1000.0

    if tea_kwh > lu_kwh:
        md = alpha * (tea_kwh - lu_kwh) + beta
    elif tea_kwh < ll_kwh:
        md = alpha * (tea_kwh - ll_kwh) + beta
    else:
        md = 0.0 + beta
    return md


def calculate_green_tariff_supply_rate(
    p_base: float = 0.145,
    e_disc: float = 0.020,
    tea_eur_mwh: float = 135.0,
    ll_eur_mwh: float = 95.0,
    lu_eur_mwh: float = 115.0,
    alpha: float = 1.15,
    beta: float = 0.0,
) -> float:
    """Computes total Green Tariff supply price P_supply = max(0, P_base - E_disc + MD)."""
    md = calculate_green_tariff_fluctuation(tea_eur_mwh, ll_eur_mwh, lu_eur_mwh, alpha, beta)
    p_supply = max(0.0, p_base - e_disc + md)
    return round(p_supply, 5)


def calculate_yellow_dynamic_supply_rate(
    tea_eur_mwh: float,
    loss_factor: float = 0.135,
    margin_eur_kwh: float = 0.015,
    p_base: float = 0.050,
) -> float:
    """
    Computes Yellow / Dynamic spot-indexed supply rate.
    P_dynamic = (TEA/1000) * (1 + loss_factor) + margin + p_base
    """
    tea_kwh = tea_eur_mwh / 1000.0
    rate = tea_kwh * (1.0 + loss_factor) + margin_eur_kwh + p_base
    return round(rate, 5)


def calculate_regulated_unit_rate(
    contracted_kva: float = 35.0,
    power_factor: float = 0.98,
    is_lv: bool = True,
) -> float:
    """
    Computes total regulated volumetric unit charge (€/kWh).
    Includes DEDDIE, ADMIE, ETMEAR, YKO, EFK, and DETE 5‰ levy.
    Applies power factor penalty multiplier F_PF = 0.85 / cos phi if cos phi < 0.85.
    """
    # DEDDIE distribution energy charge
    deddie_energy = 0.01415
    # Power factor penalty rule: F_PF = 0.85 / cos phi
    eff_pf = max(0.01, min(abs(power_factor), 1.0))
    if eff_pf < 0.85:
        pf_mult = 0.85 / eff_pf
    else:
        pf_mult = 1.0

    deddie_penalized = deddie_energy * pf_mult
    admie_energy = 0.00560
    etmear = 0.01700 if is_lv else 0.01200
    yko = 0.00690
    efk = 0.00220

    subtotal_energy = deddie_penalized + admie_energy + etmear + yko + efk
    # DETE special 5‰ duty on network & supply components (~0.005 * 0.180 ≈ 0.0009)
    dete = 0.00095

    return round(subtotal_energy + dete, 6)


def calculate_realtime_cost(
    power_kw: float,
    energy_kwh_delta: float,
    timestamp: datetime,
    tariff_profile: FacilityProfileConfig,
    tea_eur_mwh: float = 120.0,
    power_factor: float = 0.98,
) -> CostCalculationResult:
    """
    Pure deterministic cost calculation oracle.
    Computes instantaneous running cost (€/h), incremental spend, zone status, and projected excess.
    """
    is_peak = is_greek_peak_window(timestamp)
    is_offpeak = is_greek_offpeak_window(timestamp)

    # Determine base supply rate
    if tariff_profile.tariff_color == "green":
        supply_rate = calculate_green_tariff_supply_rate(
            p_base=0.155 if tariff_profile.contract_code == "G21" else 0.165,
            tea_eur_mwh=tea_eur_mwh
        )
    elif tariff_profile.tariff_color == "yellow":
        supply_rate = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=tea_eur_mwh, p_base=0.040)
    else:  # dynamic
        supply_rate = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=tea_eur_mwh, p_base=0.020)

    # If G22 / G23 dual-rate contract, apply peak / off-peak rate modifiers
    if tariff_profile.contract_code in ("G22", "G23"):
        if is_peak:
            supply_rate *= 1.25  # +25% peak surcharge
        elif is_offpeak:
            supply_rate *= 0.70  # -30% night discount

    regulated_rate = calculate_regulated_unit_rate(
        contracted_kva=tariff_profile.contracted_kva,
        power_factor=power_factor,
        is_lv=(tariff_profile.contract_code != "G23"),
    )

    total_unit_rate = (supply_rate + regulated_rate) * 1.06  # 6% Greek VAT

    running_cost_eur_per_h = round(power_kw * total_unit_rate, 2)
    incremental_cost_eur = round(energy_kwh_delta * total_unit_rate, 4)

    is_excess = (is_peak and power_kw > tariff_profile.peak_threshold_kw)
    excess_power_kw = max(0.0, power_kw - tariff_profile.peak_threshold_kw) if is_peak else 0.0

    # Project excess penalty for remaining peak window
    projected_penalty = 0.0
    if is_excess:
        # Summer peak ends at 17:00, winter at 21:00
        end_hour = 17 if (5 <= timestamp.month <= 10) else 21
        remaining_hours = max(0.1, end_hour - (timestamp.hour + timestamp.minute / 60.0))
        # Rate differential vs offpeak
        rate_diff = (supply_rate * 0.40) * 1.06
        projected_penalty = round(excess_power_kw * remaining_hours * rate_diff, 2)

    return CostCalculationResult(
        current_rate_eur_per_kwh=round(total_unit_rate, 4),
        running_cost_eur_per_h=running_cost_eur_per_h,
        incremental_cost_eur=incremental_cost_eur,
        is_peak_window=is_peak,
        is_excess_breach=is_excess,
        excess_power_kw=round(excess_power_kw, 2),
        projected_excess_penalty_eur=projected_penalty,
        regulated_rate_eur_per_kwh=round(regulated_rate, 4),
        vat_rate=0.06,
    )


# --- 3. Alert Dispatcher & State Machine (Interface Contract 3) ---

class AlertState(str, Enum):
    IDLE = "IDLE"
    TRIGGERED = "TRIGGERED"
    COOLDOWN = "COOLDOWN"
    CLEARED = "CLEARED"


@dataclass
class AlertEvent:
    facility_id: str
    facility_name: str
    facility_type: str
    timestamp: datetime
    current_kw: float
    threshold_kw: float
    excess_kw: float
    active_zone: str
    running_cost_eur_h: float
    estimated_penalty_eur: float
    pf_avg: float
    message_text: str


class AlertDispatcherStateMachine:
    """
    Full-fidelity Alert Dispatcher modeling:
    - 3-sample debounce filter
    - 30-minute cooldown suppression
    - 10% hysteresis release (clears at power <= 0.90 * threshold)
    - Greek alert templates tailored to facility types
    """

    def __init__(self, facility: FacilityProfileConfig):
        self.facility = facility
        self.state: AlertState = AlertState.IDLE
        self.debounce_counter: int = 0
        self.debounce_threshold: int = 3
        self.last_alert_time: datetime | None = None
        self.last_alert_kw: float = 0.0
        self.cooldown_duration = timedelta(seconds=facility.cooldown_seconds)
        self.history: list[AlertEvent] = []

    def process_reading(
        self,
        power_kw: float,
        timestamp: datetime,
        is_peak_window: bool,
        cost_res: CostCalculationResult,
        power_factor: float = 0.98,
    ) -> AlertEvent | None:
        threshold = self.facility.peak_threshold_kw
        is_above_threshold = (power_kw > threshold and is_peak_window)
        is_below_hysteresis = (power_kw <= 0.90 * threshold)

        # Debounce logic
        if is_above_threshold:
            self.debounce_counter += 1
        else:
            self.debounce_counter = max(0, self.debounce_counter - 1)

        # State transition: IDLE -> TRIGGERED
        if self.state == AlertState.IDLE:
            if is_above_threshold and self.debounce_counter >= self.debounce_threshold:
                self.state = AlertState.TRIGGERED
                event = self._build_breach_event(power_kw, timestamp, cost_res, power_factor)
                self.last_alert_time = timestamp
                self.last_alert_kw = power_kw
                self.state = AlertState.COOLDOWN
                self.history.append(event)
                return event
            return None

        # State: COOLDOWN
        elif self.state == AlertState.COOLDOWN:
            # Check for hysteresis recovery
            if is_below_hysteresis or not is_peak_window:
                self.state = AlertState.CLEARED
                recovery_event = self._build_recovery_event(power_kw, timestamp)
                self.state = AlertState.IDLE
                self.debounce_counter = 0
                self.history.append(recovery_event)
                return recovery_event

            # Check if cooldown expired
            if self.last_alert_time and (timestamp - self.last_alert_time) >= self.cooldown_duration:
                if is_above_threshold:
                    self.state = AlertState.TRIGGERED
                    event = self._build_breach_event(power_kw, timestamp, cost_res, power_factor)
                    self.last_alert_time = timestamp
                    self.last_alert_kw = power_kw
                    self.state = AlertState.COOLDOWN
                    self.history.append(event)
                    return event
                else:
                    self.state = AlertState.IDLE
                    return None

            # Escalation check (>25% power jump breaks cooldown)
            if power_kw >= self.last_alert_kw * 1.25 and is_above_threshold:
                event = self._build_breach_event(power_kw, timestamp, cost_res, power_factor, is_escalation=True)
                self.last_alert_time = timestamp
                self.last_alert_kw = power_kw
                self.history.append(event)
                return event

            return None

        elif self.state == AlertState.CLEARED:
            self.state = AlertState.IDLE
            return None

        return None

    def _build_breach_event(
        self,
        power_kw: float,
        timestamp: datetime,
        cost_res: CostCalculationResult,
        power_factor: float,
        is_escalation: bool = False,
    ) -> AlertEvent:
        advice_map = {
            "bakery": "Πρόταση: Μεταφέρετε το ψήσιμο παρτίδας στη ζώνη μειωμένης χρέωσης ή σβήστε προσωρινά 1 φούρνο.",
            "cold_storage": "Πρόταση: Κλείστε άμεσα τις πόρτες φορτοεκφόρτωσης και καθυστερήστε τον κύκλο απόψυξης.",
            "hotel": "Πρόταση: Αυξήστε τη θερμοκρασία κλιματισμού VRV κατά 1.5°C και αναστείλετε τα πλυντήρια.",
        }
        advice = advice_map.get(self.facility.facility_type, "Πρόταση: Μειώστε προσωρινά τα ενεργοβόρα φορτία.")

        header = "⚠️ ΚΛΙΜΑΚΩΣΗ ΥΠΕΡΒΑΣΗΣ" if is_escalation else "🚨 ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ ΣΕ ΖΩΝΗ ΑΙΧΜΗΣ"
        window_str = "14:00–17:00" if (5 <= timestamp.month <= 10) else "17:00–21:00"

        msg = (
            f"<b>{header}</b>\n\n"
            f"Εγκατάσταση: <b>{self.facility.facility_name}</b>\n"
            f"Τρέχουσα ισχύς: <b>{power_kw:.1f} kW</b> (Όριο: {self.facility.peak_threshold_kw:.1f} kW)\n"
            f"Υπέρβαση: <b>+{power_kw - self.facility.peak_threshold_kw:.1f} kW</b>\n"
            f"Ζώνη χρέωσης: <b>Ζώνη Αιχμής ({window_str})</b>\n"
            f"Τρέχον κόστος λειτουργίας: <b>{cost_res.running_cost_eur_per_h:.2f} €/h</b>\n"
            f"Εκτιμώμενη επιπλέον επιβάρυνση: <b>€{cost_res.projected_excess_penalty_eur:.2f}</b>\n"
            f"Συντελεστής ισχύος (cos φ): <b>{power_factor:.2f}</b>\n\n"
            f"💡 <i>{advice}</i>"
        )

        return AlertEvent(
            facility_id=self.facility.facility_id,
            facility_name=self.facility.facility_name,
            facility_type=self.facility.facility_type,
            timestamp=timestamp,
            current_kw=power_kw,
            threshold_kw=self.facility.peak_threshold_kw,
            excess_kw=round(power_kw - self.facility.peak_threshold_kw, 2),
            active_zone="Ζώνη Αιχμής",
            running_cost_eur_h=cost_res.running_cost_eur_per_h,
            estimated_penalty_eur=cost_res.projected_excess_penalty_eur,
            pf_avg=power_factor,
            message_text=msg,
        )

    def _build_recovery_event(self, power_kw: float, timestamp: datetime) -> AlertEvent:
        msg = (
            f"<b>✅ ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ</b>\n\n"
            f"Εγκατάσταση: <b>{self.facility.facility_name}</b>\n"
            f"Η ισχύς επανήλθε σε ασφαλή επίπεδα: <b>{power_kw:.1f} kW</b>\n"
            f"Όριο επαναφοράς (90%): <b>{self.facility.peak_threshold_kw * 0.90:.1f} kW</b>\n"
            f"Ώρα ομαλοποίησης: <b>{timestamp.strftime('%H:%M:%S')}</b>\n"
            f"Η παρακολούθηση συνεχίζεται κανονικά."
        )
        return AlertEvent(
            facility_id=self.facility.facility_id,
            facility_name=self.facility.facility_name,
            facility_type=self.facility.facility_type,
            timestamp=timestamp,
            current_kw=power_kw,
            threshold_kw=self.facility.peak_threshold_kw,
            excess_kw=0.0,
            active_zone="Ομαλοποίηση",
            running_cost_eur_h=0.0,
            estimated_penalty_eur=0.0,
            pf_avg=0.98,
            message_text=msg,
        )


# --- 4. Telegram Mock Client & Greek Bot Commands (Interface Contract 4) ---

class MockTelegramClient:
    """Thread-safe in-memory Telegram client mock for deterministic E2E verification."""

    def __init__(self):
        self.sent_messages: list[dict[str, Any]] = []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
    ) -> bool:
        self.sent_messages.append({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "timestamp": datetime.now(timezone.utc),
        })
        return True

    def get_sent_messages(self) -> list[dict[str, Any]]:
        return list(self.sent_messages)

    def get_last_message(self) -> dict[str, Any] | None:
        return self.sent_messages[-1] if self.sent_messages else None

    def clear(self) -> None:
        self.sent_messages.clear()

    def count(self) -> int:
        return len(self.sent_messages)


def format_greek_bot_response(
    command: str,
    facility: FacilityProfileConfig,
    latest_payload: TelemetryPayload | None = None,
    daily_spend_eur: float = 48.60,
    daily_energy_kwh: float = 240.5,
) -> str:
    """Formats Greek responses for interactive bot commands."""
    cmd = command.strip().lower()

    if cmd == "/status":
        if latest_payload is None:
            return "Δεν υπάρχουν διαθέσιμα δεδομένα τηλεμετρίας για την εγκατάσταση."
        p1 = latest_payload.phases["L1"]
        p2 = latest_payload.phases["L2"]
        p3 = latest_payload.phases["L3"]
        zone_str = "Ζώνη Αιχμής" if is_greek_peak_window(latest_payload.timestamp) else "Κανονική Ζώνη"
        cost_h = latest_payload.total_active_power_kw * 0.245

        return (
            f"<b>📊 Τρέχουσα Κατάσταση — {facility.facility_name}</b>\n\n"
            f"⚡ Συνολική Ισχύς: <b>{latest_payload.total_active_power_kw:.2f} kW</b>\n"
            f"📈 Φαινόμενη Ισχύς: <b>{latest_payload.total_apparent_power_kva:.2f} kVA</b>\n"
            f"🎯 Συντελεστής Ισχύος (cos φ): <b>{latest_payload.system_power_factor:.2f}</b>\n"
            f"💶 Τρέχον Κόστος: <b>{cost_h:.2f} €/h</b>\n"
            f"⏰ Ενεργή Ζώνη: <b>{zone_str}</b>\n\n"
            f"<b>Ανάλυση Φάσεων:</b>\n"
            f"• L1: {p1.voltage_v:.1f}V | {p1.current_a:.1f}A | {p1.active_power_kw:.2f}kW\n"
            f"• L2: {p2.voltage_v:.1f}V | {p2.current_a:.1f}A | {p2.active_power_kw:.2f}kW\n"
            f"• L3: {p3.voltage_v:.1f}V | {p3.current_a:.1f}A | {p3.active_power_kw:.2f}kW"
        )

    elif cmd == "/cost_today":
        return (
            f"<b>💰 Σημερινή Κατανάλωση & Κόστος — {facility.facility_name}</b>\n\n"
            f"📅 Ημερομηνία: <b>{datetime.now(timezone.utc).strftime('%d/%m/%Y')}</b>\n"
            f"⚡ Συσσωρευμένη Ενέργεια: <b>{daily_energy_kwh:.1f} kWh</b>\n"
            f"💶 Συνολικό Κόστος Σήμερα: <b>{daily_spend_eur:.2f} €</b>\n"
            f"📉 Μέση Τιμή: <b>{(daily_spend_eur / max(0.1, daily_energy_kwh)):.3f} €/kWh</b>\n"
            f"<i>Περιλαμβάνονται ανταγωνιστικές χρεώσεις, ρυθμιζόμενες και ΦΠΑ 6%.</i>"
        )

    elif cmd == "/tariff":
        color_names = {"green": "Πράσινο (Ειδικό)", "yellow": "Κίτρινο (Κυμαινόμενο)", "dynamic": "Πορτοκαλί (Δυναμικό)"}
        return (
            f"<b>📋 Στοιχεία Τιμολογίου — {facility.facility_name}</b>\n\n"
            f"• Τύπος Σύμβασης: <b>{facility.contract_code}</b>\n"
            f"• Κατηγορία: <b>{color_names.get(facility.tariff_color, facility.tariff_color)}</b>\n"
            f"• Συμφωνημένη Ισχύς: <b>{facility.contracted_kva:.0f} kVA</b>\n"
            f"• Όριο Ειδοποίησης Αιχμής: <b>{facility.peak_threshold_kw:.1f} kW</b>\n"
            f"• Ώρες Αιχμής (Θερινό): <b>14:00 - 17:00</b> (Δευ-Παρ)"
        )

    elif cmd == "/settings":
        return (
            f"<b>⚙️ Ρυθμίσεις Συστήματος — {facility.facility_name}</b>\n\n"
            f"• Όριο Υπέρβασης Αιχμής: <b>{facility.peak_threshold_kw:.1f} kW</b>\n"
            f"• Χρόνος Σίγασης (Cooldown): <b>{facility.cooldown_seconds // 60} λεπτά</b>\n"
            f"• Υστέρηση Επαναφοράς: <b>10% ({facility.peak_threshold_kw * 0.90:.1f} kW)</b>\n"
            f"• Telegram Chat ID: <code>{facility.telegram_chat_id}</code>"
        )

    else:
        return f"Άγνωστη εντολή: {command}. Διαθέσιμες εντολές: /status, /cost_today, /tariff, /settings"


# --- 5. Commercial Simulation Profile Generators ---

def get_commercial_bakery_power(hour_float: float) -> float:
    """
    Parametric load profile for Commercial Bakery (kW):
    - 03:00 - 08:30: Pre-heat & baking spikes (32 - 45 kW)
    - 08:30 - 14:00: Store open, ovens idle, refrigeration (12 - 16 kW)
    - 14:00 - 17:00: Afternoon baking prep (18 - 28 kW, breaching 22 kW peak threshold!)
    - 17:00 - 03:00: Night baseline (3 - 5 kW)
    """
    h = hour_float % 24.0
    if 3.0 <= h < 5.0:
        return 34.0 + 4.0 * math.sin((h - 3.0) * math.pi)
    elif 5.0 <= h < 8.5:
        return 42.0 + 3.0 * math.cos((h - 5.0) * 1.5)
    elif 8.5 <= h < 14.0:
        return 14.0 + 2.0 * math.sin((h - 8.5) * 0.8)
    elif 14.0 <= h < 17.0:
        # Afternoon prep surge in peak window!
        return 24.0 + 4.0 * math.sin((h - 14.0) * math.pi / 3.0)
    elif 17.0 <= h < 21.0:
        return 8.0 + 1.5 * math.cos(h - 17.0)
    else:
        return 4.0


def get_cold_storage_power(minute_float: float, door_open: bool = False) -> float:
    """
    Parametric load profile for Cold Storage Facility (kW):
    - Continuous refrigeration baseload: ~16 kW
    - Compressor pull-down cycle (45-min period): ramps to 32 kW
    - Door open disturbance: forces continuous 34 kW draw
    """
    if door_open:
        return 34.5
    cycle_phase = (minute_float % 45.0) / 45.0
    if cycle_phase < 0.60:
        # Compressor running
        return 28.0 + 4.0 * math.sin(cycle_phase * math.pi)
    else:
        # Compressor idle baseload
        return 16.0 + 1.0 * math.cos(cycle_phase * 2 * math.pi)


def get_boutique_hotel_power(hour_float: float) -> float:
    """
    Parametric load profile for Boutique Hotel (kW):
    - 07:30 - 10:30: Breakfast kitchen & laundry (22 - 28 kW)
    - 10:30 - 14:00: Midday housekeeping & pool pumps (14 - 18 kW)
    - 14:00 - 18:00: Check-in & VRV air conditioning peak (28 - 34 kW, breaching 30 kW threshold)
    - 18:00 - 23:00: Evening restaurant & lighting (18 - 24 kW)
    - 23:00 - 07:30: Night baseload (8 - 12 kW)
    """
    h = hour_float % 24.0
    if 7.5 <= h < 10.5:
        return 24.0 + 4.0 * math.sin((h - 7.5) * math.pi / 3.0)
    elif 10.5 <= h < 14.0:
        return 16.0 + 2.0 * math.sin((h - 10.5) * 0.8)
    elif 14.0 <= h < 18.0:
        # VRV AC ramp in peak heat window
        return 31.0 + 3.0 * math.sin((h - 14.0) * math.pi / 4.0)
    elif 18.0 <= h < 23.0:
        return 21.0 + 3.0 * math.cos((h - 18.0) * 0.6)
    else:
        return 10.0
