from datetime import date

import pytest

from optimization_engine.scheduling_models import GenericEquipmentAsset, ScheduleSettings
from optimization_engine.scheduling_service import EquipmentSchedulingService


def make_asset(**changes):
    return GenericEquipmentAsset(**dict(
        dict(facility_id="audit", name="oven", rated_power_kw=5,
             required_runtime_minutes=15, earliest_start="08:00", latest_finish="10:00",
             active_weekdays=list(range(7))), **changes))


def service(assets, rates=None, **changes):
    settings = ScheduleSettings(facility_id="audit", time_step_minutes=15,
                                max_facility_power_kw=changes.pop("capacity", 100),
                                forecast_uncertainty_pct=0, objective_mode="cost")
    return EquipmentSchedulingService(settings, assets, schedule_date="2026-10-01",
                                      baseline_load_kw=[0] * 96,
                                      tariff_rates_eur_kwh=rates or [.2] * 96, **changes)


@pytest.mark.parametrize("earliest,latest", [("09:07", "09:45"), ("08:00", "09:00")])
def test_operating_window_contains_entire_slot(earliest, latest):
    rates = [1.] * 96
    rates[36] = .01
    item = service([make_asset(earliest_start=earliest, latest_finish=latest)], rates).solve().items[0]
    assert item.start_time >= earliest
    assert item.end_time <= latest


def test_optional_critical_asset_is_retained_first():
    assets = [make_asset(name="low", priority=1, must_run=False, latest_finish="08:15"),
              make_asset(name="critical", priority=5, must_run=False, latest_finish="08:15")]
    items = {i.asset_name: i for i in service(assets, capacity=5).solve().items}
    assert items["low"].is_omitted
    assert not items["critical"].is_omitted


def test_overnight_run_cannot_use_same_date_early_hours_as_tomorrow():
    rates = [1.] * 96
    for s in [94, 95, 0, 1]:
        rates[s] = .01
    with pytest.raises(ValueError):
        service([make_asset(earliest_start="23:30", latest_finish="00:30",
                            required_runtime_minutes=60)], rates).solve()


def test_overnight_window_can_finish_within_current_horizon():
    item = service([make_asset(earliest_start="23:30", latest_finish="00:30",
                               required_runtime_minutes=30)]).solve().items[0]
    assert item.scheduled_slots == [94, 95]
    assert (item.start_time, item.end_time) == ("23:30", "24:00")


def test_end_of_day_can_be_declared_exactly():
    item = service([make_asset(earliest_start="23:45", latest_finish="24:00")]).solve().items[0]
    assert item.end_time == "24:00"


@pytest.mark.parametrize("target", ["2026-03-29", "2026-10-25"])
def test_every_schedule_mode_rejects_dst_calendar_days(target):
    with pytest.raises(ValueError, match="DST"):
        EquipmentSchedulingService(ScheduleSettings(facility_id="audit"), [], schedule_date=target,
                                   baseline_load_kw=[0] * 24, tariff_rates_eur_kwh=[.2] * 24)


def test_partial_slot_runtime_reports_actual_scheduled_duration():
    item = service([make_asset(required_runtime_minutes=16)]).solve().items[0]
    assert item.duration_minutes == len(item.scheduled_slots) * 15 == 30


@pytest.mark.parametrize("field,value", [("baseline_load_kw", [3.] * 23),
                                         ("tariff_rates_eur_kwh", [float("nan")] * 24)])
def test_invalid_profiles_are_rejected(field, value):
    kwargs = dict(baseline_load_kw=[0.] * 96, tariff_rates_eur_kwh=[.2] * 96)
    kwargs[field] = value
    with pytest.raises(ValueError):
        EquipmentSchedulingService(ScheduleSettings(facility_id="audit"), [], **kwargs)


def test_negative_tariffs_are_preserved():
    rates = [.2] * 96
    rates[32] = -.1
    item = service([make_asset()], rates).solve().items[0]
    assert item.start_time == "08:00"
    assert service([], rates).tariff_eur_kwh[32] == -.1


def test_fallback_profiles_are_always_identified_as_demo():
    result = EquipmentSchedulingService(ScheduleSettings(facility_id="audit"), [make_asset()],
                                       schedule_date=date(2026, 10, 1).isoformat()).solve()
    assert result.is_demo
    assert result.input_source == "demo_profile"
    assert result.warnings
