"""Export GDC weather history from the local InfluxDB for the Grafana Cloud dashboard.

Pulls every raw reading up to the cloud cut-over (2026-11-15T12:00Z), converts to metric and
writes cloud/out/history.json with pre-computed datasets (see build_cloud.py for their use).
Plain Python: no pandas/numpy needed.
"""
import csv
import io
import json
import math
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cloud" / "out" / "history.json"   # contains station data: gitignored
START, CUT = "2025-09-30T00:00:00Z", "2026-11-15T12:00:00Z"
CUT_MS = int(datetime(2026, 11, 15, 12, tzinfo=timezone.utc).timestamp() * 1000)

env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
TOKEN = env["INFLUXDB_ADMIN_TOKEN"]


def flux(q):
    req = urllib.request.Request("http://127.0.0.1:8086/api/v2/query?org=GDC", data=json.dumps({"query": q, "type": "flux"}).encode(),
                                 headers={"Authorization": f"Token {TOKEN}", "Content-Type": "application/json", "Accept": "application/csv"})
    text = urllib.request.urlopen(req, timeout=300).read().decode()
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):   # one CSV table per block, each with its own header
        if block.strip():
            out += [r for r in csv.DictReader(io.StringIO(block)) if r.get("_time")]
    return out


def ms(t):
    return int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() * 1000)


rows = flux(f'''
from(bucket: "weather") |> range(start: {START}, stop: {CUT})
  |> filter(fn: (r) => r._measurement == "weather" and ((r.sensor_type == "37" and (r._field == "temp" or r._field == "heat_index"
       or r._field == "humidity" or r._field == "wind_speed_last" or r._field == "wind_speed_hi_last_10_min"
       or r._field == "wind_dir_last" or r._field == "rainfall_last_24_hr" or r._field == "wet_bulb" or r._field == "dew_point")) or (r.sensor_type == "242" and r._field == "bar_sea_level")))
  |> map(fn: (r) => ({{_time: r._time, _field: r._field, _value: float(v: r._value)}}))
  |> group(columns: ["_field"]) |> keep(columns: ["_time", "_field", "_value"])''')

data = defaultdict(dict)                     # ms → {field: value}
for r in rows:
    data[ms(r["_time"])][r["_field"]] = float(r["_value"])
times = sorted(data)
print(f"{len(rows)} values, {len(times)} timestamps, {datetime.fromtimestamp(times[0]/1000, timezone.utc)} → {datetime.fromtimestamp(times[-1]/1000, timezone.utc)}")

F2C = lambda f: (f - 32) * 5 / 9
KN = 0.868976               # mph → knots


def get(t, f, conv=None):
    v = data[t].get(f)
    return None if v is None else (conv(v) if conv else v)


# ── 15-min samples (last reading in each 15-min window, like SAMPLE_15M) for rose / heatmap ──
W = 900_000
last15 = {}
for t in times:
    if get(t, "wind_speed_last") is not None and get(t, "wind_dir_last") is not None:
        last15[(t - 1) // W] = t        # window (start, end], stamped at window end
wind15 = [((k + 1) * W, round(get(t, "wind_speed_last") * KN, 1), int(get(t, "wind_dir_last"))) for k, t in sorted(last15.items())]


# ── Windowed aggregates (window end stamps, like Flux aggregateWindow) ──
def windows(period_ms):
    out = defaultdict(list)
    for t in times:
        out[((t - 1) // period_ms + 1) * period_ms].append(t)
    return out


def quantile(vals, q):
    s = sorted(vals)
    if not s:
        return None
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def agg(ts, f, conv, fn):
    v = [x for x in (get(t, f, conv) for t in ts) if x is not None]
    if not v:
        return None
    return {"min": min, "max": max, "mean": lambda a: sum(a) / len(a)}[fn](v)


PERIODS = {"1h": 3_600_000, "3h": 10_800_000, "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000}
bands = []
for p, pm in PERIODS.items():
    for end, ts in sorted(windows(pm).items()):
        rot = [((int(d) + 225) % 360) * 400 / 360 for d in (get(t, "wind_dir_last") for t in ts) if d is not None]
        bands.append({
            "ts": end, "p": p,
            "t_min": agg(ts, "temp", F2C, "min"), "t_mean": agg(ts, "temp", F2C, "mean"), "t_max": agg(ts, "temp", F2C, "max"),
            "feels": agg(ts, "heat_index", F2C, "mean"),
            "h_min": agg(ts, "humidity", None, "min"), "h_mean": agg(ts, "humidity", None, "mean"), "h_max": agg(ts, "humidity", None, "max"),
            "w_min": agg(ts, "wind_speed_last", lambda x: x * KN, "min"), "w_mean": agg(ts, "wind_speed_last", lambda x: x * KN, "mean"),
            "w_max": agg(ts, "wind_speed_last", lambda x: x * KN, "max"),
            "g_mean": agg(ts, "wind_speed_hi_last_10_min", lambda x: x * KN, "mean"),
            "g_max": agg(ts, "wind_speed_hi_last_10_min", lambda x: x * KN, "max"),
            "pres": agg(ts, "bar_sea_level", lambda x: x * 33.8639, "mean"),
            "wb": agg(ts, "wet_bulb", F2C, "mean"), "dp": agg(ts, "dew_point", F2C, "mean"),
            **{f"p{q}": quantile(rot, q / 100) for q in range(10, 100, 10)},
        })

# Hourly stats for summary tiles and the long-term heatmap; u/v = speed-weighted direction sums (15-min samples).
hourly = []
uv = defaultdict(lambda: [0.0, 0.0])
for ts_, spd, d in wind15:
    k = ((ts_ - 1) // 3_600_000 + 1) * 3_600_000
    uv[k][0] += spd * math.sin(math.radians(d))
    uv[k][1] += spd * math.cos(math.radians(d))
for b in bands:
    if b["p"] == "1h":
        hourly.append({"ts": b["ts"], "t_min": b["t_min"], "t_mean": b["t_mean"], "t_max": b["t_max"], "h_min": b["h_min"],
                       "h_max": b["h_max"], "g_max": b["g_max"], "u": uv[b["ts"]][0], "v": uv[b["ts"]][1]})

# Daily rain (archive stores per-interval rain in rainfall_last_24_hr), zero-filled like the local panel.
rain = defaultdict(float)
for t in times:
    v = get(t, "rainfall_last_24_hr")
    if v is not None:
        rain[t // 86_400_000 * 86_400_000] += v * 25.4
day0 = times[0] // 86_400_000 * 86_400_000
rain_daily = [(d, round(rain.get(d, 0.0), 2)) for d in range(day0, CUT_MS, 86_400_000)]

OUT.parent.mkdir(exist_ok=True)
json.dump({"cut_ms": CUT_MS, "wind15": wind15, "bands": bands, "hourly": hourly, "rain": rain_daily}, OUT.open("w"))
print(f"wind15 {len(wind15)}  bands {len(bands)}  hourly {len(hourly)}  rain days {len(rain_daily)} (wet: {sum(1 for _, r in rain_daily if r > 0)})")
