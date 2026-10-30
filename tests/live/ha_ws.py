"""Minimal Home Assistant websocket client for the live rig (stdlib only).

CLI: python3 ha_ws.py <ha-url> <token-file> '<json command>' ['<json command>' ...]
Each command is a JSON object without "id", for example '{"type": "mealie_atelier/status"}'.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import urllib.parse


class HaSocket:
    """One authenticated websocket connection; `call` returns (success, result or error)."""

    def __init__(self, base: str, token: str) -> None:
        self.base, self.token, self._id = base.rstrip("/"), token, 0

    async def __aenter__(self) -> HaSocket:
        host = urllib.parse.urlparse(self.base)
        self._reader, self._writer = await asyncio.open_connection(host.hostname, host.port)
        key = base64.b64encode(os.urandom(16)).decode()
        self._writer.write(
            f"GET /api/websocket HTTP/1.1\r\nHost: {host.netloc}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        await self._reader.readuntil(b"\r\n\r\n")
        await self._recv()
        self._send({"type": "auth", "access_token": self.token})
        if (await self._recv()).get("type") != "auth_ok":
            raise RuntimeError("Home Assistant rejected the token")
        return self

    async def __aexit__(self, *exc) -> None:
        self._writer.close()

    async def _recv(self) -> dict:
        head = await self._reader.readexactly(2)
        length = head[1] & 0x7F
        if length == 126:
            length = int.from_bytes(await self._reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await self._reader.readexactly(8), "big")
        return json.loads(await self._reader.readexactly(length))

    def _send(self, obj: dict) -> None:
        payload = json.dumps(obj).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) < 65536:
            header.append(0x80 | 126)
            header += len(payload).to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += len(payload).to_bytes(8, "big")
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._writer.write(bytes(header) + mask + masked)

    async def call(self, command: dict) -> tuple[bool, object]:
        self._id += 1
        self._send({**command, "id": self._id})
        while True:
            reply = await self._recv()
            if reply.get("id") == self._id and reply.get("type") == "result":
                ok = bool(reply.get("success"))
                return ok, reply.get("result") if ok else reply.get("error")


def state_dir(here: str) -> str:
    """Folder with the rig's tokens: LIVE_STATE when set (a worktree has no state), else ./state."""
    return os.environ.get("LIVE_STATE") or os.path.join(here, "state")


def read_token(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read().strip()


async def _cli(base: str, token: str, commands: list[dict]) -> int:
    failed = 0
    async with HaSocket(base, token) as socket:
        for command in commands:
            ok, value = await socket.call(command)
            failed += not ok
            print(json.dumps(value))
    return 1 if failed else 0


def main() -> None:
    base, token_file, *raw = sys.argv[1:]
    commands = [json.loads(item) for item in raw]
    sys.exit(asyncio.run(_cli(base, read_token(token_file), commands)))


if __name__ == "__main__":
    main()
