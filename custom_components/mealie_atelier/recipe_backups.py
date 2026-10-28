"""Recipe snapshots saved before every Mealie write, so any change can be undone."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import MealieClient, MealieError, safe_id
from .const import BACKUP_MAX_RECIPES, BACKUPS_PER_RECIPE, IMAGE_BACKUPS_PER_RECIPE
from .errors import AtelierError

STORE_VERSION = 1
STORE_MINOR_VERSION = 1
MAX_SNAPSHOT_BYTES = 512 * 1024
KINDS = frozenset({"ingredients", "basics", "instructions", "image", "step_image", "restore"})
FIELDS = (
    "name",
    "description",
    "recipeServings",
    "recipeYieldQuantity",
    "recipeYield",
    "recipeIngredient",
    "recipeInstructions",
    "assets",
)


def _failed() -> AtelierError:
    return AtelierError("backup_failed", "The backup could not be saved, so nothing was changed")


def _summary(kind: str, recipe: dict) -> str:
    ingredients = len(recipe.get("recipeIngredient") or [])
    steps = len(recipe.get("recipeInstructions") or [])
    return {
        "ingredients": f"{ingredients} ingredients",
        "instructions": f"{steps} steps",
        "step_image": f"{steps} steps",
        "image": "Cover photo",
        "basics": "Name, description and servings",
    }.get(kind, f"{ingredients} ingredients, {steps} steps")


def _make_dir(path: Path) -> None:
    """Create a private directory chain (0700), also for the intermediate levels."""
    missing = [p for p in (path, *path.parents) if not p.exists()]
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)


def _write_private(path: Path, data: bytes) -> None:
    _make_dir(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def _remove_files(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


class RecipeBackupStore:
    """Newest-first snapshots per recipe in a Store, plus cover images as private files."""

    def __init__(self, hass: HomeAssistant, entry_id: str, store: Store, data: dict):
        self._hass = hass
        self._entry_id = entry_id
        self._store = store
        self._data = data
        self._lock = asyncio.Lock()

    @classmethod
    async def async_load(cls, hass: HomeAssistant, entry_id: str) -> RecipeBackupStore:
        store = Store(
            hass,
            STORE_VERSION,
            f"mealie_atelier_recipe_backups_{entry_id}",
            minor_version=STORE_MINOR_VERSION,
        )
        data = await store.async_load()
        if not isinstance(data, dict):
            data = {}
        data.setdefault("recipes", {})
        data.setdefault("lru", [])
        return cls(hass, entry_id, store, data)

    @property
    def _root(self) -> Path:
        return Path(self._hass.config.path("mealie_atelier", "backups", self._entry_id))

    def _file(self, relative: str) -> Path:
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise _failed()
        return path

    def list(self, recipe_id: str) -> list[dict]:
        return [
            {
                "backup_id": snap["backup_id"],
                "created_at": snap["created_at"],
                "user_name": snap["user_name"],
                "kind": snap["kind"],
                "summary": snap["summary"],
                "has_image": bool(snap.get("image_file")),
            }
            for snap in self._data["recipes"].get(recipe_id, [])
        ]

    def get(self, recipe_id: str, backup_id: str) -> dict:
        for snap in self._data["recipes"].get(recipe_id, []):
            if snap["backup_id"] == backup_id:
                return snap
        raise AtelierError("backup_not_found", "This backup does not exist")

    async def read_image(self, snapshot: dict) -> bytes | None:
        if not snapshot.get("image_file"):
            return None
        path = self._file(snapshot["image_file"])
        try:
            return await self._hass.async_add_executor_job(path.read_bytes)
        except OSError:
            raise _failed() from None

    async def async_snapshot(
        self, client: MealieClient, recipe: dict, kind: str, user: Any, *, image: bool = False
    ) -> dict:
        """Save a snapshot of a freshly fetched recipe; any failure means "do not write"."""
        if kind not in KINDS:
            raise _failed()
        try:
            recipe_id = safe_id(recipe.get("id"))
        except ValueError:
            raise _failed() from None
        snapshot = {
            "backup_id": str(uuid4()),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "user_id": getattr(user, "id", None),
            "user_name": getattr(user, "name", None) or "",
            "kind": kind,
            "slug": recipe.get("slug"),
            "summary": _summary(kind, recipe),
            "fields": {key: copy.deepcopy(recipe.get(key)) for key in FIELDS},
            "image_file": None,
        }
        if len(json.dumps(snapshot, ensure_ascii=False).encode()) > MAX_SNAPSHOT_BYTES:
            raise _failed()
        async with self._lock:
            written: Path | None = None
            if image and recipe.get("image"):
                try:
                    original = await client.original_image(recipe_id)
                    snapshot["image_file"] = f"{recipe_id}/{snapshot['backup_id']}.webp"
                    written = self._file(snapshot["image_file"])
                    await self._hass.async_add_executor_job(_write_private, written, original)
                except (MealieError, OSError):
                    raise _failed() from None
            before = copy.deepcopy(self._data)
            doomed = self._insert(recipe_id, snapshot)
            try:
                await self._store.async_save(self._data)
            except Exception:  # noqa: BLE001 - any storage problem must block the write
                self._data = before
                if written:
                    await self._hass.async_add_executor_job(_remove_files, [written])
                raise _failed() from None
            await self._hass.async_add_executor_job(_remove_files, doomed)
        return snapshot

    def _insert(self, recipe_id: str, snapshot: dict) -> list[Path]:
        """Add the snapshot, apply the retention limits, return files that are now unreferenced."""
        doomed: list[Path] = []
        snapshots = [snapshot, *self._data["recipes"].get(recipe_id, [])]
        for old in snapshots[BACKUPS_PER_RECIPE:]:
            if old.get("image_file"):
                doomed.append(self._file(old["image_file"]))
        snapshots = snapshots[:BACKUPS_PER_RECIPE]
        with_image = [snap for snap in snapshots if snap.get("image_file")]
        for old in with_image[IMAGE_BACKUPS_PER_RECIPE:]:
            doomed.append(self._file(old["image_file"]))
            old["image_file"] = None
        self._data["recipes"][recipe_id] = snapshots
        lru = [key for key in self._data["lru"] if key != recipe_id]
        lru.append(recipe_id)
        while len(lru) > BACKUP_MAX_RECIPES:
            evicted = lru.pop(0)
            for old in self._data["recipes"].pop(evicted, []):
                if old.get("image_file"):
                    doomed.append(self._file(old["image_file"]))
        self._data["lru"] = lru
        return doomed

    async def async_remove(self) -> None:
        """Drop the store and every image file of this entry (entry removal)."""
        await self._store.async_remove()
        await self._hass.async_add_executor_job(shutil.rmtree, self._root, True)
