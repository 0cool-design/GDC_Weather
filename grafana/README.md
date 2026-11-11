# Grafana Integration (Future)

This directory is reserved for Grafana dashboard configs when this project
merges with the rs485-grafana dashboard.

## Current data exports

The monitor writes two Grafana-ready formats alongside the SQLite database:

| Path | Format | Use |
|------|--------|-----|
| `data/influx_lp/weather_YYYY-MM-DD.lp` | InfluxDB line protocol | Import with `influx write -f` |
| `logs/weather_YYYY-MM-DD.jsonl` | Newline-delimited JSON | Telegraf json input plugin |

## Import historic .lp files into InfluxDB

```bash
influx write \
  --bucket weather \
  --org GDC \
  --token $INFLUXDB_TOKEN \
  --file data/influx_lp/weather_2024-01-01.lp
```

## Enable live push to InfluxDB

Set in `.env`:

```
INFLUXDB_ENABLED=true
INFLUXDB_URL=http://localhost:8086
INFLUXDB_TOKEN=your_token
INFLUXDB_ORG=GDC
INFLUXDB_BUCKET=weather
```
