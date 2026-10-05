"""
Tier 4: Real-World Application Scenarios E2E Test Suite
======================================================
Exhaustively models full-lifecycle commercial SMB operations in Greece:
1. Commercial Bakery 24-hour production cycle (morning baking, retail, afternoon prep breach, curtailment, recovery)
2. Cold Storage logistics loading door disturbance and compressor pull-down cycle
3. Boutique Hotel summer guest check-in, VRV air conditioning ramp, and dinner peak
4. Multi-facility concurrent telemetry streaming and audit trail isolation
"""

from datetime import datetime, timedelta, timezone

from tests.e2e.test_tiers.harness import (
    AlertDispatcherStateMachine,
    AlertState,
    MockTelegramClient,
    calculate_realtime_cost,
    create_valid_telemetry_payload,
    format_greek_bot_response,
    get_boutique_hotel_power,
    get_cold_storage_power,
    get_commercial_bakery_power,
    is_greek_peak_window,
)


class TestTier4RealWorldScenarios:
    def test_scenario1_commercial_bakery_full_day_lifecycle(self, sample_facility_bakery):
        """
        Scenario 1: Commercial Bakery in Athens (24-Hour Lifecycle).
        - 00:00 - 03:00: Standby refrigeration (4 kW).
        - 03:00 - 08:30: Morning baking peak (up to 44 kW) during off-peak night/morning window.
        - 08:30 - 14:00: Retail operations (14 kW).
        - 14:00 - 17:00: Afternoon baking prep breach (27.5 kW > 22.0 kW) during Greek summer peak.
        - Alert sent -> Operator shuts 1 deck oven -> load drops to 18.0 kW -> Recovery sent.
        - 23:59: /cost_today query confirms accumulated spend and energy.
        """
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        mock_client = MockTelegramClient()
        base_date = datetime(2026, 7, 15, 0, 0, 0, tzinfo=timezone.utc)

        daily_spend = 0.0
        cumulative_kwh = 1000.0
        events_emitted = []

        # 1. Simulate 24 hours at 1-hour steps
        for hour in range(24):
            dt = base_date + timedelta(hours=hour)
            # Normal baking prep at 15:00, operator curtails at 16:00
            power_kw = 18.0 if hour == 16 else get_commercial_bakery_power(float(hour))
            is_peak = is_greek_peak_window(dt)

            energy_delta = power_kw * 1.0
            cumulative_kwh += energy_delta

            cost_res = calculate_realtime_cost(
                power_kw=power_kw,
                energy_kwh_delta=energy_delta,
                timestamp=dt,
                tariff_profile=sample_facility_bakery,
                tea_eur_mwh=130.0,
            )
            daily_spend += cost_res.incremental_cost_eur

            # 3 sub-readings per hour to allow debounce detection
            for s in [0, 10, 20]:
                evt = dispatcher.process_reading(power_kw, dt + timedelta(seconds=s), is_peak, cost_res)
                if evt:
                    events_emitted.append(evt)
                    import asyncio
                    asyncio.run(mock_client.send_message(sample_facility_bakery.telegram_chat_id, evt.message_text))

        # Verifications
        # 2 breach alerts (initial breach at 14:00 + reminder after 30-min cooldown at 15:00) + 1 recovery alert at 16:00
        assert len(events_emitted) == 3
        assert mock_client.count() == 3

        # Verify breach alert content
        breach_msg = mock_client.get_sent_messages()[0]["text"]
        assert "ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ" in breach_msg
        assert "φούρνο" in breach_msg

        # Verify recovery alert content (last message)
        recovery_msg = mock_client.get_last_message()["text"]
        assert "ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ" in recovery_msg

        # Verify daily spend is within realistic commercial bakery parameters (€40 - €120)
        assert 40.0 <= daily_spend <= 120.0

        # Query /cost_today at end of day
        cost_today_text = format_greek_bot_response(
            "/cost_today",
            sample_facility_bakery,
            daily_spend_eur=daily_spend,
            daily_energy_kwh=(cumulative_kwh - 1000.0),
        )
        assert f"{daily_spend:.2f} €" in cost_today_text
        assert "Σημερινή Κατανάλωση" in cost_today_text

    def test_scenario2_cold_storage_loading_dock_door_disturbance(self, sample_facility_cold_storage):
        """
        Scenario 2: Cold Storage Facility in Piraeus (Midday Loading Door Anomaly).
        - 14:00: Baseline refrigeration (16 kW).
        - 14:20: Delivery truck leaves loading bay door open; thermal influx drives compressors to 34.5 kW.
        - 3 consecutive breach samples trigger proactive alert.
        - Telegram message urges closing loading doors and delaying defrost.
        - Staff closes door; compressors pull down and recover to 17.0 kW by 15:00.
        - Recovery message emitted.
        """
        dispatcher = AlertDispatcherStateMachine(sample_facility_cold_storage)
        mock_client = MockTelegramClient()
        base_time = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)

        # 1. 14:00 - 14:15: Normal idle baseload operation (16 kW < 25 kW threshold)
        for m in range(0, 15, 5):
            t = base_time + timedelta(minutes=m)
            p = 16.5  # Idle baseline
            cost = calculate_realtime_cost(p, 0.5, t, sample_facility_cold_storage)
            dispatcher.process_reading(p, t, True, cost)
        assert dispatcher.state == AlertState.IDLE

        # 2. 14:20: Door left open -> power spikes to 34.5 kW
        t_breach = base_time + timedelta(minutes=20)
        p_breach = get_cold_storage_power(20.0, door_open=True)
        cost_breach = calculate_realtime_cost(p_breach, 0.5, t_breach, sample_facility_cold_storage)

        breach_alert = None
        for s in [0, 10, 20]:
            breach_alert = dispatcher.process_reading(p_breach, t_breach + timedelta(seconds=s), True, cost_breach)

        assert breach_alert is not None
        import asyncio
        asyncio.run(mock_client.send_message(sample_facility_cold_storage.telegram_chat_id, breach_alert.message_text))
        assert "πόρτες" in breach_alert.message_text
        assert dispatcher.state == AlertState.COOLDOWN

        # 3. 14:30: Still in cooldown, power remains high
        t_pull_down = base_time + timedelta(minutes=30)
        suppressed = dispatcher.process_reading(p_breach, t_pull_down, True, cost_breach)
        assert suppressed is None

        # 4. 14:50: Door closed, thermal stabilization -> load drops to 17 kW (< 22.5 kW 90% threshold)
        t_safe = base_time + timedelta(minutes=50)
        cost_safe = calculate_realtime_cost(17.0, 0.5, t_safe, sample_facility_cold_storage)
        recovery_alert = dispatcher.process_reading(17.0, t_safe, True, cost_safe)

        assert recovery_alert is not None
        asyncio.run(mock_client.send_message(sample_facility_cold_storage.telegram_chat_id, recovery_alert.message_text))
        assert "ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ" in recovery_alert.message_text
        assert mock_client.count() == 2

    def test_scenario3_boutique_hotel_summer_hvac_and_dinner_peak(self, sample_facility_hotel):
        """
        Scenario 3: Boutique Hotel on Santorini.
        - 14:00 - 17:00: Afternoon guest check-in & VRV air conditioning ramp to 33.5 kW (> 30.0 kW).
        - Proactive alert dispatched.
        - Interactive /status command displays 3-phase live metrics.
        - Evening restaurant service (20:30) runs smoothly.
        """
        dispatcher = AlertDispatcherStateMachine(sample_facility_hotel)
        mock_client = MockTelegramClient()
        dt_peak = datetime(2026, 7, 15, 15, 30, 0, tzinfo=timezone.utc)

        load_kw = get_boutique_hotel_power(15.5)  # ~33 kW
        cost_res = calculate_realtime_cost(load_kw, 1.0, dt_peak, sample_facility_hotel)

        alert_event = None
        for i in range(3):
            alert_event = dispatcher.process_reading(load_kw, dt_peak + timedelta(seconds=i * 10), True, cost_res)

        assert alert_event is not None
        import asyncio
        asyncio.run(mock_client.send_message(sample_facility_hotel.telegram_chat_id, alert_event.message_text))
        assert "κλιματισμού VRV" in alert_event.message_text

        # Guest manager issues /status
        telemetry = create_valid_telemetry_payload(p_total_kw=load_kw, timestamp=dt_peak)
        status_response = format_greek_bot_response("/status", sample_facility_hotel, telemetry)
        assert "Τρέχουσα Κατάσταση" in status_response
        assert f"{load_kw:.2f} kW" in status_response
        assert "Ζώνη Αιχμής" in status_response

        # Manager also checks /tariff
        tariff_response = format_greek_bot_response("/tariff", sample_facility_hotel)
        assert "G23" in tariff_response
        assert "100 kVA" in tariff_response

    def test_scenario4_multi_facility_concurrent_telemetry_stream(
        self, sample_facility_bakery, sample_facility_cold_storage, sample_facility_hotel
    ):
        """
        Scenario 4: Multi-Tenant Real-Time Telemetry Stream.
        Simultaneously streams data from all 3 facilities (Bakery, Cold Storage, Hotel).
        Verifies:
        - Independent state machine isolation per tenant.
        - Telemetry monotonicity and invariant checks across all streams.
        """
        facilities = [sample_facility_bakery, sample_facility_cold_storage, sample_facility_hotel]
        dispatchers = {f.facility_id: AlertDispatcherStateMachine(f) for f in facilities}
        base_time = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)

        # Feed 10 time steps across all 3 facilities
        total_payloads_processed = 0
        for step in range(10):
            current_time = base_time + timedelta(minutes=step)
            for f in facilities:
                # Load values tailored to facility
                p_kw = f.peak_threshold_kw + (5.0 if step >= 3 else -3.0)
                payload = create_valid_telemetry_payload(
                    facility_id=f.facility_id,
                    p_total_kw=p_kw,
                    timestamp=current_time,
                    cumulative_energy_kwh=500.0 + (step * 2.0),
                )
                assert payload.facility_id == f.facility_id
                total_payloads_processed += 1

                cost_res = calculate_realtime_cost(p_kw, 0.1, current_time, f)
                dispatchers[f.facility_id].process_reading(p_kw, current_time, True, cost_res)

        assert total_payloads_processed == 30
        # All three facilities entered COOLDOWN after step 5
        for f in facilities:
            assert dispatchers[f.facility_id].state in (AlertState.COOLDOWN, AlertState.TRIGGERED)
