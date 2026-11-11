"""Normalize WeatherLink sensor records into a flat, typed dict."""

import time
from datetime import datetime, timezone
from typing import Any

# Sensor type IDs we care about (WeatherLink v2 spec)
# Type 1  = WeatherLink ISS / VP2
# Type 2  = WeatherLink Leaf & Soil
# Type 3  = WeatherLink Wireless / Vantage Vue
# Type 4  = WeatherLink Soil Moisture
# Type 15 = WeatherLink WLL Barometric Pressure
# Type 37 = WeatherLink Barometric Pressure (WLL)
_PRIORITY_SENSOR_TYPES = {1, 2, 3, 15, 37, 45, 55}


def _ts_to_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def parse_current(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse a /current response into a list of normalized records."""
    records = []
    for sensor in raw.get("sensors", []):
        for obs in sensor.get("data", []):
            rec = _flatten_obs(obs, sensor, raw.get("station_id_uuid", ""))
            if rec:
                records.append(rec)
    return records


def parse_historic(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse a /historic response into normalized records."""
    records = []
    for sensor in raw.get("sensors", []):
        for obs in sensor.get("data", []):
            rec = _flatten_obs(obs, sensor, raw.get("station_id_uuid", ""))
            if rec:
                records.append(rec)
    return records


def _flatten_obs(obs: dict, sensor: dict, station_uuid: str) -> dict[str, Any] | None:
    ts = obs.get("ts")
    if ts is None:
        return None

    record: dict[str, Any] = {
        "station_uuid": station_uuid,
        "sensor_type": sensor.get("sensor_type"),
        "sensor_lsid": sensor.get("lsid"),
        "timestamp": ts,
        "timestamp_iso": _ts_to_iso(ts),
    }

    # Temperature & Humidity
    for field in ("temp", "temp_in", "dew_point", "wet_bulb", "heat_index",
                  "wind_chill", "thw_index", "thsw_index"):
        if field in obs:
            record[field] = obs[field]

    if "hum" in obs:
        record["humidity"] = obs["hum"]
    if "hum_in" in obs:
        record["humidity_in"] = obs["hum_in"]

    # Wind
    for field in ("wind_speed_last", "wind_dir_last", "wind_speed_avg_last_1_min",
                  "wind_speed_avg_last_2_min", "wind_speed_avg_last_10_min",
                  "wind_dir_scalar_avg_last_1_min", "wind_dir_scalar_avg_last_10_min",
                  "wind_speed_hi_last_10_min", "wind_dir_at_hi_speed_last_10_min"):
        if field in obs:
            record[field] = obs[field]

    # Rain
    for field in ("rain_rate_last", "rain_rate_hi", "rainfall_last_15_min",
                  "rain_rate_hi_last_15_min", "rainfall_last_60_min",
                  "rainfall_last_24_hr", "rain_storm", "rain_storm_start_at",
                  "rainfall_daily", "rainfall_monthly", "rainfall_year"):
        if field in obs:
            record[field] = obs[field]

    # Pressure
    for field in ("bar_sea_level", "bar_trend", "bar_absolute"):
        if field in obs:
            record[field] = obs[field]

    # Solar / UV
    for field in ("solar_rad", "uv_index", "solar_energy_day", "uv_dose_day",
                  "et_day", "et_month", "et_year"):
        if field in obs:
            record[field] = obs[field]

    # Battery / signal
    for field in ("trans_battery_flag", "rx_state", "rssi"):
        if field in obs:
            record[field] = obs[field]

    return record


def parse_v1_current(raw: dict[str, Any], did: str) -> list[dict[str, Any]]:
    """Parse a WeatherLink v1 current-conditions response.

    v1 returns a flat structure under 'conditions' or directly at root level.
    """
    records = []

    # v1 wraps data in 'conditions' list or returns a single flat dict
    conditions = raw.get("conditions") or raw.get("data", [])
    if isinstance(conditions, dict):
        conditions = [conditions]

    ts = raw.get("ts") or raw.get("timestamp") or int(time.time())

    for cond in conditions:
        rec: dict[str, Any] = {
            "station_uuid": did,
            "sensor_type": cond.get("data_structure_type", 1),
            "sensor_lsid": cond.get("lsid"),
            "timestamp": ts,
            "timestamp_iso": _ts_to_iso(ts),
        }

        # Map v1 field names (same as v2 for most fields)
        field_map = {
            "temp": "temp",
            "temp_in": "temp_in",
            "hum": "humidity",
            "hum_in": "humidity_in",
            "dew_point": "dew_point",
            "wet_bulb": "wet_bulb",
            "heat_index": "heat_index",
            "wind_chill": "wind_chill",
            "wind_speed_last": "wind_speed_last",
            "wind_dir_last": "wind_dir_last",
            "wind_speed_avg_last_1_min": "wind_speed_avg_last_1_min",
            "wind_speed_avg_last_2_min": "wind_speed_avg_last_2_min",
            "wind_speed_avg_last_10_min": "wind_speed_avg_last_10_min",
            "wind_dir_scalar_avg_last_1_min": "wind_dir_scalar_avg_last_1_min",
            "wind_dir_scalar_avg_last_10_min": "wind_dir_scalar_avg_last_10_min",
            "wind_speed_hi_last_10_min": "wind_speed_hi_last_10_min",
            "wind_dir_at_hi_speed_last_10_min": "wind_dir_at_hi_speed_last_10_min",
            "rain_rate_last": "rain_rate_last",
            "rain_rate_hi": "rain_rate_hi",
            "rainfall_last_15_min": "rainfall_last_15_min",
            "rainfall_last_60_min": "rainfall_last_60_min",
            "rainfall_last_24_hr": "rainfall_last_24_hr",
            "rain_storm": "rain_storm",
            "rainfall_daily": "rainfall_daily",
            "rainfall_monthly": "rainfall_monthly",
            "rainfall_year": "rainfall_year",
            "bar_sea_level": "bar_sea_level",
            "bar_trend": "bar_trend",
            "bar_absolute": "bar_absolute",
            "solar_rad": "solar_rad",
            "uv_index": "uv_index",
            "et_day": "et_day",
            "et_month": "et_month",
            "et_year": "et_year",
            "trans_battery_flag": "trans_battery_flag",
            "rx_state": "rx_state",
        }

        for src, dst in field_map.items():
            if src in cond and cond[src] is not None:
                rec[dst] = cond[src]

        # Handle v1 flat-root fields (some devices put data at top level)
        if len(rec) <= 5:
            for src, dst in field_map.items():
                if src in raw and raw[src] is not None:
                    rec[dst] = raw[src]

        records.append(rec)

    if not records:
        # Fallback: treat the whole dict as a single observation
        rec = {
            "station_uuid": did,
            "sensor_type": 1,
            "sensor_lsid": None,
            "timestamp": ts,
            "timestamp_iso": _ts_to_iso(ts),
        }
        for key, val in raw.items():
            if isinstance(val, (int, float)) and key not in ("ts", "timestamp", "did"):
                rec[key] = val
        records.append(rec)

    return records


def summary_line(record: dict[str, Any]) -> str:
    """One-line human-readable summary of a weather record."""
    parts = [f"[{record['timestamp_iso']}]"]

    if "temp" in record:
        parts.append(f"Temp={record['temp']:.1f}°F")
    if "humidity" in record:
        parts.append(f"Hum={record['humidity']:.0f}%")
    if "wind_speed_last" in record:
        parts.append(f"Wind={record['wind_speed_last']:.1f}mph@{record.get('wind_dir_last', '?')}°")
    if "bar_sea_level" in record:
        parts.append(f"Bar={record['bar_sea_level']:.2f}inHg")
    if "rain_rate_last" in record:
        parts.append(f"Rain={record['rain_rate_last']:.2f}in/hr")
    if "solar_rad" in record:
        parts.append(f"Solar={record['solar_rad']}W/m²")
    if "uv_index" in record:
        parts.append(f"UV={record['uv_index']:.1f}")

    return "  ".join(parts)
