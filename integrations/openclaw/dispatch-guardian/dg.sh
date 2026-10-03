#!/bin/sh
# Dispatch Guardian helper for the OpenClaw sandbox. Reaches the GB10 host API
# through the OpenShell policy preset "dispatch-guardian-api".
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
[ -f "$DIR/dg.env" ] && . "$DIR/dg.env"
API="${DG_API:-http://host.openshell.internal:8090}"
AUTH="Authorization: Bearer ${DG_API_TOKEN:-}"

json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n' ' '; }

case "${1:-brief}" in
  brief)    curl -sS -m 20 -H "$AUTH" "$API/api/chat/brief" ;;
  incident) curl -sS -m 20 -H "$AUTH" "$API/api/chat/incidents/${2:?incident id required}" ;;
  feed)     curl -sS -m 20 -H "$AUTH" "$API/api/chat/feed" ;;
  ask)
    q="${2:?question required}"
    curl -sS -m 180 -H "$AUTH" -H "Content-Type: application/json" -X POST \
      -d "{\"message\": \"$(json_escape "$q")\"}" "$API/api/agent/chat" \
      | sed -e 's/^{"answer":"//' -e 's/","steps".*$//' -e 's/\\n/\n/g' -e 's/\\"/"/g' ;;
  simulate)
    curl -sS -m 30 -H "$AUTH" -X POST "$API/api/scenarios/${2:-primary}/inject" >/dev/null && echo "Injected scenario ${2:-primary}." ;;
  *) echo "usage: dg.sh brief|incident ID|feed|ask \"question\"|simulate NAME" >&2; exit 2 ;;
esac
echo
