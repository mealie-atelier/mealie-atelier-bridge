"""Constants for Mealie Atelier (an optional companion to HA's native Mealie integration)."""

from __future__ import annotations

DOMAIN = "mealie_atelier"
CONF_API_TOKEN = "api_token"
CONF_VERIFY_SSL = "verify_ssl"
CONF_READ_ONLY = "read_only"
CONF_EXPERIMENTAL_INGREDIENTS = "experimental_ingredient_edits"
CONF_WRITE_USERS = "write_users"
DEFAULT_TIMEOUT = 20
MAX_PAGE_SIZE = 60
MAX_BODY_LENGTH = 10000

# Only explicitly supported operations may write to Mealie.
ADMIN_ACTIONS = frozenset({"recipe_create", "recipe_update", "recipe_import"})
# Everyday kitchen writes that per-user grants can open or close.
WRITE_ACTIONS = frozenset(
    {
        "shopping_toggle",
        "shopping_list_create",
        "shopping_from_recipe",
        "shopping_add",
        "mealplan_add",
        "mealplan_delete",
    }
)

CONF_FDC_KEY = "usda_fdc_api_key"
CONF_AI_KEY = "openai_api_key"

CONF_TIMER_NOTIFY_SERVICE = "timer_notify_service"
