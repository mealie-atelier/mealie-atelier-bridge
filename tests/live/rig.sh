#!/usr/bin/env bash
# Disposable live test rig for Mealie Atelier: fictional data only, never a real household.
#   ./rig.sh up      start Mealie and Home Assistant, onboard both, seed demo recipes,
#                    add the integration
#   ./rig.sh status  show container state and versions
#   ./rig.sh down    stop the rig and delete its volumes and state
# Images: HA_IMAGE (default home-assistant:beta), MEALIE_IMAGE (default mealie:latest).
# Ports: HA_PORT (18123), MEALIE_PORT (19925). Cards bundle: CARDS_DIST (../../../mealie-atelier-cards/dist).
set -euo pipefail
cd "$(dirname "$0")"
STATE=state
MEALIE="http://localhost:${MEALIE_PORT:-19925}"
HA="http://localhost:${HA_PORT:-18123}"
CATALOG="${CATALOG:-../../../mealie-atelier-cards/demo/data/recipes.json}"
LANGUAGE="${SEED_LANGUAGE:-de}"

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
  docker compose up -d
  wait_for "$MEALIE/api/app/about"
  if [ ! -s "$STATE/mealie-token" ]; then
    # Mealie's documented first-run admin; the instance is disposable.
    local access
    access=$(curl -fsS -X POST "$MEALIE/api/auth/token" \
      -d 'username=changeme@example.com&password=MyPassword' | json_field access_token)
    curl -fsS -X POST "$MEALIE/api/users/api-tokens" -H "Authorization: Bearer $access" \
      -H 'content-type: application/json' -d '{"name":"atelier-live"}' |
      json_field token >"$STATE/mealie-token"
    python3 -I seed_mealie.py "$MEALIE" "$STATE/mealie-token" "$CATALOG" "$LANGUAGE"
  fi
  wait_for "$HA/api/onboarding" 100
  [ -s "$STATE/ha-token" ] || python3 -I ha_onboard.py "$HA" "$STATE/ha-token"
  python3 -I ha_setup_entry.py "$HA" "$STATE/ha-token" "$STATE/mealie-token"
  chmod 600 "$STATE"/*-token
  status
}

status() {
  docker compose ps --format '{{.Service}} {{.Image}} {{.Status}}'
  curl -fsS "$MEALIE/api/app/about" | json_field version | sed 's/^/mealie /' || true
  [ -s "$STATE/ha-token" ] && curl -fsS -H "Authorization: Bearer $(cat "$STATE/ha-token")" \
    "$HA/api/config" | json_field version | sed 's/^/home-assistant /' || true
}

down() {
  docker compose down -v
  # The state folder holds only what this rig generated (HA config, tokens).
  if command -v trash >/dev/null; then trash "$STATE" 2>/dev/null || true; else rm -rf "$STATE"; fi
}

case "${1:-}" in
  up) up ;;
  status) status ;;
  down) down ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
