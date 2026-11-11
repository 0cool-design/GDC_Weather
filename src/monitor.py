"""Main monitoring loop — polls WeatherLink API and stores results."""

import logging
import signal
import time
from datetime import datetime, timezone
from typing import Any

import schedule
from rich.console import Console
from rich.table import Table
from rich import box

from .client import WeatherLinkClient, WeatherLinkV1Client, WeatherLinkError
from .processor import parse_current, parse_v1_current, summary_line
from .storage import SQLiteStorage, JsonLogger, InfluxLineProtocolWriter

logger = logging.getLogger(__name__)
console = Console()


class WeatherMonitor:
    def __init__(self, config: dict[str, Any]):
        self.station_id = config.get("station_id", "")
        self.did = config.get("did", "")
        self.poll_interval = config.get("poll_interval", 300)

        # Auto-select API version
        if self.did:
            self.client = WeatherLinkV1Client(
                api_token=config["api_token_v1"],
                did=self.did,
            )
            self._api_version = 1
            logger.info("Using WeatherLink v1 API (DID: %s)", self.did)
        else:
            self.client = WeatherLinkClient(
                api_key=config["api_key"],
                api_secret=config["api_secret"],
            )
            self._api_version = 2
            logger.info("Using WeatherLink v2 API (station: %s)", self.station_id)
        self.db = SQLiteStorage(config["db_path"])
        self.json_log = JsonLogger(config["log_dir"])
        self.lp_writer = InfluxLineProtocolWriter(
            lp_dir=config["lp_dir"],
            influx_client=config.get("influx_client"),
            org=config.get("influx_org", "GDC"),
            bucket=config.get("influx_bucket", "weather"),
        )

        self._running = False
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, _frame):
        logger.info("Signal %d received — shutting down.", signum)
        self._running = False

    def fetch_and_store(self):
        """Single poll cycle: fetch → parse → store → log."""
        try:
            if self._api_version == 1:
                logger.info("Fetching current conditions (v1) for DID %s", self.did)
                raw = self.client.current_conditions()
                records = parse_v1_current(raw, self.did)
            else:
                logger.info("Fetching current conditions (v2) for station %s", self.station_id)
                raw = self.client.current_conditions(self.station_id)
                records = parse_current(raw)
        except WeatherLinkError as e:
            logger.error("API error: %s", e)
            return
        if not records:
            logger.warning("No records parsed from API response")
            return

        inserted = self.db.insert(records)
        self.json_log.write(records)
        self.lp_writer.write(records)

        logger.info(
            "Poll complete: %d sensors, %d new DB rows",
            len(records), inserted,
        )
        self._print_summary(records)

    def _print_summary(self, records: list[dict]):
        """Print a rich table of the latest readings."""
        table = Table(
            title=f"GDC Weather — {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            box=box.SIMPLE_HEAVY,
            show_header=True,
        )
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="white")

        # Merge all records into one view (last value wins per key)
        merged: dict[str, Any] = {}
        for r in records:
            merged.update({k: v for k, v in r.items() if v is not None})

        display = [
            ("Temperature", "temp", "°F"),
            ("Temp (indoor)", "temp_in", "°F"),
            ("Humidity", "humidity", "%"),
            ("Dew Point", "dew_point", "°F"),
            ("Wind Speed", "wind_speed_last", " mph"),
            ("Wind Dir", "wind_dir_last", "°"),
            ("Wind 10-min avg", "wind_speed_avg_last_10_min", " mph"),
            ("Barometer", "bar_sea_level", " inHg"),
            ("Rain Rate", "rain_rate_last", " in/hr"),
            ("Rain Today", "rainfall_daily", " in"),
            ("Solar Radiation", "solar_rad", " W/m²"),
            ("UV Index", "uv_index", ""),
        ]

        for label, key, unit in display:
            val = merged.get(key)
            if val is not None:
                if isinstance(val, float):
                    table.add_row(label, f"{val:.2f}{unit}")
                else:
                    table.add_row(label, f"{val}{unit}")

        console.print(table)

    def run(self):
        """Start the polling loop."""
        self._running = True
        console.rule("[bold green]GDC Weather Monitor Starting[/bold green]")
        logger.info(
            "Monitor started — station=%s  interval=%ds",
            self.station_id, self.poll_interval,
        )

        # Run immediately on start, then on schedule
        self.fetch_and_store()
        schedule.every(self.poll_interval).seconds.do(self.fetch_and_store)

        while self._running:
            schedule.run_pending()
            time.sleep(1)

        logger.info("Monitor stopped.")
        self.db.close()
