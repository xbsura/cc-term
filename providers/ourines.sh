#!/usr/bin/env bash
# ============================================================
# Provider: ourines
# Matches: ai.ourines.com
# ============================================================
PROVIDER_NAME="ourines"
ANTHROPIC_BASE_URL="https://ai.ourines.com"
MANAGEMENT_URL="https://ai.ourines.com"

ACTION="$1"
shift

URL=""
TOKEN=""
APP_ID=""
NAME=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)    URL="$2"; shift 2 ;;
        --token)  TOKEN="$2"; shift 2 ;;
        --name)   NAME="$2"; shift 2 ;;
        --app-id) APP_ID="$2"; shift 2 ;;
        *)        shift ;;
    esac
done
URL="${URL:-$ANTHROPIC_BASE_URL}"

_ourines_call() {
    local endpoint="$1"
    local payload="$2"
    curl -sS --connect-timeout 8 --max-time 15 \
        "$MANAGEMENT_URL/apiStats/api/$endpoint" \
        -H 'Content-Type: application/json' \
        -H "Origin: $MANAGEMENT_URL" \
        -H "Referer: $MANAGEMENT_URL/admin-next/api-stats" \
        --data-raw "$payload" 2>/dev/null
}

_get_api_id() {
    if [[ -n "$APP_ID" ]]; then
        echo "$APP_ID"
        return
    fi
    [[ -z "$TOKEN" ]] && return
    local result
    result=$(_ourines_call "get-key-id" "{\"apiKey\":\"$TOKEN\"}")
    [[ -z "$result" ]] && return
    echo "$result" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    data = d.get('data', d) if isinstance(d, dict) else d
    if isinstance(data, dict):
        print(data.get('apiId') or data.get('id') or '')
    else:
        print('')
except:
    print('')
" 2>/dev/null
}

case "$ACTION" in
    name)
        echo "$PROVIDER_NAME"
        ;;
    quota)
        API_ID=$(_get_api_id)
        if [[ -z "$API_ID" ]]; then
            echo '{"remaining":"unknown","today_tokens":"unknown","total_cost":"unknown","model_usage":"cannot resolve apiId"}'
            exit 0
        fi

        USER_STATS=$(_ourines_call "user-stats" "{\"apiId\":\"$API_ID\"}")
        MODEL_STATS=$(_ourines_call "user-model-stats" "{\"apiId\":\"$API_ID\",\"period\":\"daily\"}")

        if [[ -z "$USER_STATS" && -z "$MODEL_STATS" ]]; then
            echo '{"remaining":"unknown","today_tokens":"unknown","total_cost":"unknown","model_usage":"unknown"}'
            exit 0
        fi

        USER_STATS="$USER_STATS" MODEL_STATS="$MODEL_STATS" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone, timedelta


def try_load(name):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


user_resp = try_load('USER_STATS') or {}
model_resp = try_load('MODEL_STATS') or {}

data = user_resp.get('data', {}) if isinstance(user_resp.get('data'), dict) else {}
limits = data.get('limits', {})
usage_total = (data.get('usage', {}) or {}).get('total', {})

# --- limits ---
weekly_opus_limit = limits.get('weeklyOpusCostLimit', 0)
weekly_opus_used = limits.get('weeklyOpusCost', 0)
total_cost_limit = limits.get('totalCostLimit', 0)
current_total_cost = limits.get('currentTotalCost', 0)
daily_cost_limit = limits.get('dailyCostLimit', 0)
current_daily_cost = limits.get('currentDailyCost', 0)
concurrency = limits.get('concurrencyLimit', 0)

# weekly reset countdown
now = datetime.now()
days_until_monday = (7 - now.weekday()) % 7
if days_until_monday == 0:
    days_until_monday = 7
reset_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_until_monday)
delta = reset_dt - now
d = delta.days
h = delta.seconds // 3600
reset_str = f"{d}d {h}h" if d > 0 else f"{h}h"

today_str = f"${current_daily_cost:.2f}" if current_daily_cost else '$0.00'

total_cost_val = usage_total.get('cost', current_total_cost)
total_cost_str = usage_total.get('formattedCost', f"${total_cost_val:.2f}") if total_cost_val else '$0.00'

# --- key info ---
permissions_raw = data.get('permissions', '[]')
try:
    permissions = json.loads(permissions_raw) if isinstance(permissions_raw, str) else permissions_raw
except Exception:
    permissions = []
perm_str = ', '.join(permissions) if isinstance(permissions, list) else str(permissions)

expires_at = data.get('expiresAt', '')
expire_str = ''
if expires_at:
    try:
        exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        exp_local = exp_dt.astimezone()
        expire_str = exp_local.strftime('%Y/%m/%d %H:%M')
        remain = exp_dt - datetime.now(timezone.utc)
        if remain.total_seconds() > 0:
            rd = remain.days
            rh = remain.seconds // 3600
            expire_str += f" ({rd}d {rh}h left)"
        else:
            expire_str += " (EXPIRED)"
    except Exception:
        expire_str = expires_at

key_name = data.get('name', '')

# --- quota limits table ---
C_RESET = '\033[0m'
C_BOLD  = '\033[1m'
C_DIM   = '\033[2m'
C_CYAN  = '\033[36m'
C_GREEN = '\033[32m'
C_YELLOW = '\033[33m'
C_WHITE = '\033[37m'
C_BLUE  = '\033[34m'
C_MAG   = '\033[35m'
C_RED   = '\033[31m'

quota_lines = []
quota_lines.append(f"       {C_BOLD}Quota Limits{C_RESET}")
quota_lines.append(
    f"       {C_DIM}{'Category':<20} {'Limit':>12} {'Used':>12} {'Reset':>10}{C_RESET}"
)
quota_lines.append(f"       {C_DIM}{'─'*20} {'─'*12} {'─'*12} {'─'*10}{C_RESET}")

if concurrency and concurrency > 0:
    quota_lines.append(
        f"       {C_CYAN}{'Concurrency':<20}{C_RESET} {C_WHITE}{str(concurrency):>12}{C_RESET} {C_DIM}{'-':>12}{C_RESET} {C_DIM}{'-':>10}{C_RESET}"
    )

if weekly_opus_limit and weekly_opus_limit > 0:
    pct = weekly_opus_used / weekly_opus_limit if weekly_opus_limit else 0
    used_color = C_RED if pct > 0.8 else (C_YELLOW if pct > 0.5 else C_GREEN)
    quota_lines.append(
        f"       {C_YELLOW}{'Weekly Opus':<20}{C_RESET} {C_WHITE}{'${:.0f}'.format(weekly_opus_limit):>12}{C_RESET} {used_color}{'${:.2f}'.format(weekly_opus_used):>12}{C_RESET} {C_CYAN}{reset_str:>10}{C_RESET}"
    )

if daily_cost_limit and daily_cost_limit > 0:
    pct = current_daily_cost / daily_cost_limit if daily_cost_limit else 0
    used_color = C_RED if pct > 0.8 else (C_YELLOW if pct > 0.5 else C_GREEN)
    quota_lines.append(
        f"       {C_YELLOW}{'Daily Cost':<20}{C_RESET} {C_WHITE}{'${:.0f}'.format(daily_cost_limit):>12}{C_RESET} {used_color}{'${:.2f}'.format(current_daily_cost):>12}{C_RESET} {C_DIM}{'-':>10}{C_RESET}"
    )
else:
    quota_lines.append(
        f"       {C_GREEN}{'Daily Cost':<20}{C_RESET} {C_DIM}{'unlimited':>12}{C_RESET} {C_WHITE}{'${:.2f}'.format(current_daily_cost):>12}{C_RESET} {C_DIM}{'-':>10}{C_RESET}"
    )

if total_cost_limit and total_cost_limit > 0:
    pct = current_total_cost / total_cost_limit if total_cost_limit else 0
    used_color = C_RED if pct > 0.8 else (C_YELLOW if pct > 0.5 else C_GREEN)
    quota_lines.append(
        f"       {C_YELLOW}{'Total Cost':<20}{C_RESET} {C_WHITE}{'${:.0f}'.format(total_cost_limit):>12}{C_RESET} {used_color}{total_cost_str:>12}{C_RESET} {C_DIM}{'-':>10}{C_RESET}"
    )
else:
    quota_lines.append(
        f"       {C_GREEN}{'Total Cost':<20}{C_RESET} {C_DIM}{'unlimited':>12}{C_RESET} {C_WHITE}{total_cost_str:>12}{C_RESET} {C_DIM}{'-':>10}{C_RESET}"
    )

token_limit = limits.get('tokenLimit', 0)
if token_limit and token_limit > 0:
    quota_lines.append(
        f"       {C_YELLOW}{'Token Limit':<20}{C_RESET} {C_WHITE}{str(token_limit):>12}{C_RESET} {C_DIM}{'-':>12}{C_RESET} {C_DIM}{'-':>10}{C_RESET}"
    )

quota_table = '\n'.join(quota_lines)

# --- model stats table ---
model_data = model_resp.get('data', []) if isinstance(model_resp.get('data'), list) else []
model_lines = []
if model_data:
    day_total_cost = sum(
        (item.get('costs', {}) or {}).get('total', 0)
        for item in model_data if isinstance(item, dict)
    )
    model_lines.append(f"       {C_BOLD}Today Model Usage{C_RESET}")
    model_lines.append(
        f"       {C_DIM}{'Model':<32} {'Cost':>10} {'Reqs':>6} {'Input':>12} {'Output':>10} {'Cache R':>12}{C_RESET}"
    )
    model_lines.append(f"       {C_DIM}{'─'*32} {'─'*10} {'─'*6} {'─'*12} {'─'*10} {'─'*12}{C_RESET}")
    model_colors = [C_CYAN, C_GREEN, C_YELLOW, C_BLUE, C_MAG, C_WHITE]
    for i, item in enumerate(model_data):
        if not isinstance(item, dict):
            continue
        model = item.get('model', '')
        if not model:
            continue
        formatted = item.get('formatted', {}) or {}
        reqs = item.get('requests', 0)
        inp = item.get('inputTokens', 0)
        out = item.get('outputTokens', 0)
        cache_r = item.get('cacheReadTokens', 0)
        cost_str = formatted.get('total', '$0.00')
        mc = model_colors[i % len(model_colors)]

        def fmt_num(n):
            if n >= 1_000_000:
                return f"{n/1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n/1_000:.1f}K"
            return str(n)

        model_lines.append(
            f"       {mc}{model:<32}{C_RESET} {C_WHITE}{cost_str:>10}{C_RESET} {C_DIM}{reqs:>6}{C_RESET} {C_DIM}{fmt_num(inp):>12}{C_RESET} {C_DIM}{fmt_num(out):>10}{C_RESET} {C_DIM}{fmt_num(cache_r):>12}{C_RESET}"
        )
    model_lines.append(f"       {C_DIM}{'─'*32} {'─'*10} {'─'*6}{C_RESET}")
    total_reqs = sum(item.get('requests', 0) for item in model_data if isinstance(item, dict))
    model_lines.append(
        f"       {C_BOLD}{'Total':<32}{C_RESET} {C_BOLD}{'${:.2f}'.format(day_total_cost):>10}{C_RESET} {C_DIM}{total_reqs:>6}{C_RESET}"
    )

model_table = '\n'.join(model_lines)

print(json.dumps({
    'quota_table': quota_table,
    'model_usage': model_table,
    'key_name': key_name,
    'permissions': perm_str,
    'expires_at': expire_str,
}, ensure_ascii=False))
PY
        ;;
    *)
        echo "unsupported"
        ;;
esac
