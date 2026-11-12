# GDC Weather — Grafana guide

A minimal, DevOps-style weather dashboard modelled on
[jaxzin's "cheap weather station → DevOps dashboard"](https://medium.com/@jaxzin/how-i-turned-a-cheap-weather-station-into-a-personal-devops-dashboard-5c8820790fd5),
adapted to a Davis WeatherLink station in a hot, dry climate.

## Architecture

```
WeatherLink API ──► gdc-weather (Python poller, every 5 min)
                      ├─► SQLite + .lp files            (local archive)
                      ├─► InfluxDB 2.7  bucket "weather" (history, 400+ days)
                      └─► :9001/metrics ◄── Prometheus   (live, 60 s scrape)
                                              │
                         Grafana 11.3 ◄───────┴── InfluxDB (Flux)
```

| Concern | Source | Why |
|---|---|---|
| "Now" tiles, alerts, health | Prometheus | Cheap instant queries, PromQL alerting |
| Everything historical | InfluxDB (Flux) | Long retention, windowed min/mean/max/quantile |

## Start / stop

```bash
docker compose up -d          # Grafana: http://localhost:3000  (user admin, password GF_ADMIN_PASSWORD in .env)
docker compose ps
docker compose logs -f gdc-weather
docker compose down           # data persists in named volumes
```

## The dashboard

Default range is 7 days. The **Window** variable (15m … 1d) sets the bucket size for every
min / mean / max / percentile; use 15m for a day, 1h for a week, 1d for months.

| Row | Panels | Reading it |
|---|---|---|
| **Now** | Temperature, Feels like, Humidity, Wind, Gust · 10 min, Direction | Latest value with a 24 h sparkline. Values turn color only when it means something (see thresholds). |
| **Summary · selected range** | Max Gust, Min/Max Temp, Peak Feels-like, Min/Max Humidity, Rain, Prevailing Wind | Extremes for whatever time range is picked. Prevailing wind is a speed-weighted *circular* mean. |
| **Wind** | Wind rose · Wind & gust band · Speed distribution heatmap · Direction percentiles | Rose = where wind comes from and how hard. Heatmap = where the bulk of readings sit. Percentile ribbon: narrow = steady, wide = shifting. |
| **Temperature & Humidity** | Temperature band (+ feels-like dashed) · Humidity band | Ribbon = min–max per window, solid line = mean. Red dashed line = 40 °C. |
| **Pressure & Rain** | Sea-level pressure · Daily rain | — |
| **Long-term** *(collapsed)* | 400-day daily temperature heatmap | Seasonal cycle at a glance; ignores the time picker. |
| **Station health** *(collapsed)* | Exporter up · Last reading age · Samples 24 h · Uptime | Is the pipeline healthy? |

### Design rules used

- **One hue per quantity, everywhere:** temperature orange, feels-like red, humidity blue,
  wind purple, gust yellow, pressure green, rain light blue.
- **Color only for meaning.** Neutral values stay in text color; thresholds are real scales —
  heat index uses NWS bands (32 / 41 / 54 °C), wind uses Beaufort (39 / 62 / 89 km/h).
- **Bands, not spaghetti.** Min/max is a soft ribbon, the mean is the single solid line.
- **Shared crosshair** across all time panels; every panel has an ⓘ description.
- **Metric units** (°C, km/h, hPa, mm). The station reports °F/mph/inHg/in; conversion happens in the queries.

### Things that differ from the article (and why)

| Article | Here | Reason |
|---|---|---|
| `fatcloud-windrose-panel` | `nline-plotlyjs-panel` (Plotly `barpolar`) | fatcloud is Angular, deprecated, removed in Grafana 12. |
| Arithmetic "Avg. Wind Direction" | Vector (circular) mean | Mean of 350° and 10° must be N, not S. |
| 21 direction percentiles | 5 (p10/25/50/75/90) | Same insight, less noise. |
| Sun-elevation overlay | — | No solar sensor on this station. |

Kept from the article: the **Window** variable, the 0–360 → 0–400 rescale so the y-axis ticks
land on N/E/S/W, and the raw (un-averaged) wind-rose input.

## Editing the dashboard

`dashboards/weather.json` is provisioned from disk (reloaded every 30 s). UI edits can't be saved
over it: change the JSON, or *Save as* a copy in the UI and export it back here.

## Alerts

Defined in `provisioning/alerting/weather_alerts.yaml` (Prometheus, evaluated every minute):

| Rule | Fires when | Severity |
|---|---|---|
| Station offline | no fresh reading > 15 min, for 5 min | critical |
| Implausible temperature | outside −5…55 °C for 10 min | warning |
| High / extreme heat index | > 40 °C for 10 min / > 46 °C for 5 min | warning / critical |
| High wind / severe wind | 10-min gust > 40 / > 64 km/h | warning / critical |
| Oppressive dew point | > 28 °C for 15 min | warning |
| Rapid pressure drop | < −3 hPa over 3 h | warning |

No contact point is provisioned. To get notifications, add one in **Alerting → Contact points**
(Slack, Telegram, email, webhook) and set it on the default notification policy.

## Security

| Control | Setting |
|---|---|
| Secrets | Only in `.env` (gitignored, mode 600). Template: `.env.example`. |
| InfluxDB tokens | Least privilege: poller = **write-only** `weather`; Grafana = **read-only** `weather`; admin token kept for maintenance only. |
| Network | Grafana, InfluxDB and Prometheus bind to `127.0.0.1`; `/metrics` is not published on the host at all. |
| Grafana | Sign-up, anonymous access, external snapshots, Gravatar and Angular plugins off; `SameSite=strict` cookies; plugin version pinned. |
| Poller container | Runs as non-root (`uid 10001`) and receives only the variables it reads. |

**To view from another device**, don't bind to `0.0.0.0` — use an SSH tunnel
(`ssh -L 3000:localhost:3000 host`) or Tailscale, and put HTTPS in front if it ever faces the internet.

### Rotating a credential

```bash
# Grafana admin password (after changing GF_ADMIN_PASSWORD in .env)
docker compose up -d grafana
docker compose exec grafana sh -c 'grafana cli admin reset-admin-password "$GF_SECURITY_ADMIN_PASSWORD"'

# InfluxDB token: create the new one, put it in .env, restart, then delete the old one
docker compose exec influxdb sh -c 'influx auth list --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"'
docker compose exec influxdb sh -c 'influx auth delete --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" --id <old-id>'
```

## Data notes

- Archive records store **per-interval** rainfall under the field name `rainfall_last_24_hr`.
  Totals must use `sum()`, never `max()`.
- No solar / UV sensor is installed, so those fields don't exist.
- The live feed carries no rain values, so rain is history-only (no rain alert).

## Import historic .lp files

```bash
docker compose cp data/influx_lp/weather_YYYY-MM-DD.lp influxdb:/tmp/w.lp
docker compose exec influxdb sh -c 'influx write --org GDC --bucket weather --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" --file /tmp/w.lp'
```
