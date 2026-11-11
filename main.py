#!/usr/bin/env python3
"""GDC Weather Monitoring System — entry point.

Usage:
    python main.py                      # Start continuous monitor
    python main.py --list-stations      # Print all stations linked to your API key
    python main.py --backfill DAYS      # Pull DAYS days of historic data into DB
    python main.py --show-latest        # Print most recent DB record
"""

import argparse
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging setup — file + console
# ---------------------------------------------------------------------------

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.TimedRotatingFileHandler(
            filename=LOG_DIR / "gdc_weather.log",
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        ),
    ],
)

logger = logging.getLogger("gdc_weather")


# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

def _require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        logger.error("Missing required env var: %s  (set it in .env)", key)
        sys.exit(1)
    return val


def build_config() -> dict:
    cfg = {
        "api_key": _require_env("WEATHERLINK_API_KEY"),
        "api_secret": _require_env("WEATHERLINK_API_SECRET"),
        "api_token_v1": os.getenv("WEATHERLINK_API_TOKEN_V1", ""),
        "wl_username": os.getenv("WEATHERLINK_USERNAME", ""),
        "wl_password": os.getenv("WEATHERLINK_PASSWORD", ""),
        "station_id": os.getenv("WEATHERLINK_STATION_ID", ""),
        "station_uuid": os.getenv("WEATHERLINK_STATION_UUID", ""),
        # v1 Device ID (WeatherLinkIP/USB/SER) — found in weatherlink.com station settings
        "did": os.getenv("WEATHERLINK_DID", ""),
        "poll_interval": int(os.getenv("POLL_INTERVAL", "300")),
        "db_path": Path("data") / "weather.db",
        "log_dir": LOG_DIR,
        "lp_dir": Path("data") / "influx_lp",
        "influx_client": None,
        "influx_org": os.getenv("INFLUXDB_ORG", "GDC"),
        "influx_bucket": os.getenv("INFLUXDB_BUCKET", "weather"),
        "alert_webhook": os.getenv("ALERT_WEBHOOK_URL", ""),
        "metrics_port": int(os.getenv("METRICS_PORT", "9001")),
    }

    if os.getenv("INFLUXDB_ENABLED", "false").lower() == "true":
        try:
            from influxdb_client import InfluxDBClient
            cfg["influx_client"] = InfluxDBClient(
                url=os.getenv("INFLUXDB_URL", "http://localhost:8086"),
                token=_require_env("INFLUXDB_TOKEN"),
                org=cfg["influx_org"],
            )
            logger.info("InfluxDB client connected: %s", os.getenv("INFLUXDB_URL"))
        except ImportError:
            logger.warning("influxdb-client not installed; live InfluxDB push disabled")

    return cfg


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_list_stations(cfg: dict):
    from src.client import WeatherLinkClient
    client = WeatherLinkClient(cfg["api_key"], cfg["api_secret"])
    stations = client.list_stations()
    if not stations:
        print("No stations found.")
        return
    print(f"\n{'ID':<20}  {'Name':<40}  {'City':<20}  {'State'}")
    print("-" * 100)
    for s in stations:
        print(
            f"{s.get('station_id', '')!s:<20}  "
            f"{s.get('station_name', ''):<40}  "
            f"{s.get('city', ''):<20}  "
            f"{s.get('state', '')}"
        )
    print(f"\nSet WEATHERLINK_STATION_ID in .env to one of the IDs above.\n")


def cmd_backfill(cfg: dict, days: int):
    """Pull historic data for the past N days and store in SQLite + logs."""
    from src.client import WeatherLinkClient, WeatherLinkError
    from src.processor import parse_historic
    from src.storage import SQLiteStorage, JsonLogger, InfluxLineProtocolWriter

    if not cfg["station_id"]:
        logger.error("WEATHERLINK_STATION_ID not set. Run --list-stations first.")
        sys.exit(1)

    client = WeatherLinkClient(cfg["api_key"], cfg["api_secret"])
    db = SQLiteStorage(cfg["db_path"])
    jlog = JsonLogger(cfg["log_dir"])
    lp = InfluxLineProtocolWriter(cfg["lp_dir"])

    now = datetime.now(tz=timezone.utc)
    # WeatherLink historic endpoint max window = 24 hours per request
    total_inserted = 0
    chunk_hours = 24

    total_hours = days * 24
    logger.info("Backfilling %d days (%d chunks) for station %s", days, total_hours // chunk_hours, cfg["station_id"])

    for chunk in range(0, total_hours, chunk_hours):
        end_dt = now - timedelta(hours=chunk)
        start_dt = end_dt - timedelta(hours=chunk_hours)
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())

        logger.info(
            "Chunk %d/%d  %s → %s",
            chunk // chunk_hours + 1, total_hours // chunk_hours,
            start_dt.strftime("%Y-%m-%d %H:%M"),
            end_dt.strftime("%Y-%m-%d %H:%M"),
        )

        try:
            raw = client.historic(cfg["station_id"], start_ts, end_ts)
        except WeatherLinkError as e:
            logger.warning("Skipping chunk: %s", e)
            continue

        records = parse_historic(raw)
        inserted = db.insert(records)
        jlog.write(records)
        lp.write(records)
        total_inserted += inserted
        logger.info("  → %d records (%d new)", len(records), inserted)

    db.close()
    logger.info("Backfill complete: %d total new rows", total_inserted)


def cmd_show_latest(cfg: dict):
    from src.storage import SQLiteStorage
    from src.processor import summary_line
    db = SQLiteStorage(cfg["db_path"])
    rec = db.latest(cfg.get("station_uuid") or None)
    db.close()
    if rec:
        print(summary_line(rec))
    else:
        print("No data in database yet.")


def cmd_probe_v1(cfg: dict):
    """Test the v1 API with a DID to verify connectivity."""
    if not cfg["did"]:
        print("Set WEATHERLINK_DID in .env first (find it at weatherlink.com → My Stations → Device ID).")
        sys.exit(1)
    from src.client import WeatherLinkV1Client, WeatherLinkError
    client = WeatherLinkV1Client(
        cfg["api_token_v1"], cfg["did"],
        username=cfg.get("wl_username", ""),
        password=cfg.get("wl_password", ""),
    )
    try:
        data = client.current_conditions()
        import json
        print(json.dumps(data, indent=2)[:3000])
    except WeatherLinkError as e:
        logger.error("v1 API error: %s", e)


def cmd_monitor(cfg: dict):
    if not cfg["station_id"] and not cfg["did"]:
        logger.error(
            "No station configured.\n"
            "  For v2 stations (WeatherLink Live / AirLink): run --list-stations, then set WEATHERLINK_STATION_ID in .env\n"
            "  For v1 stations (WeatherLinkIP/USB/SER): set WEATHERLINK_DID in .env (find at weatherlink.com → My Stations)"
        )
        sys.exit(1)
    from src.monitor import WeatherMonitor
    WeatherMonitor(cfg).run()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="GDC Weather Monitoring System")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list-stations", action="store_true", help="List all stations linked to this API key")
    group.add_argument("--backfill", type=int, metavar="DAYS", help="Pull N days of historic data into the database")
    group.add_argument("--show-latest", action="store_true", help="Print the most recent reading from the database")
    group.add_argument("--probe-v1", action="store_true", help="Test v1 API with WEATHERLINK_DID (for WeatherLinkIP/USB/SER stations)")
    args = parser.parse_args()

    cfg = build_config()

    if args.list_stations:
        cmd_list_stations(cfg)
    elif args.backfill:
        cmd_backfill(cfg, args.backfill)
    elif args.show_latest:
        cmd_show_latest(cfg)
    elif args.probe_v1:
        cmd_probe_v1(cfg)
    else:
        cmd_monitor(cfg)


if __name__ == "__main__":
    main()
