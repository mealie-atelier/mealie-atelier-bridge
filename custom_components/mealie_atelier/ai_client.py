"""One JSON request to the configured AI provider (OpenAI or Anthropic Claude).

The provider follows the key: keys starting with ``sk-ant-`` are Anthropic keys,
everything else is treated as an OpenAI key. The key is never logged or returned.
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from .api import MealieError

OPENAI_URL = "https://api.openai.com/v1/responses"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_MODEL = "gpt-4.1-mini"
ANTHROPIC_MODEL = "claude-opus-5-5"
ANTHROPIC_VERSION = "2026-10-21"
ANTHROPIC_BETA = "server-side-fallback-2026-07-01"
ANTHROPIC_PREFIX = "sk-ant-"
ANTHROPIC_MIN_TIMEOUT = 60.0


class AiError(MealieError):
    """An AI provider call failed or returned something unusable."""


def provider_for(key: str) -> str:
    """Return "anthropic" for Anthropic keys and "openai" for any other key."""
    return "anthropic" if key.strip().startswith(ANTHROPIC_PREFIX) else "openai"


def _raise_for_status(status: int) -> None:
    if status in (401, 403):
        raise AiError("AI provider rejected the API key", status)
    if status in (429, 529) or status >= 500:
        raise AiError(f"AI provider is temporarily unavailable (HTTP {status})", status)
    raise AiError(f"AI provider returned HTTP {status}", status)


async def request_json(
    session: aiohttp.ClientSession,
    key: str,
    *,
    instructions: str,
    input_text: str,
    schema: dict[str, Any],
    schema_name: str,
    timeout: float,
) -> Any:
    """Send one structured request and return the parsed JSON document.

    Raises AiError for transport, status, refusal and unparsable output.
    Callers validate the shape of the returned document.
    """
    key = key.strip()
    anthropic = provider_for(key) == "anthropic"
    if anthropic:
        url = ANTHROPIC_URL
        timeout = max(timeout, ANTHROPIC_MIN_TIMEOUT)
        headers = {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "anthropic-beta": ANTHROPIC_BETA,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": ANTHROPIC_MODEL,
            "max_tokens": 4000,
            "system": instructions,
            "messages": [{"role": "user", "content": input_text}],
            "output_config": {
                "effort": "low",
                "format": {"type": "json_schema", "schema": schema},
            },
            "fallbacks": "default",
        }
    else:
        url = OPENAI_URL
        headers = {"Authorization": f"Bearer {key}"}
        body = {
            "model": OPENAI_MODEL,
            "store": False,
            "instructions": instructions,
            "input": input_text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
    try:
        async with session.post(
            url,
            json=body,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            if response.status != 200:
                if anthropic:
                    _raise_for_status(response.status)
                raise AiError(f"AI provider returned HTTP {response.status}", response.status)
            data = await response.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise AiError("AI provider unavailable") from exc
    try:
        if anthropic:
            if data.get("stop_reason") == "refusal":
                raise AiError("AI provider refused the request")
            if data.get("stop_reason") == "max_tokens":
                raise AiError("AI answer was cut off")
            text = next(block["text"] for block in data["content"] if block.get("type") == "text")
        else:
            text = next(
                part["text"]
                for entry in data.get("output", [])
                for part in entry.get("content", [])
                if part.get("type") == "output_text"
            )
        return json.loads(text)
    except (KeyError, StopIteration, TypeError, AttributeError, ValueError) as exc:
        raise AiError("AI answer could not be read") from exc
