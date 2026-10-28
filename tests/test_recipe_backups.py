"""Backup store: retention, LRU eviction with photo files, size limit, failures, removal."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.mealie_atelier.const import (
    BACKUP_MAX_RECIPES,
    BACKUPS_PER_RECIPE,
    IMAGE_BACKUPS_PER_RECIPE,
)
from custom_components.mealie_atelier.errors import AtelierError
from custom_components.mealie_atelier.recipe_backups import RecipeBackupStore

from .mealie_fake import STORE_KEY, WEBP

USER = SimpleNamespace(id="u1", name="Alex")


@pytest.fixture(autouse=True)
def private_config_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    hass.config.config_dir = str(tmp_path)


def recipe(recipe_id: str = "r1", **extra) -> dict:
    return {
        "id": recipe_id,
        "slug": recipe_id,
        "name": "Soup",
        "image": "",
        "recipeIngredient": [],
        "recipeInstructions": [],
        **extra,
    }


def client() -> AsyncMock:
    mock = AsyncMock()
    mock.original_image.return_value = WEBP
    return mock


def photo_files(hass: HomeAssistant) -> list[Path]:
    root = Path(hass.config.path("mealie_atelier", "backups", "e1"))
    return sorted(root.rglob("*.webp"))


async def test_eleven_saves_keep_the_newest_ten(hass: HomeAssistant, hass_storage: dict):
    store = await RecipeBackupStore.async_load(hass, "e1")
    ids = [
        (await store.async_snapshot(client(), recipe(), "basics", USER))["backup_id"]
        for _ in range(11)
    ]

    kept = [b["backup_id"] for b in store.list("r1")]

    assert len(kept) == BACKUPS_PER_RECIPE
    assert kept == list(reversed(ids))[:BACKUPS_PER_RECIPE]
    stored = hass_storage["mealie_atelier_recipe_backups_e1"]
    assert stored["version"] == 1
    assert stored["minor_version"] == 1
    assert len(stored["data"]["recipes"]["r1"]) == BACKUPS_PER_RECIPE


async def test_store_survives_a_reload(hass: HomeAssistant, hass_storage: dict):
    store = await RecipeBackupStore.async_load(hass, "e1")
    snap = await store.async_snapshot(client(), recipe(), "basics", USER)

    reloaded = await RecipeBackupStore.async_load(hass, "e1")

    assert reloaded.get("r1", snap["backup_id"])["user_name"] == "Alex"
    with pytest.raises(AtelierError) as caught:
        reloaded.get("r1", "unknown")
    assert caught.value.code == "backup_not_found"


async def test_least_recent_recipe_is_evicted_with_its_photo(hass: HomeAssistant):
    store = await RecipeBackupStore.async_load(hass, "e1")
    first = await store.async_snapshot(client(), recipe("r0", image="x"), "image", USER, image=True)
    assert len(photo_files(hass)) == 1
    assert first["image_file"] == f"r0/{first['backup_id']}.webp"

    for number in range(1, BACKUP_MAX_RECIPES + 1):
        await store.async_snapshot(client(), recipe(f"r{number}"), "basics", USER)

    assert store.list("r0") == []
    assert len(store.list(f"r{BACKUP_MAX_RECIPES}")) == 1
    assert photo_files(hass) == []


async def test_only_the_newest_three_photos_are_kept(hass: HomeAssistant):
    store = await RecipeBackupStore.async_load(hass, "e1")
    for _ in range(IMAGE_BACKUPS_PER_RECIPE + 2):
        await store.async_snapshot(client(), recipe(image="x"), "image", USER, image=True)

    assert len(photo_files(hass)) == IMAGE_BACKUPS_PER_RECIPE
    assert sum(1 for b in store.list("r1") if b["has_image"]) == IMAGE_BACKUPS_PER_RECIPE
    assert len(store.list("r1")) == IMAGE_BACKUPS_PER_RECIPE + 2


async def test_recipe_without_photo_has_no_image_file(hass: HomeAssistant):
    store = await RecipeBackupStore.async_load(hass, "e1")
    mealie = client()

    snap = await store.async_snapshot(mealie, recipe(image=""), "image", USER, image=True)

    assert snap["image_file"] is None
    mealie.original_image.assert_not_called()


async def test_oversized_snapshot_is_refused_and_not_stored(
    hass: HomeAssistant, hass_storage: dict
):
    store = await RecipeBackupStore.async_load(hass, "e1")
    huge = recipe(description="x" * (512 * 1024))

    with pytest.raises(AtelierError) as caught:
        await store.async_snapshot(client(), huge, "basics", USER)

    assert caught.value.code == "backup_failed"
    assert store.list("r1") == []
    assert STORE_KEY.replace("test-entry", "e1") not in hass_storage


async def test_storage_failure_rolls_back_and_removes_the_photo(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
):
    store = await RecipeBackupStore.async_load(hass, "e1")

    async def broken(self, data):
        raise OSError("read-only file system")

    monkeypatch.setattr("homeassistant.helpers.storage.Store.async_save", broken)

    with pytest.raises(AtelierError) as caught:
        await store.async_snapshot(client(), recipe(image="x"), "image", USER, image=True)

    assert caught.value.code == "backup_failed"
    assert store.list("r1") == []
    assert photo_files(hass) == []


async def test_unreadable_original_photo_fails_closed(hass: HomeAssistant):
    from custom_components.mealie_atelier.api import MealieError

    store = await RecipeBackupStore.async_load(hass, "e1")
    mealie = client()
    mealie.original_image.side_effect = MealieError("gone")

    with pytest.raises(AtelierError) as caught:
        await store.async_snapshot(mealie, recipe(image="x"), "image", USER, image=True)

    assert caught.value.code == "backup_failed"
    assert store.list("r1") == []


async def test_removing_the_entry_deletes_store_and_photos(hass: HomeAssistant, hass_storage: dict):
    store = await RecipeBackupStore.async_load(hass, "e1")
    await store.async_snapshot(client(), recipe(image="x"), "image", USER, image=True)
    assert photo_files(hass)

    await store.async_remove()

    assert photo_files(hass) == []
    assert not Path(hass.config.path("mealie_atelier", "backups", "e1")).exists()
    assert "mealie_atelier_recipe_backups_e1" not in hass_storage
