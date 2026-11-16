"""Flux queries for the GDC Weather dashboard. Units: station stores °F, mph, inHg, in."""

SRC = 'from(bucket: "weather")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'


def field(f, sensor="37"):
    return SRC + f'  |> filter(fn: (r) => r._measurement == "weather" and r.sensor_type == "{sensor}" and r._field == "{f}")\n'


F2C = '  |> map(fn: (r) => ({r with _value: (r._value - 32.0) * 5.0 / 9.0}))\n'
MPH2KN = '  |> map(fn: (r) => ({r with _value: r._value * 0.868976}))\n'
IN2MM = '  |> map(fn: (r) => ({r with _value: r._value * 25.4}))\n'
INHG2HPA = '  |> map(fn: (r) => ({r with _value: r._value * 33.8639}))\n'


def single(f, conv, fn):
    return field(f) + conv + f'  |> {fn}()\n  |> keep(columns: ["_value"])'


def bands(spec):
    """spec: list of (name, field, conv, fn). Returns one wide table: _time + one column per name."""
    parts = []
    for i, (name, f, conv, fn) in enumerate(spec):
        parts.append(
            f's_{name.lower().replace(" ", "_")} = ' + field(f) + conv
            + f'  |> aggregateWindow(every: ${{period}}, fn: {fn}, createEmpty: false)\n'
            + f'  |> set(key: "_field", value: "{name}")\n'
        )
    names = ", ".join("s_" + n.lower().replace(" ", "_") for n, *_ in spec)
    return "\n".join(parts) + (
        f'\nunion(tables: [{names}])\n'
        '  |> group()\n'
        '  |> keep(columns: ["_time", "_field", "_value"])\n'
        '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
        '  |> sort(columns: ["_time"])'
    )


Q = {}

# ── Summary (selected range) ────────────────────────────────────────────────
Q["max_gust"] = single("wind_speed_hi_last_10_min", MPH2KN, "max")
Q["min_temp"] = single("temp", F2C, "min")
Q["max_temp"] = single("temp", F2C, "max")
Q["min_hum"] = single("humidity", "", "min")
Q["max_hum"] = single("humidity", "", "max")
Q["peak_feels"] = single("heat_index", F2C, "max")
# Archive records store per-interval rainfall in "rainfall_last_24_hr" despite the name, so totals are sums.
Q["rain_total"] = (
    field("rainfall_last_24_hr") + IN2MM
    + '  |> sum()\n  |> keep(columns: ["_value"])'
)
# Speed-weighted circular mean — an arithmetic mean of 350° and 10° would wrongly give 180°.
Q["prevailing_dir"] = (
    'import "math"\n\n'
    'compass = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]\n\n' + SRC
    + '  |> filter(fn: (r) => r._measurement == "weather" and r.sensor_type == "37" and (r._field == "wind_dir_last" or r._field == "wind_speed_last"))\n'
    '  |> group()\n'
    '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
    '  |> filter(fn: (r) => exists r.wind_dir_last and exists r.wind_speed_last)\n'
    '  |> map(fn: (r) => ({u: r.wind_speed_last * math.sin(x: float(v: r.wind_dir_last) * math.pi / 180.0), v: r.wind_speed_last * math.cos(x: float(v: r.wind_dir_last) * math.pi / 180.0)}))\n'
    '  |> reduce(identity: {u: 0.0, v: 0.0}, fn: (r, accumulator) => ({u: accumulator.u + r.u, v: accumulator.v + r.v}))\n'
    '  |> map(fn: (r) => {\n'
    '      d = math.atan2(y: r.u, x: r.v) * 180.0 / math.pi\n'
    '      deg = if d < 0.0 then d + 360.0 else d\n'
    '      return {Degrees: deg, Compass: compass[int(v: (deg + 11.25) / 22.5) % 16]}\n'
    '  })'
)

# ── Wind ────────────────────────────────────────────────────────────────────
# One raw reading per 15 min (the archive interval), so 1-min live data doesn't outweigh
# 15-min history in the rose and heatmap densities. "last", not "mean": directions can't be averaged.
SAMPLE_15M = '  |> aggregateWindow(every: 15m, fn: last, createEmpty: false)\n'

def rose(dir_field, speed_field):
    return (
        SRC
        + f'  |> filter(fn: (r) => r._measurement == "weather" and r.sensor_type == "37" and (r._field == "{dir_field}" or r._field == "{speed_field}"))\n'
        + SAMPLE_15M +
        '  |> group()\n'
        '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
        f'  |> filter(fn: (r) => exists r.{dir_field} and exists r.{speed_field})\n'
        f'  |> map(fn: (r) => ({{_time: r._time, dir: float(v: r.{dir_field}), speed: r.{speed_field} * 0.868976}}))'
    )


Q["wind_rose"] = rose("wind_dir_last", "wind_speed_last")
Q["gust_rose"] = rose("wind_dir_at_hi_speed_last_10_min", "wind_speed_hi_last_10_min")
Q["wind_band"] = bands([
    ("Wind min", "wind_speed_last", MPH2KN, "min"),
    ("Wind mean", "wind_speed_last", MPH2KN, "mean"),
    ("Wind max", "wind_speed_last", MPH2KN, "max"),
    ("Gust mean", "wind_speed_hi_last_10_min", MPH2KN, "mean"),
    ("Gust max", "wind_speed_hi_last_10_min", MPH2KN, "max"),
])
Q["wind_heatmap"] = (
    field("wind_speed_last") + MPH2KN + SAMPLE_15M
    + '  |> group()\n  |> keep(columns: ["_time", "_value"])\n  |> rename(columns: {_value: "Wind"})'
)
Q["gust_heatmap"] = (
    field("wind_speed_hi_last_10_min") + MPH2KN
    + '  |> group()\n  |> keep(columns: ["_time", "_value"])\n  |> rename(columns: {_value: "Gust"})'
)
# Rotated so the scale wraps at the site's rarest direction (SE), then rescaled 0–360 → 0–400
# so Grafana's round-number axis ticks land on compass points.
DIR_WRAP = 135
Q["dir_ribbon"] = (
    'data = ' + field("wind_dir_last")
    + f'  |> map(fn: (r) => ({{r with _value: float(v: (int(v: r._value) + {360 - DIR_WRAP}) % 360) * 400.0 / 360.0}}))\n\n'
    'pct = (q, name) => data\n'
    '  |> aggregateWindow(every: ${period}, fn: (column, tables=<-) => tables |> quantile(q: q, column: column), createEmpty: false)\n'
    '  |> set(key: "_field", value: name)\n\n'
    'union(tables: [pct(q: 0.10, name: "p10"), pct(q: 0.20, name: "p20"), pct(q: 0.30, name: "p30"), pct(q: 0.40, name: "p40"), pct(q: 0.50, name: "p50"), pct(q: 0.60, name: "p60"), pct(q: 0.70, name: "p70"), pct(q: 0.80, name: "p80"), pct(q: 0.90, name: "p90")])\n'
    '  |> group()\n'
    '  |> keep(columns: ["_time", "_field", "_value"])\n'
    '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
    '  |> sort(columns: ["_time"])'
)

# ── Temperature & humidity ──────────────────────────────────────────────────
Q["temp_band"] = bands([
    ("Min", "temp", F2C, "min"),
    ("Mean", "temp", F2C, "mean"),
    ("Max", "temp", F2C, "max"),
    ("Feels like", "heat_index", F2C, "mean"),
])
Q["hum_band"] = bands([
    ("Min", "humidity", "", "min"),
    ("Mean", "humidity", "", "mean"),
    ("Max", "humidity", "", "max"),
])

# ── Mining site ─────────────────────────────────────────────────────────────
Q["cooling"] = bands([
    ("Air", "temp", F2C, "mean"),
    ("Wet bulb", "wet_bulb", F2C, "mean"),
    ("Dew point", "dew_point", F2C, "mean"),
])


def hours(f, conv, op, limit):
    """Hours in the range whose hourly mean is beyond the limit."""
    return (field(f) + conv
            + '  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)\n'
            + f'  |> filter(fn: (r) => r._value {op} {limit})\n'
            + '  |> group()\n  |> count()\n  |> keep(columns: ["_value"])')


Q["hot_hours"] = hours("temp", F2C, ">=", 35.0)
Q["wb_hours"] = hours("wet_bulb", F2C, ">=", 27.0)
Q["humid_hours"] = hours("humidity", "", ">=", 90.0)

# ── Mining container (test: data copied from the cloud by cloud/pull_miners.py) ──
def mfield(f):
    """A hydro-container sensor; -2147483648 is the controller's "no reading" code, so drop it."""
    return SRC + f'  |> filter(fn: (r) => r._measurement == "miners" and r._field == "{f}" and r._value > -100.0)\n'


def combo(spec):
    """spec: list of (name, flux source, fn). One wide table: _time + one column per name."""
    parts, names = [], []
    for i, (name, src, fn) in enumerate(spec):
        parts.append(f's{i} = ' + src + f'  |> aggregateWindow(every: ${{period}}, fn: {fn}, createEmpty: false)\n'
                     f'  |> group()\n  |> keep(columns: ["_time", "_value"])\n  |> set(key: "_field", value: "{name}")\n')
        names.append(f"s{i}")
    return "\n".join(parts) + (f'\nunion(tables: [{", ".join(names)}])\n'
                               '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n'
                               '  |> sort(columns: ["_time"])')


Q["mc_air"] = combo([("Outdoor air", field("temp") + F2C, "mean"),
                     ("Cooling-tower inlet", mfield("cooling_tower_inlet_temperature_celsius"), "mean"),
                     ("Inside container", mfield("internal_temperature_celsius"), "mean")])
Q["mc_hum"] = combo([("Outdoor", field("humidity"), "mean"),
                     ("Inside container", mfield("internal_humidity_percent"), "mean")])
Q["mc_loop"] = combo([("Supply liquid", mfield("supply_liquid_temperature_celsius"), "mean"),
                      ("Return liquid", mfield("return_liquid_temperature_celsius"), "mean"),
                      ("Set point", mfield("supply_liquid_set_temperature_celsius"), "mean"),
                      ("Wet bulb", field("wet_bulb") + F2C, "mean")])
Q["mc_chip"] = combo([("Chip max", mfield("chip_max_temperature_celsius"), "mean"),
                      ("Chip mean", mfield("chip_mean_temperature_celsius"), "mean"),
                      ("PCB max", mfield("pcb_max_temperature_celsius"), "mean"),
                      ("Wet bulb", field("wet_bulb") + F2C, "mean")])
Q["mc_hashrate"] = mfield("total_hashrate_terahashes_per_second") + '  |> filter(fn: (r) => r._value > 0.0)\n  |> mean()\n  |> map(fn: (r) => ({_value: r._value / 1000.0}))'
Q["mc_chip_peak"] = mfield("chip_max_temperature_celsius") + '  |> max()\n  |> keep(columns: ["_value"])'
Q["mc_supply"] = mfield("supply_liquid_temperature_celsius") + '  |> mean()\n  |> keep(columns: ["_value"])'
# Approach: how far above the wet bulb the cooled liquid comes back (hourly means, then averaged).
Q["mc_approach"] = (
    'sup = ' + mfield("supply_liquid_temperature_celsius") + '  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)\n  |> group()\n  |> keep(columns: ["_time", "_value"])\n'
    'wb = ' + field("wet_bulb") + F2C + '  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)\n  |> group()\n  |> keep(columns: ["_time", "_value"])\n'
    'join(tables: {s: sup, w: wb}, on: ["_time"])\n'
    '  |> map(fn: (r) => ({_time: r._time, _value: r._value_s - r._value_w}))\n  |> mean()'
)

# ── Weather × miners analysis: everything on one hourly grid so it can be compared ──
def _h(name, src):
    return (f'{name} = ' + src + '  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)\n'
            '  |> group()\n  |> keep(columns: ["_time", "_value"])\n')


def _diff(name, a, b, scale=""):
    """name = a − b (or a·1000/b with scale="ratio"), matched hour by hour."""
    op = "r._value_a * 1000.0 / r._value_b" if scale == "ratio" else "r._value_a - r._value_b"
    return (f'{name} = join(tables: {{a: {a}, b: {b}}}, on: ["_time"])\n'
            f'  |> map(fn: (r) => ({{_time: r._time, _value: {op}}}))\n'
            + ('  |> filter(fn: (r) => r._value > 8.0 and r._value < 30.0)\n' if scale == "ratio" else ""))   # drop hours with miners out but power on


HOURLY = "".join([
    _h("air", field("temp") + F2C), _h("wb", field("wet_bulb") + F2C), _h("dp", field("dew_point") + F2C),
    _h("rh", field("humidity")),
    _h("tower", mfield("cooling_tower_inlet_temperature_celsius")),
    _h("in_t", mfield("internal_temperature_celsius")), _h("in_rh", mfield("internal_humidity_percent")),
    _h("supply", mfield("supply_liquid_temperature_celsius")), _h("ret", mfield("return_liquid_temperature_celsius")),
    _h("chip", mfield("chip_max_temperature_celsius")), _h("chip_mean", mfield("chip_mean_temperature_celsius")),
    _h("hr", mfield("total_hashrate_terahashes_per_second") + '  |> filter(fn: (r) => r._value > 1000.0)\n'),
    _h("p1", mfield("distribution_box_1_power_kilowatts")), _h("p2", mfield("distribution_box_2_power_kilowatts")),
    'power = join(tables: {a: p1, b: p2}, on: ["_time"])\n  |> map(fn: (r) => ({_time: r._time, _value: r._value_a + r._value_b}))\n',
    _diff("jth", "power", "hr", "ratio"),        # kW·1000 / (TH/s) = J/TH
    _diff("approach", "supply", "wb"),           # cold liquid − wet bulb: how well the tower does for the weather
    _diff("range", "ret", "supply"),             # hot − cold liquid: the heat load
])
_COLS = ["air", "wb", "dp", "rh", "tower", "in_t", "in_rh", "supply", "ret", "chip", "chip_mean", "hr", "power", "jth", "approach", "range"]


def wide(cols, names=None):
    names = names or cols
    return (HOURLY + "\nunion(tables: [" + ", ".join(f'{c} |> set(key: "_field", value: "{n}")' for c, n in zip(cols, names)) + "])\n"
            '  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")\n  |> sort(columns: ["_time"])')


Q["mx_table"] = wide(_COLS)
Q["mx_ar"] = wide(["approach", "range"], ["Approach (cold liquid − wet bulb)", "Range (hot − cold liquid)"])
Q["mx_jth"] = HOURLY + "\njth\n  |> rename(columns: {_value: \"Efficiency\"})"

# ── Pressure & rain ─────────────────────────────────────────────────────────
Q["pressure"] = (
    field("bar_sea_level", "242") + INHG2HPA
    + '  |> aggregateWindow(every: ${period}, fn: mean, createEmpty: false)\n'
    '  |> group()\n  |> keep(columns: ["_time", "_value"])\n  |> rename(columns: {_value: "Sea-level pressure"})'
)
Q["rain_daily"] = (
    field("rainfall_last_24_hr") + IN2MM
    + '  |> aggregateWindow(every: 1d, fn: sum, createEmpty: true)\n'
    '  |> fill(value: 0.0)\n'
    '  |> group()\n  |> keep(columns: ["_time", "_value"])\n  |> rename(columns: {_value: "Rain"})'
)

# ── Long-term & health ──────────────────────────────────────────────────────
Q["temp_heatmap"] = (
    field("temp") + F2C
    + '  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)\n'
    '  |> group()\n  |> keep(columns: ["_time", "_value"])\n  |> rename(columns: {_value: "Temperature"})'
)
Q["data_age"] = (
    'from(bucket: "weather")\n  |> range(start: -30d)\n'
    '  |> filter(fn: (r) => r._measurement == "weather" and r.sensor_type == "37" and r._field == "temp")\n'
    '  |> last()\n'
    '  |> map(fn: (r) => ({_value: float(v: uint(v: now()) - uint(v: r._time)) / 1000000000.0}))'
)
Q["samples_24h"] = (
    'from(bucket: "weather")\n  |> range(start: -24h)\n'
    '  |> filter(fn: (r) => r._measurement == "weather" and r.sensor_type == "37" and r._field == "temp")\n'
    '  |> count()\n  |> keep(columns: ["_value"])'
)
