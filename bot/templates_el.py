"""Localized Greek notification templates for Greek Commercial EMS Telegram Alert Bot.

Provides production-ready, HTML-formatted Greek alert and status templates
tailored for Greek commercial facilities (bakeries, cold storage, boutique hotels):
- Peak breach alert (Υπέρβαση Ορίου σε Ζώνη Αιχμής) with tailored curtailment advice.
- Escalation breach alert (Κλιμάκωση Υπέρβασης).
- Normalization recovery alert (Ομαλοποίηση Κατανάλωσης).
- Pre-warning peak alert (Προειδοποίηση Προσέγγισης Ζώνης Αιχμής).
- Low power factor warning (Χαμηλός Συντελεστής Ισχύος cos φ < 0.85).
- Capacity excess alert (Υπέρβαση Συμφωνημένης Ισχύος kVA).
"""

from __future__ import annotations

from datetime import datetime, timezone


def get_peak_window_str(dt: datetime | None = None) -> str:
    """Return Greek peak window hours string based on official DEDDIE summer/winter schedule.

    Summer (May 1 – October 31): 14:00–17:00
    Winter (November 1 – April 30): 17:00–21:00
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    month = dt.month
    if 5 <= month <= 10:
        return "14:00–17:00"
    return "17:00–21:00"


def get_tailored_curtailment_advice(facility_type: str | None) -> str:
    """Return specific, actionable load-shedding advice based on facility profile.

    - bakery: Mentions deck ovens / φούρνο.
    - cold_storage: Mentions loading doors / πόρτες and defrost / απόψυξη.
    - hotel: Mentions VRV air-conditioning / κλιματισμού VRV and laundry.
    """
    if not facility_type:
        return "Πρόταση: Μειώστε προσωρινά τα ενεργοβόρα φορτία."

    ft = facility_type.strip().lower()
    if "bakery" in ft or "αρτοποι" in ft:
        return "Πρόταση: Μεταφέρετε το ψήσιμο παρτίδας στη ζώνη μειωμένης χρέωσης ή σβήστε προσωρινά 1 φούρνο."
    elif "cold" in ft or "ψυγ" in ft or "storage" in ft:
        return "Πρόταση: Κλείστε άμεσα τις πόρτες φορτοεκφόρτωσης και καθυστερήστε τον κύκλο απόψυξης."
    elif "hotel" in ft or "ξενοδοχ" in ft:
        return "Πρόταση: Αυξήστε τη θερμοκρασία κλιματισμού VRV κατά 1.5°C και αναστείλετε τα πλυντήρια."
    return "Πρόταση: Μειώστε προσωρινά τα ενεργοβόρα φορτία."


def render_peak_breach_alert(
    facility_name: str,
    current_kw: float,
    threshold_kw: float,
    excess_kw: float | None = None,
    running_cost_eur_h: float = 0.0,
    estimated_penalty_eur: float = 0.0,
    power_factor: float = 0.98,
    peak_window_str: str | None = None,
    curtailment_advice: str | None = None,
    facility_type: str | None = None,
    is_escalation: bool = False,
    dt: datetime | None = None,
) -> str:
    """Render Greek HTML notification template for peak tariff load breach.

    Supports both standard breach and escalation (>25% jump during cooldown).
    """
    if excess_kw is None:
        excess_kw = max(0.0, current_kw - threshold_kw)
    if peak_window_str is None:
        peak_window_str = get_peak_window_str(dt)
    if curtailment_advice is None:
        curtailment_advice = get_tailored_curtailment_advice(facility_type)

    header = "⚠️ <b>ΚΛΙΜΑΚΩΣΗ ΥΠΕΡΒΑΣΗΣ</b>" if is_escalation else "🚨 <b>ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ ΣΕ ΖΩΝΗ ΑΙΧΜΗΣ</b>"

    return (
        f"{header}\n\n"
        f"Εγκατάσταση: <b>{facility_name}</b>\n"
        f"Τρέχουσα ισχύς: <b>{current_kw:.1f} kW</b> (Όριο: {threshold_kw:.1f} kW)\n"
        f"Υπέρβαση: <b>+{excess_kw:.1f} kW</b>\n"
        f"Ζώνη χρέωσης: <b>Ζώνη Αιχμής ({peak_window_str})</b>\n"
        f"Τρέχον κόστος λειτουργίας: <b>{running_cost_eur_h:.2f} €/h</b>\n"
        f"Εκτιμώμενη επιπλέον επιβάρυνση: <b>€{estimated_penalty_eur:.2f}</b>\n"
        f"Συντελεστής ισχύος (cos φ): <b>{power_factor:.2f}</b>\n\n"
        f"💡 <i>{curtailment_advice}</i>"
    )


def render_normalization_alert(
    facility_name: str,
    current_kw: float,
    threshold_kw: float,
    hysteresis_factor: float = 0.90,
    cleared_time_str: str | None = None,
    dt: datetime | None = None,
) -> str:
    """Render Greek HTML notification template for load normalization / recovery."""
    hysteresis_limit_kw = threshold_kw * hysteresis_factor
    if cleared_time_str is None:
        cleared_time_str = (dt or datetime.now(timezone.utc)).strftime("%H:%M:%S")

    return (
        f"<b>✅ ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ</b>\n\n"
        f"Εγκατάσταση: <b>{facility_name}</b>\n"
        f"Η ισχύς επανήλθε σε ασφαλή επίπεδα: <b>{current_kw:.1f} kW</b>\n"
        f"Όριο επαναφοράς ({int(hysteresis_factor * 100)}%): <b>{hysteresis_limit_kw:.1f} kW</b>\n"
        f"Ώρα ομαλοποίησης: <b>{cleared_time_str}</b>\n"
        f"Η παρακολούθηση συνεχίζεται κανονικά."
    )


def render_pre_warning_alert(
    facility_name: str,
    current_kw: float,
    threshold_kw: float,
    warning_threshold_kw: float,
    peak_window_str: str | None = None,
    curtailment_advice: str | None = None,
    facility_type: str | None = None,
    dt: datetime | None = None,
    minutes_until_peak: int | None = None,
) -> str:
    """Render Greek HTML notification template for pre-warning before or during peak window."""
    if peak_window_str is None:
        peak_window_str = get_peak_window_str(dt)
    if curtailment_advice is None:
        curtailment_advice = get_tailored_curtailment_advice(facility_type)

    if minutes_until_peak is not None:
        timing_str = f"{peak_window_str} (έναρξη σε {minutes_until_peak} λεπτά)"
    else:
        timing_str = peak_window_str

    return (
        f"<b>⚠️ ΠΡΟΕΙΔΟΠΟΙΗΣΗ: ΠΡΟΣΕΓΓΙΣΗ ΖΩΝΗΣ ΑΙΧΜΗΣ</b>\n\n"
        f"Εγκατάσταση: <b>{facility_name}</b>\n"
        f"Τρέχουσα ισχύς: <b>{current_kw:.1f} kW</b>\n"
        f"Όριο προειδοποίησης: <b>{warning_threshold_kw:.1f} kW</b> (Όριο Αιχμής: {threshold_kw:.1f} kW)\n"
        f"Επικείμενη ζώνη αιχμής: <b>{timing_str}</b>\n\n"
        f"Συνιστάται η προληπτική μείωση ενεργοβόρων λειτουργιών για αποφυγή επιβαρύνσεων.\n"
        f"💡 <i>{curtailment_advice}</i>"
    )


def render_low_power_factor_alert(
    facility_name: str,
    power_factor: float,
    active_power_kw: float,
    low_pf_threshold: float = 0.85,
) -> str:
    """Render Greek HTML notification template for low power factor (cos φ < 0.85)."""
    return (
        f"<b>⚠️ ΠΡΟΕΙΔΟΠΟΙΗΣΗ: ΧΑΜΗΛΟΣ ΣΥΝΤΕΛΕΣΤΗΣ ΙΣΧΥΟΣ (cos φ)</b>\n\n"
        f"Εγκατάσταση: <b>{facility_name}</b>\n"
        f"Τρέχων cos φ: <b>{power_factor:.2f}</b> (Όριο ΔΕΔΔΗΕ: {low_pf_threshold:.2f})\n"
        f"Ενεργός ισχύς: <b>{active_power_kw:.1f} kW</b>\n\n"
        f"⚠️ <b>Επισήμανση Προστίμου ΔΕΔΔΗΕ:</b>\n"
        f"Συντελεστής ισχύος κάτω από {low_pf_threshold:.2f} επιφέρει προσαύξηση χρεώσεων δικτύου διανομής "
        f"λόγω υπερβολικής άεργου ισχύος (kVARh).\n\n"
        f"💡 <i>Πρόταση: Ελέγξτε τη συστοιχία πυκνωτών αντιστάθμισης ή επαγωγικά φορτία (μοτέρ, συμπιεστές).</i>"
    )


def render_capacity_excess_alert(
    facility_name: str,
    total_apparent_power_kva: float,
    contracted_kva: float,
    excess_kva: float,
    penalty_rate_multiplier: float = 1.40,
) -> str:
    """Render Greek HTML notification template for exceeding contracted connection capacity."""
    return (
        f"<b>🚨 ΥΠΕΡΒΑΣΗ ΣΥΜΦΩΝΗΜΕΝΗΣ ΙΣΧΥΟΣ (kVA)</b>\n\n"
        f"Εγκατάσταση: <b>{facility_name}</b>\n"
        f"Φαινόμενη ισχύς: <b>{total_apparent_power_kva:.1f} kVA</b>\n"
        f"Συμφωνημένη ισχύς (παροχή): <b>{contracted_kva:.0f} kVA</b>\n"
        f"Υπέρβαση παροχής: <b>+{excess_kva:.1f} kVA</b>\n\n"
        f"⚠️ <i>Η υπέρβαση της συμφωνημένης ισχύος επιβαρύνεται με πολλαπλασιαστή "
        f"χρέωσης ισχύος ΔΕΔΔΗΕ ({penalty_rate_multiplier:.1f}x).</i>"
    )
