#!/usr/bin/env python3
"""
cc-term provider manager
Manage Claude Code API proxy providers — health, quota, install, edit, default.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

CC_HOME = os.path.expanduser(os.environ.get("CC_HOME", "~/.cc-term"))
PROVIDERS_FILE = os.path.join(CC_HOME, "providers.json")
PROVIDER_ENV_FILE = os.path.join(CC_HOME, "provider_env.sh")
PROVIDERS_SCRIPTS_DIR = os.path.join(CC_HOME, "providers")

C = "\033[0;36m"
G = "\033[0;32m"
Y = "\033[1;33m"
R = "\033[0;31m"
B = "\033[1m"
D = "\033[2m"
NC = "\033[0m"


def info(msg):
    print(f"{C}[cc-term]{NC} {msg}")


def ok(msg):
    print(f"{G}[cc-term]{NC} {msg}")


def warn(msg):
    print(f"{Y}[cc-term]{NC} {msg}")


def err(msg):
    print(f"{R}[cc-term]{NC} {msg}", file=sys.stderr)


# ================================================================
# DATA
# ================================================================
def _default_data():
    return {"default": "", "providers": []}


def slugify(value):
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _generate_name(existing_names):
    counter = 1
    while True:
        candidate = f"provider-{counter}"
        if candidate not in existing_names:
            return candidate
        counter += 1


def _normalize_provider(raw_provider, index, existing_names):
    name = raw_provider.get("name") or raw_provider.get("label") or ""
    name = slugify(name) if name else ""
    if not name or name in existing_names:
        name = _generate_name(existing_names)

    provider = {
        "name": name,
        "api": (raw_provider.get("api") or raw_provider.get("url") or "").rstrip("/"),
        "key": raw_provider.get("key") or raw_provider.get("token") or "",
        "app_id": raw_provider.get("app_id") or raw_provider.get("appId") or "",
        "added_at": raw_provider.get("added_at") or datetime.now().isoformat(),
        "updated_at": raw_provider.get("updated_at") or raw_provider.get("added_at") or datetime.now().isoformat(),
    }
    existing_names.add(provider["name"])
    return provider


def _normalize_data(data):
    data = data or _default_data()
    providers = data.get("providers", [])
    normalized = []
    existing_names = set()
    for index, provider in enumerate(providers):
        normalized.append(_normalize_provider(provider, index, existing_names))

    default_value = data.get("default", "")
    if isinstance(default_value, int):
        if 0 <= default_value < len(normalized):
            default_value = normalized[default_value]["name"]
        else:
            default_value = ""
    elif default_value and default_value not in {item["name"] for item in normalized}:
        default_value = ""

    if not default_value and normalized:
        default_value = normalized[0]["name"]

    return {"default": default_value, "providers": normalized}


def load_providers():
    if os.path.exists(PROVIDERS_FILE):
        with open(PROVIDERS_FILE) as handle:
            return _normalize_data(json.load(handle))
    return _default_data()


def save_providers(data):
    normalized = _normalize_data(data)
    os.makedirs(os.path.dirname(PROVIDERS_FILE), exist_ok=True)
    with open(PROVIDERS_FILE, "w") as handle:
        json.dump(normalized, handle, indent=2, ensure_ascii=False)
    return normalized


def write_default_env(data):
    data = _normalize_data(data)
    provider = get_default_provider(data)
    os.makedirs(CC_HOME, exist_ok=True)
    with open(PROVIDER_ENV_FILE, "w") as handle:
        if provider:
            handle.write(f'export ANTHROPIC_BASE_URL="{provider["api"]}"\n')
            handle.write(f'export ANTHROPIC_AUTH_TOKEN="{provider["key"]}"\n')
            handle.write(f'export CC_PROVIDER_NAME="{provider["name"]}"\n')
            handle.write(f'export CC_PROVIDER_APP_ID="{provider.get("app_id", "")}"\n')
        else:
            handle.write('unset ANTHROPIC_BASE_URL\n')
            handle.write('unset ANTHROPIC_AUTH_TOKEN\n')
            handle.write('unset CC_PROVIDER_NAME\n')
            handle.write('unset CC_PROVIDER_APP_ID\n')


# ================================================================
# HELPERS
# ================================================================
def provider_display_name(provider):
    return provider.get("name") or "provider"


def get_default_provider(data):
    default_name = data.get("default", "")
    for provider in data.get("providers", []):
        if provider.get("name") == default_name:
            return provider
    return data.get("providers", [None])[0] if data.get("providers") else None


def get_default_index(data):
    default_name = data.get("default", "")
    for index, provider in enumerate(data.get("providers", [])):
        if provider.get("name") == default_name:
            return index
    return -1


def _resolve_index(data, identifier):
    providers = data.get("providers", [])
    if not providers:
        err("No providers configured.")
        return None

    if identifier in ("", None, "default"):
        default_index = get_default_index(data)
        return default_index if default_index >= 0 else 0

    try:
        index = int(identifier) - 1
        if 0 <= index < len(providers):
            return index
    except ValueError:
        pass

    for index, provider in enumerate(providers):
        if provider["name"] == identifier:
            return index

    for index, provider in enumerate(providers):
        if provider["key"].startswith(identifier):
            return index

    for index, provider in enumerate(providers):
        if identifier in provider["api"]:
            return index

    err(f"Provider not found: {identifier}")
    return None


def _parse_provider_fields(args):
    fields = {"name": "", "api": "", "key": "", "app_id": ""}
    index = 0
    while index < len(args):
        token = args[index]
        if token in ("-name", "--name"):
            index += 1
            fields["name"] = args[index] if index < len(args) else ""
        elif token in ("-api", "--api"):
            index += 1
            fields["api"] = args[index] if index < len(args) else ""
        elif token in ("-key", "--key"):
            index += 1
            fields["key"] = args[index] if index < len(args) else ""
        elif token in ("-appId", "--appId", "-app-id", "--app-id"):
            index += 1
            fields["app_id"] = args[index] if index < len(args) else ""
        else:
            err(f"Unknown provider option: {token}")
            return None
        index += 1
    return fields


def _next_provider_name(data):
    existing_names = {provider.get("name") for provider in data.get("providers", [])}
    return _generate_name(existing_names)


def _quota_display(quota):
    if not quota:
        return None
    return quota


# ================================================================
# PROVIDER SCRIPTS
# ================================================================
def _api_variants(value):
    value = (value or "").rstrip("/")
    variants = {value}
    if value.endswith("/api"):
        variants.add(value[:-4])
    else:
        variants.add(value + "/api")
    return {item.rstrip("/") for item in variants if item}


def find_provider_script(api_url):
    if not os.path.isdir(PROVIDERS_SCRIPTS_DIR):
        return None
    api_variants = _api_variants(api_url)
    for file_name in sorted(os.listdir(PROVIDERS_SCRIPTS_DIR)):
        if file_name.startswith("_") or not file_name.endswith(".sh"):
            continue
        path = os.path.join(PROVIDERS_SCRIPTS_DIR, file_name)
        try:
            with open(path) as handle:
                for line in handle:
                    line = line.strip()
                    if line.startswith("ANTHROPIC_BASE_URL="):
                        script_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if _api_variants(script_url) & api_variants:
                            return path
        except Exception:
            pass
    return None


def run_provider_script(script_path, action, provider):
    try:
        command = [
            "bash", script_path, action,
            "--url", provider.get("api", ""),
            "--token", provider.get("key", ""),
            "--name", provider.get("name", ""),
            "--app-id", provider.get("app_id", ""),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        return result.stdout.strip()
    except Exception:
        return ""


# ================================================================
# HEALTH CHECK
# ================================================================
def check_health(api_url, key):
    api_url = (api_url or "").rstrip("/")
    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1,
            "messages": [],
        }).encode()
        request = urllib.request.Request(
            f"{api_url}/v1/messages",
            method="POST",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            data=payload,
        )
        start = time.time()
        try:
            urllib.request.urlopen(request, timeout=10)
            latency = int((time.time() - start) * 1000)
            return "healthy", latency, ""
        except urllib.error.HTTPError as http_error:
            latency = int((time.time() - start) * 1000)
            if http_error.code in (400, 403, 422):
                return "healthy", latency, ""
            if http_error.code == 401:
                return "auth_error", latency, "invalid key"
            if http_error.code == 429:
                return "rate_limited", latency, "quota exhausted"
            if http_error.code == 402:
                return "no_quota", latency, "payment required"
            return "error", latency, f"HTTP {http_error.code}"
    except urllib.error.URLError as url_error:
        return "unreachable", 0, str(url_error.reason)
    except Exception as exc:
        return "error", 0, str(exc)


def check_quota_via_script(provider):
    script = find_provider_script(provider.get("api", ""))
    if not script:
        return None
    raw = run_provider_script(script, "quota", provider)
    if not raw or raw == "unsupported":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ================================================================
# LIST
# ================================================================
def list_providers(with_health=True):
    data = load_providers()
    providers = data.get("providers", [])
    default_name = data.get("default", "")

    if not providers:
        print()
        warn("No providers configured.")
        info("Add one: cc-term -provider -new -api <url> -key <key> [-name <name>] [-appId <appId>]")
        print()
        return

    print()
    print(f"  {B}Claude Code Providers{NC}")
    print()

    health_results = {}
    if with_health:
        info("Checking provider health...")
        print()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {}
            for index, provider in enumerate(providers):
                futures[pool.submit(check_health, provider["api"], provider["key"])] = index
            for future in as_completed(futures):
                health_results[futures[future]] = future.result()

    for index, provider in enumerate(providers):
        is_default = provider.get("name") == default_name
        marker = f"{G}*{NC}" if is_default else " "

        if index in health_results:
            status, latency, _detail = health_results[index]
            if status == "healthy":
                health_text = f"{G}● healthy{NC}  {D}{latency}ms{NC}"
            elif status == "auth_error":
                health_text = f"{Y}● auth error{NC}"
            elif status == "rate_limited":
                health_text = f"{Y}● rate limited{NC}"
            elif status == "no_quota":
                health_text = f"{R}● no quota{NC}"
            elif status == "unreachable":
                health_text = f"{R}○ unreachable{NC}"
            else:
                health_text = f"{R}○ {status}{NC}"
        else:
            health_text = f"{D}?{NC}"

        script = find_provider_script(provider["api"])
        support_tag = f" {C}[SUPPORTED]{NC}" if script else ""
        key_short = provider["key"][:10] + "..." if provider.get("key") else ""
        app_id = provider.get("app_id", "")

        print(f"  {marker} {B}{index + 1}{NC}  {health_text}  {B}{provider['name']}{NC}{support_tag}")
        print(f"       api: {provider['api']}  {D}key: {key_short}{NC}")
        if app_id:
            print(f"       {D}appId: {NC}{app_id}")

        if with_health and script:
            quota = check_quota_via_script(provider)
            if quota:
                key_name = quota.get("key_name", "")
                permissions = quota.get("permissions", "")
                expires_at = quota.get("expires_at", "")
                quota_table = quota.get("quota_table", "")
                model_usage = quota.get("model_usage", "")

                # key info line
                key_info_parts = []
                if key_name:
                    key_info_parts.append(f"{D}Key:{NC} {G}{key_name}{NC}")
                if permissions:
                    key_info_parts.append(f"{D}Models:{NC} {C}{permissions}{NC}")
                if expires_at:
                    expire_color = R if "EXPIRED" in expires_at else Y
                    key_info_parts.append(f"{D}Expires:{NC} {expire_color}{expires_at}{NC}")
                if key_info_parts:
                    print(f"       {'  '.join(key_info_parts)}")

                # quota limits table
                if quota_table:
                    print()
                    print(quota_table)

                # model usage table
                if model_usage and model_usage != "unknown":
                    print()
                    print(model_usage)
        print()

    if default_name:
        print(f"  {G}*{NC} = default provider")
    print(f"  {C}[SUPPORTED]{NC} = provider-specific quota monitoring available")
    print()


# ================================================================
# MUTATIONS
# ================================================================
def add_provider(name, api, key, app_id=""):
    data = load_providers()
    api = (api or "").rstrip("/")
    key = key or ""
    if not api or not key:
        err("Usage: -new -api <url> -key <key> [-name <name>] [-appId <appId>]")
        return 1

    if not name:
        name = _next_provider_name(data)
    name = slugify(name)
    if not name:
        name = _next_provider_name(data)

    if any(provider["name"] == name for provider in data["providers"]):
        err(f"Provider name already exists: {name}")
        return 1

    for provider in data["providers"]:
        if provider["api"] == api and provider["key"] == key:
            warn("Provider with this API + key already exists.")
            return 0

    provider = {
        "name": name,
        "api": api,
        "key": key,
        "app_id": app_id or "",
        "added_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    data["providers"].append(provider)
    if not data.get("default"):
        data["default"] = name

    data = save_providers(data)
    write_default_env(data)
    ok(f"Provider added: {name}")
    print_env_hint()
    return 0


def edit_provider(identifier, name="", api="", key="", app_id_marker=None):
    data = load_providers()
    index = _resolve_index(data, identifier)
    if index is None:
        return 1

    provider = data["providers"][index]
    previous_name = provider["name"]

    if name:
        normalized_name = slugify(name)
        if not normalized_name:
            err("Invalid provider name.")
            return 1
        for other_index, other in enumerate(data["providers"]):
            if other_index != index and other["name"] == normalized_name:
                err(f"Provider name already exists: {normalized_name}")
                return 1
        provider["name"] = normalized_name

    if api:
        provider["api"] = api.rstrip("/")
    if key:
        provider["key"] = key
    if app_id_marker is not None:
        provider["app_id"] = app_id_marker
    provider["updated_at"] = datetime.now().isoformat()

    if data.get("default") == previous_name:
        data["default"] = provider["name"]

    data = save_providers(data)
    write_default_env(data)
    ok(f"Provider updated: {provider['name']}")
    print_env_hint()
    return 0


def delete_provider(identifier):
    data = load_providers()
    index = _resolve_index(data, identifier)
    if index is None:
        return 1

    removed = data["providers"].pop(index)
    if data.get("default") == removed["name"]:
        data["default"] = data["providers"][0]["name"] if data["providers"] else ""

    data = save_providers(data)
    write_default_env(data)
    ok(f"Removed provider: {removed['name']}")
    return 0


def set_default(identifier):
    data = load_providers()
    index = _resolve_index(data, identifier)
    if index is None:
        return 1

    provider = data["providers"][index]
    data["default"] = provider["name"]
    data = save_providers(data)
    write_default_env(data)
    ok(f"Default provider set to: {provider['name']}")
    print(env_exports(provider))
    return 0


def env_exports(provider):
    return "\n".join([
        f'export ANTHROPIC_BASE_URL="{provider["api"]}"',
        f'export ANTHROPIC_AUTH_TOKEN="{provider["key"]}"',
        f'export CC_PROVIDER_NAME="{provider["name"]}"',
        f'export CC_PROVIDER_APP_ID="{provider.get("app_id", "")}"',
    ])


def print_env_hint():
    info(f"Apply default env now: source {PROVIDER_ENV_FILE}")
    info("Or with ccs: ccs <provider-name>")


def print_env(identifier="default"):
    data = load_providers()
    index = _resolve_index(data, identifier)
    if index is None:
        return 1
    print(env_exports(data["providers"][index]))
    return 0


def list_identifiers():
    data = load_providers()
    for index, provider in enumerate(data.get("providers", []), start=1):
        print(index)
        print(provider["name"])
        if provider.get("key"):
            print(provider["key"][:12])


def seed():
    info("Provider seed is disabled. ccs no longer ships hard-coded provider secrets.")
    data = load_providers()
    write_default_env(data)
    return 0


def print_startup_info(identifier="default"):
    """Print provider info + quota for ccs startup banner."""
    data = load_providers()
    index = _resolve_index(data, identifier)
    if index is None:
        return 1
    provider = data["providers"][index]

    key = provider.get("key", "")
    key_display = key[:10] + "..." + key[-4:] if len(key) > 14 else key

    script = find_provider_script(provider["api"])
    support_tag = f" {C}[SUPPORTED]{NC}" if script else ""

    print(f"{C}[cc-term]{NC} Provider: {B}{provider['name']}{NC}{support_tag}")
    print(f"{C}[cc-term]{NC} URL: {D}{provider['api']}{NC}")
    print(f"{C}[cc-term]{NC} Key: {D}{key_display}{NC}")

    quota = check_quota_via_script(provider)
    if quota:
        key_name = quota.get("key_name", "")
        permissions = quota.get("permissions", "")
        expires_at = quota.get("expires_at", "")
        quota_table = quota.get("quota_table", "")
        model_usage = quota.get("model_usage", "")

        # key info
        if key_name:
            print(f"{C}[cc-term]{NC} {Y}Key Name:{NC} {G}{key_name}{NC}")
        if permissions:
            print(f"{C}[cc-term]{NC} {Y}Models:{NC} {C}{permissions}{NC}")
        if expires_at:
            expire_color = R if "EXPIRED" in expires_at else Y
            print(f"{C}[cc-term]{NC} {Y}Expires:{NC} {expire_color}{expires_at}{NC}")

        # quota limits table
        if quota_table:
            print()
            print(quota_table)

        # model usage table
        if model_usage and model_usage != "unknown":
            print()
            print(model_usage)
    return 0


# ================================================================
# MAIN
# ================================================================
def main():
    args = sys.argv[1:]
    if not args:
        list_providers()
        return 0

    action = args[0]

    if action in ("list", "ls"):
        list_providers()
        return 0

    if action == "list-fast":
        list_providers(with_health=False)
        return 0

    if action in ("add", "install", "new"):
        fields = _parse_provider_fields(args[1:])
        if fields is None:
            return 1
        return add_provider(fields["name"], fields["api"], fields["key"], fields["app_id"])

    if action == "edit":
        if len(args) < 2:
            err("Usage: edit <index|name|key> [-name <name>] [-api <url>] [-key <key>] [-appId <appId>]")
            return 1
        identifier = args[1]
        fields = _parse_provider_fields(args[2:])
        if fields is None:
            return 1
        app_id_marker = fields["app_id"] if any(flag in args[2:] for flag in ("-appId", "--appId", "-app-id", "--app-id")) else None
        return edit_provider(identifier, fields["name"], fields["api"], fields["key"], app_id_marker)

    if action in ("delete", "uninstall", "remove"):
        if len(args) < 2:
            err("Usage: delete <index|name|key>")
            return 1
        return delete_provider(args[1])

    if action in ("set-default", "use"):
        if len(args) < 2:
            err("Usage: set-default <index|name|key>")
            return 1
        return set_default(args[1])

    if action == "env":
        identifier = args[1] if len(args) > 1 else "default"
        return print_env(identifier)

    if action == "identifiers":
        list_identifiers()
        return 0

    if action == "seed":
        return seed()

    if action == "startup-info":
        identifier = args[1] if len(args) > 1 else "default"
        return print_startup_info(identifier)

    return set_default(action)


if __name__ == "__main__":
    raise SystemExit(main())
