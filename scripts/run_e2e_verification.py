#!/usr/bin/env python3
"""Dedicated standalone end-to-end integration verification script.

Demonstrates and validates the complete Greek Commercial Behind-the-Meter EMS
pipeline under 30 seconds:
1. System Health & Facility Configuration Seeding.
2. Normal Baseline Telemetry Ingestion (< threshold).
3. Peak-Window Breach & 3-Sample Debounce Filtering.
4. Real-Time Tariff Running Cost & Excess Surcharge Computation.
5. Proactive Greek Telegram Alert Formatting & Delivery.
6. Throttling & 30-Minute Cooldown Duplicate Suppression.
7. 10% Release Hysteresis Load Normalization & Recovery Notification.
8. Facility Status, Tariff, and Cumulative Cost Reporting Endpoints.
9. System Execution Timing Benchmark (< 30.0 seconds).

Usage:
    python scripts/run_e2e_verification.py [--url http://localhost:8000] [--profile bakery|cold_storage|boutique_hotel|hotel]
"""

from __future__ import annotations

import argparse
import os
import contextlib
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path for direct script execution
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure stdout uses UTF-8 encoding across Windows/Linux for Greek characters and emojis
if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        sys.stdout.reconfigure(encoding="utf-8")

PROFILE_ALIASES: dict[str, str] = {
    "bakery": "bakery",
    "commercial_bakery": "bakery",
    "cold_storage": "cold_storage",
    "refrigeration": "cold_storage",
    "boutique_hotel": "boutique_hotel",
    "hotel": "boutique_hotel",
}

PROFILE_CONFIGS: dict[str, dict[str, Any]] = {
    "bakery": {
        "facility_id": "bakery-central-athens",
        "facility_name": "Bakery Central Athens",
        "device_id": "esp32-bakery-001",
        "contract_type": "Γ22",
        "chat_id": 999111222,
        "threshold_kw": 22.0,
        "baseline_kw": 14.5,
        "breach_kw": 32.5,
        "recovery_kw": 14.0,
        "expected_excess_kw": 10.5,
        "hysteresis_limit_kw": 19.8,
        "advice_keywords": ["φούρνο", "ψήσιμο"],
    },
    "cold_storage": {
        "facility_id": "cold-storage-piraeus",
        "facility_name": "Cold Storage Piraeus",
        "device_id": "esp32-cold-001",
        "contract_type": "Γ22",
        "chat_id": 999222333,
        "threshold_kw": 30.0,
        "baseline_kw": 18.0,
        "breach_kw": 38.0,
        "recovery_kw": 16.0,
        "expected_excess_kw": 8.0,
        "hysteresis_limit_kw": 27.0,
        "advice_keywords": ["πόρτες", "απόψυξης"],
    },
    "boutique_hotel": {
        "facility_id": "hotel-plaka-boutique",
        "facility_name": "Hotel Plaka Boutique",
        "device_id": "esp32-hotel-001",
        "contract_type": "Γ21",
        "chat_id": 999333444,
        "threshold_kw": 25.0,
        "baseline_kw": 16.0,
        "breach_kw": 34.0,
        "recovery_kw": 15.0,
        "expected_excess_kw": 9.0,
        "hysteresis_limit_kw": 22.5,
        "advice_keywords": ["VRV", "κλιματισμού", "πλυντήρια"],
    },
}

# Alias resolution in PROFILE_CONFIGS for direct index lookup convenience
for _alias, _target in list(PROFILE_ALIASES.items()):
    if _alias not in PROFILE_CONFIGS and _target in PROFILE_CONFIGS:
        PROFILE_CONFIGS[_alias] = PROFILE_CONFIGS[_target]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Greek Commercial Behind-the-Meter EMS: Standalone E2E Verification Runner"
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Target live backend URL (e.g. http://localhost:8000). If omitted, runs in-process.",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="bakery",
        choices=["bakery", "cold_storage", "boutique_hotel", "hotel", "commercial_bakery", "refrigeration"],
        help=(
            "Commercial SMB load profile to exercise (default: bakery; "
            "choices: bakery, cold_storage, boutique_hotel, hotel, commercial_bakery, refrigeration)."
        ),
    )
    return parser.parse_args()


class VerificationReporter:
    """Formats and prints verification checks in a structured tabular report."""

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.start_time = time.perf_counter()

    def record(self, check_id: int, name: str, passed: bool, elapsed_ms: float, details: str = "") -> None:
        self.results.append({
            "id": check_id,
            "name": name,
            "passed": passed,
            "elapsed_ms": elapsed_ms,
            "details": details,
        })
        status_str = "[PASS]" if passed else "[FAIL]"
        print(f"[{check_id}/9] {status_str} | {name:<46} | {elapsed_ms:6.1f} ms | {details}")

    def total_elapsed_s(self) -> float:
        return time.perf_counter() - self.start_time

    def print_summary(self) -> bool:
        total_s = self.total_elapsed_s()
        all_passed = all(r["passed"] for r in self.results) and len(self.results) == 9

        print("\n" + "=" * 80)
        print(f"{'E2E VERIFICATION PIPELINE SUMMARY':^80}")
        print("=" * 80)
        print(f"{'Check':<6} | {'Status':<8} | {'Latency':<10} | {'Description'}")
        print("-" * 80)
        for r in self.results:
            st = "PASS" if r["passed"] else "FAIL"
            print(f"#{r['id']:<5} | {st:<8} | {r['elapsed_ms']:7.1f} ms | {r['name']}")
        print("-" * 80)
        print(f"Total Execution Time: {total_s:.3f} seconds (Benchmark Requirement: < 30.000s)")
        print(f"Total Checks: {len(self.results)}/9 Passed: {sum(1 for r in self.results if r['passed'])}")
        print("=" * 80)

        if all_passed and total_s < 30.0:
            print("ALL VERIFICATION CRITERIA MET (Exit Code 0)\n")
            return True
        else:
            print("VERIFICATION FAILED (Exit Code 1)\n")
            return False


def run_verification() -> int:
    args = parse_arguments()
    reporter = VerificationReporter()

    raw_profile = args.profile.strip().lower().replace("-", "_")
    canonical_profile = PROFILE_ALIASES.get(raw_profile, raw_profile)
    if canonical_profile not in PROFILE_CONFIGS:
        raise ValueError(
            f"Unsupported profile '{args.profile}'. Must resolve to one of: "
            f"{list(PROFILE_CONFIGS.keys())}"
        )
    cfg = PROFILE_CONFIGS[canonical_profile]

    print("=" * 80)
    print("Greek Commercial Behind-the-Meter EMS: Standalone E2E Verification Runner")
    print(f"Mode: {'External Server (' + args.url + ')' if args.url else 'In-Process TestClient'}")
    print(f"Target Commercial Profile: {canonical_profile.upper()} (input: '{args.profile}')")
    print(f"Facility: {cfg['facility_name']} ({cfg['facility_id']}) | Threshold: {cfg['threshold_kw']:.1f} kW")
    print("=" * 80)

    # Initialize client & dependencies
    mock_bot = None
    headers = {"X-API-Key": os.environ["API_KEY"]} if os.getenv("API_KEY") else {}
    if args.url:
        import httpx
        client = httpx.Client(base_url=args.url.rstrip("/"), timeout=15.0, headers=headers)
    else:
        from fastapi.testclient import TestClient

        from backend.database.sqlite_store import get_store
        from backend.main import create_app
        from backend.routes.telemetry import AlertDispatcher
        from bot.telegram_client import MockTelegramClient

        temp_dir = tempfile.mkdtemp(prefix="ems_e2e_")
        db_path = str(Path(temp_dir) / "e2e_verify.db")
        app = create_app(db_path=db_path)
        store = get_store(db_path)
        store.init_db()
        store.seed_default_facilities()

        mock_bot = MockTelegramClient()
        dispatcher = AlertDispatcher(telegram_client=mock_bot)
        app.state.dispatcher = dispatcher
        client = TestClient(app, headers=headers)

    try:
        from simulator.generator import TelemetryGenerator

        # ----------------------------------------------------------------------
        # Check 1: System Health & Facility Configuration Seeding
        # ----------------------------------------------------------------------
        t0 = time.perf_counter()
        h_res = client.get("/health")
        f_res = client.get("/api/v1/facilities")
        t_elapsed = (time.perf_counter() - t0) * 1000.0

        c1_ok = False
        details1 = ""
        if h_res.status_code == 200 and f_res.status_code == 200:
            facs = f_res.json()
            f_ids = [f["facility_id"] for f in facs]
            if cfg["facility_id"] in f_ids and len(facs) >= 3:
                c1_ok = True
                details1 = f"{len(facs)} default facilities verified (including '{cfg['facility_id']}')"
            else:
                details1 = f"Target facility '{cfg['facility_id']}' missing from seeded IDs: {f_ids}"
        else:
            details1 = f"HTTP error health={h_res.status_code}, facilities={f_res.status_code}"
        reporter.record(1, "System Health & Facility Seeding", c1_ok, t_elapsed, details1)

        # ----------------------------------------------------------------------
        # Check 2: Normal Baseline Telemetry Ingestion (< threshold)
        # ----------------------------------------------------------------------
        # Ensure reference weekday during summer peak window (15:00 EEST = 12:00 UTC)
        now_dt = datetime.now(timezone.utc)
        day_offset = (now_dt.weekday() - 2) if now_dt.weekday() >= 5 else 0
        ref_date = (now_dt - timedelta(days=day_offset)).date()
        peak_start_time = datetime(ref_date.year, ref_date.month, ref_date.day, 12, 0, 0, tzinfo=timezone.utc)

        gen = TelemetryGenerator(
            profile=canonical_profile,
            facility_id=cfg["facility_id"],
            device_id=cfg["device_id"],
            start_time=peak_start_time,
            seed=42,
        )

        t0 = time.perf_counter()
        # Normal reading: baseline_kw (< threshold_kw)
        gen.set_breach_mode(True, breach_power_kw=cfg["baseline_kw"])
        p_normal = gen.step(step_seconds=10.0)
        res2 = client.post("/api/v1/telemetry", json=p_normal.model_dump(mode="json"))
        t_elapsed = (time.perf_counter() - t0) * 1000.0

        c2_ok = False
        details2 = ""
        if res2.status_code == 200:
            d2 = res2.json()
            if (
                d2.get("status") == "success"
                and d2.get("facility_id") == cfg["facility_id"]
                and d2.get("alert_triggered") is False
                and d2.get("running_cost_eur_per_h", 0) > 0
                and d2.get("is_peak_window") is True
                and d2.get("is_excess_breach") is False
            ):
                c2_ok = True
                details2 = f"P={d2['total_active_power_kw']:.1f} kW, Cost={d2['running_cost_eur_per_h']:.2f} €/h"
            else:
                details2 = f"Unexpected response fields: {d2}"
        else:
            details2 = f"HTTP {res2.status_code}: {res2.text}"
        reporter.record(2, "Normal Baseline Ingestion (< Threshold)", c2_ok, t_elapsed, details2)

        # ----------------------------------------------------------------------
        # Check 3: Peak-Window Breach & 3-Sample Debounce Filtering
        # ----------------------------------------------------------------------
        t0 = time.perf_counter()
        # Surge load to breach_kw (> threshold_kw)
        gen.set_breach_mode(True, breach_power_kw=cfg["breach_kw"])

        # Sample 1: Debounce 1/3 (alert = False)
        p_b1 = gen.step(step_seconds=10.0)
        r_b1 = client.post("/api/v1/telemetry", json=p_b1.model_dump(mode="json")).json()

        # Sample 2: Debounce 2/3 (alert = False)
        p_b2 = gen.step(step_seconds=10.0)
        r_b2 = client.post("/api/v1/telemetry", json=p_b2.model_dump(mode="json")).json()

        # Sample 3: Debounce 3/3 -> TRIGGERED & DISPATCHED!
        p_b3 = gen.step(step_seconds=10.0)
        r_b3 = client.post("/api/v1/telemetry", json=p_b3.model_dump(mode="json")).json()
        t_elapsed = (time.perf_counter() - t0) * 1000.0

        c3_ok = (
            r_b1.get("alert_triggered") is False
            and r_b2.get("alert_triggered") is False
            and r_b3.get("alert_triggered") is True
            and r_b3.get("alert_dispatched") is True
        )
        details3 = "Filter verified: s1=False, s2=False, s3=TRIGGERED"
        reporter.record(3, "3-Sample Debounce Breach Filtering", c3_ok, t_elapsed, details3)

        # ----------------------------------------------------------------------
        # Check 4: Real-Time Tariff Cost & Penalty Validation
        # ----------------------------------------------------------------------
        t0 = time.perf_counter()
        is_peak = r_b3.get("is_peak_window") is True
        is_excess = r_b3.get("is_excess_breach") is True
        excess_kw = r_b3.get("excess_power_kw", 0.0)
        penalty_eur = r_b3.get("projected_excess_penalty_eur", 0.0)
        t_elapsed = (time.perf_counter() - t0) * 1000.0

        expected_excess = cfg["expected_excess_kw"]
        c4_ok = is_peak and is_excess and abs(excess_kw - expected_excess) <= 0.1 and penalty_eur > 0.0
        details4 = f"Excess={excess_kw:.1f} kW (expected {expected_excess:.1f} kW), Penalty={penalty_eur:.2f} €"
        reporter.record(4, "Tariff Cost & Peak Penalty Calculation", c4_ok, t_elapsed, details4)

        # ----------------------------------------------------------------------
        # Check 5: Greek Telegram Alert Notification Format
        # ----------------------------------------------------------------------
        t0 = time.perf_counter()
        alert_event_data = r_b3.get("alert_event") or {}
        alert_msg = alert_event_data.get("message", "")
        chat_ok = True
        if mock_bot and mock_bot.message_count() > 0:
            last_msg = mock_bot.get_last_message()
            alert_msg = last_msg["text"]
            chat_ok = (last_msg.get("chat_id") == cfg["chat_id"])

        expected_breach_str = f"{cfg['breach_kw']:.1f} kW"
        expected_thresh_str = f"{cfg['threshold_kw']:.1f} kW"
        facility_matched = (cfg["facility_name"] in alert_msg) or (cfg["facility_id"] in alert_msg)
        advice_matched = any(kw in alert_msg for kw in cfg["advice_keywords"])

        c5_ok = (
            chat_ok
            and "ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ" in alert_msg
            and expected_breach_str in alert_msg
            and expected_thresh_str in alert_msg
            and "14:00–17:00" in alert_msg
            and "€" in alert_msg
            and facility_matched
            and advice_matched
        )
        t_elapsed = (time.perf_counter() - t0) * 1000.0
        details5 = f"Greek header, facility, {expected_breach_str} > {expected_thresh_str} & advice verified"
        reporter.record(5, "Greek Telegram Notification Formatting", c5_ok, t_elapsed, details5)

        # ----------------------------------------------------------------------
        # Check 6: Throttling & 30-Minute Cooldown Duplicate Suppression
        # ----------------------------------------------------------------------
        t0 = time.perf_counter()
        # 4th reading while still at breach load (within cooldown)
        p_b4 = gen.step(step_seconds=10.0)
        r_b4 = client.post("/api/v1/telemetry", json=p_b4.model_dump(mode="json")).json()
        t_elapsed = (time.perf_counter() - t0) * 1000.0

        bot_msg_count_before_hysteresis = mock_bot.message_count() if mock_bot else 1
        c6_ok = r_b4.get("alert_triggered") is False and (bot_msg_count_before_hysteresis == 1)
        details6 = "Duplicate breach suppressed during 1800s cooldown"
        reporter.record(6, "Alert Throttling & Cooldown Suppression", c6_ok, t_elapsed, details6)

        # ----------------------------------------------------------------------
        # Check 7: 10% Release Hysteresis Recovery Notification
        # ----------------------------------------------------------------------
        t0 = time.perf_counter()
        # Load drops below 90% threshold (threshold_kw * 0.90) -> recovery_kw
        gen.set_breach_mode(True, breach_power_kw=cfg["recovery_kw"])
        p_rec = gen.step(step_seconds=10.0)
        r_rec = client.post("/api/v1/telemetry", json=p_rec.model_dump(mode="json")).json()
        t_elapsed = (time.perf_counter() - t0) * 1000.0

        rec_msg = ""
        if r_rec.get("alert_event"):
            rec_msg = r_rec["alert_event"].get("message", "")
        if mock_bot and mock_bot.message_count() >= 2:
            rec_msg = mock_bot.get_last_message()["text"]

        expected_hyst_limit_str = f"{cfg['hysteresis_limit_kw']:.1f} kW"
        c7_ok = (
            r_rec.get("alert_triggered") is True
            and "ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ" in rec_msg
            and expected_hyst_limit_str in rec_msg
            and (mock_bot.message_count() == 2 if mock_bot else True)
        )
        details7 = f"Load dropped <= {expected_hyst_limit_str} -> Recovery alert dispatched"
        reporter.record(7, "10% Release Hysteresis Recovery", c7_ok, t_elapsed, details7)

        # ----------------------------------------------------------------------
        # Check 8: Facility Status & Cost-Today Query Endpoints
        # ----------------------------------------------------------------------
        target_date_str = peak_start_time.strftime("%Y-%m-%d")
        st_res = client.get(f"/api/v1/facilities/{cfg['facility_id']}/status")
        ct_res = client.get(f"/api/v1/facilities/{cfg['facility_id']}/cost-today?date={target_date_str}")
        tf_res = client.get(f"/api/v1/facilities/{cfg['facility_id']}/tariff")
        t_elapsed = (time.perf_counter() - t0) * 1000.0

        c8_ok = False
        details8 = ""
        if st_res.status_code == 200 and ct_res.status_code == 200:
            st_data = st_res.json()
            ct_data = ct_res.json()
            tf_data = tf_res.json() if tf_res.status_code == 200 else {}
            if (
                st_data.get("facility_id") == cfg["facility_id"]
                and st_data.get("latest_telemetry") is not None
                and ct_data.get("total_spend_eur", 0) >= 0.0
                and ct_data.get("total_kwh", 0) > 0.0
                and (tf_data.get("peak_threshold_kw") == cfg["threshold_kw"] if tf_res.status_code == 200 else True)
            ):
                c8_ok = True
                details8 = f"Total kWh={ct_data['total_kwh']:.2f}, Spend={ct_data['total_spend_eur']:.2f} €"
            else:
                details8 = f"Invalid facility reporting payload: {st_data}"
        else:
            details8 = f"HTTP error status={st_res.status_code}, cost-today={ct_res.status_code}"
        reporter.record(8, "Facility Status & Cost Reporting APIs", c8_ok, t_elapsed, details8)

        # ----------------------------------------------------------------------
        # Check 9: Benchmark Timing Requirement (< 30.0s)
        # ----------------------------------------------------------------------
        total_time_s = reporter.total_elapsed_s()
        c9_ok = total_time_s < 30.0
        reporter.record(9, "Execution Benchmark (< 30.0s)", c9_ok, total_time_s * 1000.0, f"Total: {total_time_s:.2f}s")

    finally:
        if hasattr(client, "close"):
            client.close()

    success = reporter.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(run_verification())
