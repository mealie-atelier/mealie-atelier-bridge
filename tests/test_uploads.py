"""Upload sessions: limits, order, magic bytes, checksum, expiry and ownership."""

from __future__ import annotations

import base64
import hashlib

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mealie_atelier.const import (
    UPLOAD_CHUNK_BYTES,
    UPLOAD_MAX_BYTES,
    UPLOAD_TTL_SECONDS,
)
from custom_components.mealie_atelier.errors import AtelierError
from custom_components.mealie_atelier.uploads import UploadSessions

from .mealie_fake import JPEG, PNG, WEBP
from .test_recipe_editing import send, upload


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def begin(sessions: UploadSessions, payload: bytes, mime: str, user: str = "u1", **kw) -> dict:
    return sessions.begin(
        user,
        "gnocchi",
        kw.get("purpose", "cover"),
        kw.get("size", len(payload)),
        mime,
        kw.get("sha256", hashlib.sha256(payload).hexdigest()),
    )


def code_of(call) -> str:
    with pytest.raises(AtelierError) as caught:
        call()
    return caught.value.code


@pytest.mark.parametrize(
    ("payload", "mime"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP + b"\x00" * 100, "image/webp")],
)
def test_happy_path_for_each_format(payload, mime):
    sessions = UploadSessions()
    info = begin(sessions, payload, mime)
    assert info["chunk_bytes"] == UPLOAD_CHUNK_BYTES
    assert info["chunks"] == 1

    result = sessions.chunk("u1", info["upload_id"], 0, b64(payload))

    assert result == {"received": 1, "complete": True}
    session = sessions.ready("u1", info["upload_id"], "cover", "gnocchi")
    assert bytes(session.buffer) == payload
    sessions.discard(info["upload_id"])
    assert (
        code_of(lambda: sessions.ready("u1", info["upload_id"], "cover", "gnocchi"))
        == "upload_expired"
    )


def test_large_file_arrives_in_ordered_chunks():
    payload = JPEG + b"\x01" * (600 * 1024)
    sessions = UploadSessions()
    info = begin(sessions, payload, "image/jpeg")
    assert info["chunks"] == 3

    first = sessions.chunk("u1", info["upload_id"], 0, b64(payload[:UPLOAD_CHUNK_BYTES]))
    assert first == {"received": 1, "complete": False}
    assert (
        code_of(
            lambda: sessions.chunk("u1", info["upload_id"], 2, b64(payload[UPLOAD_CHUNK_BYTES:]))
        )
        == "upload_invalid"
    )
    sessions.chunk(
        "u1", info["upload_id"], 1, b64(payload[UPLOAD_CHUNK_BYTES : 2 * UPLOAD_CHUNK_BYTES])
    )
    last = sessions.chunk("u1", info["upload_id"], 2, b64(payload[2 * UPLOAD_CHUNK_BYTES :]))
    assert last == {"received": 3, "complete": True}


@pytest.mark.parametrize(
    "changes",
    [
        {"mime": "image/gif"},
        {"purpose": "avatar"},
        {"size": 99},
        {"size": UPLOAD_MAX_BYTES + 1},
        {"sha256": "abc"},
        {"sha256": "z" * 64},
    ],
)
def test_begin_validates_parameters(changes):
    sessions = UploadSessions()
    mime = changes.pop("mime", "image/jpeg")
    assert code_of(lambda: begin(sessions, JPEG, mime, **changes)) == "upload_invalid"


def test_wrong_magic_bytes_and_wrong_checksum_drop_the_session():
    sessions = UploadSessions()
    fake = b"%PDF-1.7" + b"\x00" * 200
    info = begin(sessions, fake, "image/jpeg")
    assert (
        code_of(lambda: sessions.chunk("u1", info["upload_id"], 0, b64(fake))) == "upload_invalid"
    )
    assert (
        code_of(lambda: sessions.chunk("u1", info["upload_id"], 0, b64(fake))) == "upload_expired"
    )

    info = begin(sessions, JPEG, "image/jpeg", sha256="0" * 64)
    assert (
        code_of(lambda: sessions.chunk("u1", info["upload_id"], 0, b64(JPEG))) == "upload_invalid"
    )
    assert (
        code_of(lambda: sessions.ready("u1", info["upload_id"], "cover", "gnocchi"))
        == "upload_expired"
    )


def test_chunk_validation():
    sessions = UploadSessions()
    payload = JPEG + b"\x01" * (UPLOAD_CHUNK_BYTES + 10)
    info = begin(sessions, payload, "image/jpeg")
    upload_id = info["upload_id"]

    assert code_of(lambda: sessions.chunk("u1", upload_id, 0, "not base64!")) == "upload_invalid"
    assert (
        code_of(lambda: sessions.chunk("u1", upload_id, 0, b64(b"\x01" * (UPLOAD_CHUNK_BYTES + 1))))
        == "upload_invalid"
    )
    assert (
        code_of(lambda: sessions.chunk("u1", upload_id, 1, b64(payload[:100]))) == "upload_invalid"
    )
    # A rejected chunk does not break the session: the right one still works.
    assert sessions.chunk("u1", upload_id, 0, b64(payload[:UPLOAD_CHUNK_BYTES]))["received"] == 1


def test_sessions_expire_and_are_limited():
    now = [1000.0]
    sessions = UploadSessions(clock=lambda: now[0])
    first = begin(sessions, JPEG, "image/jpeg")
    begin(sessions, PNG, "image/png")

    assert code_of(lambda: begin(sessions, JPEG, "image/jpeg")) == "upload_busy"

    now[0] += UPLOAD_TTL_SECONDS + 1
    assert (
        code_of(lambda: sessions.chunk("u1", first["upload_id"], 0, b64(JPEG))) == "upload_expired"
    )
    assert begin(sessions, JPEG, "image/jpeg")["chunks"] == 1


def test_foreign_user_and_purpose_mismatch():
    sessions = UploadSessions()
    info = begin(sessions, JPEG, "image/jpeg")

    assert (
        code_of(lambda: sessions.chunk("u2", info["upload_id"], 0, b64(JPEG))) == "upload_expired"
    )
    sessions.chunk("u1", info["upload_id"], 0, b64(JPEG))
    assert (
        code_of(lambda: sessions.ready("u2", info["upload_id"], "cover", "gnocchi"))
        == "upload_expired"
    )
    assert (
        code_of(lambda: sessions.ready("u1", info["upload_id"], "step", "gnocchi"))
        == "upload_invalid"
    )
    assert (
        code_of(lambda: sessions.ready("u1", info["upload_id"], "cover", "other"))
        == "upload_invalid"
    )


async def test_websocket_upload_errors_use_the_contract_codes(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()
    bad = await send(
        client,
        {
            "type": "mealie_atelier/upload_begin",
            "slug": "gnocchi",
            "purpose": "cover",
            "size": 10,
            "mime": "image/jpeg",
            "sha256": "0" * 64,
        },
    )
    assert bad["error"]["code"] == "upload_invalid"

    await upload(client, "gnocchi", "cover", JPEG, "image/jpeg")
    await upload(client, "gnocchi", "cover", PNG, "image/png")
    busy = await send(
        client,
        {
            "type": "mealie_atelier/upload_begin",
            "slug": "gnocchi",
            "purpose": "cover",
            "size": len(JPEG),
            "mime": "image/jpeg",
            "sha256": hashlib.sha256(JPEG).hexdigest(),
        },
    )
    assert busy["error"]["code"] == "upload_busy"

    expired = await send(
        client,
        {"type": "mealie_atelier/upload_chunk", "upload_id": "missing", "index": 0, "data": ""},
    )
    assert expired["error"]["code"] == "upload_expired"
