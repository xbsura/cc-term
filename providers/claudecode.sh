#!/usr/bin/env bash
# ============================================================
# Provider: ClaudeCode (claudecode.dpdns.org)
# ============================================================
PROVIDER_NAME="ClaudeCode"
ANTHROPIC_BASE_URL="https://claudecode.dpdns.org/api"

ACTION="$1"; shift
URL=""; TOKEN=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)   URL="$2"; shift 2 ;;
        --token) TOKEN="$2"; shift 2 ;;
        *) shift ;;
    esac
done
URL="${URL:-$ANTHROPIC_BASE_URL}"

case "$ACTION" in
    name)   echo "$PROVIDER_NAME" ;;
    quota)
        result=$(curl -s --connect-timeout 5 --max-time 10 \
            -H "Authorization: Bearer $TOKEN" \
            "${URL%/api}/api/user/info" 2>/dev/null)
        if [[ -n "$result" ]] && echo "$result" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
            echo "$result" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    data = d.get('data', d)
    remaining = data.get('balance', data.get('remaining', data.get('quota', 'unknown')))
    today = data.get('today_tokens', data.get('today_usage', 'unknown'))
    total = data.get('total_cost', data.get('used', 'unknown'))
    print(json.dumps({'remaining': str(remaining), 'today_tokens': str(today), 'total_cost': str(total)}))
except:
    print(json.dumps({'remaining': 'unknown', 'today_tokens': 'unknown', 'total_cost': 'unknown'}))
"
        else
            echo '{"remaining":"unknown","today_tokens":"unknown","total_cost":"unknown"}'
        fi
        ;;
    *)  echo "unsupported" ;;
esac
