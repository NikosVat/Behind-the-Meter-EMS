"""Peak projections are snapshots, never accumulated or invoice evidence."""
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.database.sqlite_store import SQLiteStore
from backend.main import create_app
from bot.command_handlers import BotCommandHandler, format_greek_bot_response


@pytest.mark.parametrize("last_projection", [100.0, 40.0, 0.0])
@pytest.mark.parametrize("legacy_mode", ["aggregate", "inflated", "fallback"])
def test_latest_projection_never_becomes_daily_charge(
    tmp_path, valid_telemetry_dict, last_projection, legacy_mode
):
    store = SQLiteStore(tmp_path / "finance.db")
    for minute, projection in enumerate([100.0, last_projection]):
        store.store_telemetry(
            dict(valid_telemetry_dict, timestamp=f"2026-10-01T12:0{minute}:00+00:00",
                 cumulative_energy_kwh=100.0 + minute),
            cost_factory=lambda delta, p=projection: SimpleNamespace(
                incremental_cost_eur=delta * 0.2, is_peak_window=True,
                current_rate_eur_per_kwh=0.2,
                is_excess_breach=p > 0, projected_excess_penalty_eur=p),
        )
    with store.connection() as conn:
        if legacy_mode == "inflated":
            conn.execute("UPDATE cost_aggregates SET peak_surcharges_eur = 200")
        elif legacy_mode == "fallback":
            conn.execute("DELETE FROM cost_aggregates")
    result = store.get_daily_summary("bakery-central-athens", "2026-10-01")
    assert result["peak_surcharges_eur"] == 0.0
    assert result["billed_peak_surcharges_eur"] is None
    assert result["total_spend_eur"] == 0.2
    assert result["projected_excess_penalty_eur"] == last_projection
    assert result["penalty_projection_timestamp"] == "2026-10-01T12:01:00+00:00"
    empty = store.get_daily_summary("bakery-central-athens", "2026-10-02")
    assert empty["projected_excess_penalty_eur"] is None
    assert empty["penalty_projection_timestamp"] is None


def test_bot_labels_projection_and_does_not_claim_invoice_or_safe_load():
    text = format_greek_bot_response(
        "/cost_today", {"facility_id": "demo"}, daily_spend_eur=0.2,
        projected_excess_penalty_eur=40,
        penalty_projection_timestamp="2026-10-01T12:01:00+00:00",
    )
    assert "40.00 €" in text
    assert "Πρόβλεψη" in text
    assert "2026-10-01T12:01:00+00:00" in text
    assert "όχι τιμολογημένη χρέωση" in text
    assert "εντός ορίων" not in text


def test_projection_survives_all_api_response_schemas(tmp_path, valid_telemetry_dict):
    db = tmp_path / "api.db"
    timestamp = datetime.now(timezone.utc).isoformat()
    with TestClient(create_app(str(db))) as client:
        store = SQLiteStore(db)
        store.store_telemetry(
            dict(valid_telemetry_dict, timestamp=timestamp),
            cost_factory=lambda delta: SimpleNamespace(projected_excess_penalty_eur=75),
        )
        for path in ["/api/v1/facilities/bakery-central-athens/cost-today",
                     "/api/v1/facilities/bakery-central-athens/status",
                     "/api/v1/dashboard/metrics/bakery-central-athens"]:
            response = client.get(path)
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["projected_excess_penalty_eur"] == 75
            assert data["penalty_projection_timestamp"] == timestamp
            assert data["billed_peak_surcharges_eur"] is None


def test_bot_cache_keeps_dated_projection_separate_from_spend():
    handler = BotCommandHandler({"facility_id": "demo"})
    handler.update_cost(0.2, 1, projected_excess_penalty_eur=40,
                        penalty_projection_timestamp="2026-10-01T12:01:00+00:00")
    text = handler.handle_command("/cost_today")
    assert "40.00 €" in text
    assert "0.20 €" in text
    assert "2026-10-01T12:01:00+00:00" in text
    handler.update_cost(0.3, 2)
    assert "40.00 €" not in handler.handle_command("/cost_today")


def test_unpriced_telemetry_does_not_invent_zero_projection(tmp_path, valid_telemetry_dict):
    store = SQLiteStore(tmp_path / "unpriced.db")
    store.store_telemetry(valid_telemetry_dict)
    summary = store.get_daily_summary("bakery-central-athens", "2026-09-14")
    assert summary["projected_excess_penalty_eur"] is None
    assert summary["penalty_projection_timestamp"] is None


def test_legacy_unpriced_zero_is_not_pricing_evidence(tmp_path, valid_telemetry_dict):
    store = SQLiteStore(tmp_path / "legacy.db")
    store.store_telemetry(valid_telemetry_dict)
    with store.connection() as conn:
        conn.execute("UPDATE telemetry_readings SET projected_penalty_eur = 0")
    summary = store.get_daily_summary("bakery-central-athens", "2026-09-14")
    assert summary["projected_excess_penalty_eur"] is None
    assert summary["penalty_projection_timestamp"] is None
