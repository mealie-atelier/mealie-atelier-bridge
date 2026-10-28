"""Signed, token-free image URLs for recipes returned over the WebSocket."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant

from .api import safe_id


def sign_images(hass: HomeAssistant, entry_id: str, result: Any) -> Any:
    """Allow <img> display without placing Mealie API tokens in the browser."""

    def decorate(item: Any):
        if isinstance(item, list):
            return [decorate(row) for row in item]
        if not isinstance(item, dict):
            return item
        item = {**item}
        if item.get("image") and item.get("id"):
            path = f"/api/mealie_atelier/image/{entry_id}/{item['id']}"
            item["imageUrl"] = async_sign_path(hass, path, timedelta(hours=2))
        if isinstance(item.get("recipeInstructions"), list) and item.get("id"):
            recipe_id = safe_id(item["id"])
            instructions = []
            for raw_step in item["recipeInstructions"]:
                if not isinstance(raw_step, dict) or not isinstance(raw_step.get("text"), str):
                    instructions.append(raw_step)
                    continue
                step = {**raw_step}
                image_urls = []

                def replace_image(match, image_urls=image_urls):
                    tag = match.group(0)
                    source = re.search(
                        r"\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", tag, re.IGNORECASE
                    )
                    src = (
                        next((value for value in source.groups() if value is not None), "")
                        if source
                        else ""
                    )
                    prefix = f"/api/media/recipes/{item['id']}/assets/"
                    filename = src[len(prefix) :] if src.startswith(prefix) else ""
                    if not re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", filename
                    ) or filename in (".", ".."):
                        return ""
                    asset_path = (
                        f"/api/mealie_atelier/recipe_asset/{entry_id}/{recipe_id}/{filename}"
                    )
                    image_urls.append(async_sign_path(hass, asset_path, timedelta(hours=2)))
                    return ""

                step["text"] = re.sub(
                    r"<img\b[^>]*>", replace_image, step["text"], flags=re.IGNORECASE
                ).strip()
                step["imageUrls"] = image_urls
                instructions.append(step)
            item["recipeInstructions"] = instructions
        if isinstance(item.get("recipe"), dict):
            item["recipe"] = decorate(item["recipe"])
        if isinstance(item.get("items"), list):
            item["items"] = decorate(item["items"])
        return item

    return decorate(result)
