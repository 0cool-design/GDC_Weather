"""WeatherLink API clients — v2 (HMAC) and v1 (token) for legacy devices."""

import hashlib
import hmac
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.weatherlink.com/v2"


class WeatherLinkError(Exception):
    pass


class WeatherLinkClient:
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Secret": api_secret})

    def _sign(self, params: dict) -> str:
        """Generate HMAC-SHA256 signature for sorted parameter string."""
        # Sort params and concatenate as key+value pairs
        message = "".join(f"{k}{v}" for k, v in sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _get(self, path: str, extra_params: dict | None = None, retries: int = 3) -> dict[str, Any]:
        """Signed GET request with retry logic."""
        t = int(time.time())
        params = {"api-key": self.api_key, "t": t}
        if extra_params:
            params.update(extra_params)

        params["api-signature"] = self._sign(params)

        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                logger.warning("Request timeout (attempt %d/%d): %s", attempt, retries, path)
            except requests.exceptions.HTTPError as e:
                logger.error("HTTP error %s for %s: %s", e.response.status_code, path, e.response.text)
                raise WeatherLinkError(f"HTTP {e.response.status_code}: {e.response.text}") from e
            except requests.exceptions.RequestException as e:
                logger.warning("Request error (attempt %d/%d): %s", attempt, retries, e)

            if attempt < retries:
                time.sleep(2 ** attempt)

        raise WeatherLinkError(f"All {retries} attempts failed for {path}")

    def list_stations(self) -> list[dict]:
        """Return all stations linked to this account."""
        data = self._get("/stations")
        return data.get("stations", [])

    def current_conditions(self, station_id: str) -> dict[str, Any]:
        """Return current sensor readings for a station."""
        extra = {"station-id": station_id}
        return self._get(f"/current/{station_id}", extra_params=extra)

    def historic(self, station_id: str, start_ts: int, end_ts: int) -> dict[str, Any]:
        """Return historic data for a time range (Unix timestamps)."""
        extra = {
            "station-id": station_id,
            "start-timestamp": start_ts,
            "end-timestamp": end_ts,
        }
        return self._get(f"/historic/{station_id}", extra_params=extra)


# ---------------------------------------------------------------------------
# WeatherLink v1 client (WeatherLinkIP / USB / SER / Vantage Connect)
# ---------------------------------------------------------------------------

BASE_URL_V1 = "https://api.weatherlink.com/v1"


class WeatherLinkV1Client:
    """v1 API — requires the Device ID (DID) printed on / shown in the device settings.

    DID format is typically a 12-hex-char MAC-like string, e.g. '001D0A123456'.
    Find it at weatherlink.com → My Stations → select station → Device ID.
    """

    def __init__(self, api_token: str, did: str, username: str = "", password: str = ""):
        self.api_token = api_token
        self.did = did
        self.username = username
        self.password = password
        self.session = requests.Session()

    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> dict[str, Any]:
        p: dict = {"apiToken": self.api_token}
        if self.username:
            p["user"] = self.username
        if self.password:
            p["pass"] = self.password
        if params:
            p.update(params)

        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(f"{BASE_URL_V1}{path}", params=p, timeout=15)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                logger.warning("v1 timeout (attempt %d/%d): %s", attempt, retries, path)
            except requests.exceptions.HTTPError as e:
                raise WeatherLinkError(f"v1 HTTP {e.response.status_code}: {e.response.text}") from e
            except requests.exceptions.RequestException as e:
                logger.warning("v1 request error (attempt %d/%d): %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(2 ** attempt)

        raise WeatherLinkError(f"v1: all {retries} attempts failed for {path}")

    def current_conditions(self) -> dict[str, Any]:
        """Fetch current conditions for the DID."""
        return self._get(f"/NoAA/{self.did}.json")

    def historic(self, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        """Fetch archived records between two Unix timestamps.

        WeatherLink v1 returns paginated archive records.
        Returns a flat list of raw record dicts.
        """
        from datetime import datetime, timezone
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)

        # v1 historic endpoint: /v1/NoAA/{did}/{start_epoch}/{end_epoch}.json
        data = self._get(f"/NoAA/{self.did}/{start_ts}/{end_ts}.json")
        return data.get("sensor_data", data.get("data", [data]))
