#!/usr/bin/env python3
"""
env_loader.py — Shared .env loader for gorgias-webhook, teddy, and infra.

Edit /root/.env — only two lines control the chat model everywhere:
  LLM_PROVIDER=openrouter | ollama | mimo
  LLM_MODEL=<model-name>

Everything else (base URL, API key, Hindsight) is derived automatically.
Hermes (~/.hermes/) stays independent.
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT_DOTENV = "/root/.env"

# User picks one of these names. We fill in the rest.
LLM_PROVIDERS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "gateway_provider": "openrouter",
        "hindsight_provider": "openrouter",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "base_url": "https://ollama.com/v1",
        "gateway_provider": "ollama",
        "hindsight_provider": "ollama-cloud",
        "api_key_env": "OLLAMA_API_KEY",
    },
    "mimo": {
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "gateway_provider": "openai-compatible",
        "hindsight_provider": "openrouter",
        "api_key_env": "XIAOMI_API_KEY",
    },
}


def _parse_and_set(dotenv_path: str) -> None:
    try:
        with open(dotenv_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


def resolve_provider(name: str) -> dict | None:
    """Return preset for a provider alias (openrouter, ollama, mimo, ollama-cloud, etc.)."""
    key = (name or "").strip().lower()
    aliases = {
        "openrouter": "openrouter",
        "ollama": "ollama",
        "ollama-cloud": "ollama",
        "mimo": "mimo",
        "xiaomi": "mimo",
        "openai-compatible": None,  # not a user-facing choice
    }
    canonical = aliases.get(key, key)
    if canonical is None:
        return None
    return LLM_PROVIDERS.get(canonical)


def _hindsight_model(model: str) -> str:
    """Hindsight wants deepseek-v4-flash, not deepseek-v4-flash:cloud."""
    return model.split(":")[0] if ":" in model else model


def apply_llm_presets() -> None:
    """Derive LLM_BASE_URL, LLM_API_KEY, HINDSIGHT_* from LLM_PROVIDER + LLM_MODEL."""
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    model = os.environ.get("LLM_MODEL", "").strip()
    if not provider or not model:
        return

    preset = resolve_provider(provider)
    if not preset:
        return

    os.environ["LLM_PROVIDER"] = preset["gateway_provider"]
    os.environ.setdefault("LLM_BASE_URL", preset["base_url"])
    os.environ.setdefault("LLM_MODEL", model)

    key_env = preset["api_key_env"]
    if not os.environ.get("LLM_API_KEY", "").strip():
        api_key = os.environ.get(key_env, "").strip()
        if api_key:
            os.environ["LLM_API_KEY"] = api_key

    # Hindsight follows the same model unless explicitly overridden.
    os.environ.setdefault("HINDSIGHT_API_LLM_PROVIDER", preset["hindsight_provider"])
    hindsight_model = os.environ.get("HINDSIGHT_LLM_MODEL", "").strip() or _hindsight_model(model)
    os.environ.setdefault("HINDSIGHT_API_LLM_MODEL", hindsight_model)
    if not os.environ.get("HINDSIGHT_API_LLM_API_KEY", "").strip():
        api_key = os.environ.get(key_env, "").strip()
        if api_key:
            os.environ["HINDSIGHT_API_LLM_API_KEY"] = api_key
    os.environ.setdefault("HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS", "16000")


def apply_aliases() -> None:
    """Bridge naming differences between gorgias-webhook and teddy."""
    apply_llm_presets()

    domain = os.environ.get("GORGIAS_DOMAIN", "").strip()
    base_url = os.environ.get("GORGIAS_BASE_URL", "").strip()
    if domain and not base_url:
        os.environ["GORGIAS_BASE_URL"] = f"https://{domain}.gorgias.com"
    elif base_url and not domain:
        host = base_url.rstrip("/").replace("https://", "").replace("http://", "")
        if host.endswith(".gorgias.com"):
            os.environ["GORGIAS_DOMAIN"] = host[: -len(".gorgias.com")]

    email = os.environ.get("GORGIAS_EMAIL", "").strip()
    username = os.environ.get("GORGIAS_USERNAME", "").strip()
    if email and not username:
        os.environ["GORGIAS_USERNAME"] = email
    elif username and not email:
        os.environ["GORGIAS_EMAIL"] = username

    secret = os.environ.get("WEBHOOK_SECRET", "").strip()
    token = os.environ.get("WEBHOOK_SECRET_TOKEN", "").strip()
    if secret and not token:
        os.environ["WEBHOOK_SECRET_TOKEN"] = secret
    elif token and not secret:
        os.environ["WEBHOOK_SECRET"] = token

    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    chat_ids = os.environ.get("TELEGRAM_CHAT_IDS", "").strip()
    if chat_id and not chat_ids:
        os.environ["TELEGRAM_CHAT_IDS"] = chat_id
    elif chat_ids and not chat_id:
        first = chat_ids.split(",")[0].strip()
        if first:
            os.environ["TELEGRAM_CHAT_ID"] = first

    pg_pass = os.environ.get("POSTGRES_PASSWORD", "").strip()
    pgvector_pass = os.environ.get("PGVECTOR_PASSWORD", "").strip()
    if pg_pass and not pgvector_pass:
        os.environ["PGVECTOR_PASSWORD"] = pg_pass
    elif pgvector_pass and not pg_pass:
        os.environ["POSTGRES_PASSWORD"] = pgvector_pass


def load(path: str | None = None) -> None:
    """Load /root/.env and apply presets + aliases."""
    _parse_and_set(path or ROOT_DOTENV)
    apply_aliases()


def resolved_llm() -> dict:
    """Return the effective LLM config after presets (for --show / model_gateway)."""
    load()
    provider = os.environ.get("LLM_PROVIDER", "")
    return {
        "provider": provider,
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "model": os.environ.get("LLM_MODEL", ""),
        "api_key_set": bool(os.environ.get("LLM_API_KEY", "").strip()),
        "hindsight_provider": os.environ.get("HINDSIGHT_API_LLM_PROVIDER", ""),
        "hindsight_model": os.environ.get("HINDSIGHT_API_LLM_MODEL", ""),
    }


def _update_dotenv_key(path: str, key: str, value: str) -> None:
    """Set or replace one key in a .env file."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    out = []
    for line in lines:
        if pattern.match(line):
            out.append(f"{key}={value}\n")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)


def set_llm(provider: str, model: str, path: str | None = None) -> dict:
    """Write LLM_PROVIDER + LLM_MODEL to /root/.env and return resolved config."""
    dotenv = path or ROOT_DOTENV
    canonical = provider.strip().lower()
    if canonical not in LLM_PROVIDERS:
        raise ValueError(
            f"Unknown provider {provider!r}. Choose: {', '.join(LLM_PROVIDERS)}"
        )
    _update_dotenv_key(dotenv, "LLM_PROVIDER", canonical)
    _update_dotenv_key(dotenv, "LLM_MODEL", model.strip())
    # Clear derived vars from shell so reload picks up fresh values
    for k in list(os.environ):
        if k.startswith(("LLM_", "HINDSIGHT_API_LLM")):
            del os.environ[k]
    load(dotenv)
    return resolved_llm()


def shell_exports() -> str:
    """Print bash export statements for derived LLM vars (for shell scripts)."""
    load()
    keys = (
        "LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY",
        "HINDSIGHT_API_LLM_PROVIDER", "HINDSIGHT_API_LLM_MODEL",
        "HINDSIGHT_API_LLM_API_KEY", "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS",
    )
    lines = []
    for k in keys:
        v = os.environ.get(k, "")
        if v:
            escaped = v.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'export {k}="{escaped}"')
    return "\n".join(lines)


def _cli_show(cfg: dict) -> None:
    print("Active LLM config (/root/.env):")
    print(f"  provider  : {cfg['provider']}")
    print(f"  model     : {cfg['model']}")
    print(f"  base_url  : {cfg['base_url']}")
    print(f"  api_key   : {'set' if cfg['api_key_set'] else 'MISSING'}")
    print(f"  hindsight : {cfg['hindsight_provider']} / {cfg['hindsight_model']}")
    print()
    print("Providers: openrouter | ollama | mimo")
    print("Change:    llm-set <provider> <model>")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--shell":
        print(shell_exports())
    elif len(sys.argv) >= 2 and sys.argv[1] == "--show":
        _cli_show(resolved_llm())
    elif len(sys.argv) >= 2 and sys.argv[1] == "--json":
        print(json.dumps(resolved_llm(), indent=2))
    elif len(sys.argv) >= 4 and sys.argv[1] == "--set":
        cfg = set_llm(sys.argv[2], sys.argv[3])
        _cli_show(cfg)
    else:
        print("Usage: env_loader.py --show | --shell | --set <provider> <model>")
        sys.exit(1)
