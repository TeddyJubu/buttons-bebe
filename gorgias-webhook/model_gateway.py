#!/usr/bin/env python3
"""
model_gateway.py — The single choke-point for every LLM call in the Hermes agent.

WHY THIS EXISTS:
  The classifier and the draft engine must NEVER talk to an LLM provider
  directly. They call this module instead. That gives the project ONE place to:
    * swap providers (Ollama Cloud <-> OpenRouter is still an open decision —
      see PHASE1_KB_ARCHITECTURE.md "Model-gateway consolidation"),
    * manage the API key (env or config.json, plaintext or crypto_util `enc:`),
    * add retries/timeouts/backoff, and
    * run fully OFFLINE via the built-in "mock" provider so the rest of the
      team can build and test before any real LLM key is configured.

PROVIDER MODEL:
  Both Ollama Cloud and OpenRouter speak the OpenAI-compatible
  `POST {base_url}/chat/completions` chat API, so one HTTP path covers both.
  A third provider, "mock", makes NO network call and returns deterministic,
  echo-y output for tests.

CONFIG (config.json -> "llm" block; ALL fields optional):
  {
    "llm": {
      "provider":        "mock" | "ollama" | "openrouter" | "openai-compatible",
      "base_url":        "https://ollama.com/v1",        # OpenAI-compatible root
      "model":           "glm-5.2",
      "api_key":         "enc:..." | "sk-...",            # inline key (enc: ok)
      "api_key_env":     "LLM_API_KEY",                  # OR name an env var
      "temperature":     0.2,
      "max_tokens":      1024,
      "request_timeout": 60
    }
  }
  If there is no "llm" block at all, the gateway defaults to the MOCK provider
  (no key required) so nothing crashes pre-key.

ENV OVERRIDES (win over config.json):
  LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY

CLI:
  python3 model_gateway.py selfcheck
  python3 model_gateway.py selfcheck --json
  python3 model_gateway.py complete "write a one-line greeting"
  python3 model_gateway.py config            # show resolved config (no secrets)

This module is stdlib-only: NO requests, NO openai sdk, NO new pip deps.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import yaml

# Load /root/.env so standalone `python3 model_gateway.py ...` picks up keys.
# Provider/model defaults still come from ~/.hermes/config.yaml (Hermes stays
# independent); API keys live in /root/.env.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import dotenv_loader; dotenv_loader.load()
except ImportError:
    pass

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
CONFIG_PATH = "/root/gorgias-webhook/config.json"
HERMES_CONFIG_PATH = os.path.expanduser("~/.hermes/config.yaml")
USER_AGENT = "Hermes-Agent/1.0 (+buttons-bebe; model-gateway)"
MACHINE_KEY_FILE = "/etc/gorgias-wh-key"

DEFAULTS = {
    "provider": "mock",
    "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
    "model": "glm-5.2",
    "temperature": 0.2,
    "max_tokens": 1024,
    "request_timeout": 60,
}

# Providers that hit the network and therefore need a key.
# ollama and anthropic are included so any Hermes provider name works —
# ollama uses the OpenAI-compatible API at /v1, and anthropic also has an
# OpenAI-compatible endpoint. This prevents "Unknown LLM provider" errors
# when the user switches Hermes to a different model/provider.
LIVE_PROVIDERS = ("openrouter", "openai", "openai-compatible", "ollama", "anthropic")

# Per-provider base_url hints used only when base_url is not configured.
PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
}

# Maps each provider to the env var name in /root/.env. When `hermes model`
# switches providers, this lets model_gateway pick up the correct key without
# relying on config.yaml's api_key field (which can get stale or mismatched).
# Priority: LLM_API_KEY (test override) > provider env var > config.yaml.
PROVIDER_ENV_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# --------------------------------------------------------------------------- #
# Hermes config reader — syncs draft model with Hermes default model
# --------------------------------------------------------------------------- #
def _load_hermes_config():
    """Read Hermes's config.yaml and extract the default model configuration.

    This allows the draft engine to automatically use whatever model Hermes
    is configured with. When the user runs `hermes model` to change the
    default model, the draft engine follows on the next webhook.

    Returns a dict with provider, base_url, model, api_key — or empty dict
    if Hermes config is not available or doesn't have a model section.
    """
    try:
        if not os.path.exists(HERMES_CONFIG_PATH):
            return {}
        with open(HERMES_CONFIG_PATH, "r", encoding="utf-8") as f:
            hermes_cfg = yaml.safe_load(f) or {}
        model_section = hermes_cfg.get("model") or {}
        if not model_section:
            return {}

        # Map Hermes provider names to our provider names.
        # Any unmapped provider falls back to "openai-compatible" since most
        # providers (Ollama Cloud, Xiaomi, custom endpoints) speak that API.
        hermes_provider = (model_section.get("provider") or "").lower()
        provider_map = {
            "openrouter": "openrouter",
            "anthropic": "anthropic",
            "openai": "openai",
            "google": "openai-compatible",
            "deepseek": "openai-compatible",
            "ollama": "ollama",
            "ollama-cloud": "ollama",  # Ollama Cloud uses the same /v1 API
            "custom": "openai-compatible",  # custom endpoints use OpenAI-compatible API
        }
        provider = provider_map.get(hermes_provider, "openai-compatible")

        return {
            "provider": provider,
            "base_url": model_section.get("base_url", ""),
            "model": model_section.get("default", ""),
            "api_key": model_section.get("api_key", ""),
        }
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Typed exceptions
# --------------------------------------------------------------------------- #
class LLMError(Exception):
    """Base class for every error this gateway raises."""


class LLMConfigError(LLMError):
    """Misconfiguration — e.g. a live provider selected with no API key.

    This is raised INSTEAD of a confusing urllib/JSON traceback so callers can
    catch one clear, typed error and fall back to the mock path or escalate.
    """


class LLMHTTPError(LLMError):
    """A non-2xx response (or exhausted retries) from the LLM provider."""

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


# --------------------------------------------------------------------------- #
# Config loading (mirrors gorgias_api.load_credentials style + env-gating)
# --------------------------------------------------------------------------- #
def _read_config_file(path=CONFIG_PATH):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _maybe_decrypt(value):
    """Best-effort decrypt of an `enc:`-prefixed secret via crypto_util's scheme.

    Never raises: if the machine key or cryptography lib is unavailable we
    return the raw value untouched (the caller's is_live()/health_check() will
    then surface a clear config error rather than a crash). Most LLM keys are
    expected to be plaintext or env-supplied, so this path is optional sugar.
    """
    if not isinstance(value, str) or not value.startswith("enc:"):
        return value
    token = value[4:]
    try:
        from cryptography.fernet import Fernet  # provided by the project venv
        import hashlib

        if not os.path.exists(MACHINE_KEY_FILE):
            return value
        with open(MACHINE_KEY_FILE, "rb") as fh:
            machine_key = fh.read().strip()
        # server.py derives with standard base64; mirror it for compatibility.
        derived = base64.b64encode(hashlib.sha256(machine_key).digest())
        return Fernet(derived).decrypt(token.encode()).decode()
    except Exception:
        return value


def load_llm_config(path=CONFIG_PATH):
    """Resolve LLM config: /root/.env (LLM_PROVIDER + LLM_MODEL) <- Hermes <- config.json.

    When LLM_PROVIDER and LLM_MODEL are set in /root/.env, that wins everywhere.
    Hermes config.yaml is fallback only (Hermes agent stays independent).
    """
    cfg = dict(DEFAULTS)

    env_provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    env_model = os.environ.get("LLM_MODEL", "").strip()
    env_from_root = bool(env_provider and env_model)

    if env_from_root:
        # /root/.env is the source of truth (filled by env_loader presets).
        cfg["provider"] = env_provider
        cfg["base_url"] = os.environ.get("LLM_BASE_URL", cfg["base_url"]).strip()
        cfg["model"] = env_model
        cfg["api_key"] = os.environ.get("LLM_API_KEY", "").strip()
        if not cfg["api_key"]:
            key_var = PROVIDER_ENV_KEYS.get(cfg["provider"])
            if key_var:
                cfg["api_key"] = os.environ.get(key_var, "").strip()
    else:
        hermes_cfg = _load_hermes_config()
        hermes_active = (
            hermes_cfg
            and hermes_cfg.get("provider")
            and hermes_cfg.get("api_key")
        )
        if hermes_active:
            cfg["provider"] = hermes_cfg["provider"]
            cfg["base_url"] = hermes_cfg.get("base_url", cfg["base_url"])
            cfg["model"] = hermes_cfg.get("model", cfg["model"])
            cfg["api_key"] = hermes_cfg["api_key"]
        else:
            llm = _read_config_file(path).get("llm")
            if isinstance(llm, dict):
                for key in ("provider", "base_url", "model", "temperature",
                            "max_tokens", "request_timeout"):
                    if llm.get(key) not in (None, ""):
                        cfg[key] = llm[key]
                api_key = ""
                if llm.get("api_key"):
                    api_key = _maybe_decrypt(str(llm["api_key"]).strip())
                elif llm.get("api_key_env"):
                    api_key = os.environ.get(str(llm["api_key_env"]).strip(), "").strip()
                cfg["api_key"] = api_key

        _provider_env_var = PROVIDER_ENV_KEYS.get(cfg["provider"])
        if _provider_env_var:
            _provider_key = os.environ.get(_provider_env_var, "").strip()
            if _provider_key:
                cfg["api_key"] = _provider_key

        cfg["provider"] = os.environ.get("LLM_PROVIDER", cfg["provider"]).strip().lower()
        cfg["base_url"] = os.environ.get("LLM_BASE_URL", cfg["base_url"]).strip()
        cfg["model"] = os.environ.get("LLM_MODEL", cfg["model"]).strip()
        env_key = os.environ.get("LLM_API_KEY", "").strip()
        if env_key:
            cfg["api_key"] = env_key

    # Temperature / timeouts always from config.json when present.
    llm = _read_config_file(path).get("llm")
    if isinstance(llm, dict):
        for key in ("temperature", "max_tokens", "request_timeout"):
            if llm.get(key) not in (None, ""):
                cfg[key] = llm[key]

    if cfg["provider"] in PROVIDER_BASE_URLS and not _base_url_explicit(path):
        if not env_from_root or not os.environ.get("LLM_BASE_URL", "").strip():
            cfg["base_url"] = PROVIDER_BASE_URLS[cfg["provider"]]

    cfg["temperature"] = _as_float(cfg["temperature"], DEFAULTS["temperature"])
    cfg["max_tokens"] = _as_int(cfg["max_tokens"], DEFAULTS["max_tokens"])
    cfg["request_timeout"] = _as_int(cfg["request_timeout"], DEFAULTS["request_timeout"])
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    return cfg


def _base_url_explicit(path):
    """True if base_url is set anywhere the user controls (env or config)."""
    if os.environ.get("LLM_BASE_URL", "").strip():
        return True
    llm = _read_config_file(path).get("llm")
    return isinstance(llm, dict) and bool(str(llm.get("base_url", "")).strip())


def _as_float(value, fallback):
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def is_live(cfg=None):
    """True iff a real provider is selected AND an API key is configured."""
    cfg = cfg or load_llm_config()
    return cfg["provider"] in LIVE_PROVIDERS and bool(cfg.get("api_key"))


def redacted_config(cfg=None):
    """Config safe to print/log — never exposes the key value."""
    cfg = dict(cfg or load_llm_config())
    key = cfg.get("api_key") or ""
    cfg["api_key"] = f"set (len={len(key)})" if key else "MISSING"
    cfg["live"] = is_live(cfg) if cfg.get("api_key") != "MISSING" else False
    # recompute live cleanly off the original
    cfg["live"] = cfg["provider"] in LIVE_PROVIDERS and bool(key)
    return cfg


# --------------------------------------------------------------------------- #
# HTTP — OpenAI-compatible chat completions (Ollama Cloud + OpenRouter)
# Retry-on-429/5xx with backoff, mirroring gorgias_api.request().
# --------------------------------------------------------------------------- #
def _http_chat(cfg, payload, timeout, max_retries=3):
    url = f"{cfg['base_url']}/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Harmless for non-OpenRouter providers; identifies the app on OpenRouter.
        "HTTP-Referer": "https://buttonsbebe.com",
        "X-Title": "Hermes Agent",
    }

    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                try:
                    return json.loads(body) if body else {}
                except ValueError as exc:
                    raise LLMHTTPError(
                        f"LLM returned non-JSON body: {exc}",
                        status=resp.status, body=body[:500],
                    )
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                pass
            # Retry rate-limits and transient server errors with backoff.
            if exc.code == 429 and attempt <= max_retries:
                wait = _retry_after(exc.headers, attempt)
                time.sleep(wait)
                continue
            if 500 <= exc.code < 600 and attempt <= max_retries:
                time.sleep(min(2 ** attempt, 30))
                continue
            raise LLMHTTPError(
                f"HTTP {exc.code} from LLM provider at {url}: {detail[:500]}",
                status=exc.code, body=detail[:2000],
            )
        except urllib.error.URLError as exc:
            if attempt <= max_retries:
                time.sleep(2 * attempt)
                continue
            raise LLMError(f"Network error calling LLM provider at {url}: {exc}")


def _retry_after(headers, attempt):
    try:
        wait = int(headers.get("Retry-After", "") or 0)
    except (TypeError, ValueError):
        wait = 0
    if wait <= 0:
        wait = min(2 ** attempt, 30)
    return min(wait, 30)


# --------------------------------------------------------------------------- #
# Mock provider — deterministic, NO network. The rest of the team depends on it.
# --------------------------------------------------------------------------- #
def _mock_complete(messages, model, **opts):
    """Return canned output that echoes the input so a test can assert wiring.

    The reply embeds the system prompt summary and the last user message so a
    caller can prove the gateway received what it sent. It also returns a tiny
    JSON-shaped string when the prompt looks like it wants JSON (handy for the
    classifier's offline tests).
    """
    system = ""
    last_user = ""
    for m in messages:
        role = (m.get("role") or "").lower()
        content = m.get("content") or ""
        if role == "system":
            system = content
        elif role == "user":
            last_user = content

    wants_json = "json" in (system + " " + last_user).lower()
    if wants_json:
        text = json.dumps({
            "mock": True,
            "category": "general",
            "echo_user": last_user[:200],
        })
    else:
        snippet = last_user.strip().replace("\n", " ")[:200]
        text = (
            "[MOCK DRAFT] This is a deterministic offline reply from the model "
            "gateway mock provider (no LLM key configured). It echoes your "
            f"request so wiring can be verified. You asked: \"{snippet}\""
        )

    prompt_chars = sum(len(m.get("content") or "") for m in messages)
    usage = {
        "prompt_tokens": max(1, prompt_chars // 4),
        "completion_tokens": max(1, len(text) // 4),
        "total_tokens": max(1, (prompt_chars + len(text)) // 4),
    }
    return {
        "text": text,
        "model": model or "mock",
        "usage": usage,
        "provider": "mock",
        "raw": {
            "id": "mock-cmpl-0",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": usage,
        },
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def complete(messages, *, model=None, temperature=None, max_tokens=None,
             timeout=None, cfg=None, **opts):
    """Single choke-point for chat completions.

    Args:
      messages: OpenAI-style list, e.g. [{"role":"system","content":...}, ...].
      model/temperature/max_tokens/timeout: per-call overrides of config.
      cfg: pre-resolved config dict (mostly for tests); defaults to load_llm_config().
      **opts: extra OpenAI-compatible params passed through (e.g. top_p, stop,
              response_format) — and forwarded to the mock for inspection.

    Returns a normalized dict:
      {"text": <assistant content>, "model": <model>, "usage": {...},
       "raw": <provider json>, "provider": <provider>}

    Raises:
      LLMConfigError: a live provider is selected but no API key is configured,
                      or messages are malformed.
      LLMHTTPError:   provider returned a non-2xx / non-JSON response.
      LLMError:       network failure or other gateway error.
    """
    if not isinstance(messages, (list, tuple)) or not messages:
        raise LLMConfigError("messages must be a non-empty list of {role, content} dicts.")
    for m in messages:
        if not isinstance(m, dict) or "role" not in m or "content" not in m:
            raise LLMConfigError("each message must be a dict with 'role' and 'content'.")

    cfg = dict(cfg or load_llm_config())
    if model:
        cfg["model"] = model
    temperature = cfg["temperature"] if temperature is None else temperature
    max_tokens = cfg["max_tokens"] if max_tokens is None else max_tokens
    timeout = cfg["request_timeout"] if timeout is None else timeout
    provider = cfg["provider"]

    # ---- Mock path: no network, always works offline. ----
    if provider == "mock":
        return _mock_complete(list(messages), cfg["model"], **opts)

    # ---- Live path: require config before touching the network. ----
    if provider not in LIVE_PROVIDERS:
        raise LLMConfigError(
            f"Unknown LLM provider '{provider}'. Use one of: "
            f"mock, {', '.join(LIVE_PROVIDERS)}."
        )
    if not cfg.get("api_key"):
        raise LLMConfigError(
            f"LLM provider '{provider}' is selected but no API key is configured. "
            "Set the model in Hermes (hermes model) or ensure ~/.hermes/config.yaml "
            "has model.api_key set. (Use provider='mock' to run offline.)"
        )
    if not cfg.get("base_url"):
        raise LLMConfigError(
            f"LLM provider '{provider}' has no base_url configured."
        )

    payload = {
        "model": cfg["model"],
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # Pass through any extra OpenAI-compatible options (top_p, stop, etc.).
    for k, v in opts.items():
        if v is not None:
            payload[k] = v

    raw = _http_chat(cfg, payload, timeout)
    return _normalize(raw, provider, cfg["model"])


def _normalize(raw, provider, requested_model):
    """Flatten an OpenAI-compatible response into the gateway's normal dict."""
    text = ""
    try:
        choices = raw.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            text = msg.get("content") or choices[0].get("text") or ""
            # Some reasoning models (mimo, deepseek) return reasoning_content
            # but empty content — fall back to reasoning_content if content is empty
            if not text and msg.get("reasoning_content"):
                text = msg["reasoning_content"]
            # Some models (deepseek-v4-flash) return content="" with the
            # actual response in a "reasoning" field instead
            if not text and msg.get("reasoning"):
                text = msg["reasoning"]
    except (AttributeError, TypeError):
        raise LLMHTTPError("Unexpected response shape from LLM provider.",
                           body=json.dumps(raw)[:500])
    return {
        "text": text,
        "model": (raw.get("model") if isinstance(raw, dict) else None) or requested_model,
        "usage": (raw.get("usage") if isinstance(raw, dict) else None) or {},
        "provider": provider,
        "raw": raw,
    }


def complete_text(prompt, system=None, *, model=None, temperature=None,
                  max_tokens=None, timeout=None, cfg=None, **opts):
    """Convenience wrapper: a single prompt (+optional system) -> assistant str."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return complete(messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, timeout=timeout, cfg=cfg, **opts)["text"]


def health_check(cfg=None):
    """Report gateway health WITHOUT ever failing hard for a missing key.

    Returns a dict: {ok, provider, model, live, mode, latency_ms?, message,
    sample?}. If live, makes ONE tiny real call. If not live, exercises the
    mock path. `ok` is True in mock mode by design.
    """
    cfg = cfg or load_llm_config()
    live = is_live(cfg)
    probe = [
        {"role": "system", "content": "You are a health probe. Reply with the single word: ok"},
        {"role": "user", "content": "ping"},
    ]

    if not live:
        # Run the mock so we prove the offline path end-to-end.
        mock_cfg = dict(cfg)
        mock_cfg["provider"] = "mock"
        t0 = time.time()
        out = complete(probe, cfg=mock_cfg, max_tokens=16)
        latency = int((time.time() - t0) * 1000)
        reason = ("provider='mock'"
                  if cfg["provider"] == "mock"
                  else f"provider='{cfg['provider']}' but no API key")
        return {
            "ok": True,
            "provider": cfg["provider"],
            "model": cfg["model"],
            "live": False,
            "mode": "mock",
            "latency_ms": latency,
            "message": ("mock OK — no live key configured "
                        f"({reason}). Set config.json llm.api_key or LLM_API_KEY "
                        "to go live."),
            "sample": out["text"][:120],
        }

    # Live: one tiny real call. Catch typed errors so health_check stays soft.
    t0 = time.time()
    try:
        out = complete(probe, cfg=cfg, max_tokens=16, temperature=0)
    except LLMError as exc:
        latency = int((time.time() - t0) * 1000)
        return {
            "ok": False,
            "provider": cfg["provider"],
            "model": cfg["model"],
            "live": True,
            "mode": "live",
            "latency_ms": latency,
            "message": f"live call FAILED: {type(exc).__name__}: {exc}",
            "error_type": type(exc).__name__,
        }
    latency = int((time.time() - t0) * 1000)
    return {
        "ok": True,
        "provider": cfg["provider"],
        "model": out.get("model") or cfg["model"],
        "live": True,
        "mode": "live",
        "latency_ms": latency,
        "message": f"live OK — {cfg['provider']} reachable in {latency} ms.",
        "sample": (out.get("text") or "")[:120],
        "usage": out.get("usage", {}),
    }


# Alias requested by the task spec; same as health_check.
def selfcheck(cfg=None):
    return health_check(cfg=cfg)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="LLM model gateway — the single choke-point for all LLM calls."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sc = sub.add_parser("selfcheck", help="Health check (mock if no live key). Exits 0 in mock mode.")
    p_sc.add_argument("--json", action="store_true", help="Emit the full result as JSON.")

    sub.add_parser("config", help="Show resolved LLM config (secrets redacted).")

    p_c = sub.add_parser("complete", help="Run a single prompt (mock if not live).")
    p_c.add_argument("prompt", help="The user prompt.")
    p_c.add_argument("--system", default=None, help="Optional system prompt.")
    p_c.add_argument("--model", default=None, help="Override the configured model.")
    p_c.add_argument("--max-tokens", type=int, default=None)
    p_c.add_argument("--temperature", type=float, default=None)
    p_c.add_argument("--text-only", action="store_true", help="Print only the assistant text.")

    args = parser.parse_args(argv)

    if args.cmd == "config":
        _emit(redacted_config())
        return 0

    if args.cmd == "selfcheck":
        result = health_check()
        if args.json:
            _emit(result)
        else:
            status = "OK" if result["ok"] else "FAIL"
            print(f"selfcheck: {status} [{result['mode']}] — {result['message']}")
            if result.get("latency_ms") is not None:
                print(f"  provider={result['provider']} model={result['model']} "
                      f"latency={result['latency_ms']}ms")
            if result.get("sample"):
                print(f"  sample: {result['sample']}")
        # Mock mode is a healthy state pre-key: exit 0. A failed LIVE call exits 1.
        return 0 if result["ok"] else 1

    if args.cmd == "complete":
        try:
            out = complete_text(
                args.prompt, system=args.system, model=args.model,
                max_tokens=args.max_tokens, temperature=args.temperature,
            ) if args.text_only else complete(
                ([{"role": "system", "content": args.system}] if args.system else [])
                + [{"role": "user", "content": args.prompt}],
                model=args.model, max_tokens=args.max_tokens,
                temperature=args.temperature,
            )
        except LLMConfigError as exc:
            print(f"ERROR (LLMConfigError): {exc}", file=sys.stderr)
            return 2
        except LLMError as exc:
            print(f"ERROR ({type(exc).__name__}): {exc}", file=sys.stderr)
            return 1
        if args.text_only:
            print(out)
        else:
            _emit(out)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
