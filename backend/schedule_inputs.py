"""Read-only facility forecast preparation; never fills gaps or mixes meters."""
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.database.sqlite_store import SQLiteStore
from optimization_engine.forecasting import ForecastUnavailable, forecast_day_ahead


def load_facility_forecast(store: SQLiteStore, facility_id: str, target: date, zone: str):
    origin = datetime.combine(target, time(), ZoneInfo(zone)).astimezone(timezone.utc)
    start = origin - timedelta(days=60)
    with store.connection() as conn:
        devices = conn.execute(
            "SELECT DISTINCT device_id, power_measurement_method FROM telemetry_readings "
            "WHERE facility_id=? AND timestamp>=? AND timestamp<?",
            (facility_id, start.isoformat(), origin.isoformat()),
        ).fetchall()
        if len({row["device_id"] for row in devices}) != 1:
            raise ForecastUnavailable("Forecast requires one explicitly identified whole-facility meter; history missing or multiple meters")
        rows = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:00:00', timestamp) AS hour, AVG(total_active_power_kw) AS power "
            "FROM telemetry_readings WHERE facility_id=? AND timestamp>=? AND timestamp<? "
            "GROUP BY hour ORDER BY hour",
            (facility_id, start.isoformat(), origin.isoformat()),
        ).fetchall()
    hourly = {datetime.fromisoformat(row["hour"]).replace(tzinfo=timezone.utc): row["power"] for row in rows}
    result = asdict(forecast_day_ahead(hourly, target, zone))
    result["measurement_methods"] = sorted({row["power_measurement_method"] for row in devices})
    result["is_demo"] = "simulated" in result["measurement_methods"]
    return result
