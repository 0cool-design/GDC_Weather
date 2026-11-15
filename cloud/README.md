# Grafana Cloud copy of the dashboard

The company Grafana Cloud stack has a copy of the **GDC Weather** dashboard (uid `gdc-weather`). This folder
rebuilds that copy from the local dashboard (`grafana/dashboards/weather.json`), keeping the same layout, colours
and options.

## Where the cloud gets its data

| Period | Source | How |
|---|---|---|
| From 15 Nov 2026, 12:00 UTC | Cloud Prometheus (`grafanacloud-prom`), metric `weather_station{location="Mirbat", name=…}` | Sent by a separate poller install (not this computer) |
| 30 Sep 2025 – 15 Nov 2026, 12:00 UTC | Embedded in the dashboard as CSV, read through the Infinity data source `yesoreyeram-infinity-datasource` (not the built-in `grafanacloud-infinity`, which Grafana Cloud hides from normal users) | `export_history.py` exports it from the local InfluxDB |

Each history panel queries both sources. The panel's transformations merge them into one continuous series:
live fields are `p_<name>`, history fields `h_<name>`, and the result is `<name>`.

The cloud Prometheus can't take old samples, so history can't be loaded into it. That's why it is embedded.

## Rebuild and upload

Run from the project folder, with the local stack running. `.env` needs `GRAFANA_CLOUD_URL` and
`GRAFANA_CLOUD_TOKEN`: a service-account token with Editor (or Admin) rights on the dashboard's folder.

```bash
python3 cloud/export_history.py      # local InfluxDB → cloud/out/history.json   (about 1 min)
python3 cloud/build_cloud.py         # → cloud/out/cloud_dashboard.json          (about 4.7 MB)
python3 cloud/upload.py "what changed"
```

`cloud/out/` holds station data and is gitignored. Every upload is a new dashboard version, so earlier versions can
be restored under *Dashboard settings → Versions*.

Options for `build_cloud.py`:
- `--no-plotly`: draws the wind rose as a stacked bar chart. Use this if the stack lacks the Plotly plugin
  (`nline-plotlyjs-panel`).
- `--no-history`: uses only live Prometheus data.

## Differences from the local dashboard

- **Station health** tiles measure what the cloud receives: scrape status, age of the last reading, samples in
  24 h.
- The **Window** setting `15m` uses 1-hour history, which keeps the embedded data small.
- **Rain** after 15 Nov 2026 won't appear in the cloud, because the live feed carries no rain values. Re-running
  the export adds archive rain only up to the cut-over date set in `export_history.py` (`CUT`).
- **Alert rules** are not in the cloud. They need a token with alerting permissions.

## Notes

- Grafana Cloud rejects a single PromQL look-back longer than 32 days. So the summary tiles use a stepped range
  query (`[$__interval]`) and reduce it in Grafana, not one `[$__range]` instant query.
- Don't hand-edit the history panels' queries in the Grafana UI. The embedded CSV makes them very long. Change
  `build_cloud.py` and re-upload instead.
