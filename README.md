# GDC Weather

Weather monitoring for the GDC Davis station in Muscat, Oman. A small Python service reads the
station from the WeatherLink cloud every minute, stores every reading, and shows it on a Grafana
dashboard with alerts for heat, wind, dew point, pressure drops and station outages.

```
 Davis station ──► WeatherLink cloud ──► gdc-weather (Python, every 60 s)
                                           ├─► InfluxDB      all history (since 2025-09-30)
                                           ├─► /metrics ◄── Prometheus   latest values, alerts
                                           └─► SQLite + files (local archive)
                                                     │
                            Grafana  http://localhost:3000  ◄── InfluxDB + Prometheus
```

Everything runs in Docker. Nothing needs to be installed except Docker itself.

| Part | What it does |
|---|---|
| **gdc-weather** | The poller (`main.py`, `src/`). Calls the WeatherLink v2 API, converts the response, writes it out. |
| **InfluxDB 2.7** | Time-series database holding every reading. The dashboard's charts query it. |
| **Prometheus** | Scrapes the poller's latest values every 60 s. Used for the "Now" tiles and the alert rules. |
| **Grafana 11** | The dashboard and alerting. Its config lives in `grafana/` and loads automatically. |

---

## Install on a new computer

**You need:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (on Windows it uses WSL 2), an internet
connection, about 2 GB of disk, and this folder, including its `.env` file. `.env` holds the WeatherLink API key.

1. **Copy the folder** to the computer, for example to `C:\GDC_Weather` or `~/GDC_Weather`.
2. **Check `.env`.** It must exist next to `docker-compose.yml`. If you only have `.env.example`, copy it to `.env`
   and fill in the four `WEATHERLINK_*` lines at the top.
3. **Start Docker Desktop** and wait until it says *Engine running*.
4. **Run the setup script** from the project folder:

   **Windows (PowerShell):**
   ```powershell
   cd C:\GDC_Weather
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```
   **Linux / macOS / WSL:**
   ```bash
   cd ~/GDC_Weather
   ./setup.sh
   ```

The script:
1. creates this computer's own Grafana and InfluxDB passwords;
2. creates the database access keys;
3. builds and starts all four containers;
4. downloads the station's full history (about 5 minutes).

At the end it prints the Grafana login.

5. **Open http://localhost:3000** and sign in as `admin` with the password the script printed. It is also in
   `.env` as `GF_ADMIN_PASSWORD`. The **GDC Weather** dashboard opens as the home page.

Running the script again is safe. It keeps existing passwords and data, and just rebuilds and restarts.

> Only one computer needs to run this at a time. Two computers polling the same station with the same API key is
> allowed, but each keeps its own separate copy of the data.

---

## Everyday use

Run these commands from the project folder.

| Task | Command |
|---|---|
| Start everything | `docker compose up -d` |
| Stop everything (data is kept) | `docker compose down` |
| Status | `docker compose ps` |
| Live poller log | `docker compose logs -f gdc-weather` |
| Latest reading | `docker compose exec gdc-weather python main.py --show-latest` |
| Fill a gap (last N days) | `docker compose exec gdc-weather python main.py --backfill 7` |
| Re-pull all history | `docker compose exec gdc-weather python main.py --since 2025-09-30` |
| Apply code changes | `docker compose up -d --build` |

Docker Desktop restarts the containers automatically after a reboot, once Docker Desktop itself is running.
To start it with Windows, turn on *Settings → General → Start Docker Desktop when you sign in*.

**Using the dashboard:** the panels, the colours and the **Window** setting are explained in
[grafana/README.md](grafana/README.md). The short version:
- The time picker (top right) chooses the period.
- **Window** sets how much each point on the charts averages: `1h` for a few days, `1d` for months.
- Click ⓘ on any panel for a description.

---

## Configuration (`.env`)

| Key | Set by | Meaning |
|---|---|---|
| `WEATHERLINK_API_KEY`, `WEATHERLINK_API_SECRET` | you | WeatherLink v2 API key (weatherlink.com → Account). |
| `WEATHERLINK_STATION_ID`, `WEATHERLINK_STATION_UUID` | you | Which station to poll. Find with `python main.py --list-stations`. |
| `POLL_INTERVAL` | you | Seconds between polls (default 60). |
| `ALERT_WEBHOOK_URL` | optional | Webhook for the poller's own alerts. |
| `GF_ADMIN_PASSWORD` | setup script | Grafana login for user `admin`. |
| `INFLUXDB_ADMIN_PASSWORD`, `INFLUXDB_ADMIN_TOKEN` | setup script | InfluxDB admin, for maintenance only. |
| `INFLUXDB_TOKEN` | setup script | **Write-only** key the poller uses. |
| `INFLUXDB_GRAFANA_TOKEN` | setup script | **Read-only** key Grafana uses. |
| `WEATHERLINK_API_TOKEN_V1`, `_DID`, `_USERNAME`, `_PASSWORD` | not needed | Only for old WeatherLinkIP/USB loggers (v1 API). |

After editing `.env`, run `docker compose up -d` to apply it.

---

## About the data

- **History starts 2025-09-30**, the first day the station has data on WeatherLink.
- **History has one reading every 15 minutes.** The station (WeatherLink Pro plan, recording interval 15 min) only
  archives at that rate, so older data can't be downloaded at a finer resolution.
- **Since 2026-11-15 the poller saves the live feed every minute.**
- **Known outages** (no data exists on WeatherLink either): 2–6 Oct 2025, 12 Apr 2026, 7–8 Jul 2026 and
  22 Jul – 4 Aug 2026.
- **Units:** the station reports °F, mph, inHg and inches. The dashboard converts them to °C, km/h, hPa and mm.
- **No solar or UV sensor** is installed. The live feed carries no rain values, so rain is history-only.

**Where it is stored:** in Docker volumes, not in this folder. They survive `docker compose down` and reboots:

| Volume | Contents |
|---|---|
| `gdc_weather_influxdb-data` | All readings (the important one) |
| `gdc_weather_grafana-data` | Grafana users, alert state |
| `gdc_weather_prometheus-data` | Last year of live values |
| `gdc_weather_weather-data` | SQLite archive and daily line-protocol files |

`docker compose down -v` **deletes** these volumes. If that happens, run the setup script again, which also
re-downloads the history.

---

## Project layout

```
main.py                    Poller entry point and CLI (--backfill, --since, --show-latest, --list-stations)
src/client.py              WeatherLink v2 (and legacy v1) API client, request signing
src/processor.py           Turns API responses into flat records
src/storage.py             SQLite, JSON logs, InfluxDB line protocol + live InfluxDB writes
src/metrics.py             Prometheus /metrics endpoint
src/monitor.py             The polling loop
src/alerting.py            Poller-side alerts (stale data, webhook)
Dockerfile                 Poller image (runs as a non-root user)
docker-compose.yml         The four services
setup.sh / setup.ps1       First-time install (Linux/macOS/WSL and Windows)
grafana/                   Dashboard, datasources, alert rules, Prometheus config — see grafana/README.md
dashboard.html, history.html, gdc-weather.service
                           Older stand-alone pages and a systemd unit from before the Docker setup (not used)
```

The dashboard JSON (`grafana/dashboards/weather.json`) is loaded from disk. Edit that file, or use *Save as* in
Grafana to make an editable copy. Changes to the provisioned dashboard can't be saved in the UI.

---

## Security

- **`.env` contains the WeatherLink API key and all local passwords.** It is in `.gitignore` and must never be
  committed, emailed or posted. If a USB stick or computer holding it is lost, create a new API key on
  weatherlink.com (Account → API Key v2), which disables the old one, then update `.env` everywhere.
- Each install generates its own passwords and tokens. Nothing local is shared between computers except the
  WeatherLink key.
- Grafana, InfluxDB and Prometheus listen on `127.0.0.1` only, so other devices on the network can't reach them.
  To view from another device, use an SSH tunnel or Tailscale rather than opening the ports.
- InfluxDB tokens are least-privilege: the poller can only write, Grafana can only read.
- More detail, including how to rotate each password, is in [grafana/README.md](grafana/README.md#security).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Setup says *Docker is not running* | Start Docker Desktop and wait for *Engine running*. |
| `Dashboard failed to load`, or Prometheus exits with *not a directory* | Docker can't see the project folder. On Windows with the project inside WSL: Docker Desktop → *Settings → Resources → WSL integration* → turn on your distro → *Apply & restart*, then `docker compose up -d --force-recreate`. Docker updates can switch this off. |
| Docker Desktop shows *Ingest server … bind: Only one usage of each socket address* | Click **Quit**, never *Reset to factory defaults*, which deletes all data. Then restart Windows. |
| `port is already allocated` | Something else uses port 3000, 8086 or 9090. Stop it, or change the left-hand port in `docker-compose.yml`. |
| Dashboard shows *No data* | Widen the time range. Check `docker compose logs gdc-weather` for API errors, which usually mean a wrong key or station ID in `.env`. |
| Forgot the Grafana password | It's `GF_ADMIN_PASSWORD` in `.env`. If it was changed in the UI, follow *Rotating a credential* in `grafana/README.md`. |
| Gap in the charts after downtime | `docker compose exec gdc-weather python main.py --backfill 3` (covers the last 3 days). |
