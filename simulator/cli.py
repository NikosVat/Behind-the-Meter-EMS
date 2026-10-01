"""Commercial Telemetry Simulator CLI.

Standalone simulation command-line interface for Behind-the-Meter EMS testing:
- Profiles: bakery, cold_storage, boutique_hotel
- Time compression: --speed (e.g., 1x, 60x, 3600x)
- Configurable duration: --duration-hours
- Network streaming: --url
- Physical realism: --noise, realistic 3-phase invariants
- Automated test verification: --trigger-breach, --dry-run
"""

import argparse
import os
import sys
import time
from collections.abc import Callable

import httpx

from backend.models.telemetry import TelemetryPayload
from simulator.generator import TelemetryGenerator


def parse_speed(speed_val: str | float) -> float:
    """Parse speed argument string or float into a positive speed factor.

    Supports formats: '1x', '60x', '3600x', '60', 60.0.
    """
    if isinstance(speed_val, (int, float)):
        return max(0.001, float(speed_val))

    s = str(speed_val).strip().lower()
    if s.endswith("x"):
        s = s[:-1].strip()

    try:
        val = float(s)
        return max(0.001, val)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid speed factor '{speed_val}'. Expected format: 1x, 60x, 3600x.")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser for the telemetry simulator."""
    parser = argparse.ArgumentParser(
        prog="simulator",
        description="Standalone Commercial Telemetry Simulator CLI for Greek EMS.",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="bakery",
        choices=["bakery", "cold_storage", "boutique_hotel", "commercial_bakery", "refrigeration", "hotel"],
        help="Commercial load profile to emulate (default: bakery).",
    )
    parser.add_argument(
        "--speed",
        type=parse_speed,
        default=1.0,
        help="Time compression factor, e.g. 1x, 60x, 3600x (default: 1.0).",
    )
    parser.add_argument(
        "--duration-hours",
        type=float,
        default=24.0,
        help="Simulation duration in virtual hours (default: 24.0).",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=10.0,
        help="Sampling interval in virtual seconds (default: 10.0).",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Target backend ingestion endpoint URL (e.g. http://localhost:8000/api/v1/telemetry).",
    )
    parser.add_argument(
        "--device-id",
        type=str,
        default=None,
        help="Custom device ID (default: profile-based esp32-ems-XXX).",
    )
    parser.add_argument(
        "--facility-id",
        type=str,
        default=None,
        help="Custom facility ID (default: profile default facility).",
    )
    parser.add_argument(
        "--noise",
        type=float,
        default=0.0,
        help="Gaussian load noise standard deviation in kW (default: 0.0).",
    )
    parser.add_argument(
        "--trigger-breach",
        action="store_true",
        default=False,
        help="Automated peak breach injection forcing high-rate breach for sub-30s verification.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run simulation and log readings without sending HTTP requests to --url.",
    )
    parser.add_argument(
        "--max-readings",
        type=int,
        default=None,
        help="Optional maximum number of readings to emit before stopping.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for deterministic execution.",
    )
    return parser


def format_reading_summary(payload: TelemetryPayload) -> str:
    """Format single-line telemetry summary for terminal output."""
    p1 = payload.phases["L1"]
    p2 = payload.phases["L2"]
    p3 = payload.phases["L3"]
    ts_str = payload.timestamp.strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"[{ts_str}] {payload.facility_id} | Total: {payload.total_active_power_kw:6.2f} kW "
        f"({payload.total_apparent_power_kva:6.2f} kVA, cos_phi={payload.system_power_factor:.2f}) | "
        f"L1: {p1.active_power_kw:5.2f}kW/{p1.voltage_v:5.1f}V | "
        f"L2: {p2.active_power_kw:5.2f}kW/{p2.voltage_v:5.1f}V | "
        f"L3: {p3.active_power_kw:5.2f}kW/{p3.voltage_v:5.1f}V | "
        f"Cumul: {payload.cumulative_energy_kwh:8.2f} kWh"
    )


def run_simulation(
    profile: str = "bakery",
    speed: float = 1.0,
    duration_hours: float = 24.0,
    interval_seconds: float = 10.0,
    url: str | None = None,
    device_id: str | None = None,
    facility_id: str | None = None,
    noise: float = 0.0,
    trigger_breach: bool = False,
    dry_run: bool = False,
    max_readings: int | None = None,
    seed: int | None = None,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> list[TelemetryPayload]:
    """Execute the simulation loop and emit or send telemetry readings.

    Returns:
        List of generated TelemetryPayload instances.
    """
    if duration_hours <= 0.0:
        raise ValueError(f"duration_hours must be positive, got {duration_hours}")
    if interval_seconds <= 0.0:
        raise ValueError(f"interval_seconds must be positive, got {interval_seconds}")

    logger = log_fn if log_fn is not None else print
    sleeper = sleep_fn if sleep_fn is not None else time.sleep

    generator = TelemetryGenerator(
        profile=profile,
        device_id=device_id,
        facility_id=facility_id,
        noise_kw=noise,
        trigger_breach=trigger_breach,
        seed=seed,
    )

    total_virtual_seconds = duration_hours * 3600.0
    effective_url = None if dry_run else url

    # Determine real-world sleep delay per reading
    # Wall-clock sleep = virtual_interval / speed
    wall_delay = interval_seconds / speed if speed > 0.0 else 0.0

    logger(
        f"Starting simulation: profile='{generator.profile.name}', facility='{generator.facility_id}', "
        f"speed={speed}x, duration={duration_hours}h, interval={interval_seconds}s, "
        f"noise={noise} kW, breach={'YES' if trigger_breach else 'NO'}, "
        f"mode={'DRY RUN' if effective_url is None else f'STREAMING -> {effective_url}'}"
    )

    emitted_readings: list[TelemetryPayload] = []
    virtual_elapsed = 0.0
    count = 0

    own_client = False
    client = http_client
    request_headers = {"Content-Type": "application/json"}
    if os.getenv("API_KEY"):
        request_headers["X-API-Key"] = os.environ["API_KEY"]
    if effective_url is not None and client is None:
        client = httpx.Client(timeout=5.0)
        own_client = True

    try:
        while virtual_elapsed < total_virtual_seconds:
            if max_readings is not None and count >= max_readings:
                break

            payload = generator.step(step_seconds=interval_seconds)
            emitted_readings.append(payload)
            count += 1
            virtual_elapsed += interval_seconds

            summary = format_reading_summary(payload)
            logger(summary)

            if effective_url is not None and client is not None:
                try:
                    payload_json = payload.model_dump_json()
                    response = client.post(
                        effective_url,
                        content=payload_json,
                        headers=request_headers,
                    )
                    if response.status_code not in (200, 201, 202):
                        logger(f"Warning: HTTP {response.status_code} response from {effective_url}")
                except (httpx.HTTPError, OSError) as ex:
                    logger(f"Network error sending telemetry to {effective_url}: {ex}")

            # Pause for wall-clock delay unless fast-forwarded (speed >= 3600 or zero delay)
            if wall_delay > 0.001 and (max_readings is None or wall_delay < 5.0):
                sleeper(wall_delay)

    finally:
        if own_client and client is not None:
            client.close()

    logger(f"Simulation completed. Total readings generated: {len(emitted_readings)}")
    return emitted_readings


def main(args: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_arg_parser()
    parsed = parser.parse_args(args)

    try:
        run_simulation(
            profile=parsed.profile,
            speed=parsed.speed,
            duration_hours=parsed.duration_hours,
            interval_seconds=parsed.interval_seconds,
            url=parsed.url,
            device_id=parsed.device_id,
            facility_id=parsed.facility_id,
            noise=parsed.noise,
            trigger_breach=parsed.trigger_breach,
            dry_run=parsed.dry_run,
            max_readings=parsed.max_readings,
            seed=parsed.seed,
        )
        return 0
    except KeyboardInterrupt:
        print("\nSimulation interrupted by user.")
        return 0
    except (ValueError, RuntimeError, OSError, httpx.HTTPError) as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
