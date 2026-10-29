"""Constants for Mealie Atelier (an optional companion to HA's native Mealie integration)."""

from __future__ import annotations

DOMAIN = "mealie_atelier"
# Kept equal to manifest.json and pyproject.toml (tests/test_setup_contract.py).
VERSION = "0.10.0"
CONF_API_TOKEN = "api_token"
CONF_VERIFY_SSL = "verify_ssl"
CONF_READ_ONLY = "read_only"
CONF_WRITE_USERS = "write_users"
CONF_EXPERIMENTAL_INGREDIENTS = "experimental_ingredient_edits"
# Sign-up pages linked from the options form (hassfest forbids URLs inside translations).
OPENAI_KEYS_URL = "https://platform.openai.com/api-keys"
USDA_SIGNUP_URL = "https://fdc.nal.usda.gov/api-key-signup"
DEFAULT_TIMEOUT = 20
MAX_PAGE_SIZE = 60
# Requests one entry sends to Mealie at the same time; the rest wait their turn.
MAX_PARALLEL_REQUESTS = 6
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

# Smart Shopping Sync (docs/SMART_SHOPPING_SYNC.md).
SYNC_STORE_VERSION = 1
SYNC_STORE_MINOR_VERSION = 1
SYNC_MAX_RULES = 5
SYNC_BATCH_SIZE = 50
SYNC_SERVICE_TIMEOUT = 20
SYNC_MAX_LINE_FAILURES = 3
SYNC_STARTUP_DELAY = 60
SYNC_STOP_TIMEOUT = 10
SYNC_AUDIT_LIMIT = 200
SYNC_RECORD_LIMIT = 2000
SYNC_PRUNE_DAYS = 30
EVENT_SYNC_FINISHED = "mealie_atelier_sync_finished"

# Offline shopping edits: durable idempotency ledger (docs: contract section H.2).
OFFLINE_STORE_VERSION = 1
OFFLINE_RECORD_LIMIT = 2000
OFFLINE_PRUNE_DAYS = 30
