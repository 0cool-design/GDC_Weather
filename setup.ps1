# One-command install for a new Windows computer (PowerShell, Docker Desktop).
# Same steps as setup.sh:
#   1. Needs Docker Desktop running and a .env with the WeatherLink keys filled in.
#   2. Generates every local password/token that is still blank in .env.
#   3. Creates the least-privilege InfluxDB tokens (poller = write-only, Grafana = read-only).
#   4. Starts the stack and, on a fresh install, pulls the station's full history.
# Safe to re-run: it never overwrites a value that is already set.
#
# Run from the project folder:   powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$HistorySince = if ($env:HISTORY_SINCE) { $env:HISTORY_SINCE } else { '2025-09-30' }  # first day with station data
$EnvPath = Join-Path $PSScriptRoot '.env'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)   # a BOM would break docker compose's .env parser

function Die($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Read-Env { [IO.File]::ReadAllText($EnvPath) -split "`r?`n" }
function Get-Env($key) {
    $line = Read-Env | Where-Object { $_ -match "^$key=" } | Select-Object -Last 1
    if ($line) { $line.Substring($key.Length + 1) } else { '' }
}
function Set-Env($key, $value) {
    $lines = [Collections.Generic.List[string]](Read-Env)
    $i = $lines.FindIndex({ param($l) $l -match "^$key=" })
    if ($i -ge 0) { $lines[$i] = "$key=$value" } else { $lines.Add("$key=$value") }
    [IO.File]::WriteAllText($EnvPath, (($lines | Where-Object { $_ -ne $null }) -join "`n").TrimEnd() + "`n", $Utf8NoBom)
}
function New-Secret($len = 24) {
    $chars = [char[]]'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    $bytes = New-Object byte[] $len
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}
function Compose { & docker compose @args; if ($LASTEXITCODE -ne 0) { Die "docker compose $args failed." } }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Die 'Docker is not installed. Install Docker Desktop first.' }
docker info *> $null; if ($LASTEXITCODE -ne 0) { Die 'Docker is not running. Start Docker Desktop and try again.' }
if (-not (Test-Path $EnvPath)) { Die '.env not found. Copy .env.example to .env and fill in the WeatherLink keys.' }

foreach ($k in 'WEATHERLINK_API_KEY', 'WEATHERLINK_API_SECRET', 'WEATHERLINK_STATION_ID') {
    $v = Get-Env $k
    if (-not $v -or $v -like 'your_*') { Die "$k is empty in .env." }
}

Write-Host '==> Generating missing local secrets'
foreach ($k in 'GF_ADMIN_PASSWORD', 'INFLUXDB_ADMIN_PASSWORD') { if (-not (Get-Env $k)) { Set-Env $k (New-Secret 24) } }
if (-not (Get-Env 'INFLUXDB_ADMIN_TOKEN')) { Set-Env 'INFLUXDB_ADMIN_TOKEN' (New-Secret 64) }
Set-Env 'INFLUXDB_ENABLED' 'true'

$Fresh = $false
if (-not (Get-Env 'INFLUXDB_TOKEN') -or -not (Get-Env 'INFLUXDB_GRAFANA_TOKEN')) {
    $Fresh = $true
    Write-Host '==> Starting InfluxDB to create access tokens'
    # Placeholders satisfy compose's required-variable checks until the real tokens exist.
    $env:INFLUXDB_TOKEN = 'pending'; $env:INFLUXDB_GRAFANA_TOKEN = 'pending'
    Compose up -d --wait influxdb
    $org = Get-Env 'INFLUXDB_ORG'; if (-not $org) { $org = 'GDC' }
    $bucket = Get-Env 'INFLUXDB_BUCKET'; if (-not $bucket) { $bucket = 'weather' }
    function Influx([string]$cmd) {
        # No inner quotes: Windows PowerShell 5.1 mangles them when calling native programs (the token has none to protect).
        $out = docker compose exec -T influxdb sh -c "influx $cmd --token `$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"
        if ($LASTEXITCODE -ne 0) { Die "influx $cmd failed." }
        $out
    }
    $bid = ((Influx "bucket list --org $org --name $bucket --hide-headers") -split '\s+')[0]
    if (-not $bid) { Die "Bucket $bucket not found in InfluxDB." }
    if (-not (Get-Env 'INFLUXDB_TOKEN')) {
        $w = (Influx "auth create --org $org --write-bucket $bid --description gdc-weather-poller-write --json") -join '' | ConvertFrom-Json
        Set-Env 'INFLUXDB_TOKEN' $w.token
    }
    if (-not (Get-Env 'INFLUXDB_GRAFANA_TOKEN')) {
        $r = (Influx "auth create --org $org --read-bucket $bid --description grafana-read --json") -join '' | ConvertFrom-Json
        Set-Env 'INFLUXDB_GRAFANA_TOKEN' $r.token
    }
    Remove-Item Env:INFLUXDB_TOKEN, Env:INFLUXDB_GRAFANA_TOKEN
    if (-not (Get-Env 'INFLUXDB_TOKEN') -or -not (Get-Env 'INFLUXDB_GRAFANA_TOKEN')) { Die 'Could not create InfluxDB tokens.' }
}

Write-Host '==> Building and starting the stack'
Compose up -d --build --wait

if ($Fresh) {
    Write-Host "==> Pulling station history since $HistorySince (about 5 minutes)"
    Compose exec -T gdc-weather python main.py --since $HistorySince
}

Write-Host ''
Write-Host 'Done. Open Grafana:  http://localhost:3000' -ForegroundColor Green
Write-Host '  user:     admin'
Write-Host "  password: $(Get-Env 'GF_ADMIN_PASSWORD')     (also stored in .env as GF_ADMIN_PASSWORD)"
