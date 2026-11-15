"""Upload cloud/out/cloud_dashboard.json to Grafana Cloud, replacing the GDC Weather dashboard.

Reads GRAFANA_CLOUD_URL and GRAFANA_CLOUD_TOKEN (a service-account token, Editor or Admin) from .env.
Keeps the dashboard's folder and creates a new version, so earlier versions stay restorable under
Dashboard settings → Versions.

Usage:  python3 cloud/upload.py ["change message"]
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "cloud" / "out" / "cloud_dashboard.json"
UID = "gdc-weather"

env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
url, token = env.get("GRAFANA_CLOUD_URL", "").rstrip("/"), env.get("GRAFANA_CLOUD_TOKEN", "")
if not url or not token:
    sys.exit("Set GRAFANA_CLOUD_URL and GRAFANA_CLOUD_TOKEN in .env first.")


def api(path, body=None):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        sys.exit(f"{path}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}")


current = api(f"/api/dashboards/uid/{UID}")
dash = json.loads(DASH.read_text())
dash["uid"], dash["version"] = UID, current["dashboard"]["version"]
dash.pop("id", None)
res = api("/api/dashboards/db", {"dashboard": dash, "folderUid": current["meta"]["folderUid"], "overwrite": False,
                                 "message": sys.argv[1] if len(sys.argv) > 1 else "Rebuilt from cloud/build_cloud.py"})
print(f"{res['status']}: {url}{res['url']} (version {res['version']})")
