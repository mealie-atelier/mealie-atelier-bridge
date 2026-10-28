"""Authenticated, signed-path-friendly image proxy for private Mealie servers."""

from __future__ import annotations

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS

from .api import MealieClient, MealieError, safe_id
from .models import get_loaded_entry


def _client(hass: HomeAssistant, entry_id: str) -> MealieClient | None:
    """Return the client of a loaded entry, or None for unknown or unloaded entries."""
    entry = get_loaded_entry(hass, entry_id)
    return entry.runtime_data.client if entry else None


class MealieAtelierImageView(HomeAssistantView):
    """Images are never public; browser receives a time-limited signed URL."""

    url = "/api/mealie_atelier/image/{entry_id}/{recipe_id}"
    name = "api:mealie_atelier:image"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str, recipe_id: str) -> web.Response:
        hass = request.app[KEY_HASS]
        try:
            safe_id(recipe_id)
            safe_id(entry_id)
        except ValueError:
            raise web.HTTPNotFound() from None
        client = _client(hass, entry_id)
        if client is None:
            raise web.HTTPNotFound()
        try:
            data = await client.image(recipe_id)
        except MealieError:
            raise web.HTTPBadGateway() from None
        if data is None:
            raise web.HTTPNotFound()
        return web.Response(
            body=data,
            content_type="image/webp",
            headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
        )


class MealieAtelierRecipeAssetView(HomeAssistantView):
    """Authenticated, signed proxy for raster photos embedded in recipe steps."""

    url = "/api/mealie_atelier/recipe_asset/{entry_id}/{recipe_id}/{filename}"
    name = "api:mealie_atelier:recipe_asset"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, recipe_id: str, filename: str
    ) -> web.Response:
        hass = request.app[KEY_HASS]
        try:
            safe_id(recipe_id)
            safe_id(entry_id)
        except ValueError:
            raise web.HTTPNotFound() from None
        client = _client(hass, entry_id)
        if client is None:
            raise web.HTTPNotFound()
        try:
            asset = await client.recipe_asset(recipe_id, filename)
        except ValueError:
            raise web.HTTPNotFound() from None
        except MealieError:
            raise web.HTTPBadGateway() from None
        if asset is None:
            raise web.HTTPNotFound()
        data, content_type = asset
        return web.Response(
            body=data,
            content_type=content_type,
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )
