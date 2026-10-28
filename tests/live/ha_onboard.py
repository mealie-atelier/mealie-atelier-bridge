"""Onboard a disposable Home Assistant container and write a long-lived access token.

Usage: python3 -I ha_onboard.py <ha-url> <token-out-file>
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import urllib.parse
import urllib.request

BASE, OUT = sys.argv[1].rstrip("/"), sys.argv[2]
CLIENT = BASE + "/"


def post(path: str, body: dict, token: str | None = None, form: bool = False):
    data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    content_type = "application/x-www-form-urlencoded" if form else "application/json"
    req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode() or "null")


async def long_lived(access: str) -> str:
    """Ask for a long-lived token over a minimal stdlib websocket client."""
    host = urllib.parse.urlparse(BASE)
    reader, writer = await asyncio.open_connection(host.hostname, host.port)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write(
        f"GET /api/websocket HTTP/1.1\r\nHost: {host.netloc}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    await reader.readuntil(b"\r\n\r\n")

    async def recv() -> dict:
        head = await reader.readexactly(2)
        length = head[1] & 0x7F
        if length == 126:
            length = int.from_bytes(await reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await reader.readexactly(8), "big")
        return json.loads(await reader.readexactly(length))

    def send(obj: dict) -> None:
        payload = json.dumps(obj).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        else:
            header.append(0x80 | 126)
            header += len(payload).to_bytes(2, "big")
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        writer.write(bytes(header) + mask + masked)

    await recv()
    send({"type": "auth", "access_token": access})
    await recv()
    send(
        {
            "id": 1,
            "type": "auth/long_lived_access_token",
            "client_name": "atelier-live",
            "lifespan": 3650,
        }
    )
    result = await recv()
    writer.close()
    return result["result"]


def main() -> None:
    user = {
        "client_id": CLIENT,
        "name": "Atelier Test",
        "username": "atelier",
        "password": "atelier-test-only",
        "language": "de",
    }
    code = post("/api/onboarding/users", user)["auth_code"]
    grant = {"grant_type": "authorization_code", "code": code, "client_id": CLIENT}
    access = post("/auth/token", grant, form=True)["access_token"]
    post("/api/onboarding/core_config", {}, access)
    post("/api/onboarding/analytics", {}, access)
    post("/api/onboarding/integration", {"client_id": CLIENT, "redirect_uri": CLIENT}, access)
    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write(asyncio.run(long_lived(access)))
    print("onboarded")


if __name__ == "__main__":
    main()
