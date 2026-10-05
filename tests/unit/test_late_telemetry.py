"""Late/replayed counters must not inflate energy or desynchronize costs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.database.sqlite_store import SQLiteStore
from backend.main import create_app


def reading(template, minute, kwh, device="esp32-ems-001"):
    return dict(template, timestamp=(datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
                + timedelta(minutes=minute)).isoformat(), cumulative_energy_kwh=kwh, device_id=device)


def test_late_and_duplicate_are_rejected_before_aggregate_writes(tmp_path, valid_telemetry_dict):
    store = SQLiteStore(tmp_path / "late.db")
    store.store_telemetry(reading(valid_telemetry_dict, 0, 100))
    store.store_telemetry(reading(valid_telemetry_dict, 2, 102))
    for minute in (1, 2):
        with pytest.raises(ValueError, match="timestamp"):
            store.store_telemetry(reading(valid_telemetry_dict, minute, 101))
    store.store_telemetry(reading(valid_telemetry_dict, 3, 103))
    assert store.get_daily_summary("bakery-central-athens", "2026-09-14")["total_kwh"] == 3
    assert len(store.get_telemetry_history("bakery-central-athens")) == 3


def test_device_counters_are_independent_and_reset_is_baseline(tmp_path, valid_telemetry_dict):
    store = SQLiteStore(tmp_path / "meters.db")
    for minute, kwh, device in [(0, 100, "a"), (0, 500, "b"), (1, 102, "a"),
                                (1, 503, "b"), (2, 0, "a"), (3, 1, "a")]:
        store.store_telemetry(reading(valid_telemetry_dict, minute, kwh, device))
    assert store.get_daily_summary("bakery-central-athens", "2026-09-14")["total_kwh"] == 6


def test_offset_equivalent_timestamps_are_duplicates(tmp_path, valid_telemetry_dict):
    store = SQLiteStore(tmp_path / "offset.db")
    store.store_telemetry(reading(valid_telemetry_dict, 0, 100))
    with pytest.raises(ValueError, match="timestamp"):
        store.store_telemetry(dict(valid_telemetry_dict, timestamp="2026-09-14T15:00:00+03:00"))


def test_api_returns_409_for_late_reading_and_cost_matches_energy(tmp_path, valid_telemetry_dict):
    db_path = str(tmp_path / "api.db")
    with TestClient(create_app(db_path)) as client:
        costs = []
        for minute, kwh, expected in [(0, 100, 200), (2, 102, 200), (1, 101, 409), (3, 103, 200)]:
            response = client.post("/api/v1/telemetry", json=reading(valid_telemetry_dict, minute, kwh))
            assert response.status_code == expected, response.text
            if expected == 200:
                costs.append(response.json()["incremental_cost_eur"])
    summary = SQLiteStore(db_path).get_daily_summary("bakery-central-athens", "2026-09-14")
    assert summary["total_kwh"] == 3
    assert summary["total_spend_eur"] == round(sum(costs), 2)


def test_api_exact_replay_is_conflict_without_double_billing(tmp_path, valid_telemetry_dict):
    db_path = str(tmp_path / "replay.db")
    with TestClient(create_app(db_path)) as client:
        first = reading(valid_telemetry_dict, 0, 100)
        second = reading(valid_telemetry_dict, 1, 101)
        assert client.post("/api/v1/telemetry", json=first).status_code == 200
        response = client.post("/api/v1/telemetry", json=second)
        assert response.status_code == 200
        assert client.post("/api/v1/telemetry", json=second).status_code == 409
    store = SQLiteStore(db_path)
    assert len(store.get_telemetry_history("bakery-central-athens")) == 2
    assert store.get_daily_summary("bakery-central-athens", "2026-09-14")["total_kwh"] == 1


def test_atomic_pricing_uses_same_counter_as_energy_across_store_instances(tmp_path, valid_telemetry_dict):
    db = tmp_path / "concurrent.db"
    first = SQLiteStore(db)
    second = SQLiteStore(db)
    first.store_telemetry(reading(valid_telemetry_dict, 0, 100))

    def ingest(store, minute):
        try:
            return store.store_telemetry(reading(valid_telemetry_dict, minute, 100 + minute),
                cost_factory=lambda delta: SimpleNamespace(incremental_cost_eur=delta * 0.2))
        except ValueError:
            return None  # If the later reading wins the lock, the earlier one is stale.

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ingest, first, 1), pool.submit(ingest, second, 2)]
        results = [future.result() for future in futures]
    assert results[1] is not None
    summary = first.get_daily_summary("bakery-central-athens", "2026-09-14")
    assert summary["total_kwh"] == 2
    assert summary["total_spend_eur"] == 0.4


def test_pricing_failure_rolls_back_reading_and_aggregate(tmp_path, valid_telemetry_dict):
    store = SQLiteStore(tmp_path / "rollback.db")
    store.store_telemetry(reading(valid_telemetry_dict, 0, 100))

    def fail(delta):
        raise RuntimeError("pricing unavailable")

    with pytest.raises(RuntimeError, match="pricing unavailable"):
        store.store_telemetry(reading(valid_telemetry_dict, 1, 101), cost_factory=fail)
    assert len(store.get_telemetry_history("bakery-central-athens")) == 1
    assert store.get_daily_summary("bakery-central-athens", "2026-09-14")["total_kwh"] == 0


def test_midnight_utc_delta_is_assigned_once_to_receiving_day(tmp_path, valid_telemetry_dict):
    store = SQLiteStore(tmp_path / "midnight.db")
    store.store_telemetry(dict(valid_telemetry_dict, timestamp="2026-09-14T23:59:00Z", cumulative_energy_kwh=100))
    # Local offset crosses calendar midnight; normalized UTC defines aggregation day.
    store.store_telemetry(dict(valid_telemetry_dict, timestamp="2026-09-15T03:01:00+03:00", cumulative_energy_kwh=102))
    assert store.get_daily_summary("bakery-central-athens", "2026-09-14")["total_kwh"] == 0
    assert store.get_daily_summary("bakery-central-athens", "2026-09-15")["total_kwh"] == 2
