"""Structured editing, backups, restore and photos against real HA and a mocked Mealie."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.mealie_atelier.const import (
    CONF_EXPERIMENTAL_INGREDIENTS,
    CONF_READ_ONLY,
    UPLOAD_CHUNK_BYTES,
)
from custom_components.mealie_atelier.recipe_safety import fingerprint

from .conftest import JSON, MEALIE_URL
from .mealie_fake import (
    FLOUR,
    JPEG,
    PNG,
    RECIPE_ID,
    SLUG,
    WEBP,
    FakeRecipeServer,
)


@pytest.fixture(autouse=True)
def private_config_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    """Backup photos must never land in the shared test configuration folder."""
    hass.config.config_dir = str(tmp_path)


@pytest.fixture
def server(mock_mealie: AiohttpClientMocker, hass_storage: dict[str, Any]) -> FakeRecipeServer:
    return FakeRecipeServer(mock_mealie, hass_storage)


@pytest.fixture
def structured(hass: HomeAssistant, setup_integration: MockConfigEntry) -> MockConfigEntry:
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_EXPERIMENTAL_INGREDIENTS: True}
    )
    return setup_integration


async def send(client, message: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(message)
    return await client.receive_json()


def row(**overrides: Any) -> dict[str, Any]:
    base = {
        "reference_id": FLOUR,
        "quantity": 250,
        "unit_id": "unit-g",
        "food_id": "food-flour",
        "note": "sifted",
        "original_text": "200 g flour",
    }
    return {**base, **overrides}


def mock_catalog(mock: AiohttpClientMocker) -> None:
    mock.get(
        f"{MEALIE_URL}/api/foods/food-flour",
        json={"id": "food-flour", "name": "flour"},
        headers=JSON,
    )
    mock.get(
        f"{MEALIE_URL}/api/units/unit-g",
        json={"id": "unit-g", "name": "gram", "abbreviation": "g"},
        headers=JSON,
    )
    mock.get(f"{MEALIE_URL}/api/foods/missing", status=404, json={}, headers=JSON)


def save_message(server: FakeRecipeServer, rows: list[dict], **overrides: Any) -> dict[str, Any]:
    return {
        "type": "mealie_atelier/ingredients_save",
        "slug": SLUG,
        "expected_hash": fingerprint(server.recipe),
        "rows": rows,
        **overrides,
    }


async def upload(client, slug: str, purpose: str, payload: bytes, mime: str) -> str:
    begin = await send(
        client,
        {
            "type": "mealie_atelier/upload_begin",
            "slug": slug,
            "purpose": purpose,
            "size": len(payload),
            "mime": mime,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    assert begin["success"], begin
    upload_id = begin["result"]["upload_id"]
    for index in range(begin["result"]["chunks"]):
        piece = payload[index * UPLOAD_CHUNK_BYTES : (index + 1) * UPLOAD_CHUNK_BYTES]
        done = await send(
            client,
            {
                "type": "mealie_atelier/upload_chunk",
                "upload_id": upload_id,
                "index": index,
                "data": base64.b64encode(piece).decode(),
            },
        )
        assert done["success"], done
    assert done["result"]["complete"]
    return upload_id


# Food and unit catalogue


async def test_foods_and_units_pass_search_and_clamp_page_size(
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
    hass: HomeAssistant,
):
    mock_mealie.get(
        f"{MEALIE_URL}/api/foods",
        json={"page": 2, "total": 7, "items": [{"id": "f1", "name": "flour", "extra": "x"}]},
        headers=JSON,
    )
    mock_mealie.get(
        f"{MEALIE_URL}/api/units",
        json={"page": 1, "total": 1, "items": [{"id": "u1", "name": "gram", "fraction": True}]},
        headers=JSON,
    )
    reader = await hass_ws_client(hass, hass_read_only_access_token)

    foods = await send(
        reader, {"type": "mealie_atelier/foods", "search": "flo", "page": 2, "per_page": 500}
    )
    query = mock_mealie.mock_calls[-1][1].query
    assert (query["search"], query["page"], query["perPage"]) == ("flo", "2", "50")
    assert query["orderBy"] == "name"
    assert foods["result"] == {
        "items": [{"id": "f1", "name": "flour", "pluralName": None}],
        "page": 2,
        "total": 7,
    }

    units = await send(reader, {"type": "mealie_atelier/units", "search": "g", "per_page": 0})
    assert mock_mealie.mock_calls[-1][1].query["perPage"] == "1"
    assert units["result"]["items"][0]["fraction"] is True
    assert "extra" not in foods["result"]["items"][0]

    too_long = await send(reader, {"type": "mealie_atelier/foods", "search": "x" * 81})
    assert not too_long["success"]


async def test_food_create_returns_existing_match_without_post(
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    mock_mealie.get(
        f"{MEALIE_URL}/api/foods",
        json={"items": [{"id": "f1", "name": "Flour", "pluralName": None}]},
        headers=JSON,
    )
    client = await hass_ws_client()

    response = await send(client, {"type": "mealie_atelier/food_create", "name": "flour"})

    assert response["result"] == {
        "id": "f1",
        "name": "Flour",
        "pluralName": None,
        "existing": True,
    }
    assert all(call[0].lower() != "post" for call in mock_mealie.mock_calls)


async def test_food_and_unit_create_post_new_items(
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    mock_mealie.get(f"{MEALIE_URL}/api/foods", json={"items": []}, headers=JSON)
    mock_mealie.get(f"{MEALIE_URL}/api/units", json={"items": []}, headers=JSON)
    mock_mealie.post(
        f"{MEALIE_URL}/api/foods",
        json={"id": "f9", "name": "Sage", "pluralName": "Sages"},
        headers=JSON,
    )
    mock_mealie.post(
        f"{MEALIE_URL}/api/units",
        json={"id": "u9", "name": "pinch", "abbreviation": "pn"},
        headers=JSON,
    )
    client = await hass_ws_client()

    food = await send(
        client, {"type": "mealie_atelier/food_create", "name": "Sage", "plural_name": "Sages"}
    )
    unit = await send(
        client, {"type": "mealie_atelier/unit_create", "name": "pinch", "abbreviation": "pn"}
    )

    assert food["result"]["existing"] is False
    assert unit["result"] == {"id": "u9", "name": "pinch", "abbreviation": "pn", "existing": False}
    posts = [call for call in mock_mealie.mock_calls if call[0].lower() == "post"]
    assert posts[0][2] == {"name": "Sage", "pluralName": "Sages"}
    assert posts[1][2] == {"name": "pinch", "abbreviation": "pn", "useAbbreviation": True}


async def test_catalog_creation_needs_an_admin(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
):
    reader = await hass_ws_client(hass, hass_read_only_access_token)
    calls = mock_mealie.call_count

    food = await send(reader, {"type": "mealie_atelier/food_create", "name": "Sage"})
    unit = await send(reader, {"type": "mealie_atelier/unit_create", "name": "pinch"})

    assert food["error"]["code"] == unit["error"]["code"] == "unauthorized"
    assert mock_mealie.call_count == calls


# Structured ingredients


async def test_ingredients_get_returns_snake_case_rows(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
    hass: HomeAssistant,
):
    reader = await hass_ws_client(hass, hass_read_only_access_token)

    response = await send(reader, {"type": "mealie_atelier/ingredients_get", "slug": SLUG})

    result = response["result"]
    assert result["recipe_id"] == RECIPE_ID
    assert result["servings"] == 4
    assert result["expected_hash"] == fingerprint(server.recipe)
    assert result["rows"][0] == {
        "reference_id": FLOUR,
        "quantity": 200.0,
        "unit": {"id": "unit-g", "name": "gram", "abbreviation": "g"},
        "food": {"id": "food-flour", "name": "flour"},
        "note": "",
        "original_text": "200 g flour",
        "display": "200 g flour",
        "title": "",
    }


async def test_structured_save_writes_the_snapshot_before_the_patch(
    structured: MockConfigEntry,
    server: FakeRecipeServer,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    mock_catalog(mock_mealie)
    client = await hass_ws_client()
    new_row = {
        "reference_id": None,
        "quantity": 2,
        "unit_id": None,
        "food_id": "food-flour",
        "note": "",
        "original_text": "",
    }

    response = await send(client, save_message(server, [row(), new_row]))

    assert response["success"], response
    result = response["result"]
    assert result["saved"] is True
    assert result["expected_hash"] == fingerprint(server.recipe)
    patches = [event for event in server.events if event[0] == "patch"]
    assert len(patches) == 1
    # The PATCH saw exactly one stored snapshot: the backup precedes the write.
    assert patches[0][1] == 1
    body = patches[0][2]
    assert list(body) == ["recipeIngredient"]
    sent = body["recipeIngredient"]
    assert sent[0]["referenceId"] == FLOUR
    assert sent[0]["quantity"] == 250.0
    assert sent[0]["note"] == "sifted"
    assert sent[1]["referenceId"] != FLOUR
    assert sent[1]["food"]["id"] == "food-flour"
    snapshot = server.snapshots()[0]
    assert snapshot["backup_id"] == result["backup_id"]
    assert snapshot["kind"] == "ingredients"
    assert snapshot["summary"] == "1 ingredients"
    assert snapshot["fields"]["recipeIngredient"][0]["quantity"] == 200.0
    assert result["recipe"]["id"] == RECIPE_ID


async def test_structured_save_refuses_changed_recipe_without_patch(
    structured: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()

    response = await send(client, save_message(server, [row()], expected_hash="stale"))

    assert response["error"]["code"] == "recipe_changed"
    assert not [event for event in server.events if event[0] == "patch"]
    assert server.snapshots() == []


async def test_structured_save_gates(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
):
    admin = await hass_ws_client()
    reader = await hass_ws_client(hass, hass_read_only_access_token)
    calls = mock_mealie.call_count

    # Option off: refused before any Mealie request.
    response = await send(admin, save_message(server, [row()]))
    assert response["error"]["code"] == "disabled"

    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_EXPERIMENTAL_INGREDIENTS: True}
    )
    response = await send(reader, save_message(server, [row()]))
    assert response["error"]["code"] == "unauthorized"
    assert mock_mealie.call_count == calls

    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_EXPERIMENTAL_INGREDIENTS: True, CONF_READ_ONLY: True}
    )
    response = await send(admin, save_message(server, [row()]))
    assert response["error"]["code"] == "read_only"
    assert mock_mealie.call_count == calls


async def test_structured_save_rejects_unknown_food_and_bad_rows(
    structured: MockConfigEntry,
    server: FakeRecipeServer,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    mock_catalog(mock_mealie)
    client = await hass_ws_client()

    unknown = await send(client, save_message(server, [row(food_id="missing")]))
    empty = await send(client, save_message(server, []))
    unlinked = await send(
        client,
        save_message(
            server, [row(reference_id=None, food_id=None, unit_id=None, original_text="", note="")]
        ),
    )

    for response in (unknown, empty, unlinked):
        assert response["error"]["code"] == "invalid_request"
    assert not [event for event in server.events if event[0] == "patch"]
    assert server.snapshots() == []


async def test_structured_save_detects_unverified_write(
    structured: MockConfigEntry,
    server: FakeRecipeServer,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    mock_catalog(mock_mealie)
    server.ignore_patch_keys.add("recipeIngredient")
    client = await hass_ws_client()

    response = await send(client, save_message(server, [row()]))

    assert response["error"]["code"] == "write_unverified"
    backup_id = server.snapshots()[0]["backup_id"]
    assert backup_id in response["error"]["message"]


async def test_backup_failure_blocks_the_mealie_write(
    structured: MockConfigEntry,
    server: FakeRecipeServer,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_catalog(mock_mealie)

    async def broken(self, data):
        raise OSError("disk full")

    monkeypatch.setattr("homeassistant.helpers.storage.Store.async_save", broken)
    client = await hass_ws_client()

    response = await send(client, save_message(server, [row()]))

    assert response["error"]["code"] == "backup_failed"
    assert not [event for event in server.events if event[0] == "patch"]


# Basics through mutate


async def test_mutate_rejects_plain_text_ingredients(
    structured: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()

    response = await send(
        client,
        {
            "type": "mealie_atelier/mutate",
            "action": "recipe_update",
            "payload": {"slug": SLUG, "ingredients": ["200 g flour"]},
        },
    )

    assert response["error"]["code"] == "use_structured_editor"
    assert server.events == []


async def test_mutate_name_change_snapshots_first(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()

    response = await send(
        client,
        {
            "type": "mealie_atelier/mutate",
            "action": "recipe_update",
            "payload": {"slug": SLUG, "name": "Potato gnocchi"},
        },
    )

    assert response["success"], response
    patch = next(event for event in server.events if event[0] == "patch")
    assert patch[1] == 1
    assert patch[2] == {"name": "Potato gnocchi"}
    snapshot = server.snapshots()[0]
    assert snapshot["kind"] == "basics"
    assert snapshot["fields"]["name"] == "Gnocchi"


async def test_mutate_instruction_change_is_an_instructions_snapshot(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()

    await send(
        client,
        {
            "type": "mealie_atelier/mutate",
            "action": "recipe_update",
            "payload": {"slug": SLUG, "instructions": ["Boil", "Knead"]},
        },
    )

    assert server.snapshots()[0]["kind"] == "instructions"


# Restore


async def test_restore_writes_fields_after_a_restore_snapshot(
    structured: MockConfigEntry,
    server: FakeRecipeServer,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    mock_catalog(mock_mealie)
    client = await hass_ws_client()
    saved = await send(client, save_message(server, [row(quantity=999)]))
    original_backup = saved["result"]["backup_id"]
    assert server.recipe["recipeIngredient"][0]["quantity"] == 999.0

    response = await send(
        client,
        {"type": "mealie_atelier/recipe_restore", "slug": SLUG, "backup_id": original_backup},
    )

    assert response["success"], response
    assert server.recipe["recipeIngredient"][0]["quantity"] == 200.0
    undo = response["result"]["backup_id"]
    assert undo != original_backup
    kinds = [snap["kind"] for snap in server.snapshots()]
    assert kinds == ["restore", "ingredients"]
    restore_patch = [event for event in server.events if event[0] == "patch"][-1]
    assert restore_patch[1] == 2
    assert "id" not in restore_patch[2]
    # The restore point holds the state that was overwritten.
    assert server.snapshots()[0]["fields"]["recipeIngredient"][0]["quantity"] == 999.0

    listing = await send(client, {"type": "mealie_atelier/recipe_backups", "slug": SLUG})
    assert [b["kind"] for b in listing["result"]["backups"]] == ["restore", "ingredients"]
    assert set(listing["result"]["backups"][0]) == {
        "backup_id",
        "created_at",
        "user_name",
        "kind",
        "summary",
        "ingredient_count",
        "step_count",
        "has_image",
    }


async def test_restore_unknown_backup(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()

    response = await send(
        client, {"type": "mealie_atelier/recipe_restore", "slug": SLUG, "backup_id": "nope"}
    )

    assert response["error"]["code"] == "backup_not_found"
    assert not [event for event in server.events if event[0] == "patch"]


# Cover photo


async def test_cover_replace_backs_up_the_old_photo_then_restore_uploads_it(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
    hass: HomeAssistant,
):
    client = await hass_ws_client()
    upload_id = await upload(client, SLUG, "cover", JPEG, "image/jpeg")

    response = await send(
        client,
        {
            "type": "mealie_atelier/recipe_image_set",
            "slug": SLUG,
            "upload_id": upload_id,
            "expected_image": "abcd",
        },
    )

    assert response["success"], response
    put = next(event for event in server.events if event[0] == "put_image")
    assert put[1] == 1
    snapshot = server.snapshots()[0]
    assert snapshot["kind"] == "image"
    photo = Path(
        hass.config.path("mealie_atelier", "backups", "test-entry", snapshot["image_file"])
    )
    assert photo.read_bytes() == WEBP
    assert photo.stat().st_mode & 0o777 == 0o600
    assert server.recipe["image"] == "efgh"

    restored = await send(
        client,
        {
            "type": "mealie_atelier/recipe_restore",
            "slug": SLUG,
            "backup_id": snapshot["backup_id"],
        },
    )
    assert restored["success"], restored
    uploads = [event for event in server.events if event[0] == "put_image"]
    assert len(uploads) == 2
    sent = [call for call in mock_mealie.mock_calls if call[0].lower() == "put"][-1][2]
    assert any(value == WEBP for _opts, _headers, value in sent._fields)


async def test_cover_replace_refuses_a_changed_photo(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()
    upload_id = await upload(client, SLUG, "cover", PNG, "image/png")

    response = await send(
        client,
        {
            "type": "mealie_atelier/recipe_image_set",
            "slug": SLUG,
            "upload_id": upload_id,
            "expected_image": "other",
        },
    )

    assert response["error"]["code"] == "recipe_changed"
    assert not [event for event in server.events if event[0] == "put_image"]
    assert server.snapshots() == []


async def test_cover_delete_is_backed_up_and_guarded(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()

    stale = await send(
        client,
        {"type": "mealie_atelier/recipe_image_delete", "slug": SLUG, "expected_image": "zzzz"},
    )
    response = await send(
        client,
        {"type": "mealie_atelier/recipe_image_delete", "slug": SLUG, "expected_image": "abcd"},
    )

    assert stale["error"]["code"] == "recipe_changed"
    assert response["result"]["deleted"] is True
    assert server.recipe["image"] == ""
    assert next(e for e in server.events if e[0] == "delete_image")[1] == 1
    assert server.snapshots()[0]["image_file"].endswith(".webp")


# Step photos


async def test_step_photo_uploads_an_asset_then_references_it_in_one_patch(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()
    upload_id = await upload(client, SLUG, "step", JPEG, "image/jpeg")

    response = await send(
        client,
        {
            "type": "mealie_atelier/step_image_add",
            "slug": SLUG,
            "step_index": 1,
            "step_id": "step-b",
            "upload_id": upload_id,
        },
    )

    assert response["success"], response
    file_name = response["result"]["file_name"]
    assert file_name.startswith("atelier-step-2-") and file_name.endswith(".jpg")
    kinds = [event[0] for event in server.events if event[0] in ("post_asset", "patch")]
    assert kinds == ["post_asset", "patch"]
    patch = next(event for event in server.events if event[0] == "patch")
    assert patch[1] == 1
    assert set(patch[2]) == {"assets", "recipeInstructions"}
    assert patch[2]["assets"][-1]["fileName"] == file_name
    text = patch[2]["recipeInstructions"][1]["text"]
    assert text == (
        f'Knead the dough.\n\n<img src="/api/media/recipes/{RECIPE_ID}/assets/{file_name}" alt="" />'
    )
    assert patch[2]["recipeInstructions"][0]["text"] == "Boil the potatoes."
    step = response["result"]["recipe"]["recipeInstructions"][1]
    assert step["text"] == "Knead the dough."
    assert step["imageUrls"][0].startswith(
        f"/api/mealie_atelier/recipe_asset/test-entry/{RECIPE_ID}/{file_name}?authSig="
    )
    assert server.snapshots()[0]["kind"] == "step_image"


async def test_step_photo_rejects_mismatched_step_id(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()
    upload_id = await upload(client, SLUG, "step", PNG, "image/png")

    wrong_id = await send(
        client,
        {
            "type": "mealie_atelier/step_image_add",
            "slug": SLUG,
            "step_index": 0,
            "step_id": "step-b",
            "upload_id": upload_id,
        },
    )
    out_of_range = await send(
        client,
        {
            "type": "mealie_atelier/step_image_add",
            "slug": SLUG,
            "step_index": 5,
            "upload_id": upload_id,
        },
    )

    assert wrong_id["error"]["code"] == out_of_range["error"]["code"] == "step_not_found"
    assert not [e for e in server.events if e[0] in ("post_asset", "patch")]


async def test_step_photo_replace_and_remove_touch_only_tag_and_asset(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    old = "atelier-step-1-aaaaaaaa.png"
    other = "keep-me.jpg"
    server.recipe["assets"] = [
        {"name": "old", "icon": "mdi-image", "fileName": old},
        {"name": "other", "icon": "mdi-image", "fileName": other},
    ]
    server.recipe["recipeInstructions"][0]["text"] = (
        f'Boil the potatoes.\n\n<img src="/api/media/recipes/{RECIPE_ID}/assets/{old}" alt="" />'
    )
    client = await hass_ws_client()
    upload_id = await upload(client, SLUG, "step", JPEG, "image/jpeg")

    replaced = await send(
        client,
        {
            "type": "mealie_atelier/step_image_add",
            "slug": SLUG,
            "step_index": 0,
            "step_id": "step-a",
            "upload_id": upload_id,
            "replace_file_name": old,
        },
    )

    assert replaced["success"], replaced
    patches = [event for event in server.events if event[0] == "patch"]
    assert len(patches) == 1
    names = [asset["fileName"] for asset in patches[0][2]["assets"]]
    assert names == [other, replaced["result"]["file_name"]]
    assert old not in patches[0][2]["recipeInstructions"][0]["text"]

    new_name = replaced["result"]["file_name"]
    removed = await send(
        client,
        {
            "type": "mealie_atelier/step_image_remove",
            "slug": SLUG,
            "step_index": 0,
            "step_id": "step-a",
            "file_name": new_name,
        },
    )

    assert removed["result"]["removed"] is True
    assert server.recipe["recipeInstructions"][0]["text"] == "Boil the potatoes."
    assert [a["fileName"] for a in server.recipe["assets"]] == [other]
    assert len([e for e in server.events if e[0] == "post_asset"]) == 1

    unknown = await send(
        client,
        {
            "type": "mealie_atelier/step_image_remove",
            "slug": SLUG,
            "step_index": 0,
            "step_id": "step-a",
            "file_name": "never-referenced.jpg",
        },
    )
    bad_name = await send(
        client,
        {
            "type": "mealie_atelier/step_image_remove",
            "slug": SLUG,
            "step_index": 0,
            "step_id": "step-a",
            "file_name": "../etc/passwd",
        },
    )
    assert unknown["error"]["code"] == "step_not_found"
    assert bad_name["error"]["code"] == "invalid_request"


async def test_photo_commands_refuse_read_only_and_non_admins(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    mock_mealie: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
):
    reader = await hass_ws_client(hass, hass_read_only_access_token)
    admin = await hass_ws_client()
    begin = {
        "type": "mealie_atelier/upload_begin",
        "slug": SLUG,
        "purpose": "cover",
        "size": 500,
        "mime": "image/png",
        "sha256": "0" * 64,
    }
    commands = [
        begin,
        {"type": "mealie_atelier/upload_chunk", "upload_id": "x", "index": 0, "data": ""},
        {
            "type": "mealie_atelier/recipe_image_set",
            "slug": SLUG,
            "upload_id": "x",
            "expected_image": "",
        },
        {"type": "mealie_atelier/recipe_image_delete", "slug": SLUG, "expected_image": ""},
        {
            "type": "mealie_atelier/step_image_add",
            "slug": SLUG,
            "step_index": 0,
            "upload_id": "x",
        },
        {
            "type": "mealie_atelier/step_image_remove",
            "slug": SLUG,
            "step_index": 0,
            "file_name": "a.jpg",
        },
        {"type": "mealie_atelier/recipe_restore", "slug": SLUG, "backup_id": "x"},
    ]
    calls = mock_mealie.call_count
    for command in commands:
        assert (await send(reader, command))["error"]["code"] == "unauthorized"

    hass.config_entries.async_update_entry(setup_integration, options={CONF_READ_ONLY: True})
    for command in commands:
        assert (await send(admin, command))["error"]["code"] == "read_only"
    assert mock_mealie.call_count == calls


# Step objects and unchanged saves


async def test_mutate_step_objects_keep_titles_ids_and_photos(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    server.recipe["recipeInstructions"][0].update(
        {"title": "Dough", "summary": "Warm", "ingredientReferences": [{"referenceId": FLOUR}]}
    )
    client = await hass_ws_client()

    response = await send(
        client,
        {
            "type": "mealie_atelier/mutate",
            "action": "recipe_update",
            "payload": {
                "slug": SLUG,
                "instructions": [
                    {"id": "step-b", "text": "Knead well."},
                    {"id": "step-a", "text": "Boil gently."},
                    {"id": "unknown", "text": "Serve."},
                ],
            },
        },
    )

    assert response["success"], response
    steps = server.recipe["recipeInstructions"]
    assert steps[0] == {"id": "step-b", "title": "", "text": "Knead well."}
    assert steps[1]["id"] == "step-a"
    assert steps[1]["title"] == "Dough"
    assert steps[1]["ingredientReferences"] == [{"referenceId": FLOUR}]
    assert steps[1]["text"] == "Boil gently."
    assert steps[2] == {"text": "Serve."}
    assert server.snapshots()[0]["kind"] == "instructions"


async def test_mutate_without_changes_writes_no_backup_and_no_patch(
    setup_integration: MockConfigEntry,
    server: FakeRecipeServer,
    hass_ws_client: WebSocketGenerator,
):
    client = await hass_ws_client()

    response = await send(
        client,
        {
            "type": "mealie_atelier/mutate",
            "action": "recipe_update",
            "payload": {
                "slug": SLUG,
                "name": " Gnocchi ",
                "description": "Soft potato dumplings",
                "servings": 4,
                "instructions": [
                    {"id": "step-a", "text": "Boil the potatoes."},
                    {"id": "step-b", "text": "Knead the dough."},
                ],
            },
        },
    )

    assert response["success"], response
    assert response["result"]["unchanged"] is True
    assert server.snapshots() == []
    assert not any(event[0] == "patch" for event in server.events)
