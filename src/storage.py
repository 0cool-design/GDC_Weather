"""Persist weather records to SQLite and rotating JSON logs.

SQLite schema is intentionally wide — all known fields in one table so
Grafana / InfluxDB migrations are a simple SELECT *.

For Grafana: the InfluxDB writer uses line protocol so you can point
Telegraf or the InfluxDB CLI at the generated .lp files later.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS weather_observations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    station_uuid     TEXT,
    sensor_type      INTEGER,
    sensor_lsid      INTEGER,
    timestamp        INTEGER NOT NULL,
    timestamp_iso    TEXT,
    -- Temperature / Humidity
    temp             REAL,
    temp_in          REAL,
    dew_point        REAL,
    wet_bulb         REAL,
    heat_index       REAL,
    wind_chill       REAL,
    thw_index        REAL,
    thsw_index       REAL,
    humidity         REAL,
    humidity_in      REAL,
    -- Wind
    wind_speed_last                      REAL,
    wind_dir_last                        INTEGER,
    wind_speed_avg_last_1_min            REAL,
    wind_speed_avg_last_2_min            REAL,
    wind_speed_avg_last_10_min           REAL,
    wind_dir_scalar_avg_last_1_min       INTEGER,
    wind_dir_scalar_avg_last_10_min      INTEGER,
    wind_speed_hi_last_10_min            REAL,
    wind_dir_at_hi_speed_last_10_min     INTEGER,
    -- Rain
    rain_rate_last           REAL,
    rain_rate_hi             REAL,
    rainfall_last_15_min     REAL,
    rain_rate_hi_last_15_min REAL,
    rainfall_last_60_min     REAL,
    rainfall_last_24_hr      REAL,
    rain_storm               REAL,
    rain_storm_start_at      INTEGER,
    rainfall_daily           REAL,
    rainfall_monthly         REAL,
    rainfall_year            REAL,
    -- Pressure
    bar_sea_level  REAL,
    bar_trend      REAL,
    bar_absolute   REAL,
    -- Solar / UV
    solar_rad      REAL,
    uv_index       REAL,
    solar_energy_day REAL,
    uv_dose_day    REAL,
    et_day         REAL,
    et_month       REAL,
    et_year        REAL,
    -- Status
    trans_battery_flag INTEGER,
    rx_state           INTEGER,
    rssi               REAL,
    UNIQUE(sensor_lsid, timestamp)
)
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_weather_ts ON weather_observations(timestamp)
"""


class SQLiteStorage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(CREATE_TABLE)
        self._conn.execute(CREATE_INDEX)
        self._conn.commit()
        logger.info("SQLite database: %s", self.db_path)

    def insert(self, records: list[dict[str, Any]]) -> int:
        """Insert records, skipping duplicates. Returns count inserted."""
        if not records:
            return 0

        cols = [
            "station_uuid", "sensor_type", "sensor_lsid", "timestamp", "timestamp_iso",
            "temp", "temp_in", "dew_point", "wet_bulb", "heat_index", "wind_chill",
            "thw_index", "thsw_index", "humidity", "humidity_in",
            "wind_speed_last", "wind_dir_last", "wind_speed_avg_last_1_min",
            "wind_speed_avg_last_2_min", "wind_speed_avg_last_10_min",
            "wind_dir_scalar_avg_last_1_min", "wind_dir_scalar_avg_last_10_min",
            "wind_speed_hi_last_10_min", "wind_dir_at_hi_speed_last_10_min",
            "rain_rate_last", "rain_rate_hi", "rainfall_last_15_min",
            "rain_rate_hi_last_15_min", "rainfall_last_60_min", "rainfall_last_24_hr",
            "rain_storm", "rain_storm_start_at", "rainfall_daily", "rainfall_monthly",
            "rainfall_year", "bar_sea_level", "bar_trend", "bar_absolute",
            "solar_rad", "uv_index", "solar_energy_day", "uv_dose_day",
            "et_day", "et_month", "et_year",
            "trans_battery_flag", "rx_state", "rssi",
        ]
        placeholders = ",".join("?" * len(cols))
        sql = (
            f"INSERT OR REPLACE INTO weather_observations ({','.join(cols)}) "
            f"VALUES ({placeholders})"
        )
        rows = [[r.get(c) for c in cols] for r in records]
        cur = self._conn.executemany(sql, rows)
        self._conn.commit()
        inserted = cur.rowcount
        logger.debug("SQLite: inserted %d / %d records", inserted, len(records))
        return inserted

    def latest(self, station_uuid: str | None = None) -> dict | None:
        """Return most recent observation, merging all sensors from the latest timestamp."""
        base = "WHERE station_uuid=?" if station_uuid else ""
        args = (station_uuid,) if station_uuid else ()

        ts_row = self._conn.execute(
            f"SELECT MAX(timestamp) FROM weather_observations {base}", args
        ).fetchone()
        if not ts_row or ts_row[0] is None:
            return None
        latest_ts = ts_row[0]

        rows = self._conn.execute(
            f"SELECT * FROM weather_observations {base} AND timestamp=?"
            if station_uuid else
            "SELECT * FROM weather_observations WHERE timestamp=?",
            (station_uuid, latest_ts) if station_uuid else (latest_ts,),
        ).fetchall()

        merged: dict = {}
        for row in rows:
            for k, v in dict(row).items():
                if v is not None:
                    merged[k] = v
        return merged if merged else None

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Rotating JSON log
# ---------------------------------------------------------------------------

class JsonLogger:
    """Append-only newline-delimited JSON log, rotating daily."""

    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _path(self) -> Path:
        date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        return self.log_dir / f"weather_{date_str}.jsonl"

    def write(self, records: list[dict[str, Any]]):
        if not records:
            return
        path = self._path()
        with open(path, "a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        logger.debug("JSON log: wrote %d records to %s", len(records), path)


# ---------------------------------------------------------------------------
# InfluxDB line protocol exporter (Grafana-ready)
# ---------------------------------------------------------------------------

class InfluxLineProtocolWriter:
    """Write records as InfluxDB line protocol (.lp) for future Grafana import.

    If INFLUXDB_ENABLED=true the writer also pushes to a live InfluxDB instance.
    """

    NUMERIC_FIELDS = [
        "temp", "temp_in", "dew_point", "wet_bulb", "heat_index", "wind_chill",
        "thw_index", "thsw_index", "humidity", "humidity_in",
        "wind_speed_last", "wind_dir_last", "wind_speed_avg_last_1_min",
        "wind_speed_avg_last_2_min", "wind_speed_avg_last_10_min",
        "wind_dir_scalar_avg_last_1_min", "wind_dir_scalar_avg_last_10_min",
        "wind_speed_hi_last_10_min", "wind_dir_at_hi_speed_last_10_min",
        "rain_rate_last", "rain_rate_hi", "rainfall_last_15_min",
        "rainfall_last_60_min", "rainfall_last_24_hr", "rain_storm",
        "rainfall_daily", "rainfall_monthly", "rainfall_year",
        "bar_sea_level", "bar_trend", "bar_absolute",
        "solar_rad", "uv_index", "et_day", "et_month", "et_year",
    ]

    def __init__(self, lp_dir: str | Path, influx_client=None, org: str = "", bucket: str = ""):
        self.lp_dir = Path(lp_dir)
        self.lp_dir.mkdir(parents=True, exist_ok=True)
        self._influx = influx_client
        self._org = org
        self._bucket = bucket

    def _to_lp(self, record: dict[str, Any]) -> str | None:
        tags = {}
        if record.get("station_uuid"):
            tags["station"] = record["station_uuid"]
        if record.get("sensor_type") is not None:
            tags["sensor_type"] = str(record["sensor_type"])

        fields = {}
        for f in self.NUMERIC_FIELDS:
            if record.get(f) is not None:
                fields[f] = float(record[f])

        if not fields:
            return None

        tag_str = ",".join(f"{k}={v}" for k, v in tags.items())
        field_str = ",".join(f"{k}={v}" for k, v in fields.items())
        ts_ns = record["timestamp"] * 1_000_000_000

        meas = "weather"
        return f"{meas},{tag_str} {field_str} {ts_ns}" if tag_str else f"{meas} {field_str} {ts_ns}"

    def write(self, records: list[dict[str, Any]]):
        lines = [lp for r in records if (lp := self._to_lp(r))]
        if not lines:
            return

        # Append to daily .lp file (Telegraf/CLI import later)
        date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        lp_path = self.lp_dir / f"weather_{date_str}.lp"
        with open(lp_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

        # Push to live InfluxDB if configured
        if self._influx:
            try:
                write_api = self._influx.write_api()
                write_api.write(bucket=self._bucket, org=self._org, record="\n".join(lines))
                logger.debug("InfluxDB: pushed %d points", len(lines))
            except Exception as e:
                logger.warning("InfluxDB write failed: %s", e)
