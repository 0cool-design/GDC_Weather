"""Generate grafana/dashboards/weather.json — minimal GDC Weather dashboard."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from queries import Q, DIR_WRAP  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "dashboards" / "weather.json"

INFLUX = {"type": "influxdb", "uid": "influxdb"}
PROM = {"type": "prometheus", "uid": "prometheus"}

# One hue per quantity, used everywhere that quantity appears.
TEMP, FEELS, HUM, WIND, GUST, PRES, RAIN = (
    "orange", "red", "blue", "purple", "yellow", "green", "light-blue")  # Grafana named colors adapt to light/dark

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass_mappings(full=360.0, offset=0.0):
    """Value→compass text for a 0..full scale whose 0 sits at `offset` degrees."""
    step = full / 16
    maps = []

    def rng(lo, hi, name):
        maps.append({"type": "range", "options": {"from": lo, "to": hi, "result": {"text": name}}})

    for i, name in enumerate(COMPASS):
        centre = ((i * 22.5 - offset) % 360) / 360 * full
        lo, hi = centre - step / 2, centre + step / 2
        if lo < 0:
            rng(0.0, hi, name); rng(full + lo, full, name)
        elif hi > full:
            rng(lo, full, name); rng(0.0, hi - full, name)
        else:
            rng(lo, hi, name)
    return maps


_id = 0


def nid():
    global _id
    _id += 1
    return _id


def steps(*pairs):
    out = [{"color": pairs[0], "value": None}]
    for i in range(1, len(pairs), 2):
        out.append({"color": pairs[i + 1], "value": pairs[i]})
    return {"mode": "absolute", "steps": out}


NEUTRAL = steps("text")
HEAT = steps("text", 32, TEMP, 41, FEELS, 54, "dark-red")          # NWS heat-index bands, °C
BEAUFORT = steps("text", 22, GUST, 34, TEMP, 48, FEELS)            # strong breeze / gale / storm, knots


def influx(q, ref="A"):
    return {"refId": ref, "datasource": INFLUX, "query": q}


def prom(expr, ref="A", legend="", instant=True):
    return {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend,
            "range": not instant, "instant": instant}


def row(title, y, collapsed=False, panels=None):
    r = {"id": nid(), "type": "row", "title": title, "collapsed": collapsed,
         "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": panels or []}
    return r


def stat(title, target, ds, pos, unit, decimals=1, thresholds=NEUTRAL, desc="",
         sparkline=False, mappings=None, size=None, **extra):
    p = {
        "id": nid(), "type": "stat", "title": title, "description": desc,
        "datasource": ds, "gridPos": dict(zip("xywh", pos)), "targets": [target],
        "fieldConfig": {"defaults": {
            "unit": unit, "decimals": decimals, "thresholds": thresholds,
            "color": {"mode": "thresholds"}, "mappings": mappings or [],
        }, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "value", "graphMode": "area" if sparkline else "none",
            "justifyMode": "center", "textMode": "value", "wideLayout": True,
            "showPercentChange": False, "orientation": "auto",
            "text": {"valueSize": size} if size else {},
        },
    }
    p.update(extra)
    return p


def ts_defaults(unit, color, decimals=1, **custom):
    c = {
        "drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 1,
        "fillOpacity": 0, "gradientMode": "none", "showPoints": "never", "spanNulls": False,
        "axisPlacement": "auto", "axisSoftMin": None, "axisBorderShow": False,
        "scaleDistribution": {"type": "linear"}, "thresholdsStyle": {"mode": "off"},
        "lineStyle": {"fill": "solid"}, "barAlignment": 0, "pointSize": 4,
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
    }
    c.update(custom)
    return {"unit": unit, "decimals": decimals, "color": {"mode": "fixed", "fixedColor": color},
            "custom": c, "thresholds": NEUTRAL, "mappings": []}


def ov(name, **props):
    plist = []
    for k, v in props.items():
        key = {"color": "color", "fillBelowTo": "custom.fillBelowTo", "fillOpacity": "custom.fillOpacity",
               "lineWidth": "custom.lineWidth", "lineStyle": "custom.lineStyle"}[k]
        if k == "color":
            v = {"mode": "fixed", "fixedColor": v}
        plist.append({"id": key, "value": v})
    return {"matcher": {"id": "byName", "options": name}, "properties": plist}


def timeseries(title, target, pos, defaults, overrides=(), desc="", legend=True, **extra):
    p = {
        "id": nid(), "type": "timeseries", "title": title, "description": desc,
        "datasource": target["datasource"], "gridPos": dict(zip("xywh", pos)), "targets": [target],
        "fieldConfig": {"defaults": defaults, "overrides": list(overrides)},
        "options": {
            "legend": {"showLegend": legend, "displayMode": "list", "placement": "bottom", "calcs": []},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }
    p.update(extra)
    return p


def band(lo, mid, hi, color, opacity=18):
    """Min/max drawn as a soft ribbon, mean as the one solid line."""
    return [ov(hi, color=color, fillBelowTo=lo, fillOpacity=opacity, lineWidth=0), ov(lo, color=color, lineWidth=0),
            ov(mid, color=color, lineWidth=2)]


def heatmap(title, target, pos, unit, scheme, ybucket, desc="", xbucket="3h", color_range=None, **extra):
    calc = {"yBuckets": {"mode": "size", "value": str(ybucket)}}
    if xbucket:
        calc["xBuckets"] = {"mode": "size", "value": xbucket}
    p = {
        "id": nid(), "type": "heatmap", "title": title, "description": desc,
        "datasource": target["datasource"], "gridPos": dict(zip("xywh", pos)), "targets": [target],
        "fieldConfig": {"defaults": {"custom": {"hideFrom": {"legend": False, "tooltip": False, "viz": False},
                                                "scaleDistribution": {"type": "linear"}}}, "overrides": []},
        "options": {
            "calculate": True, "calculation": calc, "cellGap": 2,
            "color": {"mode": "scheme", "scheme": scheme, "scale": "linear", "exponent": 0.5,
                      "steps": 64, "reverse": False},
            "yAxis": {"axisPlacement": "left", "unit": unit, "reverse": False},
            "rowsFrame": {"layout": "auto"}, "cellValues": {"unit": "none"},
            "tooltip": {"mode": "single", "yHistogram": True, "showColorScale": True},
            "legend": {"show": False}, "showValue": "never",
            "filterValues": {"le": 1e-9}, "exemplars": {"color": "rgba(255,0,255,0.7)"},
        },
    }
    if color_range:  # pin the scale so 1 reading is purple and a full 2 h bucket (8 readings) is lime, as in the article
        p["options"]["color"].update(min=color_range[0], max=color_range[1])
    p.update(extra)
    return p


ROSE_SCRIPT = r"""
const BINS = __BINS__;
const s = data.series[0];
// Rainbow speed scale, calm = blue → strong = red (as in the article).
const COLORS = ['#3D3DF5', '#3B8CF2', '#3CC7E8', '#3DE8B0', '#45E85A', '#A9EE3C', '#F0E23C', '#F2A43A', '#F0503C'];
if (!s) return { data: [], layout: { annotations: [{ text: 'No data in range', showarrow: false }] } };
const col = (name, i) => (s.fields.find(f => f.name === name) || s.fields[i]).values;
const dir = col('dir', 2), spd = col('speed', 1);
const SECT = 36, STEP = 360 / SECT;                      // 10° sectors
const counts = BINS.map(() => new Array(SECT).fill(0));
let n = 0;
for (let i = 0; i < dir.length; i++) {
  const d = dir[i], v = spd[i];
  if (d == null || v == null) continue;
  const k = Math.round((((d % 360) + 360) % 360) / STEP) % SECT;
  const b = BINS.findIndex(([lo, hi]) => v >= lo && (hi === null || v < hi));
  counts[b][k]++;
  n++;
}
const compass = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
const name = deg => compass[Math.round(deg / 22.5) % 16];
const label = ([lo, hi]) => (hi === null ? lo + '+' : lo + ' - ' + hi) + ' kn';
const axis = 'rgba(200,200,200,0.85)', ring = 'rgba(160,160,160,0.6)';
const theta = counts[0].map((_, k) => k * STEP);
return {
  data: BINS.map((bin, i) => ({
    type: 'barpolar', name: label(bin), theta, width: STEP * 0.92,
    r: counts[i].map(c => (n ? (100 * c) / n : 0)),
    customdata: theta.map(t => name(t) + ' (' + t + '°)'),
    marker: { color: COLORS[i % COLORS.length], line: { width: 0 } },
    hovertemplate: '%{customdata}<br>' + label(bin) + ': %{r:.1f}%<extra></extra>',
  })),
  layout: {
    polar: {
      bgcolor: 'rgba(0,0,0,0)', barmode: 'stack', hole: 0,
      angularaxis: { direction: 'clockwise', rotation: 90, thetaunit: 'degrees',
        tickmode: 'array', tickvals: [0, 45, 90, 135, 180, 225, 270, 315],
        ticktext: ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'], tickfont: { size: 13 },
        showgrid: true, gridcolor: axis, gridwidth: 1, showline: false, ticks: 'outside', ticklen: 6, tickcolor: axis },
      radialaxis: { ticksuffix: '%', angle: 90, tickangle: 0, tickfont: { size: 11 }, nticks: 4,
        showgrid: true, gridcolor: ring, griddash: 'dash', showline: false, ticks: '', layer: 'above traces' },
    },
    showlegend: true,
    legend: { orientation: 'v', x: 1, xanchor: 'right', y: 1, yanchor: 'top', font: { size: 11 },
      traceorder: 'reversed', bgcolor: 'rgba(0,0,0,0)', itemclick: 'toggle' },
    margin: { t: 34, b: 34, l: 34, r: 34 },
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  },
  config: { displayModeBar: false, responsive: true },
};
""".strip()


def rose(title, query, bins, pos, desc):
    script = ROSE_SCRIPT.replace("__BINS__", json.dumps(bins))
    return {
        "id": nid(), "type": "nline-plotlyjs-panel", "title": title, "description": desc,
        "datasource": INFLUX, "gridPos": dict(zip("xywh", pos)), "targets": [influx(query)],
        "options": {"allData": {}, "data": [], "layout": {}, "config": {}, "script": script,
                    "onclick": "", "syncTimeRange": False, "imgFormat": "png", "exportWidth": 0,
                    "exportHeight": 0, "resScale": 2, "timeCol": ""},
    }


panels = []
y = 0

# ── Now (Prometheus, last 24 h sparkline) ─────────────────────────────────────
panels.append(row("Now", y)); y += 1
now = dict()
panels += [
    stat("Temperature", prom('(weather_station{name="temperature_f"} - 32) * 5 / 9'), PROM, (0, y, 4, 4), "celsius",
         thresholds=steps("text", 40, TEMP, 46, FEELS), desc="Outdoor air temperature. Sparkline: last 24 h.", **now),
    stat("Feels like", prom('(weather_station{name="heat_index_f"} - 32) * 5 / 9'), PROM, (4, y, 4, 4), "celsius",
         thresholds=HEAT, desc="Heat index. Orange ≥ 32 °C extreme caution, red ≥ 41 °C danger, dark red ≥ 54 °C extreme danger (NWS).", **now),
    stat("Humidity", prom('weather_station{name="humidity_pct"}'), PROM, (8, y, 4, 4), "percent", decimals=0,
         desc="Relative humidity.", **now),
    stat("Wind", prom('weather_station{name="wind_speed_mph"} * 0.868976'), PROM, (12, y, 4, 4), "velocityknot",
         thresholds=BEAUFORT, desc="Latest wind speed.", **now),
    stat("Gust · 10 min", prom('weather_station{name="wind_speed_hi_10min_mph"} * 0.868976'), PROM, (16, y, 4, 4), "velocityknot",
         thresholds=BEAUFORT, desc="Highest gust in the last 10 minutes. Yellow ≥ 22 kn strong breeze, orange ≥ 34 gale, red ≥ 48 storm.", **now),
    stat("Direction", prom('weather_station{name="wind_dir_deg"}'), PROM, (20, y, 4, 4), "degree", decimals=0,
         mappings=compass_mappings(), desc="Direction the wind is coming from.",
)
]
y += 4

# ── Mining site (ids from 101 so the ids after it stay stable for cloud/build_cloud.py) ──
_saved_id, _id = _id, 100
T = lambda n: f'(weather_station{{name="{n}"}} - 32) * 5 / 9'
GUST_KN = 'weather_station{name="wind_speed_hi_10min_mph"} * 0.868976'
panels.append(row("Mining site", y)); y += 1
panels += [
    stat("Wet bulb", prom(T("wet_bulb_f")), PROM, (0, y, 6, 4), "celsius",
         thresholds=steps("text", 24, GUST, 27, TEMP, 30, FEELS),
         desc="Lowest temperature evaporative cooling (pads, misting, cooling towers) can bring air down to. "
              "Yellow ≥ 24 °C, orange ≥ 27 °C (evaporative cooling weak), red ≥ 30 °C."),
    stat("Dew point", prom(T("dew_point_f")), PROM, (6, y, 6, 4), "celsius",
         desc="Any surface colder than this gets wet: chilled pipes, cold-aisle walls, hydro-cooling plates."),
    stat("Dew-point margin", prom('(weather_station{name="temperature_f"} - ignoring(name) weather_station{name="dew_point_f"}) * 5 / 9'),
         PROM, (12, y, 6, 4), "celsius", thresholds=steps(FEELS, 2, TEMP, 4, PRES),
         desc="Air temperature minus dew point. Red < 2 °C, orange < 4 °C: air near saturation, condensation and mist "
              "on equipment likely. Miners are rated for non-condensing humidity."),
    stat("Dust risk", prom(f'clamp_min(({GUST_KN} >= bool 15) + ({GUST_KN} >= bool 22) '
                           '- ignoring(name) (weather_station{name="humidity_pct"} > bool 70), 0)'),
         PROM, (18, y, 6, 4), "none", 0, thresholds=steps(PRES, 1, GUST, 2, FEELS),
         mappings=[{"type": "value", "options": {"0": {"text": "Low"}, "1": {"text": "Watch"}, "2": {"text": "High"}}}],
         desc="From the 10-min gust: Watch ≥ 15 kn, High ≥ 22 kn; one step lower when humidity is above 70 % "
              "(damp ground lifts less dust). On High: check intake filters and close dampers."),
]
y += 4
panels += [
    timeseries(
        "Cooling conditions", influx(Q["cooling"]), (0, y, 12, 8),
        {**ts_defaults("celsius", TEMP, thresholdsStyle={"mode": "dashed"}), "thresholds": steps("transparent", 27, "#8E8E8E")},
        [ov("Air", color=TEMP, lineWidth=2), ov("Wet bulb", color="blue", lineWidth=2),
         ov("Dew point", color="green", lineStyle={"fill": "dash", "dash": [6, 4]})],
        "Mean per window. Air = what air-cooled miners breathe. Wet bulb = the floor for evaporative cooling. "
        "Dew point = surfaces below it sweat. Dashed grey line: wet bulb 27 °C.",
    ),
    stat("Hours ≥ 35 °C", influx(Q["hot_hours"]), INFLUX, (12, y, 4, 8), "h", 0, steps("text", 1, TEMP),
         "Hours in the selected range with an hourly mean air temperature of 35 °C or more. "
         "Inside a container that is about 40 °C, the usual top of a miner's operating range: expect fans at full speed and derating."),
    stat("Hours wet bulb ≥ 27 °C", influx(Q["wb_hours"]), INFLUX, (16, y, 4, 8), "h", 0, steps("text", 1, TEMP),
         "Hours in the selected range with an hourly mean wet bulb of 27 °C or more, when evaporative cooling loses most of its effect."),
    stat("Hours humidity ≥ 90 %", influx(Q["humid_hours"]), INFLUX, (20, y, 4, 8), "h", 0, steps("text", 1, "blue"),
         "Hours in the selected range with an hourly mean humidity of 90 % or more: the top of the non-condensing range "
         "miners are rated for. Risk of corrosion on boards and PSUs."),
]
for _p in panels[-3:]:
    _p["fieldConfig"]["defaults"].update(noValue="0 h", unit="suffix: h")   # hours, never "3 days"; 0 = none beyond the limit
y += 8
_id = _saved_id

# ── Mining container · Hydro A01 (TEST on staging: data copied from the cloud by cloud/pull_miners.py) ──
_saved_id, _id = _id, 200
panels.append(row("Mining container · Hydro A01 (test data)", y)); y += 1
panels += [
    stat("Hashrate · mean", influx(Q["mc_hashrate"]), INFLUX, (0, y, 6, 4), "suffix: PH/s", 1,
         desc="Mean total hashrate of container A01 in the selected range (petahash per second), minutes at 0 left out."),
    stat("Chip max · peak", influx(Q["mc_chip_peak"]), INFLUX, (6, y, 6, 4), "celsius", 0, steps("text", 80, TEMP, 85, FEELS),
         desc="Hottest chip reading in the selected range. Orange ≥ 80 °C, red ≥ 85 °C."),
    stat("Supply liquid · mean", influx(Q["mc_supply"]), INFLUX, (12, y, 6, 4), "celsius", 1, steps("text", 37, TEMP, 40, FEELS),
         desc="Mean temperature of the cooling liquid going to the miners. Set point 35 °C."),
    stat("Cooling approach · mean", influx(Q["mc_approach"]), INFLUX, (18, y, 6, 4), "celsius", 1, NEUTRAL,
         desc="Supply liquid minus outdoor wet bulb (hourly means). An evaporative cooling tower can't cool the liquid below "
              "the wet bulb; the smaller this gap, the less spare cooling capacity is left."),
]
y += 4
panels += [
    timeseries("Outside vs inside the container", influx(Q["mc_air"]), (0, y, 12, 9),
               ts_defaults("celsius", TEMP),
               [ov("Outdoor air", color=TEMP, lineWidth=2), ov("Cooling-tower inlet", color="yellow"),
                ov("Inside container", color="red")],
               "Station air temperature next to the container's own air sensors (mean per window)."),
    timeseries("Humidity · outside vs inside", influx(Q["mc_hum"]), (12, y, 12, 9),
               {**ts_defaults("percent", "blue", 0), "min": 0, "max": 100},
               [ov("Outdoor", color="blue", lineWidth=2), ov("Inside container", color="purple")],
               "Station humidity next to the container's internal humidity. They move together: the container breathes outside air."),
]
y += 9
panels += [
    timeseries("Liquid loop vs wet bulb", influx(Q["mc_loop"]), (0, y, 12, 9),
               ts_defaults("celsius", "blue"),
               [ov("Supply liquid", color="blue", lineWidth=2), ov("Return liquid", color="red"),
                ov("Set point", color="#8E8E8E", lineStyle={"fill": "dash", "dash": [6, 4]}),
                ov("Wet bulb", color="green", lineWidth=2)],
               "Cooling liquid to (supply) and from (return) the miners, the 35 °C set point, and the outdoor wet bulb: "
               "the floor the cooling tower can reach."),
    timeseries("Chip temperature vs wet bulb", influx(Q["mc_chip"]), (12, y, 12, 9),
               ts_defaults("celsius", TEMP),
               [ov("Chip max", color="red", lineWidth=2), ov("Chip mean", color=TEMP), ov("PCB max", color="yellow"),
                ov("Wet bulb", color="green", lineWidth=2)],
               "Miner chip and board temperatures next to the outdoor wet bulb (mean per window)."),
]
y += 9
_id = _saved_id

# ── Weather × miners · analysis (TEST on staging; ids from 300) ───────────────────────────────
# Method: everything averaged onto one hourly grid (queries.HOURLY), then compared three ways:
#   how strongly (Pearson r), how much (scatter + trend slope), and how it spreads (box plot per band).
_saved_id, _id = _id, 300
PLOTLY_HEAD = r"""
const s = data.series[0];
const none = { data: [], layout: { annotations: [{ text: 'No data in range', showarrow: false, font: { size: 16 } }],
  xaxis: { visible: false }, yaxis: { visible: false }, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)' } };
const tf = s && s.fields.find(f => f.type === 'time');
if (!tf) return none;
const col = n => { const f = s.fields.find(f => f.name === n); return f ? Array.from(f.values) : []; };
const time = Array.from(tf.values);
function fit(x, y) {           // least-squares line and Pearson r over the hours that have both values
  const p = [];
  for (let i = 0; i < x.length; i++) if (x[i] != null && y[i] != null) p.push([x[i], y[i]]);
  const n = p.length; if (n < 3) return null;
  const mx = p.reduce((a, q) => a + q[0], 0) / n, my = p.reduce((a, q) => a + q[1], 0) / n;
  let sxx = 0, sxy = 0, syy = 0;
  for (const [a, b] of p) { sxx += (a - mx) ** 2; sxy += (a - mx) * (b - my); syy += (b - my) ** 2; }
  const slope = sxy / sxx;
  return { p, n, slope, icpt: my - slope * mx, r: sxy / Math.sqrt(sxx * syy) };
}
const GRID = 'rgba(128,128,128,0.22)';
const axis = (title, extra) => Object.assign({ title: { text: title, standoff: 6 }, gridcolor: GRID, zeroline: false }, extra || {});
const base = (xt, yt, extra) => Object.assign({ margin: { t: 30, b: 46, l: 52, r: 12 }, paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)', xaxis: axis(xt, { type: 'linear' }), yaxis: axis(yt, { type: 'linear' }), showlegend: false, hovermode: 'closest' }, extra || {});
const CONFIG = { displayModeBar: false, responsive: true };
""".strip()

SCATTER = PLOTLY_HEAD + r"""
const X = '__X__', Y = '__Y__';
const f = fit(col(X), col(Y));
if (!f) return none;
const xs = f.p.map(q => q[0]), lo = Math.min(...xs), hi = Math.max(...xs);
const traces = [
  { type: 'scatter', mode: 'markers', x: xs, y: f.p.map(q => q[1]), name: 'hours',
    marker: { size: 5, color: '__COLOR__', opacity: 0.35 }, hovertemplate: '__XL__ %{x:.1f} °C<br>__YL__ %{y:.1f} __YU__<extra></extra>' },
  { type: 'scatter', mode: 'lines', x: [lo, hi], y: [f.icpt + f.slope * lo, f.icpt + f.slope * hi], name: 'trend',
    line: { color: '#E02F44', width: 2 }, hoverinfo: 'skip' },
];
const shapes = __SHAPES__;
const sign = f.slope >= 0 ? '+' : '−';
return { data: traces, config: CONFIG, layout: base('__XL__ (°C, hourly mean)', '__YL__ (__YU__)', { shapes,
  annotations: [{ xref: 'paper', yref: 'paper', x: 0.01, y: 1.07, xanchor: 'left', showarrow: false, align: 'left', font: { size: 12 },
    text: 'r = ' + f.r.toFixed(2) + '   ·   ' + sign + Math.abs(f.slope).toFixed(2) + ' __YU__ per °C   ·   ' + f.n + ' h' }] }) };
""".strip()


def scatter(x, y, xl, yl, yu, color, shapes="[]"):
    return (SCATTER.replace("__X__", x).replace("__Y__", y).replace("__XL__", xl).replace("__YL__", yl)
            .replace("__YU__", yu).replace("__COLOR__", color).replace("__SHAPES__", shapes))


BOX = PLOTLY_HEAD + r"""
const wb = col('wb'), chip = col('chip');
const W = 2, groups = {};
for (let i = 0; i < wb.length; i++) if (wb[i] != null && chip[i] != null) {
  const lo = Math.floor(wb[i] / W) * W; (groups[lo] = groups[lo] || []).push(chip[i]);
}
const keys = Object.keys(groups).map(Number).sort((a, b) => a - b).filter(k => groups[k].length >= 5);
if (!keys.length) return none;
const label = k => k + '–' + (k + W) + ' °C';
return { config: CONFIG, data: keys.map(k => ({ type: 'box', y: groups[k], name: label(k), boxpoints: false,
    marker: { color: '#FF9830' }, line: { width: 1.5 }, fillcolor: 'rgba(255,152,48,0.25)',
    hovertemplate: label(k) + ' wet bulb<br>%{y:.1f} °C<extra></extra>' })),
  layout: base('Outdoor wet bulb band (hours in band above each box)', 'Chip max (°C)', { xaxis: axis('Outdoor wet bulb band (hours in band above each box)', { type: 'category' }),
    annotations: keys.map(k => ({ x: label(k), xref: 'x', y: 1.0, yref: 'paper', yanchor: 'bottom',
    text: groups[k].length + ' h', showarrow: false, font: { size: 10 } })) }) };
""".strip()

DAY = PLOTLY_HEAD + r"""
// Mean per hour of the day, site time (Gulf Standard Time, UTC+4).
const SERIES = [['air', 'Outdoor air', '#FF9830', 'solid'], ['wb', 'Wet bulb', '#73BF69', 'solid'],
  ['tower', 'Tower inlet', '#FADE2A', 'dot'], ['in_t', 'Inside container', '#F2495C', 'solid'],
  ['supply', 'Supply liquid', '#5794F2', 'solid'], ['ret', 'Return liquid', '#B877D9', 'solid']];
const hourOf = t => (new Date(t).getUTCHours() + 4) % 24;
const traces = SERIES.map(([k, name, color, dash]) => {
  const v = col(k), sum = new Array(24).fill(0), n = new Array(24).fill(0);
  v.forEach((x, i) => { if (x != null) { const h = hourOf(time[i]); sum[h] += x; n[h]++; } });
  return { type: 'scatter', mode: 'lines', name, x: [...Array(24).keys()], y: sum.map((a, h) => n[h] ? a / n[h] : null),
    line: { color, width: 2, dash, shape: 'spline' }, hovertemplate: name + ' %{y:.1f} °C<extra>%{x}:00</extra>' };
});
return { data: traces, config: CONFIG, layout: base('Hour of day (Oman time)', '°C', {
  showlegend: true, legend: { orientation: 'h', y: -0.22, font: { size: 11 } }, margin: { t: 10, b: 70, l: 52, r: 12 },
  hovermode: 'x unified', xaxis: axis('Hour of day (Oman time)', { type: 'linear', range: [0, 23], tickvals: [0, 3, 6, 9, 12, 15, 18, 21], ticksuffix: ':00' }) }) };
""".strip()


def plotly(title, query, script, pos, desc):
    return {
        "id": nid(), "type": "nline-plotlyjs-panel", "title": title, "description": desc,
        "datasource": INFLUX, "gridPos": dict(zip("xywh", pos)), "targets": [influx(query)],
        "options": {"allData": {}, "data": [], "layout": {}, "config": {}, "script": script,
                    "onclick": "", "syncTimeRange": False, "imgFormat": "png", "exportWidth": 0,
                    "exportHeight": 0, "resScale": 2, "timeCol": ""},
    }


CORR = PLOTLY_HEAD + r"""
// Pearson r of hourly means over the selected range, worked out here from the shared hourly table.
const PAIRS = [['Inside humidity ↔ outdoor humidity', 'in_rh', 'rh'], ['Tower inlet ↔ outdoor air', 'tower', 'air'],
  ['Inside temp ↔ outdoor air', 'in_t', 'air'], ['Return liquid ↔ outdoor air', 'ret', 'air'],
  ['Supply liquid ↔ wet bulb', 'supply', 'wb'], ['Chip max ↔ wet bulb', 'chip', 'wb'],
  ['Efficiency J/TH ↔ wet bulb', 'jth', 'wb'], ['Hashrate ↔ outdoor air', 'hr', 'air']];
const rows = PAIRS.map(([name, a, b]) => { const f = fit(col(a), col(b)); return { name, r: f ? f.r : null, n: f ? f.n : 0 }; })
  .filter(x => x.r != null).reverse();
if (!rows.length) return none;
const color = r => r >= 0.5 ? '#FF9830' : r >= 0.2 ? '#FFCB7D' : r > -0.2 ? '#8E8E8E' : r > -0.5 ? '#8AB8FF' : '#5794F2';
return { config: CONFIG, data: [{ type: 'bar', orientation: 'h', y: rows.map(x => x.name), x: rows.map(x => x.r),
    marker: { color: rows.map(x => color(x.r)) }, text: rows.map(x => x.r.toFixed(2)), textposition: 'outside', cliponaxis: false,
    customdata: rows.map(x => x.n), hovertemplate: '%{y}<br>r = %{x:.2f} over %{customdata} h<extra></extra>' }],
  layout: base('r  (−1 opposite · 0 unrelated · +1 moves together)', '', { margin: { t: 6, b: 46, l: 210, r: 40 },
    xaxis: axis('r  (−1 opposite · 0 unrelated · +1 moves together)', { type: 'linear', range: [-1, 1], dtick: 0.5 }),
    yaxis: { type: 'category', automargin: true }, bargap: 0.35,
    shapes: [{ type: 'line', x0: 0, x1: 0, yref: 'paper', y0: 0, y1: 1, line: { color: '#8E8E8E', width: 1 } }] }) };
""".strip()

corr = plotly("How closely the container follows the weather", Q["mx_table"], CORR, (0, y + 1, 8, 10),
              "Pearson correlation r of hourly means over the selected range: +1 = moves exactly with the weather, "
              "0 = unrelated, −1 = moves opposite. |r| ≥ 0.5 is a strong link (orange). Correlation is not cause: "
              "load and the controller also move these signals.")

panels.append(row("Weather × miners · analysis (test data)", y)); y += 1
panels += [
    corr,
    timeseries("Cooling tower · approach & range", influx(Q["mx_ar"]), (8, y, 16, 10),
               ts_defaults("celsius", "blue", lineInterpolation="linear"),
               [ov("Approach (cold liquid − wet bulb)", color="green", lineWidth=2), ov("Range (hot − cold liquid)", color=TEMP)],
               "Hourly. Approach = cold liquid minus outdoor wet bulb; the tower can never cool below the wet bulb. "
               "Range = how much the miners heat the liquid (the load; steady ≈ 10 °C here). The cold liquid is held at a "
               "35 °C set point, so approach simply grows as the weather gets drier and cooler. Trouble shows as approach "
               "SHRINKING toward ~5 °C on humid days, the cold liquid climbing above 35 °C (see Supply liquid vs wet bulb), "
               "or range jumping (load change or low flow)."),
]
y += 10
panels += [
    plotly("Supply liquid vs wet bulb", Q["mx_table"],
           scatter("wb", "supply", "Wet bulb", "Supply liquid", "°C", "#5794F2",
                   "[{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: 35, y1: 35, line: { color: '#8E8E8E', dash: 'dash', width: 1 } }]"),
           (0, y, 8, 11),
           "Each dot is one hour. Flat along the 35 °C set point (dashed) = the tower keeps up; dots rising at the right "
           "= the wet bulb is too high for the tower to hold the set point. Red line: trend."),
    plotly("Chip max vs wet bulb", Q["mx_table"], scatter("wb", "chip", "Wet bulb", "Chip max", "°C", "#FF9830"), (8, y, 8, 11),
           "Each dot is one hour of hottest-chip temperature against the outdoor wet bulb, with the trend line, "
           "correlation r and slope (°C of chip per °C of wet bulb)."),
    plotly("Chip max by wet-bulb band", Q["mx_table"], BOX, (16, y, 8, 11),
           "Hourly chip max grouped by outdoor wet bulb in 2 °C bands. Box = middle half of the hours, line = median, "
           "whiskers = typical spread. Read left to right: how much hotter the chips run as the weather gets more humid-hot."),
]
y += 11
panels += [
    plotly("Average day", Q["mx_table"], DAY, (0, y, 12, 10),
           "Mean of each signal per hour of the day (Oman time) over the selected range: when the daily peak arrives "
           "outside and how it carries through the container and the liquid loop."),
    {**timeseries("Mining efficiency", influx(Q["mx_jth"]), (12, y, 12, 10),
                  {**ts_defaults("suffix: J/TH", "purple", 2, lineInterpolation="linear"), "thresholds": NEUTRAL},
                  [ov("Efficiency", color="purple", lineWidth=2)],
                  "Joules per terahash: container power (distribution boxes 1 + 2) ÷ hashrate, hourly. Lower is better. "
                  "Compare with the Average day and wet-bulb charts to see whether hot or humid hours cost efficiency.",
                  legend=False)},
]
y += 10
for _p in panels[-7:]:
    _p["maxDataPoints"] = 20000   # hourly rows for a full year (~9 800) must not be truncated
_id = _saved_id

# ── Summary (selected range) — same 8 | 4+4 | 8 grid as the article ───────────
panels.append(row("Summary", y)); y += 1
panels += [
    stat("Max Gust", influx(Q["max_gust"]), INFLUX, (0, y, 8, 8), "velocityknot", 0, steps("yellow"),
         "Highest 10-minute gust in the selected time range."),
    stat("Min Temp", influx(Q["min_temp"]), INFLUX, (8, y, 4, 4), "celsius", 1, steps("blue")),
    stat("Max Temp", influx(Q["max_temp"]), INFLUX, (12, y, 4, 4), "celsius", 1, steps("text")),
    stat("Min Humidity", influx(Q["min_hum"]), INFLUX, (8, y + 4, 4, 4), "percent", 0, steps("text")),
    stat("Max Humidity", influx(Q["max_hum"]), INFLUX, (12, y + 4, 4, 4), "percent", 0, steps("blue")),
    {**stat("Prevailing Wind Direction", influx(Q["prevailing_dir"]), INFLUX, (16, y, 8, 8), "degree", 0, steps("green"),
            "Speed-weighted circular mean of wind direction (vector average, so 350° and 10° average to N, not S)."),
     "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/.*/", "values": False},
                 "colorMode": "value", "graphMode": "none", "justifyMode": "center", "textMode": "value",
                 "wideLayout": True, "showPercentChange": False, "orientation": "vertical", "text": {}}},
]
y += 8

# ── Wind ──────────────────────────────────────────────────────────────────────
panels.append(row("Wind", y)); y += 1
WIND_BINS = [[0, 2], [2, 4], [4, 6], [6, 8], [8, 10], [10, 12], [12, 15], [15, 20], [20, None]]
GUST_BINS = [[0, 10], [10, 20], [20, 30], [30, 40], [40, 50], [50, None]]
panels += [
    rose("Wind Speed vs Wind Direction", Q["wind_rose"], WIND_BINS, (0, y, 10, 12),
         "Share of readings by direction and speed band. Raw readings, not averaged — averaging would invent calm directions in gusty wind."),
    heatmap("Wind Speed", influx(Q["wind_heatmap"]), (10, y, 14, 12), "velocityknot", "Turbo", 1,
            "Each column is 2 h, each cell 1 kn; purple = few readings, green/yellow = most readings.", xbucket="2h",
            color_range=(0, 13)),
]
y += 12
fill = lambda name, to, color: ov(name, color=color, fillBelowTo=to, fillOpacity=24)
panels += [
    timeseries(
        "Wind & Gust Speed", influx(Q["wind_band"]), (0, y, 12, 10),
        ts_defaults("velocityknot", "blue", axisSoftMin=0, fillOpacity=14, gradientMode="opacity"),
        [ov("Gust max", color="#8B7BA8"), ov("Gust mean", color="#A89CC0"),
         ov("Wind max", color="#5A7FBF"), ov("Wind min", color="#3F5F99"),
         ov("Wind mean", color="blue", lineWidth=2)],
        "Per window: max, mean and min wind with max and mean gust, as overlapping translucent areas.",
    ),
    timeseries(
        "Wind Direction History", influx(Q["dir_ribbon"]), (12, y, 12, 10),
        {**ts_defaults("none", "purple", 0, axisSoftMin=0, thresholdsStyle={"mode": "dashed+area"},
                       lineStyle={"fill": "dash", "dash": [2, 4]}),
         "min": 0, "max": 400, "mappings": compass_mappings(400.0, DIR_WRAP),
         "thresholds": steps("#1F60C4", 100, "#1F60C4", 200, "#1F60C4", 300, "#1F60C4")},
        [fill(a, b, "purple") for a, b in zip(['p10', 'p20', 'p30', 'p40'], ['p20', 'p30', 'p40', 'p50'])]
        + [fill(a, b, "green") for a, b in zip(['p60', 'p70', 'p80', 'p90'], ['p50', 'p60', 'p70', 'p80'])]
        + [ov("p50", color="#E8DAF5", lineWidth=2, lineStyle={"fill": "solid"})],
        "Percentiles p10–p90 of wind direction per window: purple below the median, green above, median solid. "
        "Narrow river = steady wind, wide = shifting. The axis wraps at SE, the rarest direction here.",
    ),
]
y += 10

# ── Temperature & humidity ────────────────────────────────────────────────────
panels.append(row("Temperature & Humidity", y)); y += 1
panels += [
    timeseries(
        "Temperature", influx(Q["temp_band"]), (0, y, 12, 9),
        {**ts_defaults("celsius", "blue", thresholdsStyle={"mode": "dashed"}),
         "thresholds": steps("transparent", 40, "#8E8E8E")},
        band("Min", "Mean", "Max", "blue", 35) + [ov("Mean", color="red", lineWidth=1)]
        + [ov("Feels like", color="orange", lineStyle={"fill": "dash", "dash": [6, 4]})],
        "Min–max ribbon, mean line, and heat index (dashed). Red dashed line marks 40 °C.",
    ),
    timeseries(
        "Humidity", influx(Q["hum_band"]), (12, y, 12, 9),
        {**ts_defaults("percent", "text", 0), "min": 0, "max": 100},
        [{"matcher": {"id": "byName", "options": "Max"}, "properties": [
            {"id": "color", "value": {"mode": "continuous-YlBl"}}, {"id": "custom.gradientMode", "value": "scheme"},
            {"id": "custom.fillBelowTo", "value": "Min"}, {"id": "custom.fillOpacity", "value": 40},
            {"id": "custom.lineWidth", "value": 0}]},
         ov("Min", lineWidth=0), ov("Mean", color="text", lineWidth=1)],
        "Relative humidity: min–max ribbon with mean line.",
    ),
]
y += 9

# ── Pressure & rain ───────────────────────────────────────────────────────────
panels.append(row("Pressure & Rain", y)); y += 1
panels += [
    timeseries("Pressure", influx(Q["pressure"]), (0, y, 12, 7),
               ts_defaults("pressurehpa", PRES, lineWidth=2), desc="Mean sea-level pressure per window.", legend=False),
    timeseries("Rain · daily", influx(Q["rain_daily"]), (12, y, 12, 7),
               ts_defaults("lengthmm", RAIN, drawStyle="bars", fillOpacity=70, lineWidth=0, axisSoftMin=0, axisSoftMax=5),
               desc="Total rainfall per day.", legend=False),
]
y += 7

# ── Long-term (collapsed) ─────────────────────────────────────────────────────
lt_y = y + 1
panels.append(row("Long-term", y, collapsed=True, panels=[
    heatmap("Daily Temperature Range · last 400 days", influx(Q["temp_heatmap"]), (0, lt_y, 24, 10), "celsius", "Plasma", 1,
            "Each column is one day, each cell 1 °C; brighter = more hours at that temperature. Fixed to 400 days regardless of the time picker.",
            xbucket="1d", timeFrom="400d"),
]))
y += 1

# ── Station health (collapsed) ────────────────────────────────────────────────
h_y = y + 1
panels.append(row("Station health", y, collapsed=True, panels=[
    stat("Exporter", prom('up{job="gdc_weather"}', instant=True), PROM, (0, h_y, 6, 4), "none", 0,
         steps(FEELS, 1, PRES), "Is Prometheus able to scrape the poller's /metrics endpoint?",
         mappings=[{"type": "value", "options": {"0": {"text": "Down"}, "1": {"text": "Up"}}}]),
    stat("Last Reading", influx(Q["data_age"]), INFLUX, (6, h_y, 6, 4), "s", 0,
         steps(PRES, 900, TEMP, 1800, FEELS), "Age of the newest sensor reading in InfluxDB. The poller runs every 5 min; amber > 15 min, red > 30 min."),
    stat("Samples · 24 h", influx(Q["samples_24h"]), INFLUX, (12, h_y, 6, 4), "none", 0, NEUTRAL,
         "Temperature readings stored in the last 24 hours."),
    stat("Uptime · range", prom("avg_over_time(weather_up[$__range]) * 100", instant=True), PROM, (18, h_y, 6, 4),
         "percent", 1, steps(FEELS, 95, TEMP, 99, PRES), "Share of scrapes where station data was fresher than 15 min."),
]))

# "Now" and health tiles always show the current state, whatever period the time picker shows:
# a panel-level relative time ("last 15 min") replaces the dashboard range for them.
ALWAYS_NOW = {2, 3, 4, 5, 6, 7, 28, 29, 30, 102, 103, 104, 105}
def _walk(ps):
    for p in ps:
        yield p
        yield from _walk(p.get("panels", []))
for _p in _walk(panels):
    if _p.get("id") in ALWAYS_NOW:
        _p["timeFrom"] = "24h" if _p["id"] == 30 else "15m"
        _p["hideTimeOverride"] = True

dashboard = {
    "uid": "gdc-weather",
    "title": "GDC Weather",
    "description": "GDC weather station — live conditions (Prometheus) and history (InfluxDB).",
    "tags": ["weather", "gdc"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "fiscalYearStartMonth": 0,
    "liveNow": False,
    "refresh": "5m",
    "schemaVersion": 39,
    "version": 1,
    "time": {"from": "now-7d", "to": "now"},
    "timepicker": {"refresh_intervals": ["1m", "5m", "15m", "1h"]},
    "links": [],
    "annotations": {"list": [{
        "builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
        "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"}]},
    # Interval variable with "auto": the window adapts to the time range (~150 points per chart), so a
    # 3-hour view gets 1–2 min windows instead of a single 3 h point. Fixed windows stay selectable.
    "templating": {"list": [{
        "name": "period", "label": "Window", "type": "interval", "hide": 0,
        "description": "Aggregation window for min / mean / max / percentiles. auto = fits the time range.",
        "query": "15m,1h,3h,6h,12h,1d",
        "auto": True, "auto_count": 150, "auto_min": "1m", "refresh": 2,
        "current": {"selected": True, "text": "auto", "value": "$__auto_interval_period"},
        "options": [{"selected": True, "text": "auto", "value": "$__auto_interval_period"}]
                   + [{"selected": False, "text": v, "value": v} for v in ["15m", "1h", "3h", "6h", "12h", "1d"]],
        "skipUrlSync": False,
    }]},
    "panels": panels,
}

OUT.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n")
print(f"wrote {OUT} — {sum(1 for p in panels if p['type'] != 'row') + sum(len(p['panels']) for p in panels if p['type'] == 'row')} panels")
