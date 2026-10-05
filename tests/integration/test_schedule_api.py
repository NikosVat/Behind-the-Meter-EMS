"""Integration tests for the SME Schedule Studio REST API.

Verifies:
1. Equipment Assets CRUD under /api/v1/facilities/{facility_id}/assets.
2. Facility-level Schedule Settings GET/PUT under /api/v1/facilities/{facility_id}/schedule-settings.
3. Advisory Schedule Preview under /api/v1/facilities/{facility_id}/schedules/preview.
4. Schedule Persistence and Retrieval under /api/v1/facilities/{facility_id}/schedules.
5. Strict Multi-Facility Isolation and 404/422 validation guards.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from backend.database.sqlite_store import get_store
from backend.main import create_app


@pytest.fixture
def test_db_path(tmp_path) -> str:
    db_file = tmp_path / "test_schedules_api.db"
    return str(db_file)


@pytest.fixture
def app_client(test_db_path: str) -> Generator[TestClient, None, None]:
    app = create_app(db_path=test_db_path)
    store = get_store(test_db_path)
    store.init_db()
    store.seed_default_facilities()

    with TestClient(app) as client:
        yield client


class TestEquipmentAssetsAPI:
    """Test asset management endpoints."""

    def test_crud_equipment_asset(self, app_client: TestClient):
        facility_id = "bakery-central-athens"

        # 1. Create Asset
        payload = {
            "name": "Βιομηχανικός Φούρνος",
            "category": "oven",
            "rated_power_kw": 12.5,
            "required_runtime_minutes": 90,
            "earliest_start": "04:00",
            "latest_finish": "10:00",
            "active_weekdays": [0, 1, 2, 3, 4],
            "must_run": True,
            "interruptible": False,
            "priority": 5,
            "preferred_start": "05:00",
        }
        res_create = app_client.post(f"/api/v1/facilities/{facility_id}/assets", json=payload)
        assert res_create.status_code == 201
        data = res_create.json()
        asset_id = data["asset_id"]
        assert asset_id.startswith("ast_")
        assert data["name"] == "Βιομηχανικός Φούρνος"
        assert data["rated_power_kw"] == 12.5
        assert data["enabled"] is True

        # 2. List Assets
        res_list = app_client.get(f"/api/v1/facilities/{facility_id}/assets")
        assert res_list.status_code == 200
        items = res_list.json()
        assert len(items) >= 1
        assert any(a["asset_id"] == asset_id for a in items)

        # 3. Get Single Asset
        res_get = app_client.get(f"/api/v1/facilities/{facility_id}/assets/{asset_id}")
        assert res_get.status_code == 200
        assert res_get.json()["name"] == "Βιομηχανικός Φούρνος"

        # 4. Update Asset
        update_payload = {
            "name": "Αναβαθμισμένος Φούρνος",
            "rated_power_kw": 14.0,
            "priority": 4,
        }
        res_update = app_client.put(f"/api/v1/facilities/{facility_id}/assets/{asset_id}", json=update_payload)
        assert res_update.status_code == 200
        assert res_update.json()["name"] == "Αναβαθμισμένος Φούρνος"
        assert res_update.json()["rated_power_kw"] == 14.0
        assert res_update.json()["priority"] == 4

        # 5. Delete Asset
        res_del = app_client.delete(f"/api/v1/facilities/{facility_id}/assets/{asset_id}")
        assert res_del.status_code == 204

        # Verify Deleted
        res_check = app_client.get(f"/api/v1/facilities/{facility_id}/assets/{asset_id}")
        assert res_check.status_code == 404

    def test_asset_validation_errors(self, app_client: TestClient):
        facility_id = "bakery-central-athens"

        # Invalid power <= 0
        bad_power = {
            "name": "Bad Asset",
            "rated_power_kw": -2.0,
            "required_runtime_minutes": 60,
        }
        res = app_client.post(f"/api/v1/facilities/{facility_id}/assets", json=bad_power)
        assert res.status_code == 422

        # Invalid time format
        bad_time = {
            "name": "Bad Time Asset",
            "rated_power_kw": 5.0,
            "required_runtime_minutes": 60,
            "earliest_start": "25:99",
        }
        res = app_client.post(f"/api/v1/facilities/{facility_id}/assets", json=bad_time)
        assert res.status_code == 422

        # Nonexistent facility
        res = app_client.get("/api/v1/facilities/nonexistent-fac/assets")
        assert res.status_code == 404

    def test_facility_isolation(self, app_client: TestClient):
        fac1 = "bakery-central-athens"
        fac2 = "supermarket-crete"

        res = app_client.post(
            f"/api/v1/facilities/{fac1}/assets",
            json={
                "name": "Athens Bakery Mixer",
                "rated_power_kw": 3.0,
                "required_runtime_minutes": 30,
            },
        )
        assert res.status_code == 201
        asset_id = res.json()["asset_id"]

        # fac2 should not see fac1 asset
        res_fac2 = app_client.get(f"/api/v1/facilities/{fac2}/assets/{asset_id}")
        assert res_fac2.status_code == 404


class TestScheduleSettingsAPI:
    """Test facility schedule settings endpoints."""

    def test_get_and_update_settings(self, app_client: TestClient):
        facility_id = "bakery-central-athens"

        # 1. Default settings exist
        res_get = app_client.get(f"/api/v1/facilities/{facility_id}/schedule-settings")
        assert res_get.status_code == 200
        data = res_get.json()
        assert data["facility_id"] == facility_id
        assert data["time_step_minutes"] == 15
        assert data["objective_mode"] == "balanced"

        # 2. Update settings
        update_data = {
            "time_step_minutes": 30,
            "max_facility_power_kw": 32.0,
            "objective_mode": "cost",
            "forecast_uncertainty_pct": 12.5,
        }
        res_put = app_client.put(f"/api/v1/facilities/{facility_id}/schedule-settings", json=update_data)
        assert res_put.status_code == 200
        updated = res_put.json()
        assert updated["time_step_minutes"] == 30
        assert updated["max_facility_power_kw"] == 32.0
        assert updated["objective_mode"] == "cost"
        assert updated["forecast_uncertainty_pct"] == 12.5

    def test_settings_validation_rejects_invalid_values(self, app_client: TestClient):
        facility_id = "bakery-central-athens"

        # Invalid resolution (e.g., 20 min)
        res = app_client.put(
            f"/api/v1/facilities/{facility_id}/schedule-settings",
            json={"time_step_minutes": 20},
        )
        assert res.status_code == 422

        # Invalid objective
        res = app_client.put(
            f"/api/v1/facilities/{facility_id}/schedule-settings",
            json={"objective_mode": "random_gamble"},
        )
        assert res.status_code == 422


class TestSchedulePreviewAndSaveAPI:
    """Test generating and saving schedules."""

    def test_schedule_preview_with_asset_overrides(self, app_client: TestClient):
        facility_id = "bakery-central-athens"

        payload = {
            "schedule_date": "2026-09-23",
            "time_step_minutes": 15,
            "max_facility_power_kw": 30.0,
            "objective_mode": "balanced",
            "asset_overrides": [
                {
                    "facility_id": facility_id,
                    "name": "Πλυντήριο",
                    "category": "cleaning",
                    "rated_power_kw": 5.0,
                    "required_runtime_minutes": 45,
                    "earliest_start": "08:00",
                    "latest_finish": "14:00",
                    "must_run": True,
                    "interruptible": False,
                    "priority": 3,
                }
            ],
        }

        res = app_client.post(f"/api/v1/facilities/{facility_id}/schedules/preview", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "preview"
        assert data["time_step_minutes"] == 15
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["asset_name"] == "Πλυντήριο"
        assert item["start_time"] is not None
        assert item["end_time"] is not None
        assert len(item["explanation"]) > 0
        assert len(data["timeline_load"]["timestamps"]) == 96

    def test_schedule_save_and_retrieve(self, app_client: TestClient):
        facility_id = "bakery-central-athens"

        # 1. Create an asset in the facility
        app_client.post(
            f"/api/v1/facilities/{facility_id}/assets",
            json={
                "name": "Ψυγείο Κατάψυξης",
                "category": "cooling",
                "rated_power_kw": 4.0,
                "required_runtime_minutes": 60,
                "earliest_start": "02:00",
                "latest_finish": "08:00",
                "must_run": True,
                "interruptible": False,
            },
        )

        # 2. Save schedule
        res_save = app_client.post(
            f"/api/v1/facilities/{facility_id}/schedules/save",
            json={
                "schedule_date": "2026-09-23",
                "time_step_minutes": 30,
                "objective_mode": "cost",
            },
        )
        assert res_save.status_code == 201
        saved_data = res_save.json()
        schedule_id = saved_data["schedule_id"]
        assert saved_data["status"] == "saved"
        assert saved_data["time_step_minutes"] == 30

        # 3. List schedules
        res_list = app_client.get(f"/api/v1/facilities/{facility_id}/schedules")
        assert res_list.status_code == 200
        schedules = res_list.json()
        assert len(schedules) >= 1
        assert any(s["schedule_id"] == schedule_id for s in schedules)

        # 4. Get specific schedule
        res_get = app_client.get(f"/api/v1/facilities/{facility_id}/schedules/{schedule_id}")
        assert res_get.status_code == 200
        retrieved = res_get.json()
        assert retrieved["schedule_id"] == schedule_id
        assert retrieved["status"] == "saved"
        assert len(retrieved["items"]) >= 1
        assert len(retrieved["timeline_load"]["timestamps"]) == 48
