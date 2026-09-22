"""Interactive Greek Telegram Command Handlers for Greek Commercial EMS.

Implements Greek-language command handlers for commercial business owners:
- /start: Welcome and system overview.
- /status: 3-phase voltages, currents, active kW, pf, €/h running cost, active tariff zone.
- /cost_today: Daily kWh, total spend (€), peak surcharge (€).
- /tariff: Contract type (Γ21/Γ22/Γ23), color, schedule, active rate.
- /settings: Thresholds, hysteresis, debounce, and configuration.
- /help: Command guide.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.models.telemetry import TelemetryPayload
from bot.telegram_client import ITelegramClient

try:
    from tariff_engine.contracts import (
        Season,
        TariffColor,
        TariffContract,
        get_greek_season,
        is_greek_offpeak_window,
        is_greek_peak_window,
    )
except ImportError:
    # Standalone fallbacks
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

    def is_greek_offpeak_window(dt: datetime | None = None) -> bool:  # type: ignore[misc]
        if dt is None:
            dt = datetime.now(timezone.utc)
        h = dt.hour
        m = dt.month
        if 5 <= m <= 10:
            return 23 <= h or h < 7
        return (2 <= h < 8) or (15 <= h < 17)

    def get_greek_season(dt: datetime | None = None) -> Any:  # type: ignore[misc]
        if dt is None:
            dt = datetime.now(timezone.utc)
        return "SUMMER" if 5 <= dt.month <= 10 else "WINTER"

    TariffContract = None  # type: ignore[misc,assignment]
    TariffColor = None  # type: ignore[misc,assignment]
    Season = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


def _format_contract_code(code: str) -> str:
    """Format contract code to satisfy both Greek Γ and Latin G conventions (e.g. Γ22 (G22))."""
    c = code.strip().upper()
    if c in ("G21", "Γ21"):
        return "Γ21 (G21)"
    elif c in ("G22", "Γ22"):
        return "Γ22 (G22)"
    elif c in ("G23", "Γ23"):
        return "Γ23 (G23)"
    elif c.startswith("G"):
        return f"Γ{c[1:]} ({c})"
    elif c.startswith("Γ"):
        return f"{c} (G{c[1:]})"
    return c


def _format_tariff_color(color: str) -> str:
    """Translate tariff color into official Greek retail market terminology."""
    c = color.strip().lower()
    if "green" in c or "πρασιν" in c:
        return "Πράσινο (Ειδικό Τιμολόγιο)"
    elif "yellow" in c or "κιτριν" in c:
        return "Κίτρινο (Κυμαινόμενο)"
    elif "dynamic" in c or "orange" in c or "πορτοκαλ" in c or "δυναμικ" in c:
        return "Πορτοκαλί (Δυναμικό Spot DAM)"
    return color.capitalize()


def _cfg_get(config: Any, key: str, default: Any = None) -> Any:
    """Safely extract configuration attribute from dict, dataclass, or Pydantic model."""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _get_phase_val(phase_obj: Any, key: str, default: float = 0.0) -> float:
    """Safely extract phase voltage, current, or power from dict or PhaseReading."""
    if isinstance(phase_obj, dict):
        return float(phase_obj.get(key, default))
    return float(getattr(phase_obj, key, default))


def format_greek_bot_response(
    command: str,
    facility: Any,
    latest_payload: TelemetryPayload | Any | None = None,
    daily_spend_eur: float = 48.60,
    daily_energy_kwh: float = 240.5,
    peak_surcharge_eur: float = 0.0,
    active_rate_eur_kwh: float | None = None,
) -> str:
    """Core function formatting Greek responses for interactive bot commands."""
    cmd = command.strip().split()[0].lower() if command.strip() else ""

    # Extract facility attributes safely across FacilityProfileConfig and AlertThresholdConfig
    facility_name = _cfg_get(
        facility, "facility_name", _cfg_get(facility, "facility_id", "Εγκατάσταση")
    )
    contract_raw = str(_cfg_get(facility, "contract_code", _cfg_get(facility, "contract_type", "Γ22")))
    contract_code_fmt = _format_contract_code(contract_raw)
    tariff_color_raw = str(_cfg_get(facility, "tariff_color", _cfg_get(facility, "color", "green")))
    tariff_color_fmt = _format_tariff_color(tariff_color_raw)
    contracted_kva = float(
        _cfg_get(facility, "contracted_capacity_kva", _cfg_get(facility, "contracted_kva", 35.0))
    )
    peak_threshold_kw = float(_cfg_get(facility, "peak_threshold_kw", 22.0))
    cooldown_seconds = int(_cfg_get(facility, "cooldown_seconds", 1800))
    cooldown_minutes = max(1, cooldown_seconds // 60)
    hysteresis_factor = float(_cfg_get(facility, "hysteresis_factor", 0.90))
    hysteresis_limit_kw = peak_threshold_kw * hysteresis_factor
    chat_id = _cfg_get(facility, "chat_id", _cfg_get(facility, "telegram_chat_id", "Δεν έχει οριστεί"))

    # --------------------------------------------------------------------------
    # 1. /start: Welcome and System Overview
    # --------------------------------------------------------------------------
    if cmd == "/start":
        return (
            f"<b>🇬🇷 Καλώς ήρθατε στο Commercial EMS Bot — {facility_name}</b>\n\n"
            f"Το σύστημα παρακολουθεί αδιάλειπτα την 3-φασική κατανάλωση ηλεκτρικής ενέργειας, "
            f"υπολογίζει το τρέχον κόστος βάσει των ελληνικών εμπορικών τιμολογίων "
            f"(Γ21, Γ22, Γ23 - Πράσινο, Κίτρινο, Δυναμικό) και σας προστατεύει από ακριβές "
            f"χρεώσεις αιχμής με προληπτικές ειδοποιήσεις.\n\n"
            f"<b>Διαθέσιμες Εντολές:</b>\n"
            f"• /status — Τρέχουσα ισχύς 3 φάσεων, τάσεις, cos φ και κόστος ανά ώρα\n"
            f"• /cost_today — Σημερινή κατανάλωση (kWh) και εκτιμώμενο κόστος (€)\n"
            f"• /tariff — Στοιχεία σύμβασης, ωράριο αιχμής και ενεργό τιμολόγιο\n"
            f"• /settings — Όρια ειδοποίησης, χρόνος σίγασης και ρυθμίσεις\n"
            f"• /help — Αναλυτικός οδηγός χρήσης"
        )

    # --------------------------------------------------------------------------
    # 2. /status: 3-phase voltages, currents, active kW, pf, €/h running cost, active zone
    # --------------------------------------------------------------------------
    elif cmd == "/status":
        if latest_payload is None:
            return "Δεν υπάρχουν διαθέσιμα δεδομένα τηλεμετρίας για την εγκατάσταση."

        phases_dict = _cfg_get(latest_payload, "phases", {})
        p1 = phases_dict.get("L1") if isinstance(phases_dict, dict) else getattr(phases_dict, "L1", None)
        p2 = phases_dict.get("L2") if isinstance(phases_dict, dict) else getattr(phases_dict, "L2", None)
        p3 = phases_dict.get("L3") if isinstance(phases_dict, dict) else getattr(phases_dict, "L3", None)

        dt = _cfg_get(latest_payload, "timestamp", datetime.now(timezone.utc))
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(timezone.utc)
        if is_greek_peak_window(dt):
            zone_str = "Ζώνη Αιχμής"
        elif is_greek_offpeak_window(dt):
            zone_str = "Ζώνη Μειωμένης Χρέωσης (Νυχτερινό)"
        else:
            zone_str = "Κανονική Ζώνη"

        total_active_kw = float(_cfg_get(latest_payload, "total_active_power_kw", 0.0))
        total_apparent_kva = float(_cfg_get(latest_payload, "total_apparent_power_kva", total_active_kw))
        sys_pf = float(_cfg_get(latest_payload, "system_power_factor", 0.98))

        rate = active_rate_eur_kwh if active_rate_eur_kwh is not None else 0.245
        running_cost_eur_h = total_active_kw * rate

        p1_v = _get_phase_val(p1, "voltage_v", 230.0)
        p1_a = _get_phase_val(p1, "current_a", 0.0)
        p1_kw = _get_phase_val(p1, "active_power_kw", total_active_kw / 3.0)

        p2_v = _get_phase_val(p2, "voltage_v", 230.0)
        p2_a = _get_phase_val(p2, "current_a", 0.0)
        p2_kw = _get_phase_val(p2, "active_power_kw", total_active_kw / 3.0)

        p3_v = _get_phase_val(p3, "voltage_v", 230.0)
        p3_a = _get_phase_val(p3, "current_a", 0.0)
        p3_kw = _get_phase_val(p3, "active_power_kw", total_active_kw / 3.0)

        return (
            f"<b>📊 Τρέχουσα Κατάσταση — {facility_name}</b>\n\n"
            f"⚡ Συνολική Ισχύς: <b>{total_active_kw:.2f} kW</b>\n"
            f"📈 Φαινόμενη Ισχύς: <b>{total_apparent_kva:.2f} kVA</b>\n"
            f"🎯 Συντελεστής Ισχύος (cos φ): <b>{sys_pf:.2f}</b>\n"
            f"💶 Τρέχον Κόστος: <b>{running_cost_eur_h:.2f} €/h</b>\n"
            f"⏰ Ενεργή Ζώνη: <b>{zone_str}</b>\n\n"
            f"<b>Ανάλυση Φάσεων:</b>\n"
            f"• L1: {p1_v:.1f}V | {p1_a:.1f}A | {p1_kw:.2f}kW\n"
            f"• L2: {p2_v:.1f}V | {p2_a:.1f}A | {p2_kw:.2f}kW\n"
            f"• L3: {p3_v:.1f}V | {p3_a:.1f}A | {p3_kw:.2f}kW"
        )

    # --------------------------------------------------------------------------
    # 3. /cost_today: Daily kWh, total spend (€), peak surcharge (€)
    # --------------------------------------------------------------------------
    elif cmd == "/cost_today":
        avg_rate = daily_spend_eur / max(0.1, daily_energy_kwh)
        date_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")

        surcharge_line = ""
        if peak_surcharge_eur > 0.0:
            surcharge_line = f"⚠️ Επιβάρυνση Ζώνης Αιχμής: <b>{peak_surcharge_eur:.2f} €</b>\n"
        else:
            surcharge_line = "⚠️ Επιβάρυνση Ζώνης Αιχμής: <b>0.00 €</b> (εντός ορίων)\n"

        return (
            f"<b>💰 Σημερινή Κατανάλωση & Κόστος — {facility_name}</b>\n\n"
            f"📅 Ημερομηνία: <b>{date_str}</b>\n"
            f"⚡ Συσσωρευμένη Ενέργεια: <b>{daily_energy_kwh:.1f} kWh</b>\n"
            f"💶 Συνολικό Κόστος Σήμερα: <b>{daily_spend_eur:.2f} €</b>\n"
            f"{surcharge_line}"
            f"📉 Μέση Τιμή: <b>{avg_rate:.3f} €/kWh</b>\n"
            f"<i>Περιλαμβάνονται ανταγωνιστικές χρεώσεις, ρυθμιζόμενες και ΦΠΑ 6%.</i>"
        )

    # --------------------------------------------------------------------------
    # 4. /tariff: Contract type, color, schedule, active rate
    # --------------------------------------------------------------------------
    elif cmd == "/tariff":
        rate_val = active_rate_eur_kwh if active_rate_eur_kwh is not None else 0.178
        return (
            f"<b>📋 Στοιχεία Τιμολογίου — {facility_name}</b>\n\n"
            f"• Τύπος Σύμβασης: <b>{contract_code_fmt}</b>\n"
            f"• Κατηγορία: <b>{tariff_color_fmt}</b>\n"
            f"• Συμφωνημένη Ισχύς: <b>{contracted_kva:.0f} kVA</b>\n"
            f"• Όριο Ειδοποίησης Αιχμής: <b>{peak_threshold_kw:.1f} kW</b>\n"
            f"• Τρέχουσα Τιμή Ενέργειας: <b>{rate_val:.4f} €/kWh</b>\n"
            f"• Ώρες Αιχμής (Θερινό): <b>14:00 - 17:00</b> (Δευ-Παρ)\n"
            f"• Ώρες Αιχμής (Χειμερινό): <b>17:00 - 21:00</b> (Δευ-Παρ)"
        )

    # --------------------------------------------------------------------------
    # 5. /settings: Thresholds and configuration
    # --------------------------------------------------------------------------
    elif cmd == "/settings":
        return (
            f"<b>⚙️ Ρυθμίσεις Συστήματος — {facility_name}</b>\n\n"
            f"• Όριο Υπέρβασης Αιχμής: <b>{peak_threshold_kw:.1f} kW</b>\n"
            f"• Χρόνος Σίγασης (Cooldown): <b>{cooldown_minutes} λεπτά</b>\n"
            f"• Υστέρηση Επαναφοράς: <b>{int(hysteresis_factor * 100)}% ({hysteresis_limit_kw:.1f} kW)</b>\n"
            f"• Συμφωνημένη Ισχύς: <b>{contracted_kva:.0f} kVA</b>\n"
            f"• Δείγματα Debounce: <b>{getattr(facility, 'debounce_samples', 3)} διαδοχικά</b>\n"
            f"• Telegram Chat ID: <code>{chat_id}</code>"
        )

    # --------------------------------------------------------------------------
    # 6. /help: Command guide
    # --------------------------------------------------------------------------
    elif cmd in ("/help", "help"):
        return (
            "<b>📖 Οδηγός Εντολών — Greek Commercial EMS</b>\n\n"
            "<b>/status</b> — Προβολή αναλυτικών μετρήσεων 3 φάσεων (τάσεις, ρεύματα, ενεργός kW, cos φ), "
            "τρέχοντος κόστους λειτουργίας ανά ώρα (€/h) και ενεργής ζώνης χρέωσης.\n\n"
            "<b>/cost_today</b> — Σημερινή συσσωρευμένη ενέργεια (kWh), συνολικό εκτιμώμενο κόστος (€), "
            "επιβάρυνση αιχμής και μέση τιμή ανά kWh.\n\n"
            "<b>/tariff</b> — Πληροφορίες σύμβασης ηλεκτρικής ενέργειας (Γ21/Γ22/Γ23, Χρώμα τιμολογίου, "
            "συμφωνημένη ισχύς, ωράριο ζώνης αιχμής και τρέχουσα χρέωση ενέργειας).\n\n"
            "<b>/settings</b> — Προβολή παραμέτρων προστασίας (όριο ισχύος kW, περίοδος cooldown, "
            "ποσοστό υστέρησης επαναφοράς και chat ID).\n\n"
            "<b>/start</b> — Επισκόπηση συστήματος και εισαγωγικό μήνυμα."
        )

    # --------------------------------------------------------------------------
    # Fallback for unrecognized commands
    # --------------------------------------------------------------------------
    else:
        return f"Άγνωστη εντολή: {command}. Διαθέσιμες εντολές: /status, /cost_today, /tariff, /settings, /help, /start"


class BotCommandHandler:
    """Manages Greek command execution and async dispatch via ITelegramClient."""

    def __init__(
        self,
        facility_config: Any,
        telegram_client: ITelegramClient | None = None,
        latest_payload: TelemetryPayload | None = None,
        daily_spend_eur: float = 48.60,
        daily_energy_kwh: float = 240.5,
        peak_surcharge_eur: float = 0.0,
    ) -> None:
        self.facility_config = facility_config
        self.telegram_client = telegram_client
        self.latest_payload = latest_payload
        self.daily_spend_eur = daily_spend_eur
        self.daily_energy_kwh = daily_energy_kwh
        self.peak_surcharge_eur = peak_surcharge_eur

    def update_telemetry(self, payload: TelemetryPayload) -> None:
        """Update cached latest telemetry reading."""
        self.latest_payload = payload

    def update_cost(self, daily_spend_eur: float, daily_energy_kwh: float, peak_surcharge_eur: float = 0.0) -> None:
        """Update daily spend metrics."""
        self.daily_spend_eur = daily_spend_eur
        self.daily_energy_kwh = daily_energy_kwh
        self.peak_surcharge_eur = peak_surcharge_eur

    def handle_command(self, command_text: str) -> str:
        """Process a text command synchronously and return the Greek formatted HTML response."""
        return format_greek_bot_response(
            command=command_text,
            facility=self.facility_config,
            latest_payload=self.latest_payload,
            daily_spend_eur=self.daily_spend_eur,
            daily_energy_kwh=self.daily_energy_kwh,
            peak_surcharge_eur=self.peak_surcharge_eur,
        )

    async def dispatch_command_response(
        self,
        chat_id: int | str,
        command_text: str,
    ) -> str:
        """Handle command and asynchronously send response to Telegram chat if client is present."""
        response_text = self.handle_command(command_text)
        if self.telegram_client is not None:
            await self.telegram_client.send_message(
                chat_id=chat_id,
                text=response_text,
                parse_mode="HTML",
            )
        return response_text
