"""Constants for Mealie Atelier (an optional companion to HA's native Mealie integration)."""

from __future__ import annotations

DOMAIN = "mealie_atelier"
CONF_API_TOKEN = "api_token"
CONF_VERIFY_SSL = "verify_ssl"
CONF_READ_ONLY = "read_only"
CONF_WRITE_USERS = "write_users"
CONF_EXPERIMENTAL_INGREDIENTS = "experimental_ingredient_edits"
CONF_WRITE_USERS = "write_users"
DEFAULT_TIMEOUT = 20
MAX_PAGE_SIZE = 60
MAX_BODY_LENGTH = 10000

# Recipe photos and the backups taken before every recipe write.
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 256 * 1024
UPLOAD_TTL_SECONDS = 600
UPLOAD_MAX_SESSIONS = 2
BACKUPS_PER_RECIPE = 10
BACKUP_MAX_RECIPES = 200
IMAGE_BACKUPS_PER_RECIPE = 3

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
