"""SQLite time-series storage engine for Greek Commercial EMS backend.

Implements high-throughput time-series persistence with:
- PRAGMA journal_mode=WAL and PRAGMA synchronous=NORMAL
- PRAGMA foreign_keys=ON and PRAGMA busy_timeout=5000
- Tables: telemetry_readings, facility_configs, cost_aggregates
- Composite indices on (facility_id, timestamp) and (facility_id, date)
- Full CRUD and aggregation queries for telemetry, facility configurations, and daily costs
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.models.telemetry import TelemetryPayload

logger = logging.getLogger(__name__)

# Default Greek commercial facility configurations
DEFAULT_FACILITIES = [
    {
        "facility_id": "bakery-central-athens",
        "name": "Bakery Central Athens",
        "facility_type": "bakery",
        "contract_type": "Γ22",
        "tariff_color": "green",
        "contracted_kva": 35.0,
        "peak_threshold_kw": 22.0,
        "warning_threshold_ratio": 0.85,
        "low_pf_threshold": 0.85,
        "cooldown_seconds": 1800,
        "hysteresis_factor": 0.90,
        "debounce_samples": 3,
        "chat_id": 999111222,
    },
    {
        "facility_id": "cold-storage-piraeus",
        "name": "Cold Storage Piraeus",
        "facility_type": "cold_storage",
        "contract_type": "Γ22",
        "tariff_color": "green",
        "contracted_kva": 50.0,
        "peak_threshold_kw": 30.0,
        "warning_threshold_ratio": 0.85,
        "low_pf_threshold": 0.85,
        "cooldown_seconds": 1800,
        "hysteresis_factor": 0.90,
        "debounce_samples": 3,
        "chat_id": 999222333,
    },
    {
        "facility_id": "hotel-plaka-boutique",
        "name": "Hotel Plaka Boutique",
        "facility_type": "boutique_hotel",
        "contract_type": "Γ21",
        "tariff_color": "green",
        "contracted_kva": 40.0,
        "peak_threshold_kw": 25.0,
        "warning_threshold_ratio": 0.85,
        "low_pf_threshold": 0.85,
        "cooldown_seconds": 1800,
        "hysteresis_factor": 0.90,
        "debounce_samples": 3,
        "chat_id": 999333444,
    },
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facility_configs (
    facility_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    facility_type TEXT NOT NULL,
    contract_type TEXT NOT NULL,
    tariff_color TEXT NOT NULL,
    contracted_kva REAL NOT NULL DEFAULT 35.0,
    peak_threshold_kw REAL NOT NULL DEFAULT 22.0,
    warning_threshold_ratio REAL NOT NULL DEFAULT 0.85,
    low_pf_threshold REAL NOT NULL DEFAULT 0.85,
    cooldown_seconds INTEGER NOT NULL DEFAULT 1800,
    hysteresis_factor REAL NOT NULL DEFAULT 0.90,
    debounce_samples INTEGER NOT NULL DEFAULT 3,
    chat_id INTEGER,
    viber_receiver_id TEXT,
    notification_channel TEXT NOT NULL DEFAULT 'telegram',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
);

CREATE TABLE IF NOT EXISTS telemetry_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    facility_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    total_active_power_kw REAL NOT NULL,
    total_apparent_power_kva REAL NOT NULL,
    system_power_factor REAL NOT NULL,
    cumulative_energy_kwh REAL NOT NULL,
    grid_frequency_hz REAL NOT NULL DEFAULT 50.0,
    wifi_rssi_dbm REAL NOT NULL DEFAULT -60.0,
    phases_json TEXT NOT NULL,
    running_cost_eur_per_h REAL DEFAULT 0.0,
    current_rate_eur_per_kwh REAL DEFAULT 0.0,
    incremental_cost_eur REAL DEFAULT 0.0,
    is_peak_window INTEGER DEFAULT 0,
    is_excess_breach INTEGER DEFAULT 0,
    projected_penalty_eur REAL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (facility_id) REFERENCES facility_configs(facility_id)
);

CREATE TABLE IF NOT EXISTS cost_aggregates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facility_id TEXT NOT NULL,
    date TEXT NOT NULL,
    total_kwh REAL NOT NULL DEFAULT 0.0,
    total_cost_eur REAL NOT NULL DEFAULT 0.0,
    peak_kwh REAL NOT NULL DEFAULT 0.0,
    offpeak_kwh REAL NOT NULL DEFAULT 0.0,
    peak_surcharges_eur REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    UNIQUE(facility_id, date),
    FOREIGN KEY (facility_id) REFERENCES facility_configs(facility_id)
);

CREATE INDEX IF NOT EXISTS idx_telemetry_facility_timestamp
ON telemetry_readings (facility_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_cost_aggregates_facility_date
ON cost_aggregates (facility_id, date);

-- 24-hour HEnEx Day-Ahead Market hourly clearing prices
CREATE TABLE IF NOT EXISTS market_dam_hourly_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    price_eur_mwh REAL NOT NULL,
    price_eur_kwh REAL NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    UNIQUE(date, hour)
);
CREATE INDEX IF NOT EXISTS idx_dam_date_hour ON market_dam_hourly_prices(date, hour);

-- Monthly RAE Green Tariff announcements (per supplier & contract)
CREATE TABLE IF NOT EXISTS market_green_tariffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    supplier_name TEXT NOT NULL,
    contract_type TEXT NOT NULL,
    p_base REAL NOT NULL,
    e_disc REAL NOT NULL DEFAULT 0.0,
    prompt_discount_percent REAL DEFAULT 0.0,
    alpha REAL NOT NULL DEFAULT 1.15,
    lu_eur_mwh REAL NOT NULL DEFAULT 115.0,
    ll_eur_mwh REAL NOT NULL DEFAULT 95.0,
    beta REAL NOT NULL DEFAULT 0.0,
    fixed_monthly_fee_eur REAL NOT NULL DEFAULT 5.0,
    published_final_rate_eur_per_kwh REAL NOT NULL,
    tea_m1_eur_mwh REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    UNIQUE(month, supplier_id, contract_type)
);
CREATE INDEX IF NOT EXISTS idx_green_tariffs_month ON market_green_tariffs(month);

-- Equipment assets for generic SME schedule studio
CREATE TABLE IF NOT EXISTS equipment_assets (
    asset_id TEXT PRIMARY KEY,
    facility_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'custom',
    rated_power_kw REAL NOT NULL,
    required_runtime_minutes INTEGER NOT NULL,
    earliest_start TEXT NOT NULL DEFAULT '00:00',
    latest_finish TEXT NOT NULL DEFAULT '23:59',
    active_weekdays TEXT NOT NULL DEFAULT '[0,1,2,3,4]',
    must_run INTEGER NOT NULL DEFAULT 1,
    interruptible INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 3,
    preferred_start TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (facility_id) REFERENCES facility_configs(facility_id)
);
CREATE INDEX IF NOT EXISTS idx_assets_facility ON equipment_assets(facility_id);

-- Facility scheduling preferences and constraints
CREATE TABLE IF NOT EXISTS schedule_settings (
    facility_id TEXT PRIMARY KEY,
    time_step_minutes INTEGER NOT NULL DEFAULT 15,
    max_facility_power_kw REAL NOT NULL DEFAULT 25.0,
    objective_mode TEXT NOT NULL DEFAULT 'balanced',
    timezone TEXT NOT NULL DEFAULT 'Europe/Athens',
    forecast_uncertainty_pct REAL NOT NULL DEFAULT 10.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (facility_id) REFERENCES facility_configs(facility_id)
);

-- Generated and saved multi-resolution daily schedules
CREATE TABLE IF NOT EXISTS generated_schedules (
    schedule_id TEXT PRIMARY KEY,
    facility_id TEXT NOT NULL,
    schedule_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'preview',
    time_step_minutes INTEGER NOT NULL DEFAULT 15,
    objective_mode TEXT NOT NULL DEFAULT 'balanced',
    input_source TEXT NOT NULL DEFAULT 'forecast',
    is_demo INTEGER NOT NULL DEFAULT 0,
    baseline_cost_eur REAL NOT NULL DEFAULT 0.0,
    optimized_cost_eur REAL NOT NULL DEFAULT 0.0,
    estimated_savings_eur REAL NOT NULL DEFAULT 0.0,
    baseline_peak_kw REAL NOT NULL DEFAULT 0.0,
    optimized_peak_kw REAL NOT NULL DEFAULT 0.0,
    uncertainty_margin_kw REAL NOT NULL DEFAULT 0.0,
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    items_json TEXT NOT NULL DEFAULT '[]',
    timeline_load_json TEXT NOT NULL DEFAULT '{}',
    generated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY (facility_id) REFERENCES facility_configs(facility_id)
);
CREATE INDEX IF NOT EXISTS idx_schedules_facility_date ON generated_schedules(facility_id, schedule_date);
"""


class SQLiteStore:
    """Thread-safe SQLite storage engine with WAL mode and time-series query capabilities."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or settings.SQLITE_DB_PATH)
        self._is_memory = (self.db_path == ":memory:" or "mode=memory" in self.db_path)
        self._lock = threading.Lock()
        self._memory_conn: sqlite3.Connection | None = None

        if self._is_memory:
            self._memory_conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
            self._memory_conn.row_factory = sqlite3.Row
            self._apply_pragmas(self._memory_conn)
            self._create_schema(self._memory_conn)
        else:
            # Ensure target directory exists for file-based database
            if not self.db_path.startswith("file:"):
                dir_name = os.path.dirname(os.path.abspath(self.db_path))
                if dir_name:
                    os.makedirs(dir_name, exist_ok=True)
            with self.connection() as conn:
                self._create_schema(conn)

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        """Apply required WAL and concurrency pragmas."""
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Initialize database schema tables and indices."""
        conn.executescript(SCHEMA_SQL)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(telemetry_readings)")}
        if "power_measurement_method" not in columns:
            conn.execute("ALTER TABLE telemetry_readings ADD COLUMN power_measurement_method TEXT NOT NULL DEFAULT 'unknown'")
        try:
            conn.execute("ALTER TABLE facility_configs ADD COLUMN viber_receiver_id TEXT;")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE facility_configs ADD COLUMN notification_channel TEXT NOT NULL DEFAULT 'telegram';")
        except Exception:
            pass
        conn.commit()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provide a contextual SQLite connection."""
        if self._is_memory and self._memory_conn:
            with self._lock:
                try:
                    yield self._memory_conn
                    self._memory_conn.commit()
                except Exception:
                    self._memory_conn.rollback()
                    raise
        else:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
            conn.row_factory = sqlite3.Row
            self._apply_pragmas(conn)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def init_db(self) -> None:
        """Re-verify schema initialization."""
        with self.connection() as conn:
            self._create_schema(conn)

    def store_facility_config(self, config: dict[str, Any] | Any) -> None:
        """Store or update facility configuration."""
        if hasattr(config, "model_dump"):
            data = config.model_dump()
        elif isinstance(config, dict):
            data = dict(config)
        else:
            data = {k: getattr(config, k) for k in dir(config) if not k.startswith("_")}

        facility_id = str(data["facility_id"])
        name = str(data.get("name", data.get("facility_name", facility_id)))
        facility_type = str(data.get("facility_type", "bakery"))
        contract_type = str(data.get("contract_type", data.get("contract_code", "Γ22")))
        tariff_color = str(data.get("tariff_color", data.get("color", "green")))
        contracted_kva = float(data.get("contracted_kva", data.get("contracted_capacity_kva", 35.0)))
        peak_threshold_kw = float(data.get("peak_threshold_kw", 22.0))
        warning_threshold_ratio = float(data.get("warning_threshold_ratio", 0.85))
        low_pf_threshold = float(data.get("low_pf_threshold", 0.85))
        cooldown_seconds = int(data.get("cooldown_seconds", 1800))
        hysteresis_factor = float(data.get("hysteresis_factor", 0.90))
        debounce_samples = int(data.get("debounce_samples", 3))
        chat_id = data.get("chat_id", data.get("telegram_chat_id"))
        viber_receiver_id = data.get("viber_receiver_id", data.get("viber_chat_id"))
        notification_channel = str(data.get("notification_channel", "telegram")).lower()

        query = """
        INSERT INTO facility_configs (
            facility_id, name, facility_type, contract_type, tariff_color,
            contracted_kva, peak_threshold_kw, warning_threshold_ratio,
            low_pf_threshold, cooldown_seconds, hysteresis_factor,
            debounce_samples, chat_id, viber_receiver_id, notification_channel, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))
        ON CONFLICT(facility_id) DO UPDATE SET
            name = excluded.name,
            facility_type = excluded.facility_type,
            contract_type = excluded.contract_type,
            tariff_color = excluded.tariff_color,
            contracted_kva = excluded.contracted_kva,
            peak_threshold_kw = excluded.peak_threshold_kw,
            warning_threshold_ratio = excluded.warning_threshold_ratio,
            low_pf_threshold = excluded.low_pf_threshold,
            cooldown_seconds = excluded.cooldown_seconds,
            hysteresis_factor = excluded.hysteresis_factor,
            debounce_samples = excluded.debounce_samples,
            chat_id = excluded.chat_id,
            viber_receiver_id = excluded.viber_receiver_id,
            notification_channel = excluded.notification_channel,
            updated_at = datetime('now', 'utc');
        """
        with self.connection() as conn:
            conn.execute(
                query,
                (
                    facility_id,
                    name,
                    facility_type,
                    contract_type,
                    tariff_color,
                    contracted_kva,
                    peak_threshold_kw,
                    warning_threshold_ratio,
                    low_pf_threshold,
                    cooldown_seconds,
                    hysteresis_factor,
                    debounce_samples,
                    chat_id,
                    viber_receiver_id,
                    notification_channel,
                ),
            )

    def get_facility_config(self, facility_id: str) -> dict[str, Any] | None:
        """Fetch facility configuration by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM facility_configs WHERE facility_id = ?;",
                (facility_id,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def list_facility_configs(self) -> list[dict[str, Any]]:
        """List all configured commercial facilities."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM facility_configs ORDER BY facility_id ASC;"
            ).fetchall()
            return [dict(r) for r in rows]

    def seed_default_facilities(self) -> None:
        """Ensure standard commercial facilities (Bakery, Cold Storage, Hotel) are seeded."""
        for fac in DEFAULT_FACILITIES:
            self.store_facility_config(fac)

    def store_telemetry(
        self,
        payload: TelemetryPayload | dict[str, Any],
        cost_result: Any | None = None,
    ) -> int:
        """Store a telemetry reading and update daily cost aggregates.

        Returns:
            The inserted telemetry reading ID.
        """
        if isinstance(payload, TelemetryPayload):
            measurement_method: str = str(payload.power_measurement_method)
            device_id = payload.device_id
            facility_id = payload.facility_id
            ts = payload.timestamp
            p_tot = float(payload.total_active_power_kw)
            s_tot = float(payload.total_apparent_power_kva)
            pf = float(payload.system_power_factor)
            kwh = float(payload.cumulative_energy_kwh)
            freq = float(payload.grid_frequency_hz)
            rssi = float(payload.wifi_rssi_dbm)
            # Serialize phases
            phases_dict = {
                k: v.model_dump() if hasattr(v, "model_dump") else v
                for k, v in payload.phases.items()
            }
        else:
            device_id = str(payload["device_id"])
            measurement_method = str(payload.get("power_measurement_method", "unknown"))
            facility_id = str(payload["facility_id"])
            ts = payload["timestamp"]
            p_tot = float(payload["total_active_power_kw"])
            s_tot = float(payload["total_apparent_power_kva"])
            pf = float(payload["system_power_factor"])
            kwh = float(payload["cumulative_energy_kwh"])
            freq = float(payload.get("grid_frequency_hz", 50.0))
            rssi = float(payload.get("wifi_rssi_dbm", -60.0))
            phases_dict = payload.get("phases", {})

        # Ensure timestamp is formatted consistently as ISO 8601 string
        if isinstance(ts, datetime):
            ts_str = ts.astimezone(timezone.utc).isoformat()
            reading_date = ts.date()
        else:
            ts_str = str(ts)
            try:
                reading_date = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).date()
            except Exception:
                reading_date = datetime.now(timezone.utc).date()

        phases_json = json.dumps(phases_dict)

        # Cost metrics extraction
        cost_h = getattr(cost_result, "running_cost_eur_per_h", 0.0) if cost_result else 0.0
        unit_rate = getattr(cost_result, "current_rate_eur_per_kwh", 0.0) if cost_result else 0.0
        incremental_cost = getattr(cost_result, "incremental_cost_eur", 0.0) if cost_result else 0.0
        is_peak = 1 if getattr(cost_result, "is_peak_window", False) else 0
        is_excess = 1 if getattr(cost_result, "is_excess_breach", False) else 0
        projected_penalty = getattr(cost_result, "projected_excess_penalty_eur", 0.0) if cost_result else 0.0

        with self.connection() as conn:
            # Auto-provision facility if not yet recorded to prevent foreign key violation
            fac_exists = conn.execute(
                "SELECT 1 FROM facility_configs WHERE facility_id = ?;", (facility_id,)
            ).fetchone()
            if not fac_exists:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO facility_configs (
                        facility_id, name, facility_type, contract_type, tariff_color,
                        contracted_kva, peak_threshold_kw
                    ) VALUES (?, ?, 'commercial', 'Γ22', 'green', 35.0, 22.0);
                    """,
                    (facility_id, facility_id),
                )

            # Insert reading
            cursor = conn.execute(
                """
                INSERT INTO telemetry_readings (
                    device_id, facility_id, timestamp, total_active_power_kw,
                    total_apparent_power_kva, system_power_factor, cumulative_energy_kwh,
                    grid_frequency_hz, wifi_rssi_dbm, phases_json, running_cost_eur_per_h,
                    current_rate_eur_per_kwh, incremental_cost_eur, is_peak_window,
                    is_excess_breach, projected_penalty_eur, power_measurement_method
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    device_id,
                    facility_id,
                    ts_str,
                    p_tot,
                    s_tot,
                    pf,
                    kwh,
                    freq,
                    rssi,
                    phases_json,
                    cost_h,
                    unit_rate,
                    incremental_cost,
                    is_peak,
                    is_excess,
                    projected_penalty,
                    measurement_method,
                ),
            )
            reading_id = cursor.lastrowid

            # Calculate energy delta for daily aggregate
            # Query the previous reading for this facility
            prev_reading = conn.execute(
                """
                SELECT cumulative_energy_kwh FROM telemetry_readings
                WHERE facility_id = ? AND id < ?
                ORDER BY id DESC LIMIT 1;
                """,
                (facility_id, reading_id),
            ).fetchone()

            if prev_reading and prev_reading["cumulative_energy_kwh"] is not None:
                delta_kwh = max(0.0, kwh - prev_reading["cumulative_energy_kwh"])
            else:
                delta_kwh = 0.0

            peak_kwh = delta_kwh if is_peak else 0.0
            offpeak_kwh = delta_kwh if not is_peak else 0.0
            penalty_contrib = projected_penalty if is_excess else 0.0
            date_str = reading_date.isoformat()

            conn.execute(
                """
                INSERT INTO cost_aggregates (
                    facility_id, date, total_kwh, total_cost_eur, peak_kwh,
                    offpeak_kwh, peak_surcharges_eur, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))
                ON CONFLICT(facility_id, date) DO UPDATE SET
                    total_kwh = total_kwh + excluded.total_kwh,
                    total_cost_eur = total_cost_eur + excluded.total_cost_eur,
                    peak_kwh = peak_kwh + excluded.peak_kwh,
                    offpeak_kwh = offpeak_kwh + excluded.offpeak_kwh,
                    peak_surcharges_eur = peak_surcharges_eur + excluded.peak_surcharges_eur,
                    updated_at = datetime('now', 'utc');
                """,
                (
                    facility_id,
                    date_str,
                    delta_kwh,
                    incremental_cost,
                    peak_kwh,
                    offpeak_kwh,
                    penalty_contrib,
                ),
            )

            return reading_id if reading_id is not None else 0

    def get_telemetry_interval(self, facility_id: str, start_id: int, end_id: int) -> list[dict[str, Any]]:
        """Resolve stored interval endpoints and all samples from the same meter.

        Use SQLite date conversion so equivalent ISO offsets compare chronologically.
        Counters must not reset within an interval used for energy verification.
        """
        with self.connection() as conn:
            endpoints = [conn.execute(
                "SELECT * FROM telemetry_readings WHERE id = ? AND facility_id = ?",
                (reading_id, facility_id)).fetchone() for reading_id in (start_id, end_id)]
            if any(row is None for row in endpoints):
                raise ValueError("Interval readings not found for this facility")
            start, end = endpoints
            if start["device_id"] != end["device_id"]:
                raise ValueError("Interval endpoints must belong to the same device")
            rows = conn.execute(
                "SELECT * FROM telemetry_readings WHERE facility_id = ? AND device_id = ? "
                "AND julianday(timestamp) BETWEEN julianday(?) AND julianday(?) "
                "ORDER BY julianday(timestamp), id",
                (facility_id, start["device_id"], start["timestamp"], end["timestamp"])).fetchall()
            # Endpoints must identify an unambiguous, chronological interval.
            if len(rows) < 2 or rows[0]["id"] != start_id or rows[-1]["id"] != end_id:
                raise ValueError("Interval must have distinct chronological endpoints")
            for previous, current in itertools.pairwise(rows):
                if current["cumulative_energy_kwh"] < previous["cumulative_energy_kwh"]:
                    raise ValueError("Energy counter reset within verification interval")
            return [dict(row) for row in rows]

    def get_latest_telemetry(self, facility_id: str) -> dict[str, Any] | None:
        """Fetch the most recent telemetry record for a facility."""
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM telemetry_readings
                WHERE facility_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT 1;
                """,
                (facility_id,),
            ).fetchone()
            if row is None:
                return None

            result = dict(row)
            try:
                result["phases"] = json.loads(result.pop("phases_json", "{}"))
            except Exception:
                result["phases"] = {}
            return result

    def get_telemetry_history(
        self,
        facility_id: str,
        start_time: datetime | str | None = None,
        end_time: datetime | str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve historical telemetry records within an optional time range."""
        start_str = start_time.isoformat() if isinstance(start_time, datetime) else start_time
        end_str = end_time.isoformat() if isinstance(end_time, datetime) else end_time

        query = """
        SELECT * FROM telemetry_readings
        WHERE facility_id = ?
          AND (? IS NULL OR timestamp >= ?)
          AND (? IS NULL OR timestamp <= ?)
        ORDER BY timestamp DESC, id DESC
        LIMIT ?;
        """
        with self.connection() as conn:
            rows = conn.execute(
                query, (facility_id, start_str, start_str, end_str, end_str, limit)
            ).fetchall()

            history = []
            for row in rows:
                item = dict(row)
                try:
                    item["phases"] = json.loads(item.pop("phases_json", "{}"))
                except Exception:
                    item["phases"] = {}
                history.append(item)
            return history

    def get_daily_summary(
        self,
        facility_id: str,
        target_date: date | str | None = None,
    ) -> dict[str, Any]:
        """Get accumulated daily consumption, spend, and peak surcharge breakdown."""
        if target_date is None:
            date_str = datetime.now(timezone.utc).date().isoformat()
        elif isinstance(target_date, date):
            date_str = target_date.isoformat()
        else:
            date_str = str(target_date)

        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM cost_aggregates
                WHERE facility_id = ? AND date = ?;
                """,
                (facility_id, date_str),
            ).fetchone()

            if row:
                tot_kwh = float(row["total_kwh"])
                tot_cost = float(row["total_cost_eur"])
                avg_rate = round(tot_cost / tot_kwh, 4) if tot_kwh > 0 else 0.0
                return {
                    "facility_id": facility_id,
                    "date": date_str,
                    "total_kwh": round(tot_kwh, 3),
                    "total_spend_eur": round(tot_cost, 2),
                    "peak_kwh": round(float(row["peak_kwh"]), 3),
                    "offpeak_kwh": round(float(row["offpeak_kwh"]), 3),
                    "peak_surcharges_eur": round(float(row["peak_surcharges_eur"]), 2),
                    "average_rate_eur_per_kwh": avg_rate,
                }

            # Fallback computation from raw telemetry readings if no cost aggregate row exists
            fallback = conn.execute(
                """
                SELECT
                    COUNT(*) as count,
                    COALESCE(SUM(incremental_cost_eur), 0.0) as sum_cost,
                    COALESCE(SUM(CASE WHEN is_peak_window = 1 THEN incremental_cost_eur ELSE 0.0 END), 0.0) as peak_cost,
                    COALESCE(SUM(CASE WHEN is_excess_breach = 1 THEN projected_penalty_eur ELSE 0.0 END), 0.0) as surcharges,
                    COALESCE(MIN(cumulative_energy_kwh), 0.0) as min_kwh,
                    COALESCE(MAX(cumulative_energy_kwh), 0.0) as max_kwh
                FROM telemetry_readings
                WHERE facility_id = ? AND date(timestamp) = ?;
                """,
                (facility_id, date_str),
            ).fetchone()

            if fallback and fallback["count"] > 0:
                kwh_delta = max(0.0, fallback["max_kwh"] - fallback["min_kwh"])
                sum_cost = float(fallback["sum_cost"])
                avg_rate = round(sum_cost / kwh_delta, 4) if kwh_delta > 0 else 0.0
                return {
                    "facility_id": facility_id,
                    "date": date_str,
                    "total_kwh": round(kwh_delta, 3),
                    "total_spend_eur": round(sum_cost, 2),
                    "peak_kwh": round(kwh_delta, 3),
                    "offpeak_kwh": 0.0,
                    "peak_surcharges_eur": round(float(fallback["surcharges"]), 2),
                    "average_rate_eur_per_kwh": avg_rate,
                }

            # If no data at all for this date
            return {
                "facility_id": facility_id,
                "date": date_str,
                "total_kwh": 0.0,
                "total_spend_eur": 0.0,
                "peak_kwh": 0.0,
                "offpeak_kwh": 0.0,
                "peak_surcharges_eur": 0.0,
                "average_rate_eur_per_kwh": 0.0,
            }

    def store_dam_hourly_prices(self, prices: list[dict[str, Any] | Any]) -> int:
        """Upsert 24-hour DAM hourly clearing prices."""
        if not prices:
            return 0
        inserted = 0
        with self.connection() as conn:
            for p in prices:
                item = p if isinstance(p, dict) else (p.model_dump() if hasattr(p, "model_dump") else dict(p))
                conn.execute(
                    """
                    INSERT INTO market_dam_hourly_prices (
                        date, hour, price_eur_mwh, price_eur_kwh, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, datetime('now', 'utc'))
                    ON CONFLICT(date, hour) DO UPDATE SET
                        price_eur_mwh = excluded.price_eur_mwh,
                        price_eur_kwh = excluded.price_eur_kwh,
                        source = excluded.source,
                        created_at = datetime('now', 'utc');
                    """,
                    (
                        str(item["date"]),
                        int(item["hour"]),
                        float(item["price_eur_mwh"]),
                        float(item["price_eur_kwh"]),
                        str(item.get("source", "henex_live")),
                    ),
                )
                inserted += 1
        return inserted

    def get_dam_hourly_prices(self, target_date: str) -> list[dict[str, Any]]:
        """Retrieve all DAM hourly prices for a given date ordered by hour."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT date, hour, price_eur_mwh, price_eur_kwh, source, created_at
                FROM market_dam_hourly_prices
                WHERE date = ?
                ORDER BY hour ASC;
                """,
                (target_date,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_dam_hourly_price(self, target_date: str, hour: int) -> dict[str, Any] | None:
        """Retrieve a specific hour's DAM clearing price."""
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT date, hour, price_eur_mwh, price_eur_kwh, source, created_at
                FROM market_dam_hourly_prices
                WHERE date = ? AND hour = ?
                LIMIT 1;
                """,
                (target_date, hour),
            ).fetchone()
            return dict(row) if row else None

    def get_cached_dam_dates(self) -> list[str]:
        """Return distinct dates with cached DAM hourly prices."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT date FROM market_dam_hourly_prices ORDER BY date DESC;
                """
            ).fetchall()
            return [r["date"] for r in rows]

    def store_green_tariffs(self, tariffs: list[dict[str, Any] | Any]) -> int:
        """Upsert monthly Green Tariff announcements."""
        if not tariffs:
            return 0
        inserted = 0
        with self.connection() as conn:
            for t in tariffs:
                item = t if isinstance(t, dict) else (t.model_dump() if hasattr(t, "model_dump") else dict(t))
                conn.execute(
                    """
                    INSERT INTO market_green_tariffs (
                        month, supplier_id, supplier_name, contract_type,
                        p_base, e_disc, prompt_discount_percent, alpha,
                        lu_eur_mwh, ll_eur_mwh, beta, fixed_monthly_fee_eur,
                        published_final_rate_eur_per_kwh, tea_m1_eur_mwh, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'))
                    ON CONFLICT(month, supplier_id, contract_type) DO UPDATE SET
                        supplier_name = excluded.supplier_name,
                        p_base = excluded.p_base,
                        e_disc = excluded.e_disc,
                        prompt_discount_percent = excluded.prompt_discount_percent,
                        alpha = excluded.alpha,
                        lu_eur_mwh = excluded.lu_eur_mwh,
                        ll_eur_mwh = excluded.ll_eur_mwh,
                        beta = excluded.beta,
                        fixed_monthly_fee_eur = excluded.fixed_monthly_fee_eur,
                        published_final_rate_eur_per_kwh = excluded.published_final_rate_eur_per_kwh,
                        tea_m1_eur_mwh = excluded.tea_m1_eur_mwh,
                        source = excluded.source,
                        created_at = datetime('now', 'utc');
                    """,
                    (
                        str(item["month"]),
                        str(item["supplier_id"]),
                        str(item["supplier_name"]),
                        str(item["contract_type"]),
                        float(item["p_base"]),
                        float(item.get("e_disc", 0.0)),
                        float(item.get("prompt_discount_percent", 0.0)),
                        float(item.get("alpha", 1.15)),
                        float(item.get("lu_eur_mwh", 115.0)),
                        float(item.get("ll_eur_mwh", 95.0)),
                        float(item.get("beta", 0.0)),
                        float(item.get("fixed_monthly_fee_eur", 5.0)),
                        float(item["published_final_rate_eur_per_kwh"]),
                        float(item["tea_m1_eur_mwh"]) if item.get("tea_m1_eur_mwh") is not None else None,
                        str(item.get("source", "energycost_live")),
                    ),
                )
                inserted += 1
        return inserted

    def get_green_tariffs(
        self, month: str | None = None, supplier_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve green tariffs filtered by month and/or supplier."""
        query = "SELECT * FROM market_green_tariffs WHERE 1=1"
        params: list[Any] = []
        if month:
            query += " AND month = ?"
            params.append(month)
        if supplier_id:
            query += " AND supplier_id = ?"
            params.append(supplier_id)
        query += " ORDER BY month DESC, supplier_id ASC, contract_type ASC;"

        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def get_green_tariff(
        self, month: str, supplier_id: str, contract_type: str
    ) -> dict[str, Any] | None:
        """Retrieve a specific green tariff announcement."""
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM market_green_tariffs
                WHERE month = ? AND supplier_id = ? AND contract_type = ?
                LIMIT 1;
                """,
                (month, supplier_id, contract_type),
            ).fetchone()
            return dict(row) if row else None

    def get_latest_green_month(self) -> str | None:
        """Get the most recent month present in market_green_tariffs."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT DISTINCT month FROM market_green_tariffs ORDER BY month DESC LIMIT 1;"
            ).fetchone()
            return row["month"] if row else None

    # --- Equipment Assets CRUD ---
    def create_equipment_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        """Create a new generic equipment asset for a facility."""
        now_utc = datetime.now(timezone.utc).isoformat()
        asset_id = asset.get("asset_id") or f"ast_{uuid.uuid4().hex[:8]}"
        facility_id = asset["facility_id"]
        name = asset["name"]
        category = asset.get("category", "custom")
        rated_power_kw = float(asset["rated_power_kw"])
        required_runtime_minutes = int(asset["required_runtime_minutes"])
        earliest_start = asset.get("earliest_start", "00:00")
        latest_finish = asset.get("latest_finish", "23:59")
        active_weekdays = json.dumps(asset.get("active_weekdays", [0, 1, 2, 3, 4]))
        must_run = 1 if asset.get("must_run", True) else 0
        interruptible = 1 if asset.get("interruptible", False) else 0
        priority = int(asset.get("priority", 3))
        preferred_start = asset.get("preferred_start")
        enabled = 1 if asset.get("enabled", True) else 0
        created_at = asset.get("created_at") or now_utc
        updated_at = now_utc

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO equipment_assets (
                    asset_id, facility_id, name, category, rated_power_kw,
                    required_runtime_minutes, earliest_start, latest_finish,
                    active_weekdays, must_run, interruptible, priority,
                    preferred_start, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    asset_id, facility_id, name, category, rated_power_kw,
                    required_runtime_minutes, earliest_start, latest_finish,
                    active_weekdays, must_run, interruptible, priority,
                    preferred_start, enabled, created_at, updated_at
                ),
            )
        return self.get_equipment_asset(facility_id, asset_id)  # type: ignore

    def get_equipment_assets(self, facility_id: str, enabled_only: bool = False) -> list[dict[str, Any]]:
        """List all equipment assets for a facility."""
        query = "SELECT * FROM equipment_assets WHERE facility_id = ?"
        params: list[Any] = [facility_id]
        if enabled_only:
            query += " AND enabled = 1"
        query += " ORDER BY priority DESC, created_at ASC;"

        with self.connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["must_run"] = bool(item["must_run"])
                item["interruptible"] = bool(item["interruptible"])
                item["enabled"] = bool(item["enabled"])
                item["active_weekdays"] = json.loads(item["active_weekdays"])
                results.append(item)
            return results

    def get_equipment_asset(self, facility_id: str, asset_id: str) -> dict[str, Any] | None:
        """Fetch a single equipment asset by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM equipment_assets WHERE facility_id = ? AND asset_id = ? LIMIT 1;",
                (facility_id, asset_id),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["must_run"] = bool(item["must_run"])
            item["interruptible"] = bool(item["interruptible"])
            item["enabled"] = bool(item["enabled"])
            item["active_weekdays"] = json.loads(item["active_weekdays"])
            return item

    def update_equipment_asset(self, facility_id: str, asset_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update fields of an existing equipment asset."""
        existing = self.get_equipment_asset(facility_id, asset_id)
        if not existing:
            return None

        fields: list[str] = []
        params: list[Any] = []
        for key, val in updates.items():
            if key in ("asset_id", "facility_id", "created_at"):
                continue
            if key == "active_weekdays" and isinstance(val, (list, tuple)):
                fields.append("active_weekdays = ?")
                params.append(json.dumps(val))
            elif key in ("must_run", "interruptible", "enabled"):
                fields.append(f"{key} = ?")
                params.append(1 if val else 0)
            elif key in (
                "name", "category", "rated_power_kw", "required_runtime_minutes",
                "earliest_start", "latest_finish", "priority", "preferred_start"
            ):
                fields.append(f"{key} = ?")
                params.append(val)

        if not fields:
            return existing

        fields.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())

        params.append(facility_id)
        params.append(asset_id)

        query = f"UPDATE equipment_assets SET {', '.join(fields)} WHERE facility_id = ? AND asset_id = ?;"
        with self.connection() as conn:
            conn.execute(query, tuple(params))

        return self.get_equipment_asset(facility_id, asset_id)

    def delete_equipment_asset(self, facility_id: str, asset_id: str) -> bool:
        """Delete an equipment asset."""
        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM equipment_assets WHERE facility_id = ? AND asset_id = ?;",
                (facility_id, asset_id),
            )
            return cursor.rowcount > 0

    # --- Schedule Settings CRUD ---
    def get_schedule_settings(self, facility_id: str) -> dict[str, Any] | None:
        """Retrieve facility scheduling settings, falling back to defaults if unconfigured."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM schedule_settings WHERE facility_id = ? LIMIT 1;",
                (facility_id,),
            ).fetchone()
            if row:
                return dict(row)

        # Check facility config for peak_threshold_kw default
        fac = self.get_facility_config(facility_id)
        default_power = float(fac.get("peak_threshold_kw", 25.0)) if fac else 25.0
        return {
            "facility_id": facility_id,
            "time_step_minutes": 15,
            "max_facility_power_kw": default_power,
            "objective_mode": "balanced",
            "timezone": "Europe/Athens",
            "forecast_uncertainty_pct": 10.0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def upsert_schedule_settings(self, facility_id: str, settings_dict: dict[str, Any]) -> dict[str, Any]:
        """Insert or update facility scheduling settings."""
        now_utc = datetime.now(timezone.utc).isoformat()
        current = self.get_schedule_settings(facility_id) or {}
        time_step = settings_dict.get("time_step_minutes", current.get("time_step_minutes", 15))
        max_power = float(settings_dict.get("max_facility_power_kw", current.get("max_facility_power_kw", 25.0)))
        obj_mode = settings_dict.get("objective_mode", current.get("objective_mode", "balanced"))
        tz = settings_dict.get("timezone", current.get("timezone", "Europe/Athens"))
        uncertainty = float(settings_dict.get("forecast_uncertainty_pct", current.get("forecast_uncertainty_pct", 10.0)))

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO schedule_settings (
                    facility_id, time_step_minutes, max_facility_power_kw,
                    objective_mode, timezone, forecast_uncertainty_pct, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(facility_id) DO UPDATE SET
                    time_step_minutes = excluded.time_step_minutes,
                    max_facility_power_kw = excluded.max_facility_power_kw,
                    objective_mode = excluded.objective_mode,
                    timezone = excluded.timezone,
                    forecast_uncertainty_pct = excluded.forecast_uncertainty_pct,
                    updated_at = excluded.updated_at;
                """,
                (facility_id, time_step, max_power, obj_mode, tz, uncertainty, now_utc),
            )
        return self.get_schedule_settings(facility_id)  # type: ignore

    # --- Generated Schedules CRUD ---
    def save_generated_schedule(self, schedule_dict: dict[str, Any]) -> dict[str, Any]:
        """Persist a generated daily schedule."""
        now_utc = datetime.now(timezone.utc).isoformat()
        schedule_id = schedule_dict.get("schedule_id") or f"sch_{uuid.uuid4().hex[:10]}"
        facility_id = schedule_dict["facility_id"]
        schedule_date = str(schedule_dict.get("schedule_date", datetime.now(timezone.utc).date().isoformat()))
        status = schedule_dict.get("status", "saved")
        time_step = int(schedule_dict.get("time_step_minutes", 15))
        objective_mode = schedule_dict.get("objective_mode", "balanced")
        input_source = schedule_dict.get("input_source", "forecast")
        is_demo = 1 if schedule_dict.get("is_demo", False) else 0
        baseline_cost = float(schedule_dict.get("baseline_cost_eur", 0.0))
        optimized_cost = float(schedule_dict.get("optimized_cost_eur", 0.0))
        estimated_savings = float(schedule_dict.get("estimated_savings_eur", 0.0))
        baseline_peak = float(schedule_dict.get("baseline_peak_kw", 0.0))
        optimized_peak = float(schedule_dict.get("optimized_peak_kw", 0.0))
        uncertainty_margin = float(schedule_dict.get("uncertainty_margin_kw", 0.0))
        assumptions = json.dumps(schedule_dict.get("assumptions", []))
        warnings = json.dumps(schedule_dict.get("warnings", []))
        items = json.dumps(schedule_dict.get("items", []))
        timeline = json.dumps(schedule_dict.get("timeline_load", {}))
        generated_at = schedule_dict.get("generated_at") or now_utc

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO generated_schedules (
                    schedule_id, facility_id, schedule_date, status,
                    time_step_minutes, objective_mode, input_source, is_demo,
                    baseline_cost_eur, optimized_cost_eur, estimated_savings_eur,
                    baseline_peak_kw, optimized_peak_kw, uncertainty_margin_kw,
                    assumptions_json, warnings_json, items_json, timeline_load_json,
                    generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(schedule_id) DO UPDATE SET
                    status = excluded.status,
                    optimized_cost_eur = excluded.optimized_cost_eur,
                    estimated_savings_eur = excluded.estimated_savings_eur,
                    optimized_peak_kw = excluded.optimized_peak_kw,
                    items_json = excluded.items_json,
                    timeline_load_json = excluded.timeline_load_json;
                """,
                (
                    schedule_id, facility_id, schedule_date, status,
                    time_step, objective_mode, input_source, is_demo,
                    baseline_cost, optimized_cost, estimated_savings,
                    baseline_peak, optimized_peak, uncertainty_margin,
                    assumptions, warnings, items, timeline,
                    generated_at
                ),
            )
        return self.get_generated_schedule(facility_id, schedule_id)  # type: ignore

    def get_generated_schedules(self, facility_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """List historical generated and saved schedules for a facility."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT schedule_id, facility_id, schedule_date, status,
                       time_step_minutes, objective_mode, input_source, is_demo,
                       baseline_cost_eur, optimized_cost_eur, estimated_savings_eur,
                       baseline_peak_kw, optimized_peak_kw, uncertainty_margin_kw,
                       generated_at
                FROM generated_schedules
                WHERE facility_id = ?
                ORDER BY generated_at DESC
                LIMIT ?;
                """,
                (facility_id, limit),
            ).fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["is_demo"] = bool(item["is_demo"])
                results.append(item)
            return results

    def get_generated_schedule(self, facility_id: str, schedule_id: str) -> dict[str, Any] | None:
        """Fetch a specific schedule and its full details."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM generated_schedules WHERE facility_id = ? AND schedule_id = ? LIMIT 1;",
                (facility_id, schedule_id),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["is_demo"] = bool(item["is_demo"])
            item["assumptions"] = json.loads(item.pop("assumptions_json"))
            item["warnings"] = json.loads(item.pop("warnings_json"))
            item["items"] = json.loads(item.pop("items_json"))
            item["timeline_load"] = json.loads(item.pop("timeline_load_json"))
            return item


# Global store instance registry
_stores: dict[str, SQLiteStore] = {}
_stores_lock = threading.Lock()


def get_store(db_path: str | Path | None = None) -> SQLiteStore:
    """Retrieve or create a SQLiteStore instance for the given database path."""
    key = str(db_path or settings.SQLITE_DB_PATH)
    with _stores_lock:
        if key not in _stores:
            _stores[key] = SQLiteStore(key)
        return _stores[key]


# Top-level functional wrappers matching requirement interface
def init_db(db_path: str | Path | None = None) -> None:
    """Initialize database schema and pragmas."""
    store = get_store(db_path)
    store.init_db()


def store_telemetry(
    payload: TelemetryPayload | dict[str, Any],
    cost_result: Any | None = None,
    db_path: str | Path | None = None,
) -> int:
    """Store telemetry reading and update daily aggregates."""
    return get_store(db_path).store_telemetry(payload, cost_result)


def get_latest_telemetry(
    facility_id: str,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Fetch latest telemetry reading for facility."""
    return get_store(db_path).get_latest_telemetry(facility_id)


def get_telemetry_history(
    facility_id: str,
    start_time: datetime | str | None = None,
    end_time: datetime | str | None = None,
    limit: int = 100,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch telemetry history for facility."""
    return get_store(db_path).get_telemetry_history(facility_id, start_time, end_time, limit)


def get_daily_summary(
    facility_id: str,
    target_date: date | str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Fetch daily consumption and spend summary."""
    return get_store(db_path).get_daily_summary(facility_id, target_date)


def store_facility_config(
    config: dict[str, Any] | Any,
    db_path: str | Path | None = None,
) -> None:
    """Store or update facility configuration."""
    get_store(db_path).store_facility_config(config)


def get_facility_config(
    facility_id: str,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Fetch facility configuration."""
    return get_store(db_path).get_facility_config(facility_id)


def list_facility_configs(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """List all facility configurations."""
    return get_store(db_path).list_facility_configs()


def seed_default_facilities(db_path: str | Path | None = None) -> None:
    """Seed default facilities into database."""
    get_store(db_path).seed_default_facilities()


def store_dam_hourly_prices(
    prices: list[dict[str, Any] | Any],
    db_path: str | Path | None = None,
) -> int:
    """Store 24-hour DAM hourly prices."""
    return get_store(db_path).store_dam_hourly_prices(prices)


def get_dam_hourly_prices(
    target_date: str,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch DAM hourly prices for a given date."""
    return get_store(db_path).get_dam_hourly_prices(target_date)


def get_dam_hourly_price(
    target_date: str,
    hour: int,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Fetch DAM hourly price for a specific hour."""
    return get_store(db_path).get_dam_hourly_price(target_date, hour)


def get_cached_dam_dates(db_path: str | Path | None = None) -> list[str]:
    """Fetch distinct dates with cached DAM prices."""
    return get_store(db_path).get_cached_dam_dates()


def store_green_tariffs(
    tariffs: list[dict[str, Any] | Any],
    db_path: str | Path | None = None,
) -> int:
    """Store monthly Green Tariff announcements."""
    return get_store(db_path).store_green_tariffs(tariffs)


def get_green_tariffs(
    month: str | None = None,
    supplier_id: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch green tariffs filtered by month and/or supplier."""
    return get_store(db_path).get_green_tariffs(month, supplier_id)


def get_green_tariff(
    month: str,
    supplier_id: str,
    contract_type: str,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Fetch specific green tariff announcement."""
    return get_store(db_path).get_green_tariff(month, supplier_id, contract_type)


def get_latest_green_month(db_path: str | Path | None = None) -> str | None:
    """Get the latest green month recorded in store."""
    return get_store(db_path).get_latest_green_month()
