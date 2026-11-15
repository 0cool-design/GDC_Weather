"""Build the Grafana Cloud copy of the GDC Weather dashboard.

Starts from the local dashboard (grafana/dashboards/weather.json), keeps layout, colours and
options, and swaps every query to PromQL on the cloud Prometheus (weather_station{name=...}).
Writes cloud/out/cloud_dashboard.json; upload it with cloud/upload.py.

Usage:  python3 cloud/build_cloud.py [--no-plotly] [--no-history]
  --no-plotly   bar-chart rose instead of the Plotly one (if the plugin isn't installed)
  --no-history  live Prometheus data only (no embedded history from cloud/out/history.json)
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cloud" / "out" / "cloud_dashboard.json"   # contains station data: gitignored
OUT.parent.mkdir(exist_ok=True)
DS = {"type": "prometheus", "uid": "grafanacloud-prom"}


def M(name):
    return f'weather_station{{location="Mirbat", name="{name}"}}'


C = lambda e: f"({e} - 32) * 5 / 9"          # °F → °C
KMH = lambda e: f"{e} * 1.609344"            # mph → km/h
WIN = "[$__interval]"                        # one "Window" ($period) per point


def tgt(expr, legend="", ref="A", instant=False, interval=None, fmt=None):
    t = {"refId": ref, "datasource": DS, "expr": expr, "legendFormat": legend or "__auto",
         "range": not instant, "instant": instant, "editorMode": "code"}
    if interval:
        t["interval"] = interval
    if fmt:
        t["format"] = fmt
    return t


def band(spec):
    """spec: [(legend, fn, metric_expr)] → one range target per line, step = Window."""
    return [tgt(f"{fn}({e}{WIN})" if fn else e, leg, chr(65 + i), interval="$period")
            for i, (leg, fn, e) in enumerate(spec)]


local = json.loads((ROOT / "grafana/dashboards/weather.json").read_text())
dash = copy.deepcopy(local)


def walk(ps):
    for p in ps:
        yield p
        yield from walk(p.get("panels", []))


P = {p["id"]: p for p in walk(dash["panels"])}

# ── Now ──────────────────────────────────────────────────────────────────────
now = {2: C(M("temperature_f")), 3: C(M("heat_index_f")), 4: M("humidity_pct"),
       5: KMH(M("wind_speed_mph")), 6: KMH(M("wind_speed_hi_10min_mph")), 7: M("wind_dir_deg")}
for i, e in now.items():
    P[i]["targets"] = [tgt(e, instant=True)]

# ── Summary (selected range) ─────────────────────────────────────────────────
summary = {
    9: KMH(f'max_over_time({M("wind_speed_hi_10min_mph")}[$__range])'),
    10: C(f'min_over_time({M("temperature_f")}[$__range])'),
    11: C(f'max_over_time({M("temperature_f")}[$__range])'),
    12: f'min_over_time({M("humidity_pct")}[$__range])',
    13: f'max_over_time({M("humidity_pct")}[$__range])',
}
for i, e in summary.items():
    P[i]["targets"] = [tgt(e, instant=True)]

# Prevailing direction: speed-weighted vector mean (u = east, v = north), sampled each minute.
spd, dirr = M("wind_speed_mph"), M("wind_dir_deg")
u = f"sum_over_time(({spd} * on(station) sin(rad({dirr})))[$__range:1m])"
v = f"sum_over_time(({spd} * on(station) cos(rad({dirr})))[$__range:1m])"
deg = f"(deg({u} atan2 {v}) + 360) % 360"
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
p = P[14]
p["targets"] = [tgt(deg, "Compass", "A", instant=True), tgt(deg, "Degrees", "B", instant=True)]
ranges = [{"type": "range", "options": {"from": (k * 22.5 - 11.25) % 360 if k else 348.75, "to": 360 if not k else k * 22.5 + 11.25,
                                         "result": {"text": c, "index": k}}} for k, c in enumerate(COMPASS)]
ranges.append({"type": "range", "options": {"from": 0, "to": 11.25, "result": {"text": "N", "index": 16}}})
p["options"]["reduceOptions"]["fields"] = ""   # numeric fields only (no Time column)
p["fieldConfig"]["overrides"] = [{"matcher": {"id": "byName", "options": "Compass"},
                                  "properties": [{"id": "mappings", "value": ranges}]}]

# ── Wind rose → stacked bars per compass sector (no Plotly plugin on the cloud stack) ──
BINS = [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10), (10, 12), (12, 15), (15, 20), (20, None)]
COLORS = ['#3D3DF5', '#3B8CF2', '#3CC7E8', '#3DE8B0', '#45E85A', '#A9EE3C', '#F0E23C', '#F2A43A', '#F0503C']
sector = f"floor((({dirr} + 11.25) % 360) / 22.5)"
total = f"scalar(count_over_time({dirr}[$__range:15m]))"
rose_t, rose_ov, rename = [], [], {}
for i, (lo, hi) in enumerate(BINS):
    cond = f"{KMH(spd)} >= {lo}" + (f" < {hi}" if hi else "")
    label = f"{lo}–{hi} km/h" if hi else f"≥ {lo} km/h"
    ref = chr(65 + i)
    rose_t.append(tgt(
        f'100 * sum by (sector) (count_over_time(count_values("sector", {sector} and on(station) ({cond}))[$__range:15m])) / {total}',
        label, ref, instant=True, fmt="table"))
    rename[f"Value #{ref}"] = label
    rose_ov.append({"matcher": {"id": "byName", "options": label},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": COLORS[i]}}]})
# Zero row for every sector so all 16 bars show even when the wind never blew from some.
zero = " or ".join(f'label_replace(vector(0), "sector", "{k}", "", "")' for k in range(16))
rose_t.append(tgt(zero, "zero", "Z", instant=True, fmt="table"))
r = P[16]
r.update({
    "type": "barchart",
    "pluginVersion": None,
    "description": "Share of time the wind blew FROM each of 16 compass sectors, stacked by speed (km/h). "
                   "One reading per 15 min. Same colours as the local wind rose.",
    "targets": rose_t,
    "transformations": [
        {"id": "merge", "options": {}},
        {"id": "organize", "options": {"excludeByName": {"Value #Z": True, "Time": True, "instance": True, "job": True, "location": True, "station": True},
                                       "renameByName": rename}},
        {"id": "convertFieldType", "options": {"conversions": [{"targetField": "sector", "destinationType": "number"}]}},
        {"id": "sortBy", "options": {"sort": [{"field": "sector"}]}},
    ],
    "fieldConfig": {
        "defaults": {"unit": "percent", "decimals": 1, "min": 0, "color": {"mode": "fixed", "fixedColor": "#3D3DF5"},
                     "custom": {"fillOpacity": 90, "lineWidth": 0, "gradientMode": "none", "axisSoftMin": 0,
                                "axisGridShow": True, "axisPlacement": "auto", "hideFrom": {"legend": False, "tooltip": False, "viz": False}}},
        "overrides": rose_ov + [{"matcher": {"id": "byName", "options": "sector"}, "properties": [
            {"id": "mappings", "value": [{"type": "value", "options": {str(k): {"text": c, "index": k} for k, c in enumerate(COMPASS)}}]},
            {"id": "unit", "value": "none"}, {"id": "decimals", "value": 0}]}],
    },
    "options": {"orientation": "vertical", "xField": "sector", "stacking": "normal", "showValue": "never",
                "groupWidth": 0.85, "barWidth": 0.97, "barRadius": 0, "xTickLabelRotation": 0, "xTickLabelSpacing": 0,
                "fullHighlight": False, "colorByField": None,
                "legend": {"showLegend": True, "displayMode": "list", "placement": "right", "calcs": []},
                "tooltip": {"mode": "multi", "sort": "desc"}},
})
r.pop("pluginVersion")
r["title"] = "Wind Speed vs Wind Direction"

# ── Wind heatmap / bands / direction ribbon ──────────────────────────────────
P[17]["targets"] = [tgt(KMH(spd), "Wind", interval="15m")]
P[18]["targets"] = band([
    ("Wind min", "min_over_time", spd), ("Wind mean", "avg_over_time", spd), ("Wind max", "max_over_time", spd),
    ("Gust mean", "avg_over_time", M("wind_speed_hi_10min_mph")), ("Gust max", "max_over_time", M("wind_speed_hi_10min_mph")),
])
for t in P[18]["targets"]:
    t["expr"] = KMH(t["expr"])
rot = f"((({dirr} + 225) % 360) * 400 / 360)"   # same 135° wrap + 0–400 rescale as local
P[19]["targets"] = [tgt(f"quantile_over_time({q / 100}, {rot}[$__interval:1m])", f"p{q}", chr(65 + i), interval="$period")
                    for i, q in enumerate(range(10, 100, 10))]

# ── Temperature, humidity, pressure ──────────────────────────────────────────
P[21]["targets"] = band([("Min", "min_over_time", M("temperature_f")), ("Mean", "avg_over_time", M("temperature_f")),
                         ("Max", "max_over_time", M("temperature_f")), ("Feels like", "avg_over_time", M("heat_index_f"))])
for t in P[21]["targets"]:
    t["expr"] = C(t["expr"])
P[22]["targets"] = band([("Min", "min_over_time", M("humidity_pct")), ("Mean", "avg_over_time", M("humidity_pct")),
                         ("Max", "max_over_time", M("humidity_pct"))])
P[24]["targets"] = [tgt(f'avg_over_time({M("barometer_inhg")}{WIN}) * 33.8639', "Sea-level pressure", interval="$period")]

# Rain: same panel as local. Daily total = highest rolling-24 h rainfall reading in each day.
P[25]["targets"] = [tgt(f'max_over_time({M("rainfall_24h_in")}[1d]) * 25.4', "Rain", interval="1d")]
P[25]["fieldConfig"]["defaults"]["noValue"] = "No rain reported"

# ── Long-term & health ───────────────────────────────────────────────────────
P[26]["targets"] = [tgt(C(f'avg_over_time({M("temperature_f")}[1h])'), "Temperature", interval="1h")]
P[26]["description"] = ("Each column is one day, each cell 1 °C; brighter = more hours at that temperature. "
                        "Fixed to 400 days; cloud data starts 15 Nov 2026.")
P[28]["targets"] = [tgt('max(up{job="gdc_weather"})', instant=True)]
P[29]["targets"] = [tgt(f'time() - max(timestamp({M("temperature_f")}))', instant=True)]
P[29]["description"] = "Seconds since the cloud last received a reading. Amber > 15 min, red > 30 min."
P[30]["targets"] = [tgt(f'sum(count_over_time({M("temperature_f")}[24h]))', instant=True)]
P[30]["description"] = "Temperature samples received in the last 24 hours."
P[31]["targets"] = [tgt('avg_over_time(max(up{job="gdc_weather"})[$__range:1m]) * 100', instant=True)]
P[31]["description"] = "Share of the selected range where the poller was being scraped successfully."

# Every panel → cloud Prometheus.
for p in walk(dash["panels"]):
    if p["type"] != "row":
        p["datasource"] = DS
        bad = [t for t in p.get("targets", []) if "expr" not in t]
        assert not bad, (p["id"], p["title"])

dash["description"] = "GDC weather station, Mirbat — cloud copy. All panels read the cloud Prometheus (data from 15 Nov 2026)."
dash.pop("id", None)
OUT.write_text(json.dumps(dash, indent=2, ensure_ascii=False))
print("wrote", OUT, sum(1 for p in walk(dash["panels"]) if p["type"] != "row"), "panels")

# ── Plotly rose (same as local) — used once nline-plotlyjs-panel is installed on the stack ──
if "--no-plotly" not in sys.argv:
    loc = {p["id"]: p for p in walk(local["panels"])}[16]
    rp = copy.deepcopy(loc)
    rp["datasource"] = DS
    rp["targets"] = [tgt(KMH(spd), "speed", "A", interval="15m"), tgt(dirr, "dir", "B", interval="15m")]  # order matters: script falls back to field 1 = speed, 2 = dir
    rp["transformations"] = [{"id": "joinByField", "options": {"byField": "Time", "mode": "inner"}},
                             {"id": "organize", "options": {"renameByName": {}}}]
    for i, p in enumerate(dash["panels"]):
        if p.get("id") == 16:
            dash["panels"][i] = rp
    OUT.write_text(json.dumps(dash, indent=2, ensure_ascii=False))
    print("Plotly rose panel swapped in")

# ══ History: embed the local InfluxDB history (to 15 Nov 2026 12:00 UTC) via Infinity inline CSV ══
# Each panel gets a second query (refId H) with pre-computed history; transformations merge it with the
# live Prometheus series ("p_X" + "h_X" → "X"), so the panel shows one continuous line.
if "--no-history" not in sys.argv:
    H = json.loads((OUT.parent / "history.json").read_text())   # from cloud/export_history.py
    # Not grafanacloud-infinity: Grafana Cloud hides that one from normal users ("datasource not found").
    INF = {"type": "yesoreyeram-infinity-datasource", "uid": "efu6ubimp6xhcb"}
    MIXED = {"type": "datasource", "uid": "-- Mixed --"}
    BASE = 1759190400000                                   # 2025-09-30T00:00Z; CSV stores minutes since BASE
    RANGE = "Time >= ${__from} && Time <= ${__to}"
    PER = '(p == "${period}" || ("${period}" == "15m" && p == "1h"))'
    P2 = {p["id"]: p for p in walk(dash["panels"])}

    def fmt(v):
        if isinstance(v, str):
            return v
        if v is None:
            return ""
        return str(int(v)) if float(v).is_integer() else f"{v:.2f}".rstrip("0").rstrip(".")

    def inline(cols, rows, filt=RANGE, summ=None):
        """cols: CSV column names after m (minutes since BASE). rows: [(ms, v1, v2…)]."""
        text = "m," + ",".join(cols) + "\n" + "".join(
            f"{(r[0] - BASE) // 60000}," + ",".join(fmt(x) for x in r[1:]) + "\n" for r in rows)
        t = {"refId": "H", "datasource": INF, "type": "csv", "source": "inline", "parser": "backend", "format": "table",
             "data": text, "filterExpression": filt,
             "columns": [{"selector": "m", "text": "m", "type": "number"}]
                        + [{"selector": c, "text": c, "type": "string" if c == "p" else "number"} for c in cols],
             "computed_columns": [{"selector": f"m*60000+{BASE}", "text": "Time"}]}
        if summ:
            t["summarizeExpression"] = summ
        return t

    def merged(p, names, hist_target, reducer="max"):
        """Live targets must use legends p_<name>; history columns are h_<name>."""
        for t in p["targets"]:
            t["legendFormat"] = "p_" + t["legendFormat"]
        p["targets"].append(hist_target)
        p["datasource"] = MIXED
        p["transformations"] = (
            [{"id": "convertFieldType", "options": {"conversions": [{"targetField": "Time", "destinationType": "time"}]}},
             {"id": "joinByField", "options": {"byField": "Time", "mode": "outer"}}]
            + [{"id": "calculateField", "options": {"mode": "reduceRow", "reduce": {"reducer": reducer, "include": [f"p_{n}", f"h_{n}"]},
                                                     "alias": n}} for n in names]
            + [{"id": "filterFieldsByName", "options": {"include": {"names": ["Time"] + names}}}]
            + [t for t in p.get("transformations", []) if t["id"] not in ("convertFieldType", "joinByField", "filterFieldsByName")
               and t.get("options", {}).get("mode") != "reduceRow"])

    # Summary tiles: live max/min over the range, history max/min over the range → combined.
    hourly = H["hourly"]
    for pid, col, red in ((9, "g_max", "max"), (10, "t_min", "min"), (11, "t_max", "max"), (12, "h_min", "min"), (13, "h_max", "max")):
        p = P2[pid]
        e = p["targets"][0]["expr"].replace("[$__range]", "[$__interval]")
        name = p["title"]
        p["targets"] = [tgt(e, name, "A", interval="1h")]  # range query: Mimir rejects one look-back > 32 days
        merged(p, [name], inline([f"h_{name}"], [(r["ts"], r[col]) for r in hourly]), reducer=red)
        p["options"]["reduceOptions"].update({"calcs": [red], "fields": ""})

    # Prevailing direction: Plotly text tile, vector sum of live (pu, pv) + history (u, v) over the range.
    p = P2[14]
    p.update({"type": "nline-plotlyjs-panel", "datasource": MIXED, "fieldConfig": {"defaults": {}, "overrides": []}})
    p.pop("transformations", None)
    p["targets"] = [tgt(u.replace("[$__range:1m]", "[$__interval:1m]"), "pu", "A", interval="1h"), tgt(v.replace("[$__range:1m]", "[$__interval:1m]"), "pv", "B", interval="1h"),
                    inline(["u", "v"], [(r["ts"], round(r["u"], 1), round(r["v"], 1)) for r in hourly])]
    p["options"] = {**copy.deepcopy(P2[16]["options"]), "script": r"""
let u = 0, v = 0;
for (const s of data.series) for (const f of s.fields) {
  const n = (f.config && f.config.displayNameFromDS) || f.name;
  const sum = f.values.reduce((a, x) => a + (typeof x === 'number' ? x : 0), 0);
  if (n === 'pu' || n === 'u') u += sum;
  if (n === 'pv' || n === 'v') v += sum;
}
const green = '#73BF69';
if (!u && !v) return { data: [{ type: 'scatter', x: [0], y: [0], mode: 'markers', marker: { opacity: 0 }, hoverinfo: 'skip' }], layout: { annotations: [{ text: 'No data', showarrow: false, font: { size: 20, color: '#888' } }],
  xaxis: { visible: false }, yaxis: { visible: false }, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)' } };
const deg = (Math.atan2(u, v) * 180 / Math.PI + 360) % 360;
const C = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
const txt = (x, s, size) => ({ text: s, x, y: 0.5, xref: 'paper', yref: 'paper', showarrow: false, font: { size, color: green } });
return { data: [{ type: 'scatter', x: [0], y: [0], mode: 'markers', marker: { opacity: 0 }, hoverinfo: 'skip' }], config: { displayModeBar: false, responsive: true }, layout: {
  annotations: [txt(0.5, C[Math.round(deg / 22.5) % 16] + '&nbsp;&nbsp;&nbsp;&nbsp;' + Math.round(deg) + '°', 64)],
  xaxis: { visible: false }, yaxis: { visible: false }, margin: { t: 0, b: 0, l: 0, r: 0 },
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)' } };
"""}

    w15 = H["wind15"]
    # Wind rose (Plotly): history 15-min samples + live, merged into one frame with speed & dir.
    merged(P2[16], ["speed", "dir"], inline(["h_speed", "h_dir"], w15))
    # Wind speed heatmap.
    merged(P2[17], ["Wind"], inline(["h_Wind"], [(t, s) for t, s, _ in w15]))

    bands = H["bands"]
    def band_hist(pid, names, cols):
        merged(P2[pid], names, inline(["p"] + [f"h_{n}" for n in names], [(b["ts"], b["p"], *(b[c] for c in cols)) for b in bands],
                                      filt=f"{RANGE} && {PER}"))
    band_hist(18, ["Wind min", "Wind mean", "Wind max", "Gust mean", "Gust max"], ["w_min", "w_mean", "w_max", "g_mean", "g_max"])
    band_hist(19, [f"p{q}" for q in range(10, 100, 10)], [f"p{q}" for q in range(10, 100, 10)])
    band_hist(21, ["Min", "Mean", "Max", "Feels like"], ["t_min", "t_mean", "t_max", "feels"])
    band_hist(22, ["Min", "Mean", "Max"], ["h_min", "h_mean", "h_max"])
    band_hist(24, ["Sea-level pressure"], ["pres"])
    merged(P2[25], ["Rain"], inline(["h_Rain"], H["rain"]))
    merged(P2[26], ["Temperature"], inline(["h_Temperature"], [(r["ts"], r["t_mean"]) for r in hourly], filt=""))
    P2[25]["description"] = "Total rainfall per day. History from the station archive; the live feed carries no rain values."
    P2[26]["description"] = P2[26]["description"].replace("cloud data starts 15 Nov 2026.", "history embedded up to 15 Nov 2026, live after.")
    for t in P2[26]["targets"]:
        t.pop("filterExpression", None) if not t.get("filterExpression") else None

    dash["description"] = ("GDC weather station, Mirbat — cloud copy. Live data from the cloud Prometheus (since 15 Nov 2026 12:00 UTC); "
                           "history before that is embedded from the station archive.")
    OUT.write_text(json.dumps(dash, indent=2, ensure_ascii=False))
    print(f"history embedded; dashboard JSON {OUT.stat().st_size / 1e6:.1f} MB")
