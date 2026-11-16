"""Copy the hydro mining-container data into the local InfluxDB, for testing on staging.

Source (through the Grafana Cloud API, using GRAFANA_CLOUD_URL / GRAFANA_CLOUD_TOKEN from .env):
  site  (default)  the site's own Prometheus on the Raspberry Pi (data source "prometheus-local"), 10-year
                   retention: container A01 from before the weather history starts (30 Sep 2025).
                   Old metric names (container_colding_tower_inlet_temp, …) are mapped to the cloud ones.
  cloud            the cloud Prometheus, metric container{location="Mirbat"}: only from 28 Jul 2026.

Writes to the local InfluxDB bucket "weather", measurement "miners": tags unit, client; one field per sensor
(e.g. chip_max_temperature_celsius), 5-minute step. Re-running overwrites the same points, so it is safe to repeat.
Needs INFLUXDB_ADMIN_TOKEN in .env and the local stack running.

Usage:  python3 cloud/pull_miners.py [--from 2025-09-30] [--cloud]
"""
import argparse
import calendar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
CLOUD, TOKEN = env["GRAFANA_CLOUD_URL"].rstrip("/"), env["GRAFANA_CLOUD_TOKEN"]
INFLUX = "http://127.0.0.1:8086/api/v2/write?org=GDC&bucket=weather&precision=s"
STEP, CHUNK = 300, 7 * 86400          # 5-min points, 7 days per request (≈ 2000 points per series)

SOURCES = {
    "site": ("aftgrjo7wy680e", '{__name__=~"container_.+", container_name="A01"}'),
    "cloud": ("grafanacloud-prom", 'container{location="Mirbat", type=~"value|state"}'),
}
# Site (old) names that differ from the cloud names beyond "pump1" → "pump_1".
RENAME = {"colding_tower_inlet_temp": "cooling_tower_inlet_temperature_celsius",
          "total_hashrate_terahash": "total_hashrate_terahashes_per_second",
          "liquid_flow_rate": "supply_liquid_flow", "phasefailure": "phase_failure"}

ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="start", default="2025-09-30", help="first day, UTC (default: start of the weather history)")
ap.add_argument("--cloud", action="store_true", help="read the cloud Prometheus instead of the site Pi")
args = ap.parse_args()
ds, QUERY = SOURCES["cloud" if args.cloud else "site"]
start = calendar.timegm(time.strptime(args.start, "%Y-%m-%d")) // STEP * STEP
end = int(time.time()) // STEP * STEP


def prom(a, b):
    url = f"{CLOUD}/api/datasources/proxy/uid/{ds}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": QUERY, "start": a, "end": b, "step": STEP})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    for attempt in range(5):
        try:
            return json.load(urllib.request.urlopen(req, timeout=300))["data"]["result"]
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if attempt == 4 or (isinstance(e, urllib.error.HTTPError) and e.code not in (429, 502, 503, 504)):
                raise
            time.sleep(5 * (attempt + 1))


def field(m):
    if not args.cloud:
        n = m["__name__"][len("container_"):]
        n = RENAME.get(n, re.sub(r"([a-z])(\d)", r"\1_\2", n))
        return n, m.get("container_name", ""), m.get("client", "")
    return m["name"].replace(" ", "_"), m.get("unit", ""), m.get("client", "")


def esc(s):
    return s.replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


total = 0
for a in range(start, end, CHUNK):
    b = min(a + CHUNK, end)
    points = {}                                   # (unit, client, ts) → {field: value}
    for s in prom(a, b):
        name, unit, client = field(s["metric"])
        for ts, v in s["values"]:
            if v not in ("NaN", "+Inf", "-Inf"):
                points.setdefault((unit, client, int(ts)), {})[name] = float(v)
    lines = [f"miners,unit={esc(u)},client={esc(c)} " + ",".join(f"{esc(k)}={v}" for k, v in sorted(f.items())) + f" {ts}"
             for (u, c, ts), f in sorted(points.items())]
    for i in range(0, len(lines), 5000):
        req = urllib.request.Request(INFLUX, data="\n".join(lines[i:i + 5000]).encode(),
                                     headers={"Authorization": f"Token {env['INFLUXDB_ADMIN_TOKEN']}"})
        urllib.request.urlopen(req, timeout=120)
    total += len(lines)
    print(f"{time.strftime('%Y-%m-%d', time.gmtime(a))} → {time.strftime('%Y-%m-%d', time.gmtime(b))}: {len(lines)} points", flush=True)
print(f"done: {total} points written to measurement 'miners' from the {'cloud' if args.cloud else 'site'} Prometheus")
