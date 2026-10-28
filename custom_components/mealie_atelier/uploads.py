"""In-memory, user-bound chunked photo uploads with size, checksum and magic-byte checks."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import time
from dataclasses import dataclass, field
from uuid import uuid4

from .const import UPLOAD_CHUNK_BYTES, UPLOAD_MAX_BYTES, UPLOAD_MAX_SESSIONS, UPLOAD_TTL_SECONDS
from .errors import AtelierError

PURPOSES = frozenset({"cover", "step"})
MIME_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_MIN_BYTES = 100


def _invalid(message: str) -> AtelierError:
    return AtelierError("upload_invalid", message)


def magic_matches(mime: str, data: bytes) -> bool:
    """Check the leading bytes against the declared image type."""
    if mime == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if mime == "image/png":
        return data[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10])
    if mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


@dataclass
class UploadSession:
    upload_id: str
    user_id: str
    slug: str
    purpose: str
    size: int
    mime: str
    sha256: str
    chunks: int
    touched: float
    received: int = 0
    chunks_done: int = 0
    buffer: bytearray = field(default_factory=bytearray)
    complete: bool = False

    @property
    def extension(self) -> str:
        return MIME_EXTENSIONS[self.mime]


class UploadSessions:
    """Open sessions of one config entry; every session belongs to the user who began it."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._sessions: dict[str, UploadSession] = {}

    def _sweep(self) -> None:
        now = self._clock()
        for key in [k for k, s in self._sessions.items() if now - s.touched > UPLOAD_TTL_SECONDS]:
            del self._sessions[key]

    def begin(
        self, user_id: str, slug: str, purpose: str, size: int, mime: str, sha256: str
    ) -> dict:
        self._sweep()
        if purpose not in PURPOSES or mime not in MIME_EXTENSIONS:
            raise _invalid("Photo must be a JPEG, PNG or WebP for a cover or a step")
        if isinstance(size, bool) or not _MIN_BYTES <= size <= UPLOAD_MAX_BYTES:
            raise _invalid("Photo must be between 100 bytes and 10 MiB")
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise _invalid("The checksum must be 64 hexadecimal characters")
        if len(self._sessions) >= UPLOAD_MAX_SESSIONS:
            raise AtelierError("upload_busy", "Too many uploads are open. Try again in a moment")
        chunks = -(-size // UPLOAD_CHUNK_BYTES)
        session = UploadSession(
            upload_id=str(uuid4()),
            user_id=user_id,
            slug=slug,
            purpose=purpose,
            size=size,
            mime=mime,
            sha256=sha256.lower(),
            chunks=chunks,
            touched=self._clock(),
        )
        self._sessions[session.upload_id] = session
        return {
            "upload_id": session.upload_id,
            "chunk_bytes": UPLOAD_CHUNK_BYTES,
            "chunks": chunks,
        }

    def _session(self, user_id: str, upload_id: str) -> UploadSession:
        self._sweep()
        session = self._sessions.get(upload_id)
        # A foreign session looks exactly like an unknown one.
        if session is None or session.user_id != user_id:
            raise AtelierError("upload_expired", "The upload expired. Start it again")
        return session

    def chunk(self, user_id: str, upload_id: str, index: int, data: str) -> dict:
        session = self._session(user_id, upload_id)
        if session.complete or isinstance(index, bool) or index != session.chunks_done:
            raise _invalid("Chunks must arrive in order")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise _invalid("The chunk is not valid base64") from None
        if not raw or len(raw) > UPLOAD_CHUNK_BYTES:
            raise _invalid("A chunk may hold at most 256 KiB")
        if session.received + len(raw) > session.size:
            self._sessions.pop(upload_id, None)
            raise _invalid("More data arrived than announced")
        session.buffer += raw
        session.received += len(raw)
        session.chunks_done += 1
        session.touched = self._clock()
        if session.chunks_done == session.chunks:
            self._finish(session)
        return {"received": session.chunks_done, "complete": session.complete}

    def _finish(self, session: UploadSession) -> None:
        data = bytes(session.buffer)
        if (
            len(data) != session.size
            or hashlib.sha256(data).hexdigest() != session.sha256
            or not magic_matches(session.mime, data)
        ):
            self._sessions.pop(session.upload_id, None)
            raise _invalid("The photo does not match its size, checksum or type")
        session.complete = True

    def ready(self, user_id: str, upload_id: str, purpose: str, slug: str) -> UploadSession:
        """Return a finished session of this user; it stays open until `discard`."""
        session = self._session(user_id, upload_id)
        if session.purpose != purpose or session.slug != slug or not session.complete:
            raise _invalid("This upload cannot be used here")
        return session

    def discard(self, upload_id: str) -> None:
        self._sessions.pop(upload_id, None)
