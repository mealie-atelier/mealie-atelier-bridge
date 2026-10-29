"""AI provider selection and the Anthropic request/response contract (local fake servers)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from aiohttp import ClientSession, web

from custom_components.mealie_atelier import ai_client, studio_ai
from custom_components.mealie_atelier import nutrition as n
from custom_components.mealie_atelier.api import MealieError

ANT_KEY = "sk-ant-test-secret"
RECIPE = {
    "name": "Pasta",
    "recipeIngredient": [{"display": "200 g Pasta"}],
    "recipeInstructions": [{"text": "Kochen."}],
}

ANSWERS = {
    "ingredient_suggestions": {"suggestions": [{"index": 0, "query": "oats", "note": ""}]},
    "substitute_options": {
        "alternatives": [{"name": "Margarine", "adjustment": "gleich viel"}],
        "warning": "Prüfen.",
    },
    "recipe_audit": {"issues": [], "summary": "Alles klar."},
}


def _walk_objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _walk_objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk_objects(value)


@pytest.fixture
async def fake(socket_enabled, aiohttp_client):
    """Fake Anthropic endpoint; `state` selects the reply and records the request."""
    state = {"status": 200, "reply": None, "seen": []}

    async def handler(request):
        state["seen"].append({"headers": dict(request.headers), "body": await request.json()})
        if state["status"] != 200:
            return web.json_response({"error": "x"}, status=state["status"])
        body = state["seen"][-1]["body"]
        name = state["answer"] if "answer" in state else None
        reply = state["reply"] or {
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "hm"},
                {"type": "text", "text": json.dumps(ANSWERS[name])},
            ],
        }
        assert body["model"] == "claude-opus-5-5"
        return web.json_response(reply)

    app = web.Application()
    app.router.add_post("/v1/messages", handler)
    client = await aiohttp_client(app)
    url = str(client.make_url("/v1/messages"))
    async with ClientSession() as session:
        with patch.object(ai_client, "ANTHROPIC_URL", url):
            yield state, session


def test_provider_follows_key_prefix():
    assert ai_client.provider_for("sk-ant-api03-abc") == "anthropic"
    assert ai_client.provider_for("  sk-ant-abc") == "anthropic"
    assert ai_client.provider_for("sk-proj-abc") == "openai"
    assert ai_client.provider_for("TEST_AI_KEY") == "openai"


def test_all_schemas_are_strict_objects():
    schemas = []

    async def capture(session, key, *, schema, **_):
        schemas.append(schema)
        raise MealieError("stop")

    import asyncio

    with patch.object(n, "request_json", capture), patch.object(studio_ai, "request_json", capture):
        for call in (
            n.ai_suggest(None, "k", ["a"]),
            studio_ai.substitutes(None, "k", "Butter", "Kuchen"),
            studio_ai.audit_recipe(None, "k", RECIPE),
        ):
            with pytest.raises(MealieError):
                asyncio.run(call)
    assert len(schemas) == 3
    for schema in schemas:
        for obj in _walk_objects(schema):
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])


def _check_request(seen, name):
    request = seen[-1]
    assert request["headers"]["x-api-key"] == ANT_KEY
    assert request["headers"]["anthropic-version"] == "2026-10-21"
    assert "Authorization" not in request["headers"]
    body = request["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["type"] == "object"
    for forbidden in ("thinking", "temperature", "top_p", "top_k"):
        assert forbidden not in body
    assert ANT_KEY not in json.dumps(body)


async def test_suggest_with_anthropic_key(fake):
    state, session = fake
    state["answer"] = "ingredient_suggestions"
    result = await n.ai_suggest(session, ANT_KEY, ["100 g oats"])
    assert result[0]["query"] == "oats"
    _check_request(state["seen"], "ingredient_suggestions")


async def test_substitutes_with_anthropic_key(fake):
    state, session = fake
    state["answer"] = "substitute_options"
    result = await studio_ai.substitutes(session, ANT_KEY, "Butter", "Kuchen")
    assert result["alternatives"][0]["name"] == "Margarine"
    _check_request(state["seen"], "substitute_options")


async def test_audit_with_anthropic_key(fake):
    state, session = fake
    state["answer"] = "recipe_audit"
    result = await studio_ai.audit_recipe(session, ANT_KEY, RECIPE)
    assert result["summary"] == "Alles klar."
    _check_request(state["seen"], "recipe_audit")


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_refusal_and_truncation_are_errors(fake, stop_reason):
    state, session = fake
    state["reply"] = {
        "stop_reason": stop_reason,
        "content": [{"type": "text", "text": json.dumps(ANSWERS["recipe_audit"])}],
    }
    with pytest.raises(MealieError):
        await studio_ai.audit_recipe(session, ANT_KEY, RECIPE)
    with pytest.raises(MealieError):
        await n.ai_suggest(session, ANT_KEY, ["oats"])


@pytest.mark.parametrize(
    ("status", "words"),
    [
        (401, "rejected"),
        (403, "rejected"),
        (429, "temporarily"),
        (529, "temporarily"),
        (503, "temporarily"),
        (400, "HTTP 400"),
    ],
)
async def test_http_status_mapping(fake, status, words):
    state, session = fake
    state["status"] = status
    with pytest.raises(MealieError, match=words) as info:
        await n.ai_suggest(session, ANT_KEY, ["oats"])
    assert info.value.status == status
    assert ANT_KEY not in str(info.value)


async def test_answer_without_text_block_is_an_error(fake):
    state, session = fake
    state["reply"] = {"stop_reason": "end_turn", "content": [{"type": "thinking"}]}
    with pytest.raises(MealieError):
        await n.ai_suggest(session, ANT_KEY, ["oats"])


async def test_german_instructions_address_the_reader_with_du(fake):
    state, session = fake
    state["answer"] = "substitute_options"
    await studio_ai.substitutes(session, ANT_KEY, "Butter", "Kuchen")
    state["answer"] = "recipe_audit"
    await studio_ai.audit_recipe(session, ANT_KEY, RECIPE)
    for request in state["seen"]:
        assert "informally with du" in request["body"]["system"]


@pytest.mark.parametrize(
    ("key", "requested", "expected"),
    [
        (ANT_KEY, 25, ai_client.ANTHROPIC_MIN_TIMEOUT),
        (ANT_KEY, 120, 120),
        ("sk-openai-test", 25, 25),
    ],
)
async def test_anthropic_gets_a_longer_total_timeout(key, requested, expected):
    seen = {}

    def post(url, **kwargs):
        seen["total"] = kwargs["timeout"].total
        raise TimeoutError

    session = type("S", (), {"post": staticmethod(post)})()
    with pytest.raises(ai_client.AiError):
        await ai_client.request_json(
            session,
            key,
            instructions="i",
            input_text="t",
            schema={},
            schema_name="s",
            timeout=requested,
        )
    assert seen["total"] == expected
    assert ai_client.ANTHROPIC_MIN_TIMEOUT >= 60


async def test_ai_helpers_raise_ai_error_on_bad_shape(fake):
    state, session = fake
    state["reply"] = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps({"unexpected": 1})}],
    }
    with pytest.raises(ai_client.AiError):
        await n.ai_suggest(session, ANT_KEY, ["oats"])
    with pytest.raises(ai_client.AiError):
        await studio_ai.substitutes(session, ANT_KEY, "Butter", "Kuchen")
    with pytest.raises(ai_client.AiError):
        await studio_ai.audit_recipe(session, ANT_KEY, RECIPE)


@pytest.mark.parametrize(
    ("language", "name", "word", "disclaimer"),
    [
        ("en", "English", "Non-binding", "AI review that may contain mistakes"),
        ("de", "German", "Unverbindliche", "KI-Prüfung"),
        ("de-DE", "German", "Unverbindliche", "KI-Prüfung"),
        ("fr", "English", "Non-binding", "AI review"),
    ],
)
async def test_ai_answers_follow_the_requested_language(language, name, word, disclaimer):
    from unittest.mock import AsyncMock, patch

    answers = {
        "substitute_options": {"alternatives": [], "warning": "w"},
        "recipe_audit": {"issues": [], "summary": "s"},
    }

    async def fake_request(*args, **kwargs):
        return answers[kwargs["schema_name"]]

    mock = AsyncMock(side_effect=fake_request)
    with patch.object(studio_ai, "request_json", mock):
        subs = await studio_ai.substitutes(None, "k", "Butter", "Kuchen", language)
        audit = await studio_ai.audit_recipe(None, "k", RECIPE, language)
    for call in mock.call_args_list:
        assert f"in {name}" in call.kwargs["instructions"]
    assert subs["disclaimer"].startswith(word)
    assert disclaimer in audit["disclaimer"]


async def test_studio_ai_without_key_raises_the_coded_error():
    import pytest

    from custom_components.mealie_atelier.errors import AtelierError

    for call in (
        studio_ai.substitutes(None, "", "Butter", "Kuchen"),
        studio_ai.audit_recipe(None, "", RECIPE),
    ):
        with pytest.raises(AtelierError) as caught:
            await call
        assert caught.value.code == "ai_not_configured"
