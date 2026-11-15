#!/usr/bin/env bash
# One-command install for a new computer (Linux, macOS, WSL, Git Bash).
#   1. Needs Docker (Desktop) running and a .env with the WeatherLink keys filled in.
#   2. Generates every local password/token that is still blank in .env.
#   3. Creates the least-privilege InfluxDB tokens (poller = write-only, Grafana = read-only).
#   4. Starts the stack and, on a fresh install, pulls the station's full history.
# Safe to re-run: it never overwrites a value that is already set.
set -euo pipefail
cd "$(dirname "$0")"

HISTORY_SINCE="${HISTORY_SINCE:-2025-09-30}"   # first day the GDC station has data on WeatherLink
DC="docker compose"

die() { echo "ERROR: $*" >&2; exit 1; }
get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
set_env() {  # set_env KEY VALUE — replace the line, or append it
  if grep -qE "^$1=" .env; then
    local tmp; tmp=$(mktemp)
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k{print k"="v; next} {print}' .env > "$tmp" && cat "$tmp" > .env && rm -f "$tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
rand() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-24}"; }

command -v docker >/dev/null || die "Docker is not installed. Install Docker Desktop first."
docker info >/dev/null 2>&1 || die "Docker is not running. Start Docker Desktop and try again."
[ -f .env ] || die ".env not found. Copy .env.example to .env and fill in the WeatherLink keys."
sed -i.bak 's/\r$//' .env && rm -f .env.bak       # tolerate a .env edited on Windows
chmod 600 .env 2>/dev/null || true

for k in WEATHERLINK_API_KEY WEATHERLINK_API_SECRET WEATHERLINK_STATION_ID; do
  v=$(get "$k"); [ -n "$v" ] && [[ "$v" != your_* ]] || die "$k is empty in .env."
done

echo "==> Generating missing local secrets"
for k in GF_ADMIN_PASSWORD INFLUXDB_ADMIN_PASSWORD; do [ -n "$(get $k)" ] || set_env "$k" "$(rand 24)"; done
[ -n "$(get INFLUXDB_ADMIN_TOKEN)" ] || set_env INFLUXDB_ADMIN_TOKEN "$(rand 64)"
set_env INFLUXDB_ENABLED true

FRESH=false
if [ -z "$(get INFLUXDB_TOKEN)" ] || [ -z "$(get INFLUXDB_GRAFANA_TOKEN)" ]; then
  FRESH=true
  echo "==> Starting InfluxDB to create access tokens"
  # Placeholders satisfy compose's required-variable checks until the real tokens exist.
  INFLUXDB_TOKEN=pending INFLUXDB_GRAFANA_TOKEN=pending $DC up -d --wait influxdb
  ORG=$(get INFLUXDB_ORG); ORG=${ORG:-GDC}
  BUCKET=$(get INFLUXDB_BUCKET); BUCKET=${BUCKET:-weather}
  ix() { INFLUXDB_TOKEN=pending INFLUXDB_GRAFANA_TOKEN=pending $DC exec -T influxdb sh -c "influx $* --token \"\$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN\""; }
  BID=$(ix bucket list --org "$ORG" --name "$BUCKET" --hide-headers | awk '{print $1}')
  [ -n "$BID" ] || die "Bucket $BUCKET not found in InfluxDB."
  tok() { grep -o '"token": *"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/'; }
  [ -n "$(get INFLUXDB_TOKEN)" ] || set_env INFLUXDB_TOKEN \
    "$(ix auth create --org "$ORG" --write-bucket "$BID" --description gdc-weather-poller-write --json | tok)"
  [ -n "$(get INFLUXDB_GRAFANA_TOKEN)" ] || set_env INFLUXDB_GRAFANA_TOKEN \
    "$(ix auth create --org "$ORG" --read-bucket "$BID" --description grafana-read --json | tok)"
  [ -n "$(get INFLUXDB_TOKEN)" ] && [ -n "$(get INFLUXDB_GRAFANA_TOKEN)" ] || die "Could not create InfluxDB tokens."
fi

echo "==> Building and starting the stack"
$DC up -d --build --wait

if $FRESH; then
  echo "==> Pulling station history since $HISTORY_SINCE (about 5 minutes)"
  $DC exec -T gdc-weather python main.py --since "$HISTORY_SINCE"
fi

cat <<EOF

Done. Open Grafana:  http://localhost:3000
  user:     admin
  password: $(get GF_ADMIN_PASSWORD)     (also stored in .env as GF_ADMIN_PASSWORD)
EOF
