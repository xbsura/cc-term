#!/usr/bin/env bash
# ============================================================
# Provider: GPTClubAPI (api.gptclubapi.xyz)
# ============================================================
PROVIDER_NAME="GPTClubAPI"
ANTHROPIC_BASE_URL="https://api.gptclubapi.xyz/api"

ACTION="$1"
shift

URL=""
TOKEN=""
APP_ID=""
NAME=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)
            URL="$2"
            shift 2
            ;;
        --token)
            TOKEN="$2"
            shift 2
            ;;
        --name)
            NAME="$2"
            shift 2
            ;;
        --app-id)
            APP_ID="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done
URL="${URL:-$ANTHROPIC_BASE_URL}"

_gptclub_stats_call() {
    local endpoint="$1"
    local payload="$2"
    curl -sS --connect-timeout 8 --max-time 15 \
        'https://gptai.work/api/proxy/'"$endpoint" \
        -H 'Accept: */*' \
        -H 'Accept-Language: en,zh-CN;q=0.9,zh;q=0.8' \
        -H 'Cache-Control: no-cache' \
        -H 'Connection: keep-alive' \
        -H 'Content-Type: application/json' \
        -H 'Origin: https://gptai.work' \
        -H 'Pragma: no-cache' \
        -H 'Referer: https://gptai.work/dashboard' \
        -H 'Sec-Fetch-Dest: empty' \
        -H 'Sec-Fetch-Mode: cors' \
        -H 'Sec-Fetch-Site: same-origin' \
        -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36' \
        -H 'sec-ch-ua: "Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"' \
        -H 'sec-ch-ua-mobile: ?0' \
        -H 'sec-ch-ua-platform: "macOS"' \
        --data-raw "$payload" 2>/dev/null
}

case "$ACTION" in
    name)
        echo "$PROVIDER_NAME"
        ;;
    quota)
        if [[ -z "$APP_ID" ]]; then
            echo '{"remaining":"unknown","today_tokens":"unknown","total_cost":"unknown","model_usage":"missing appId"}'
            exit 0
        fi

        USER_STATS=$(_gptclub_stats_call "user-stats" "{\"apiId\":\"$APP_ID\"}")
        MODEL_STATS=$(_gptclub_stats_call "user-model-stats" "{\"apiId\":\"$APP_ID\",\"period\":\"daily\"}")

        if [[ -z "$USER_STATS" && -z "$MODEL_STATS" ]]; then
            echo '{"remaining":"unknown","today_tokens":"unknown","total_cost":"unknown","model_usage":"unknown"}'
            exit 0
        fi

        USER_STATS="$USER_STATS" MODEL_STATS="$MODEL_STATS" python3 - <<'PY'
import json
import os


def try_load(name):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def flatten_values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from flatten_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from flatten_values(item)
    else:
        yield node


def first_value(node, keys):
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in keys and value not in (None, ''):
                return value
            found = first_value(value, keys)
            if found not in (None, ''):
                return found
    elif isinstance(node, list):
        for item in node:
            found = first_value(item, keys)
            if found not in (None, ''):
                return found
    return None


def iter_model_rows(node):
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict):
                model = first_value(item, {'model', 'modelname', 'model_name', 'name'})
                usage = first_value(item, {'count', 'calls', 'requests', 'usage', 'times', 'total'})
                tokens = first_value(item, {'tokens', 'token', 'total_tokens', 'output_tokens', 'input_tokens'})
                if model is not None:
                    yield {
                        'model': str(model),
                        'usage': usage if usage not in (None, '') else tokens,
                    }
                yield from iter_model_rows(item)
    elif isinstance(node, dict):
        for value in node.values():
            yield from iter_model_rows(value)


user_stats = try_load('USER_STATS') or {}
model_stats = try_load('MODEL_STATS') or {}

remaining = first_value(user_stats, {'remaining', 'balance', 'quota', 'available', 'rest'})
today = first_value(user_stats, {'today_tokens', 'todayusage', 'today_usage', 'dailyusage', 'usedtoday'})
total_cost = first_value(user_stats, {'total_cost', 'cost', 'used', 'total_used', 'usage_cost'})

rows = []
seen = set()
for row in iter_model_rows(model_stats):
    model = row.get('model', '').strip()
    usage = row.get('usage')
    if not model or model in seen:
        continue
    seen.add(model)
    if usage not in (None, ''):
        rows.append(f"{model}:{usage}")
    else:
        rows.append(model)

if today in (None, '') and rows:
    today = ' / '.join(rows[:3])
if total_cost in (None, ''):
    total_cost = first_value(user_stats, {'total', 'sum', 'usage'})

print(json.dumps({
    'remaining': str(remaining if remaining not in (None, '') else 'unknown'),
    'today_tokens': str(today if today not in (None, '') else 'unknown'),
    'total_cost': str(total_cost if total_cost not in (None, '') else 'unknown'),
    'model_usage': ' / '.join(rows[:6]) if rows else 'unknown',
}, ensure_ascii=False))
PY
        ;;
    *)
        echo "unsupported"
        ;;
esac
