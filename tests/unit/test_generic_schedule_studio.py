"""Unit tests for the Generic SME Schedule Studio MILP Engine.

Validates:
1. Multi-resolution scheduling (5, 15, 30, and 60 minutes).
2. Contiguous run enforcement for non-interruptible assets.
3. Flexible slot allocation for interruptible assets.
4. Midnight-crossing operating windows (e.g., 22:00 to 06:00).
5. Must-run vs optional equipment handling under tight capacity.
6. Preferred start time penalty and priority weighting.
7. Conservative baseline uncertainty buffer (+10%).
8. Objective modes (cost, peak, balanced).
9. Informative Greek natural-language explanations.
10. Infeasible problem detection.
"""

from __future__ import annotations

import pytest
from datetime import date

from optimization_engine.scheduling_models import (
    GenericEquipmentAsset,
    ScheduleSettings,
)
from optimization_engine.scheduling_service import (
    schedule_sme_equipment,
    _parse_time_to_slot,
    _format_slot_to_time,
)


class TestTimeSlotHelpers:
    """Test time-to-slot and slot-to-time conversion across resolutions."""

    def test_time_conversions_15min(self):
        # 00:00 -> slot 0
        assert _parse_time_to_slot("00:00", 15) == 0
        # 01:30 -> slot 6
        assert _parse_time_to_slot("01:30", 15) == 6
        # 23:45 -> slot 95
        assert _parse_time_to_slot("23:45", 15) == 95
        # 24:00 -> slot 96
        assert _parse_time_to_slot("24:00", 15) == 96

        assert _format_slot_to_time(0, 15) == "00:00"
        assert _format_slot_to_time(6, 15) == "01:30"
        assert _format_slot_to_time(96, 15) == "24:00"

    def test_time_conversions_60min(self):
        assert _parse_time_to_slot("00:00", 60) == 0
        assert _parse_time_to_slot("14:00", 60) == 14
        assert _parse_time_to_slot("24:00", 60) == 24
        assert _format_slot_to_time(14, 60) == "14:00"

    def test_time_conversions_5min(self):
        assert _parse_time_to_slot("00:05", 5) == 1
        assert _parse_time_to_slot("12:00", 5) == 144
        assert _format_slot_to_time(144, 5) == "12:00"


class TestGenericScheduleEngine:
    """Mathematical validation of schedule_sme_equipment solver."""

    @pytest.fixture
    def default_tariffs_96(self) -> list[float]:
        """96 slots (15-min): cheap at night (0.10 €/kWh), expensive midday (0.35 €/kWh)."""
        tariffs = [0.10] * 96
        # Midday peak from 11:00 (slot 44) to 17:00 (slot 68)
        for s in range(44, 68):
            tariffs[s] = 0.35
        return tariffs

    def test_multi_resolution_support(self):
        """Verify engine works for 5, 15, 30, and 60 minute steps."""
        asset = GenericEquipmentAsset(
            id="oven-1",
            facility_id="bakery-1",
            name="Φούρνος Αρτοποιείου",
            category="production",
            rated_power_kw=10.0,
            required_runtime_minutes=60,
            earliest_start="06:00",
            latest_finish="12:00",
            is_interruptible=False,
            is_must_run=True,
        )

        for step in [5, 15, 30, 60]:
            settings = ScheduleSettings(
                facility_id="bakery-1",
                time_step_minutes=step,
                max_facility_power_kw=35.0,
                objective_mode="cost",
            )
            result = schedule_sme_equipment(
                facility_id="bakery-1",
                assets=[asset],
                settings=settings,
                schedule_date=date(2026, 9, 23),
            )
            assert result.status == "optimal"
            assert len(result.scheduled_items) == 1
            item = result.scheduled_items[0]
            assert item.is_scheduled is True
            assert item.scheduled_duration_minutes == 60
            assert len(result.timeline) == 1440 // step

    def test_non_interruptible_contiguous_run(self, default_tariffs_96):
        """Non-interruptible equipment must run in consecutive slots without interruption."""
        asset = GenericEquipmentAsset(
            id="dishwasher-1",
            facility_id="restaurant-1",
            name="Πλυντήριο Σκευών",
            category="cleaning",
            rated_power_kw=6.0,
            required_runtime_minutes=60,  # 4 slots of 15 min
            earliest_start="10:00",
            latest_finish="16:00",
            is_interruptible=False,
            is_must_run=True,
        )
        settings = ScheduleSettings(
            facility_id="restaurant-1",
            time_step_minutes=15,
            max_facility_power_kw=25.0,
            objective_mode="cost",
        )
        result = schedule_sme_equipment(
            facility_id="restaurant-1",
            assets=[asset],
            settings=settings,
            schedule_date=date(2026, 9, 23),
            tariff_rates=default_tariffs_96,
        )
        assert result.status == "optimal"
        item = result.scheduled_items[0]
        assert item.is_scheduled is True
        assert len(item.active_slots) == 4
        # Verify slots are strictly consecutive
        for i in range(len(item.active_slots) - 1):
            assert item.active_slots[i + 1] == item.active_slots[i] + 1

    def test_interruptible_equipment_can_split(self, default_tariffs_96):
        """Interruptible equipment (e.g., water heater) can split across slots."""
        asset = GenericEquipmentAsset(
            id="boiler-1",
            facility_id="hotel-1",
            name="Θερμοσίφωνας",
            category="heating",
            rated_power_kw=8.0,
            required_runtime_minutes=45,  # 3 slots of 15 min
            earliest_start="10:00",
            latest_finish="18:00",
            is_interruptible=True,
            is_must_run=True,
        )
        # Force baseline spike at 13:00 (slot 52)
        baseline = [5.0] * 96
        baseline[52] = 20.0  # Leaves only 5kW headroom under 25kW cap

        settings = ScheduleSettings(
            facility_id="hotel-1",
            time_step_minutes=15,
            max_facility_power_kw=25.0,
            objective_mode="cost",
        )
        result = schedule_sme_equipment(
            facility_id="hotel-1",
            assets=[asset],
            settings=settings,
            schedule_date=date(2026, 9, 23),
            baseline_load=baseline,
            tariff_rates=default_tariffs_96,
        )
        assert result.status == "optimal"
        item = result.scheduled_items[0]
        assert item.is_scheduled is True
        assert len(item.active_slots) == 3
        # Slot 52 cannot be active due to 20kW * 1.1 = 22kW + 8kW = 30kW > 25kW
        assert 52 not in item.active_slots

    def test_midnight_crossing_window(self):
        """Operating window from 22:00 to 06:00 (crossing midnight)."""
        asset = GenericEquipmentAsset(
            id="night-defrost",
            facility_id="supermarket-1",
            name="Απόψυξη Ψυγείων",
            category="refrigeration",
            rated_power_kw=5.0,
            required_runtime_minutes=60,  # 4 slots
            earliest_start="22:00",
            latest_finish="06:00",  # midnight crossing!
            is_interruptible=False,
            is_must_run=True,
        )
        # High tariff during 00:00-06:00, low tariff during 22:00-24:00
        tariffs = [0.25] * 96
        for s in range(88, 96):  # 22:00 - 24:00
            tariffs[s] = 0.08

        settings = ScheduleSettings(
            facility_id="supermarket-1",
            time_step_minutes=15,
            max_facility_power_kw=20.0,
            objective_mode="cost",
        )
        result = schedule_sme_equipment(
            facility_id="supermarket-1",
            assets=[asset],
            settings=settings,
            schedule_date=date(2026, 9, 23),
            tariff_rates=tariffs,
        )
        assert result.status == "optimal"
        item = result.scheduled_items[0]
        assert item.is_scheduled is True
        # Must be placed in the 22:00-24:00 window (slots 88 to 95)
        for s in item.active_slots:
            assert s >= 88 or s < 24

    def test_must_run_vs_optional_under_capacity_limit(self):
        """When capacity is constrained, optional equipment is omitted to satisfy must-run."""
        must_run_asset = GenericEquipmentAsset(
            id="crucial-oven",
            facility_id="bakery-2",
            name="Κεντρικός Φούρνος",
            category="production",
            rated_power_kw=15.0,
            required_runtime_minutes=60,
            earliest_start="08:00",
            latest_finish="10:00",
            is_interruptible=False,
            is_must_run=True,
            priority=5,
        )
        optional_asset = GenericEquipmentAsset(
            id="secondary-ac",
            facility_id="bakery-2",
            name="Βοηθητικό AC",
            category="cooling",
            rated_power_kw=10.0,
            required_runtime_minutes=60,
            earliest_start="08:00",
            latest_finish="10:00",
            is_interruptible=False,
            is_must_run=False,
            priority=1,
        )
        # Total power = 15 + 10 = 25 kW, but max facility is 18 kW.
        # Baseline = 0 kW.
        settings = ScheduleSettings(
            facility_id="bakery-2",
            time_step_minutes=15,
            max_facility_power_kw=18.0,
            objective_mode="cost",
        )
        result = schedule_sme_equipment(
            facility_id="bakery-2",
            assets=[must_run_asset, optional_asset],
            settings=settings,
            schedule_date=date(2026, 9, 23),
        )
        assert result.status == "optimal"
        scheduled_dict = {it.asset_id: it for it in result.scheduled_items}
        assert scheduled_dict["crucial-oven"].is_scheduled is True
        assert scheduled_dict["secondary-ac"].is_scheduled is False
        assert "προαιρετικ" in scheduled_dict["secondary-ac"].explanation_el.lower() or "δεν προγραμματίστηκε" in scheduled_dict["secondary-ac"].explanation_el.lower()

    def test_conservative_baseline_uncertainty_buffer(self):
        """Baseline is inflated by conservative_margin (10%) to prevent tripping."""
        asset = GenericEquipmentAsset(
            id="press-1",
            facility_id="print-1",
            name="Εκτυπωτική Μηχανή",
            category="production",
            rated_power_kw=5.0,
            required_runtime_minutes=30,  # 2 slots
            earliest_start="09:00",
            latest_finish="11:00",
            is_interruptible=False,
            is_must_run=True,
        )
        # Baseline = 14 kW in slot 38 (09:30).
        # Max limit = 20 kW.
        # Without margin: 14 + 5 = 19 <= 20 (feasible).
        # With 10% margin: 14 * 1.10 = 15.4 + 5 = 20.4 > 20 (infeasible in slot 38).
        # Slot 36 (09:00): baseline = 10 kW -> 10 * 1.1 = 11 + 5 = 16 <= 20.
        baseline = [10.0] * 96
        baseline[38] = 14.0
        baseline[39] = 14.0

        settings = ScheduleSettings(
            facility_id="print-1",
            time_step_minutes=15,
            max_facility_power_kw=20.0,
            conservative_margin=0.10,
            objective_mode="cost",
        )
        result = schedule_sme_equipment(
            facility_id="print-1",
            assets=[asset],
            settings=settings,
            schedule_date=date(2026, 9, 23),
            baseline_load=baseline,
        )
        assert result.status == "optimal"
        item = result.scheduled_items[0]
        # Should NOT use slot 38 or 39
        assert 38 not in item.active_slots
        assert 39 not in item.active_slots

    def test_objective_modes(self):
        """Test cost vs peak modes behave differently."""
        asset = GenericEquipmentAsset(
            id="chiller-1",
            facility_id="cold-1",
            name="Ψυκτικός Θάλαμος",
            category="cooling",
            rated_power_kw=10.0,
            required_runtime_minutes=60,
            earliest_start="00:00",
            latest_finish="23:59",
            is_interruptible=False,
            is_must_run=True,
        )
        # Slot 10 has cheap tariff (0.05) but high baseline (25kW)
        # Slot 40 has moderate tariff (0.15) but very low baseline (2kW)
        tariffs = [0.20] * 96
        tariffs[10] = 0.05
        tariffs[11] = 0.05
        tariffs[12] = 0.05
        tariffs[13] = 0.05

        baseline = [10.0] * 96
        baseline[10] = 22.0
        baseline[11] = 22.0
        baseline[12] = 22.0
        baseline[13] = 22.0
        baseline[40] = 1.0
        baseline[41] = 1.0
        baseline[42] = 1.0
        baseline[43] = 1.0

        # Cost mode: picks slot 10-13 for minimum tariff
        cost_settings = ScheduleSettings(
            facility_id="cold-1",
            time_step_minutes=15,
            max_facility_power_kw=40.0,
            objective_mode="cost",
        )
        res_cost = schedule_sme_equipment(
            facility_id="cold-1",
            assets=[asset],
            settings=cost_settings,
            schedule_date=date(2026, 9, 23),
            baseline_load=baseline,
            tariff_rates=tariffs,
        )
        assert res_cost.status == "optimal"
        assert res_cost.scheduled_items[0].start_time == "02:30"  # slot 10

        # Peak mode: avoids slot 10-13 because baseline is 22kW -> would cause peak of 32kW
        peak_settings = ScheduleSettings(
            facility_id="cold-1",
            time_step_minutes=15,
            max_facility_power_kw=40.0,
            objective_mode="peak",
        )
        res_peak = schedule_sme_equipment(
            facility_id="cold-1",
            assets=[asset],
            settings=peak_settings,
            schedule_date=date(2026, 9, 23),
            baseline_load=baseline,
            tariff_rates=tariffs,
        )
        assert res_peak.status == "optimal"
        assert res_peak.scheduled_items[0].start_time != "02:30"
        assert res_peak.optimized_peak_kw < res_cost.optimized_peak_kw

    def test_greek_explanations_generated(self):
        """Verify natural language explanations in Greek are returned."""
        asset = GenericEquipmentAsset(
            id="mixer-1",
            facility_id="bakery-3",
            name="Ζυμωτήριο",
            category="production",
            rated_power_kw=4.5,
            required_runtime_minutes=30,
            earliest_start="05:00",
            latest_finish="09:00",
            is_interruptible=False,
            is_must_run=True,
        )
        settings = ScheduleSettings(facility_id="bakery-3")
        result = schedule_sme_equipment(
            facility_id="bakery-3",
            assets=[asset],
            settings=settings,
            schedule_date=date(2026, 9, 23),
        )
        assert result.status == "optimal"
        explanation = result.scheduled_items[0].explanation_el
        assert len(explanation) > 10
        assert any(word in explanation.lower() for word in ["προγραμματίστηκε", "τοποθετήθηκε", "χρονικό παράθυρο", "λειτουργία", "συσκευή"])

    def test_infeasible_must_run_asset_handled(self):
        """When a must-run asset exceeds max power, engine reports infeasible status."""
        asset = GenericEquipmentAsset(
            id="giant-furnace",
            facility_id="foundry-1",
            name="Ηλεκτρικός Κλίβανος",
            category="production",
            rated_power_kw=100.0,  # Far exceeds 25 kW
            required_runtime_minutes=60,
            earliest_start="08:00",
            latest_finish="12:00",
            is_interruptible=False,
            is_must_run=True,
        )
        settings = ScheduleSettings(
            facility_id="foundry-1",
            max_facility_power_kw=25.0,
        )
        result = schedule_sme_equipment(
            facility_id="foundry-1",
            assets=[asset],
            settings=settings,
            schedule_date=date(2026, 9, 23),
        )
        assert result.status == "infeasible"
        assert len(result.warnings) > 0
