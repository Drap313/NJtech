#!/bin/sh
# Executes a dispatcher-approved recovery plan. OpenClaw exec approvals gate this
# script: it is NOT allowlisted, so Slack shows Approve/Deny buttons to the
# configured approvers before it can run. Dispatch Guardian then re-validates.
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
[ -f "$DIR/dg.env" ] && . "$DIR/dg.env"
API="${DG_API:-http://host.openshell.internal:8090}"
plan="${1:?plan id required}"; user="${2:?Slack user id of the approving human required}"
case "$user" in *[!A-Za-z0-9]*) echo "bad user id" >&2; exit 2;; esac
curl -sS -m 60 -H "Authorization: Bearer ${DG_API_TOKEN:-}" -H "Content-Type: application/json" -X POST \
  -d "{\"actor\": \"slack:$user\"}" "$API/api/chat/plans/$plan/approve"
echo
