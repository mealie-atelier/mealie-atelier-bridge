"""Add the Mealie Atelier integration to the rig's Home Assistant through the config flow.

Usage: python3 -I ha_setup_entry.py <ha-url> <ha-token-file> <mealie-token-file>
Skips when an entry exists. Home Assistant reaches Mealie by its compose service name.
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE, HA_TOKEN, MEALIE_TOKEN = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {read(HA_TOKEN)}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode() or "null")


def main() -> None:
    entries = call("GET", "/api/config/config_entries/entry")
    if any(entry["domain"] == "mealie_atelier" for entry in entries):
        print("entry exists")
        return
    flow = call("POST", "/api/config/config_entries/flow", {"handler": "mealie_atelier"})
    form = {"url": "http://mealie:9000", "api_token": read(MEALIE_TOKEN), "verify_ssl": True}
    result = call("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", form)
    if result.get("type") != "create_entry":
        raise SystemExit(f"config flow did not finish: {result.get('errors') or result}")
    print("entry created:", result["title"])


if __name__ == "__main__":
    main()
