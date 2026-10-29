"""Strictly scoped, async Mealie client. No user-controlled URL paths."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp

from .const import MAX_PAGE_SIZE, MAX_PARALLEL_REQUESTS, UPLOAD_MAX_BYTES
from .errors import structured_editor_required

_RECIPE_SLUG = re.compile(r"^[\w\-]{1,150}$", re.ASCII)
_ITEM_ID = re.compile(r"^[\w\-]{1,150}$", re.ASCII)
_ASSET_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$", re.ASCII)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IMAGE_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
_VALID_MEALS = frozenset({"breakfast", "lunch", "dinner", "side", "dessert", "drink", "snack"})


class MealieError(Exception):
    """Remote server or API problem, deliberately without leaking API keys."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class MealieAuthError(MealieError):
    """Invalid API key or insufficient Mealie permissions."""


def safe_id(value: Any) -> str:
    """Constrain path segments to non-URL, non-directory identifiers."""
    if not isinstance(value, str) or not _ITEM_ID.fullmatch(value):
        raise ValueError("Invalid identifier")
    return quote(value, safe="-")


def safe_slug(value: Any) -> str:
    if not isinstance(value, str) or not _RECIPE_SLUG.fullmatch(value):
        raise ValueError("Invalid recipe slug")
    return quote(value, safe="-")


def safe_date(value: Any) -> str:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError("Expected YYYY-MM-DD")
    # Reject impossible calendar dates.
    from datetime import date

    date.fromisoformat(value)
    return value


def safe_text(value: Any, limit: int = 500) -> str:
    if not isinstance(value, str):
        # Validation failures share the ValueError response contract at the API boundary.
        raise ValueError("Text required")  # noqa: TRY004
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        raise ValueError("Text length outside allowed range")
    return cleaned


def check_image(data: bytes, ext: str) -> str:
    """Validate size and magic bytes against the claimed extension; return the content type."""
    if ext not in _IMAGE_TYPES or not 100 <= len(data) <= UPLOAD_MAX_BYTES:
        raise ValueError("Photo must be JPEG, PNG or WebP and 10 MiB or smaller")
    magic = {
        "jpg": data[:3] == b"\xff\xd8\xff",
        "png": data[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10]),
        "webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }
    if not magic[ext]:
        raise ValueError("Photo data does not match file extension")
    return _IMAGE_TYPES[ext]


async def read_capped(content: aiohttp.StreamReader, limit: int) -> bytes:
    """Read a body up to limit + 1 bytes; one `read(n)` only returns what is buffered."""
    body = bytearray()
    while len(body) <= limit:
        chunk = await content.read(limit + 1 - len(body))
        if not chunk:
            break
        body.extend(chunk)
    return bytes(body)


class MealieClient:
    """Mealie API client for both dashboard data and allowlisted mutations."""

    def __init__(
        self, session: aiohttp.ClientSession, url: str, token: str, verify_ssl: bool = True
    ):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Enter an http(s) URL without credentials, query or fragment")
        # urlsplit only checks the port when it is read: a port above 65535 must fail here.
        parsed.port  # noqa: B018
        self.url = url.rstrip("/")
        self._session = session
        # Mealie stops answering for good once its database pool runs dry (about 30 parallel
        # requests on 3.x), and a tablet with several cards easily sends that many at once.
        self._slots = asyncio.Semaphore(MAX_PARALLEL_REQUESTS)
        self._token = token
        self._verify_ssl = verify_ssl

    async def request(
        self, method: str, path: str, *, params: dict | None = None, data: Any = None
    ) -> Any:
        if (
            method not in ("GET", "POST", "PUT", "PATCH", "DELETE")
            or not path.startswith("/api/")
            or ".." in path
        ):
            raise ValueError("Unsupported request")
        timeout = aiohttp.ClientTimeout(total=25)
        try:
            async with (
                self._slots,
                self._session.request(
                    method,
                    f"{self.url}{path}",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/json",
                    },
                    params=params,
                    json=data,
                    ssl=self._verify_ssl,
                    allow_redirects=False,
                    timeout=timeout,
                ) as response,
            ):
                if response.status in (401, 403):
                    raise MealieAuthError(
                        "Mealie refused access: verify API token and permissions",
                        status=response.status,
                    )
                if response.status >= 300:
                    raise MealieError(
                        f"Mealie returned HTTP {response.status}", status=response.status
                    )
                if response.status == 204:
                    return None
                if "json" in response.content_type:
                    return await response.json()
                result = await response.text()
                return result
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise MealieError(f"Unable to reach Mealie: {type(exc).__name__}") from exc

    async def image(self, recipe_id: str) -> bytes | None:
        """Small images only; no redirect and no media path injection."""
        path = f"/api/media/recipes/{safe_id(recipe_id)}/images/min-original.webp"
        try:
            async with (
                self._slots,
                self._session.get(
                    f"{self.url}{path}",
                    headers={"Authorization": f"Bearer {self._token}"},
                    ssl=self._verify_ssl,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response,
            ):
                if response.status == 404:
                    return None
                if response.status != 200 or response.content_type != "image/webp":
                    raise MealieError("Recipe image not available")
                if response.content_length and response.content_length > 2_000_000:
                    raise MealieError("Image exceeds size limit")
                payload = await read_capped(response.content, 2_000_000)
                if len(payload) > 2_000_000:
                    raise MealieError("Image exceeds size limit")
                return payload
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise MealieError("Unable to load recipe image") from exc

    async def recipe_asset(self, recipe_id: str, filename: str) -> tuple[bytes, str] | None:
        """Fetch one bounded raster asset from one recipe, never an arbitrary media path."""
        recipe = safe_id(recipe_id)
        if (
            not isinstance(filename, str)
            or not _ASSET_FILENAME.fullmatch(filename)
            or filename in (".", "..")
        ):
            raise ValueError("Invalid recipe asset")
        path = f"/api/media/recipes/{recipe}/assets/{quote(filename, safe='.-_')}"
        try:
            async with (
                self._slots,
                self._session.get(
                    f"{self.url}{path}",
                    headers={"Authorization": f"Bearer {self._token}"},
                    ssl=self._verify_ssl,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response,
            ):
                if response.status == 404:
                    return None
                if response.status != 200:
                    raise MealieError("Recipe step image not available")
                if response.content_length and response.content_length > UPLOAD_MAX_BYTES:
                    raise MealieError("Recipe step image exceeds size limit")
                payload = await read_capped(response.content, UPLOAD_MAX_BYTES)
                if len(payload) > UPLOAD_MAX_BYTES:
                    raise MealieError("Recipe step image exceeds size limit")
                if payload.startswith(b"\xff\xd8\xff"):
                    return payload, "image/jpeg"
                if payload.startswith(bytes([137, 80, 78, 71, 13, 10, 26, 10])):
                    return payload, "image/png"
                if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
                    return payload, "image/webp"
                raise MealieError("Recipe step image has an unsupported format")
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise MealieError("Unable to load recipe step image") from exc

    async def upload_image(self, slug: str, image: bytes, ext: str) -> dict:
        """Upload an original photo to the documented Mealie multipart endpoint."""
        content_type = check_image(image, ext)
        form = aiohttp.FormData()
        form.add_field("image", image, filename=f"photo.{ext}", content_type=content_type)
        form.add_field("extension", ext)
        try:
            async with (
                self._slots,
                self._session.put(
                    f"{self.url}/api/recipes/{safe_slug(slug)}/image",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/json",
                    },
                    data=form,
                    ssl=self._verify_ssl,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as response,
            ):
                if response.status != 200:
                    raise MealieError(f"Mealie rejected photo (HTTP {response.status})")
                return await response.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise MealieError("Photo upload failed") from exc

    async def upload_asset(self, slug: str, name: str, ext: str, data: bytes) -> dict:
        """Attach a raster photo as a recipe asset; returns Mealie's name, icon and fileName."""
        content_type = check_image(data, ext)
        form = aiohttp.FormData()
        form.add_field("name", safe_text(name, 120))
        form.add_field("icon", "mdi-image")
        form.add_field("extension", ext)
        form.add_field("file", data, filename=f"{name}.{ext}", content_type=content_type)
        try:
            async with (
                self._slots,
                self._session.post(
                    f"{self.url}/api/recipes/{safe_slug(slug)}/assets",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/json",
                    },
                    data=form,
                    ssl=self._verify_ssl,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as response,
            ):
                if response.status >= 300:
                    raise MealieError(f"Mealie rejected photo (HTTP {response.status})")
                asset = await response.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise MealieError("Photo upload failed") from exc
        if not isinstance(asset, dict) or not _ASSET_FILENAME.fullmatch(
            str(asset.get("fileName") or "")
        ):
            raise MealieError("Mealie returned an unexpected asset response")
        return asset

    async def delete_image(self, slug: str) -> None:
        await self.request("DELETE", f"/api/recipes/{safe_slug(slug)}/image")

    async def original_image(self, recipe_id: str) -> bytes:
        """Backup original photo before replacing it. Fail closed on missing original."""
        url = f"{self.url}/api/media/recipes/{safe_id(recipe_id)}/images/original.webp"
        try:
            async with (
                self._slots,
                self._session.get(
                    url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    ssl=self._verify_ssl,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as response,
            ):
                if response.status != 200 or response.content_type != "image/webp":
                    raise MealieError("Could not back up original photo")
                payload = await read_capped(response.content, UPLOAD_MAX_BYTES)
                if len(payload) > UPLOAD_MAX_BYTES:
                    raise MealieError("Original photo too large for safety backup")
                return payload
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise MealieError("Could not back up original photo") from exc

    async def check_connection(self) -> dict:
        data = await self.request("GET", "/api/users/self")
        if not isinstance(data, dict) or not data.get("id"):
            raise MealieError("Mealie returned an unexpected account response")
        return data

    async def recipes(self, page: int = 1, per_page: int = 24, search: str = "") -> dict:
        page = max(1, min(10000, int(page)))
        per_page = max(1, min(MAX_PAGE_SIZE, int(per_page)))
        params: dict[str, Any] = {
            "page": page,
            "perPage": per_page,
            "orderBy": "name",
            "orderDirection": "asc",
        }
        if search.strip():
            params["search"] = search.strip()[:100]
        result = await self.request("GET", "/api/recipes", params=params)
        if not isinstance(result, dict):
            raise MealieError("Unexpected recipe listing")
        return result

    async def recipe(self, slug: str) -> dict:
        result = await self.request("GET", f"/api/recipes/{safe_slug(slug)}")
        if not isinstance(result, dict):
            raise MealieError("Unexpected recipe response")
        return result

    async def patch_recipe(self, slug: str, changes: dict) -> dict:
        """PATCH only the given recipe fields; Mealie answers with the updated recipe."""
        if not isinstance(changes, dict) or not changes:
            raise ValueError("Nothing to update")
        result = await self.request("PATCH", f"/api/recipes/{safe_slug(slug)}", data=changes)
        if not isinstance(result, dict):
            raise MealieError("Unexpected recipe response")
        return result

    async def _catalog(self, kind: str, search: str, page: int, per_page: int) -> dict:
        params: dict[str, Any] = {
            "page": max(1, min(10000, int(page))),
            "perPage": max(1, min(50, int(per_page))),
            "orderBy": "name",
            "orderDirection": "asc",
        }
        if search.strip():
            params["search"] = search.strip()[:80]
        result = await self.request("GET", f"/api/{kind}", params=params)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise MealieError(f"Unexpected {kind} listing")
        return result

    async def foods(self, search: str = "", page: int = 1, per_page: int = 20) -> dict:
        return await self._catalog("foods", search, page, per_page)

    async def units(self, search: str = "", page: int = 1, per_page: int = 20) -> dict:
        return await self._catalog("units", search, page, per_page)

    async def food(self, food_id: str) -> dict:
        result = await self.request("GET", f"/api/foods/{safe_id(food_id)}")
        if not isinstance(result, dict):
            raise MealieError("Unexpected food response")
        return result

    async def unit(self, unit_id: str) -> dict:
        result = await self.request("GET", f"/api/units/{safe_id(unit_id)}")
        if not isinstance(result, dict):
            raise MealieError("Unexpected unit response")
        return result

    async def _exact(self, kind: str, name: str) -> dict | None:
        found = await self._catalog(kind, name, 1, 50)
        wanted = name.casefold()
        return next(
            (
                item
                for item in found["items"]
                if isinstance(item, dict) and str(item.get("name", "")).casefold() == wanted
            ),
            None,
        )

    async def create_food(self, name: str, plural_name: str = "") -> dict:
        """Create a food, or return the existing exact (case-insensitive) match."""
        name = safe_text(name, 120)
        existing = await self._exact("foods", name)
        if existing:
            return {**existing, "existing": True}
        body = {"name": name}
        if isinstance(plural_name, str) and plural_name.strip():
            body["pluralName"] = safe_text(plural_name, 120)
        created = await self.request("POST", "/api/foods", data=body)
        if not isinstance(created, dict) or not created.get("id"):
            raise MealieError("Unexpected food response")
        return {**created, "existing": False}

    async def create_unit(self, name: str, abbreviation: str = "") -> dict:
        """Create a unit, or return the existing exact (case-insensitive) match."""
        name = safe_text(name, 60)
        existing = await self._exact("units", name)
        if existing:
            return {**existing, "existing": True}
        abbreviation = abbreviation.strip() if isinstance(abbreviation, str) else ""
        if len(abbreviation) > 20:
            raise ValueError("Abbreviation too long")
        body = {"name": name, "abbreviation": abbreviation, "useAbbreviation": bool(abbreviation)}
        created = await self.request("POST", "/api/units", data=body)
        if not isinstance(created, dict) or not created.get("id"):
            raise MealieError("Unexpected unit response")
        return {**created, "existing": False}

    async def mealplan(self, start: str, end: str) -> list:
        safe_date(start)
        safe_date(end)
        if end < start:
            raise ValueError("End date is before start date")
        from datetime import date

        if (date.fromisoformat(end) - date.fromisoformat(start)).days > 62:
            raise ValueError("Date range must be 62 days or shorter")
        result = await self.request(
            "GET", "/api/households/mealplans", params={"start_date": start, "end_date": end}
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("items", [])
        raise MealieError("Unexpected mealplan response")

    async def shopping(self) -> dict:
        lists = await self.request("GET", "/api/households/shopping/lists", params={"perPage": -1})
        items = await self.request("GET", "/api/households/shopping/items", params={"perPage": -1})
        return {
            "lists": lists.get("items", []) if isinstance(lists, dict) else lists,
            "items": items.get("items", []) if isinstance(items, dict) else items,
        }

    async def metadata(self) -> dict:
        categories, tags, cookbooks = await asyncio.gather(
            self.request("GET", "/api/organizers/categories", params={"perPage": -1}),
            self.request("GET", "/api/organizers/tags", params={"perPage": -1}),
            self.request("GET", "/api/households/cookbooks", params={"perPage": -1}),
        )
        return {
            "categories": categories.get("items", [])
            if isinstance(categories, dict)
            else categories,
            "tags": tags.get("items", []) if isinstance(tags, dict) else tags,
            "cookbooks": cookbooks.get("items", []) if isinstance(cookbooks, dict) else cookbooks,
        }

    async def mutate(self, action: str, payload: dict) -> Any:
        """All operations are explicit to prevent arbitrary proxying to Mealie."""
        if action == "shopping_toggle":
            item_id = safe_id(payload.get("item_id"))
            checked = payload.get("checked")
            if not isinstance(checked, bool):
                raise ValueError("checked must be boolean")
            current = await self.request("GET", f"/api/households/shopping/items/{item_id}")
            # Mealie update schema accepts the existing item's fields; preserve food/units.
            updated = {**current, "checked": checked}
            return await self.request(
                "PUT", f"/api/households/shopping/items/{item_id}", data=updated
            )
        if action == "shopping_list_create":
            return await self.request(
                "POST",
                "/api/households/shopping/lists",
                data={
                    "name": safe_text(payload.get("name"), 100),
                },
            )
        if action == "shopping_from_recipe":
            list_id = safe_id(payload.get("list_id"))
            recipe_id = safe_id(payload.get("recipe_id"))
            try:
                return await self.request(
                    "POST",
                    f"/api/households/shopping/lists/{list_id}/recipe",
                    data=[{"recipeId": recipe_id}],
                )
            except MealieError as err:
                if err.status not in (404, 405):
                    raise
                return await self.request(
                    "POST", f"/api/households/shopping/lists/{list_id}/recipe/{recipe_id}", data={}
                )
        if action == "shopping_add":
            item = {
                "shoppingListId": safe_id(payload.get("list_id")),
                "note": safe_text(payload.get("note"), 250),
                "quantity": 1,
                "checked": False,
            }
            return await self.request("POST", "/api/households/shopping/items", data=item)
        if action == "mealplan_add":
            day = safe_date(payload.get("date"))
            meal = payload.get("meal_type", "dinner")
            if meal not in _VALID_MEALS:
                raise ValueError("Invalid meal type")
            recipe_id = safe_id(payload.get("recipe_id"))
            return await self.request(
                "POST",
                "/api/households/mealplans",
                data={
                    "date": day,
                    "entryType": meal,
                    "recipeId": recipe_id,
                    "title": "",
                    "text": "",
                },
            )
        if action == "mealplan_delete":
            return await self.request(
                "DELETE", f"/api/households/mealplans/{safe_id(payload.get('entry_id'))}"
            )
        if action == "recipe_create":
            return await self.request(
                "POST", "/api/recipes", data={"name": safe_text(payload.get("name"), 120)}
            )
        if action == "recipe_update":
            slug, changes = recipe_update_changes(payload)
            return await self.request("PATCH", f"/api/recipes/{slug}", data=changes)
        if action == "recipe_import":
            recipe_url = safe_text(payload.get("url"), 2000)
            parsed = urlsplit(recipe_url)
            if (
                parsed.scheme not in ("https", "http")
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Enter a valid recipe URL")
            return await self.request("POST", "/api/recipes/create/url", data={"url": recipe_url})
        raise ValueError("Unsupported operation")

    async def shopping_lists(self) -> list[dict]:
        """Shopping list summaries, used to validate and name a sync source."""
        result = await self.request("GET", "/api/households/shopping/lists", params={"perPage": -1})
        lists = result.get("items", []) if isinstance(result, dict) else result
        if not isinstance(lists, list):
            raise MealieError("Unexpected shopping list response")
        return [row for row in lists if isinstance(row, dict)]

    async def shopping_items(self, list_id: str) -> list[dict]:
        """Items of one shopping list in list order."""
        list_id = safe_id(list_id)
        result = await self.request(
            "GET",
            "/api/households/shopping/items",
            params={
                "queryFilter": f'shoppingListId="{list_id}"',
                "perPage": -1,
                "orderBy": "position",
            },
        )
        items = result.get("items", []) if isinstance(result, dict) else result
        if not isinstance(items, list):
            raise MealieError("Unexpected shopping item response")
        # The server-side filter is a convenience; never let another list's items through.
        return [
            item
            for item in items
            if isinstance(item, dict) and item.get("shoppingListId") == list_id
        ]

    async def shopping_item(self, item_id: str) -> dict:
        result = await self.request("GET", f"/api/households/shopping/items/{safe_id(item_id)}")
        if not isinstance(result, dict):
            raise MealieError("Unexpected shopping item response")
        return result

    async def set_shopping_item_checked(
        self, item_id: str, checked: bool, current: dict | None = None
    ) -> dict:
        """Check or uncheck one item, preserving its food, unit and note.

        Pass the item that was just read as `current` to write exactly the state that was compared.
        """
        if not isinstance(checked, bool):
            raise ValueError("checked must be boolean")
        if current is None:
            current = await self.shopping_item(item_id)
        result = await self.request(
            "PUT",
            f"/api/households/shopping/items/{safe_id(item_id)}",
            data={**current, "checked": checked},
        )
        return result if isinstance(result, dict) else {**current, "checked": checked}


def recipe_update_changes(payload: dict) -> tuple[str, dict]:
    """Validate a recipe_update payload and return the slug and the Mealie PATCH body."""
    slug = safe_slug(payload.get("slug"))
    changes = {}
    if "name" in payload:
        changes["name"] = safe_text(payload["name"], 120)
    if "description" in payload:
        if not isinstance(payload["description"], str) or len(payload["description"]) > 2000:
            raise ValueError("Description too long")
        changes["description"] = payload["description"]
    if "servings" in payload:
        servings = payload["servings"]
        if isinstance(servings, bool) or not isinstance(servings, int) or not 1 <= servings <= 100:
            raise ValueError("Servings must be a whole number from 1 to 100")
        changes["recipeServings"] = servings
    if "instructions" in payload:
        lines = payload["instructions"]
        if not isinstance(lines, list) or len(lines) > 60:
            raise ValueError("Too many cooking steps")
        changes["recipeInstructions"] = [{"text": safe_text(line, 2000)} for line in lines]
    if "ingredients" in payload:
        # Plain text would destroy linked foods and units; the structured editor owns this.
        raise structured_editor_required()
    if not changes:
        raise ValueError("Nothing to update")
    return slug, changes
