#!/usr/bin/env bash
# Disposable live test rig for Mealie Atelier: fictional data only, never a real household.
#   ./rig.sh up      start Mealie and Home Assistant, onboard both, seed demo recipes,
#                    add the integration
#   ./rig.sh status  show container state and versions
#   ./rig.sh down    stop the rig and delete its volumes and state
# Images: HA_IMAGE (default home-assistant:beta), MEALIE_IMAGE (default mealie:latest).
# Language of seed data and HA user: SEED_LANGUAGE (de or en).
# Ports: HA_PORT (18123), MEALIE_PORT (19925). Cards bundle: CARDS_DIST (../../../mealie-atelier-cards/dist).
set -euo pipefail
umask 077 # tokens and generated config stay private from the first write
cd "$(dirname "$0")"
STATE=state
MEALIE="http://localhost:${MEALIE_PORT:-19925}"
HA="http://localhost:${HA_PORT:-18123}"
CATALOG="${CATALOG:-../../../mealie-atelier-cards/demo/data/recipes.json}"
LANGUAGE="${SEED_LANGUAGE:-de}"
# Pin file and project so exported COMPOSE_FILE or COMPOSE_PROJECT_NAME never redirect the rig.
compose() { docker compose -f compose.yaml -p atelier-live "$@"; }

wait_for() {
  local url=$1 tries=${2:-60}
  for _ in $(seq "$tries"); do
    curl -fsS -o /dev/null "$url" 2>/dev/null && return 0
    sleep 3
  done
  echo "timeout waiting for $url" >&2
  return 1
}

json_field() { python3 -I -c "import json,sys; print(json.load(sys.stdin)[sys.argv[1]])" "$1"; }

up() {
  mkdir -p "$STATE/ha-config"
  compose up -d
  wait_for "$MEALIE/api/app/about"
  if [ ! -s "$STATE/mealie-token" ]; then
    # Mealie's documented first-run admin; the instance is disposable.
    local access
    access=$(curl -fsS -X POST "$MEALIE/api/auth/token" \
      -d 'username=changeme@example.com&password=MyPassword' | json_field access_token)
    curl -fsS -X POST "$MEALIE/api/users/api-tokens" -H "Authorization: Bearer $access" \
      -H 'content-type: application/json' -d '{"name":"atelier-live"}' |
      json_field token >"$STATE/mealie-token"
  fi
  if [ ! -e "$STATE/seeded" ]; then
    python3 -I seed_mealie.py "$MEALIE" "$STATE/mealie-token" "$CATALOG" "$LANGUAGE"
    touch "$STATE/seeded"
  fi
  wait_for "$HA/manifest.json" 100
  if [ ! -s "$STATE/ha-token" ]; then
    # The onboarding API exists only until onboarding is done.
    wait_for "$HA/api/onboarding" 100
    python3 -I ha_onboard.py "$HA" "$STATE/ha-token" "$LANGUAGE"
  fi
  python3 -I ha_setup_entry.py "$HA" "$STATE/ha-token" "$STATE/mealie-token"
  status
}

status() {
  compose ps --format '{{.Service}} {{.Image}} {{.Status}}'
  curl -fsS "$MEALIE/api/app/about" | json_field version | sed 's/^/mealie /' || true
  [ -s "$STATE/ha-token" ] && curl -fsS -H "Authorization: Bearer $(cat "$STATE/ha-token")" \
    "$HA/api/config" | json_field version | sed 's/^/home-assistant /' || true
}

down() {
  compose down -v
  # The state folder holds only what this rig generated (HA config, tokens).
  if command -v trash >/dev/null; then trash "$STATE" 2>/dev/null || true; else rm -rf "$STATE"; fi
}

case "${1:-}" in
  up) up ;;
  status) status ;;
  down) down ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
