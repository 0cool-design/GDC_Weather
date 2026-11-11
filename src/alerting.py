"""Stale-data and anomaly alerting for GDC Weather Monitor.

Fires a webhook (POST JSON) or writes an alert log entry when:
- No new data received for STALE_THRESHOLD seconds
- A sensor reading is outside expected bounds
- Station comes back online after being offline

Configure via .env:
  ALERT_WEBHOOK_URL=https://hooks.slack.com/...  (optional)
  ALERT_STALE_MINUTES=15                          (default 15)
"""

import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

STALE_THRESHOLD = int(os.getenv("ALERT_STALE_MINUTES", "15")) * 60

# Sanity bounds — readings outside these trigger an alert
BOUNDS: dict[str, tuple[float, float]] = {
    "temp": (-60, 150),          # °F
    "humidity": (0, 100),        # %
    "bar_sea_level": (25.0, 32.0),  # inHg
    "wind_speed_last": (0, 250),    # mph
    "uv_index": (0, 20),
    "solar_rad": (0, 2000),         # W/m²
}


class AlertManager:
    def __init__(self, webhook_url: str | None = None):
        self._webhook = webhook_url or os.getenv("ALERT_WEBHOOK_URL", "")
        self._last_data_ts: float = time.time()
        self._was_stale: bool = False

    def record_poll(self, records: list[dict[str, Any]]):
        """Call after every successful poll, even if records is empty."""
        if records:
            self._last_data_ts = time.time()
            if self._was_stale:
                self._fire("RECOVERED", "Station back online — data is flowing again.")
                self._was_stale = False
            self._check_bounds(records)

    def check_stale(self):
        """Call periodically (e.g. every minute) from a background timer."""
        age = time.time() - self._last_data_ts
        if age > STALE_THRESHOLD and not self._was_stale:
            self._was_stale = True
            mins = int(age // 60)
            self._fire("STALE", f"No weather data received for {mins} minutes.")

    def _check_bounds(self, records: list[dict[str, Any]]):
        merged: dict[str, Any] = {}
        for r in records:
            merged.update({k: v for k, v in r.items() if v is not None})

        for field, (lo, hi) in BOUNDS.items():
            val = merged.get(field)
            if val is None:
                continue
            if not (lo <= float(val) <= hi):
                self._fire(
                    "ANOMALY",
                    f"Sensor reading out of bounds: {field}={val} (expected {lo}–{hi})",
                )

    def _fire(self, level: str, message: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        full = f"[{ts}] GDC WEATHER {level}: {message}"
        logger.warning(full)

        if not self._webhook:
            return

        payload = {"text": full}
        try:
            r = requests.post(self._webhook, json=payload, timeout=5)
            if not r.ok:
                logger.warning("Alert webhook returned %s", r.status_code)
        except Exception as e:
            logger.warning("Alert webhook failed: %s", e)
