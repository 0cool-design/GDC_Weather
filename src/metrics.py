"""Prometheus metrics endpoint for GDC Weather Monitor.

Exposes current sensor readings at http://localhost:9001/metrics
so Prometheus can scrape it — same pattern as rs485-grafana.

Grafana → Prometheus datasource → scrape :9001/metrics
"""

import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger(__name__)

_latest: dict[str, Any] = {}
LOCATION = os.getenv("STATION_LOCATION", "Mirbat")
_lock = threading.Lock()

# (name label, record field, description). Every reading is one series of the single
# metric `weather_station`, told apart by its `name` label; the unit is the name's suffix.
GAUGE_FIELDS = [
    ("temperature_f", "temp", "Outdoor temperature in Fahrenheit"),
    ("temperature_indoor_f", "temp_in", "Indoor temperature in Fahrenheit"),
    ("humidity_pct", "humidity", "Outdoor relative humidity percent"),
    ("humidity_indoor_pct", "humidity_in", "Indoor relative humidity percent"),
    ("dew_point_f", "dew_point", "Dew point temperature in Fahrenheit"),
    ("wet_bulb_f", "wet_bulb", "Wet-bulb temperature in Fahrenheit"),
    ("heat_index_f", "heat_index", "Heat index (feels-like) in Fahrenheit"),
    ("wind_speed_mph", "wind_speed_last", "Current wind speed in mph"),
    ("wind_dir_deg", "wind_dir_last", "Current wind direction in degrees"),
    ("wind_speed_avg_10min_mph", "wind_speed_avg_last_10_min", "10-minute average wind speed"),
    ("wind_speed_hi_10min_mph", "wind_speed_hi_last_10_min", "10-minute high wind speed"),
    ("barometer_inhg", "bar_sea_level", "Barometric pressure sea level in inHg"),
    ("rain_rate_in_hr", "rain_rate_hi", "Peak rain rate over the last interval in/hr"),
    ("rainfall_24h_in", "rainfall_last_24_hr", "Rolling 24-hour rainfall in inches"),
    ("rainfall_monthly_in", "rainfall_monthly", "Monthly rainfall in inches"),
    ("rainfall_yearly_in", "rainfall_year", "Yearly rainfall in inches"),
    ("solar_radiation_wm2", "solar_rad", "Solar radiation W/m²"),
    ("uv_index", "uv_index", "UV index"),
    ("et_day_in", "et_day", "Daily evapotranspiration in inches"),
]


def update(records: list[dict[str, Any]]):
    """Called by the monitor after each successful poll."""
    merged: dict[str, Any] = {}
    for r in records:
        merged.update({k: v for k, v in r.items() if v is not None})
    with _lock:
        _latest.clear()
        _latest.update(merged)


def _label(value: Any) -> str:
    """Escape a Prometheus label value."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _build_metrics() -> str:
    with _lock:
        snapshot = dict(_latest)

    if not snapshot:
        return "# No data yet\n"

    station = _label(snapshot.get("station_uuid", "gdc"))
    location = _label(LOCATION)
    lines = [
        "# HELP weather_station Latest station reading; the name label says which (unit = its suffix)",
        "# TYPE weather_station gauge",
    ]
    for name, field, _help in GAUGE_FIELDS:
        val = snapshot.get(field)
        if val is None:
            continue
        lines.append(f'weather_station{{station="{station}", name="{name}", location="{location}"}} {float(val)}')

    # Uptime indicator — 1 if data is fresh (<15 min old), 0 if stale
    age = time.time() - snapshot.get("timestamp", 0)
    fresh = 1 if age < 900 else 0
    lines.append("# HELP weather_up 1 if station data is fresh (<15 min), 0 if stale")
    lines.append("# TYPE weather_up gauge")
    lines.append(f'weather_up{{station="{station}", location="{location}"}} {fresh}')

    return "\n".join(lines) + "\n"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/metrics", "/metrics/"):
            body = _build_metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/", "/health"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # silence HTTP access log; use app logger instead


def start(port: int = 9001):
    """Start the metrics HTTP server in a background daemon thread."""
    server = HTTPServer(("0.0.0.0", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("Prometheus metrics endpoint: http://0.0.0.0:%d/metrics", port)
    return server
