#!/usr/bin/env python3
"""Read-only Shopify content-write safety monitor.

This monitor deliberately uses only local inspection and HTTP GET requests.  It
does not call the Shopify Admin API directly, send webhook payloads, or invoke
any warehouse mutation route.  A failed check is a failed safety verdict.

The module is stdlib-only so the systemd timer can run from the host Python
without installing another runtime.  ``SafetyDependencies`` keeps command,
file, and HTTP access injectable for deterministic tests.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_MAX_HTTP_BODY = 64 * 1024
_WRITE_FLAG = "SHOPIFY_CONTENT_WRITES_ENABLED"
_MUTATION_FLAG = "SHOPIFY_MUTATIONS_ENABLED"
# One ordered source of truth for the approved Caddy fragments: the manifest
# set and the entrypoint's deterministic import list are both derived from it,
# so a newly approved fragment can't leave the two out of sync.
_CADDY_FRAGMENTS = (
    "sites/support.caddy",
    "sites/exchange.caddy",
    "sites/warehouse.caddy",
    # Owner-approved 2026-09-17: the receiving workspace origin (runbook
    # step 4; proxies 127.0.0.1:3210 behind the console session gate).
    "sites/receiving.caddy",
)
_CADDY_MANIFEST_FILES = frozenset({"Caddyfile", *_CADDY_FRAGMENTS})
_CADDY_IMPORTS = [f"import {fragment}" for fragment in _CADDY_FRAGMENTS]


class MonitorTransportError(RuntimeError):
    """A GET could not be completed without exposing transport details."""


class CheckFailure(RuntimeError):
    """A check failed with a deliberately safe, non-secret detail."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str = ""


@dataclass(frozen=True)
class SafetyConfig:
    warehouse_service: str = "warehouse.service"
    caddy_service: str = "caddy"
    warehouse_port: int = 4000
    shopify_source_path: Path = Path("/opt/warehouse/src/shopify.js")
    process_environ_dir: Path = Path("/proc")
    webhook_endpoint: str = "http://127.0.0.1:4000/api/shopify/webhooks"
    warehouse_public_base_url: str = "https://wh.buttonsbebe.com"
    support_public_base_url: str = "https://support.buttonsbebe.com"
    caddy_manifest_path: Path = Path("/etc/caddy/buttonsbebe-caddy.sha256")
    warehouse_source_manifest_path: Path = Path(
        "/opt/warehouse/.codex-safety-source.sha256"
    )
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class SafetyDependencies:
    run_command: Callable[[Sequence[str]], CommandResult]
    http_get: Callable[[str, float], HttpResponse]
    read_text: Callable[[Path], str]
    read_bytes: Callable[[Path], bytes]
    stat_mtime: Callable[[Path], float]
    now: Callable[[], float]
    clock_ticks: int


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "PASS"

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SafetyReport:
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "checks": [check.as_dict() for check in self.checks]}


def default_run_command(argv: Sequence[str]) -> CommandResult:
    """Run a fixed argv without a shell and discard command diagnostics."""

    try:
        completed = subprocess.run(
            tuple(argv),
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(returncode=124)
    except OSError:
        return CommandResult(returncode=127)
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


class _NoRedirect(HTTPRedirectHandler):
    """Keep health probes on the configured HTTPS host."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def default_http_get(url: str, timeout: float) -> HttpResponse:
    """Perform one unauthenticated GET; never send a request body or auth."""

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ButtonsBebe-Shopify-Safety/1.0",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(_MAX_HTTP_BODY).decode("utf-8", "replace")
            return HttpResponse(
                status=int(response.getcode()),
                headers=dict(response.headers.items()),
                body=body,
            )
    except HTTPError as error:
        # 401/404 are expected evidence for two of the probes.  Preserve only
        # the bounded body in memory; callers never include it in diagnostics.
        body = error.read(_MAX_HTTP_BODY).decode("utf-8", "replace")
        headers = dict(error.headers.items()) if error.headers else {}
        return HttpResponse(status=int(error.code), headers=headers, body=body)
    except (OSError, URLError, TimeoutError) as error:
        raise MonitorTransportError from error


def default_read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def default_read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def default_stat_mtime(path: Path) -> float:
    return path.stat().st_mtime


def default_dependencies() -> SafetyDependencies:
    return SafetyDependencies(
        run_command=default_run_command,
        http_get=default_http_get,
        read_text=default_read_text,
        read_bytes=default_read_bytes,
        stat_mtime=default_stat_mtime,
        now=time.time,
        clock_ticks=int(os.sysconf("SC_CLK_TCK")),
    )


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return ""


def _returncode(result: object) -> int:
    try:
        return int(getattr(result, "returncode"))
    except (AttributeError, TypeError, ValueError) as error:
        raise CheckFailure("command returned no usable status") from error


def _validate_url(url: str, *, scheme: str, loopback: bool = False) -> None:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _ = parsed.port
    except (TypeError, ValueError):
        raise CheckFailure("configured endpoint URL is invalid")
    if parsed.scheme.lower() != scheme or not hostname:
        raise CheckFailure("configured endpoint URL uses an unsafe scheme")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CheckFailure("configured endpoint URL contains disallowed credentials or data")
    if loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname.lower() == "localhost"
        if not is_loopback:
            raise CheckFailure("webhook inspection endpoint must be loopback-only")
    elif parsed.path not in {"", "/"}:
        raise CheckFailure("configured public endpoint must be a host base URL")


def _public_endpoint(base_url: str, path: str) -> str:
    _validate_url(base_url, scheme="https")
    return base_url.rstrip("/") + path


def _local_endpoint(url: str) -> str:
    _validate_url(url, scheme="http", loopback=True)
    return url


def _run_check(name: str, callback: Callable[[], str]) -> CheckResult:
    try:
        return CheckResult(name=name, status="PASS", detail=callback())
    except CheckFailure as error:
        return CheckResult(name=name, status="DRIFT", detail=str(error))
    except (OSError, MonitorTransportError, ValueError, TypeError):
        return CheckResult(name=name, status="UNKNOWN", detail="check could not be completed")
    except Exception:
        # A monitor must fail closed, but never turn an unexpected exception or
        # a dependency's message into a possible secret disclosure.
        return CheckResult(name=name, status="UNKNOWN", detail="check failed unexpectedly")


def _check_caddy_manifest(config: SafetyConfig, deps: SafetyDependencies) -> str:
    manifest_path = config.caddy_manifest_path
    manifest = deps.read_text(manifest_path)
    expected: dict[str, str] = {}
    for raw_line in manifest.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            raise CheckFailure("Caddy manifest has an invalid entry")
        relative = fields[1].lstrip("*")
        if relative not in _CADDY_MANIFEST_FILES or relative in expected:
            raise CheckFailure("Caddy manifest has an unexpected or duplicate path")
        expected[relative] = fields[0]
    if set(expected) != _CADDY_MANIFEST_FILES:
        raise CheckFailure("Caddy manifest does not cover the exact approved file set")

    root = manifest_path.parent
    for relative, wanted in expected.items():
        actual = hashlib.sha256(deps.read_bytes(root / relative)).hexdigest()
        if actual != wanted:
            raise CheckFailure("active Caddy configuration differs from its approved manifest")

    entrypoint = deps.read_text(root / "Caddyfile")
    imports = [
        line.strip()
        for line in entrypoint.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if imports != _CADDY_IMPORTS:
        raise CheckFailure("active Caddy entrypoint is not the deterministic import set")
    return "active Caddy entrypoint and fragments match the approved manifest"


def _read_sha256_manifest(
    manifest_path: Path,
    deps: SafetyDependencies,
) -> dict[str, str]:
    expected: dict[str, str] = {}
    for raw_line in deps.read_text(manifest_path).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            raise CheckFailure("source manifest has an invalid entry")
        relative = fields[1].lstrip("*")
        candidate = Path(relative)
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or candidate.suffix != ".js"
            or not candidate.parts
            or candidate.parts[0] != "src"
            or relative in expected
        ):
            raise CheckFailure("source manifest has an unexpected or duplicate path")
        expected[relative] = fields[0]
    if not expected:
        raise CheckFailure("source manifest is empty")
    return expected


def _check_warehouse_source_manifest(
    config: SafetyConfig,
    deps: SafetyDependencies,
) -> str:
    manifest_path = config.warehouse_source_manifest_path
    root = manifest_path.parent
    expected = _read_sha256_manifest(manifest_path, deps)
    source_root = root / "src"
    actual = {
        str(path.relative_to(root))
        for path in source_root.rglob("*.js")
        if path.is_file() and not path.is_symlink()
    }
    if actual != set(expected):
        raise CheckFailure("warehouse JavaScript inventory differs from its approved manifest")

    admin_markers = (
        "X-Shopify-Access-Token",
        "/admin/api/",
        "SHOPIFY_ENDPOINT",
        "myshopify.com/admin",
    )
    for relative, wanted in expected.items():
        path = root / relative
        if path.is_symlink():
            raise CheckFailure("warehouse source manifest contains a symlink")
        source_bytes = deps.read_bytes(path)
        if hashlib.sha256(source_bytes).hexdigest() != wanted:
            raise CheckFailure("warehouse source differs from its approved manifest")
        if relative != "src/shopify.js":
            source = source_bytes.decode("utf-8", "replace")
            if any(marker in source for marker in admin_markers):
                raise CheckFailure("Shopify Admin transport exists outside the guarded module")
    if "src/shopify.js" not in expected:
        raise CheckFailure("guarded Shopify module is absent from the source manifest")
    return "warehouse JavaScript inventory and hashes match the approved manifest"


def _check_services(config: SafetyConfig, deps: SafetyDependencies) -> str:
    for unit in (config.warehouse_service, config.caddy_service):
        result = deps.run_command(("systemctl", "is-active", "--quiet", unit))
        if _returncode(result) != 0:
            raise CheckFailure(f"{unit} is not active")
    return "warehouse and Caddy services are active"


def _split_ss_endpoint(endpoint: str) -> tuple[str, int] | None:
    endpoint = endpoint.strip()
    if endpoint.startswith("["):
        closing = endpoint.find("]")
        if closing < 0 or not endpoint[closing + 1 :].startswith(":"):
            return None
        host = endpoint[1:closing]
        port_text = endpoint[closing + 2 :]
    else:
        if ":" not in endpoint:
            return None
        host, port_text = endpoint.rsplit(":", 1)
    if not port_text.isdigit():
        return None
    return host.split("%", 1)[0], int(port_text)


def parse_listening_endpoints(output: str, port: int) -> tuple[str, ...]:
    """Return local addresses from ``ss -H -ltn`` for one TCP port."""

    addresses: list[str] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        parsed = _split_ss_endpoint(fields[3])
        if parsed and parsed[1] == port:
            addresses.append(parsed[0])
    return tuple(addresses)


def _is_loopback_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return address.lower() == "localhost"


def _check_port_loopback(config: SafetyConfig, deps: SafetyDependencies) -> str:
    result = deps.run_command(("ss", "-H", "-ltn"))
    if _returncode(result) != 0:
        raise CheckFailure("could not inspect TCP listeners")
    addresses = parse_listening_endpoints(str(getattr(result, "stdout", "")), config.warehouse_port)
    if not addresses:
        raise CheckFailure(f"TCP port {config.warehouse_port} has no listening socket")
    if any(not _is_loopback_address(address) for address in addresses):
        raise CheckFailure(f"TCP port {config.warehouse_port} has a non-loopback listener")
    return f"TCP port {config.warehouse_port} is loopback-only"


def _strip_js_comments(source: str) -> str:
    """Remove JS comments while preserving strings and line structure."""

    output: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(source):
        char = source[i]
        if quote:
            output.append(char)
            if char == "\\" and i + 1 < len(source):
                output.append(source[i + 1])
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            output.append(char)
            i += 1
            continue
        if source.startswith("//", i):
            while i < len(source) and source[i] != "\n":
                output.append(" ")
                i += 1
            continue
        if source.startswith("/*", i):
            output.extend((" ", " "))
            i += 2
            while i < len(source) and not source.startswith("*/", i):
                output.append("\n" if source[i] == "\n" else " ")
                i += 1
            if i < len(source):
                output.extend((" ", " "))
                i += 2
            continue
        output.append(char)
        i += 1
    return "".join(output)


def _balanced_body(code: str, opening_brace: int) -> str | None:
    depth = 0
    quote: str | None = None
    i = opening_brace
    while i < len(code):
        char = code[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return code[opening_brace + 1 : i]
        i += 1
    return None


_FUNCTION_RE = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+"
    r"([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
)


def _function_bodies(code: str) -> dict[str, str]:
    bodies: dict[str, str] = {}
    for match in _FUNCTION_RE.finditer(code):
        body = _balanced_body(code, match.end() - 1)
        if body is not None:
            bodies[match.group(1)] = body
    return bodies


_DECL_RE = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:`(?:(?:\\.)|[^`])*`|\"(?:(?:\\.)|[^\"])*\"|'(?:(?:\\.)|[^'])*')",
    re.DOTALL,
)


def _mutation_constants(code: str) -> set[str]:
    names: set[str] = set()
    for match in _DECL_RE.finditer(code):
        if re.search(r"\bmutation\b", match.group(0), re.IGNORECASE):
            names.add(match.group(1))
    return names


def inspect_shopify_write_guard(source: str) -> tuple[bool, str]:
    """Prove the known GraphQL mutation callers are guarded before the call."""

    code = _strip_js_comments(source)
    if _WRITE_FLAG not in code or "contentWritesEnabled" not in code:
        return False, "content-write feature flag or guard is missing"
    if "process.env.SHOPIFY_CONTENT_WRITES_ENABLED" not in code:
        return False, "content-write guard does not read the runtime feature flag"
    if "process.env.SHOPIFY_MUTATIONS_ENABLED" not in code:
        return False, "central mutation guard does not read its runtime feature flag"

    # The runtime default must be empty/false, and enabling must use a small
    # explicit allow-list.  This rejects truthiness checks where an arbitrary
    # non-empty environment value would turn writes on.
    has_false_default = bool(
        re.search(
            r"process\.env\.SHOPIFY_CONTENT_WRITES_ENABLED\s*(?:\|\||\?\?)\s*"
            r"(?:''|\"\"|'0'|\"0\"|'false'|\"false\")",
            code,
        )
    )
    has_allow_list = bool(re.search(r"\^\(1\|true\|yes\|on\)\$", code, re.IGNORECASE))
    if not has_false_default or not has_allow_list:
        return False, "content-write flag is not demonstrably false by default"
    mutation_false_default = bool(
        re.search(
            r"process\.env\.SHOPIFY_MUTATIONS_ENABLED\s*(?:\|\||\?\?)\s*"
            r"(?:''|\"\"|'0'|\"0\"|'false'|\"false\")",
            code,
        )
    )
    if not mutation_false_default:
        return False, "central mutation flag is not demonstrably false by default"

    bodies = _function_bodies(code)
    guard_body = bodies.get("requireContentWrites")
    if not guard_body:
        return False, "requireContentWrites guard function is missing"
    if not re.search(r"!\s*contentWritesEnabled\s*\(\s*\)", guard_body):
        return False, "requireContentWrites does not fail closed"
    if not re.search(r"\bthrow\s+new\s+Error\b", guard_body):
        return False, "requireContentWrites does not stop disabled writes"

    graphql_body = bodies.get("shopifyGraphQL")
    mutation_guard = bodies.get("requireShopifyMutationApproval")
    if not graphql_body or not mutation_guard:
        return False, "central Shopify mutation boundary is missing"
    boundary_position = graphql_body.find("requireShopifyMutationApproval")
    fetch_position = graphql_body.find("fetch(")
    if boundary_position < 0 or fetch_position < 0 or boundary_position > fetch_position:
        return False, "central Shopify mutation boundary does not run before network access"
    if "shopifyMutationsEnabled" not in mutation_guard or not re.search(
        r"\bthrow\s+new\s+Error\b", mutation_guard
    ):
        return False, "central Shopify mutation boundary does not fail closed"

    mutation_names = _mutation_constants(code)
    callers: list[str] = []
    unguarded: list[str] = []
    for name, body in bodies.items():
        call = re.search(r"\bshopifyGraphQL\s*\(\s*([A-Za-z_$][\w$]*)", body)
        if not call or (call.group(1) not in mutation_names and "mutation" not in body):
            continue
        callers.append(name)
        guard_position = body.find("requireContentWrites")
        graphql_position = call.start()
        if guard_position < 0 or guard_position > graphql_position:
            unguarded.append(name)

    if not callers:
        return False, "no GraphQL mutation callers were found to audit"
    if unguarded:
        return False, "unguarded mutation callers: " + ", ".join(sorted(unguarded))
    return True, "all detected GraphQL mutation callers are fail-closed"


def _check_source_guard(config: SafetyConfig, deps: SafetyDependencies) -> str:
    source = deps.read_text(config.shopify_source_path)
    ok, detail = inspect_shopify_write_guard(source)
    if not ok:
        raise CheckFailure(detail)
    return detail


def _pid_for_service(config: SafetyConfig, deps: SafetyDependencies) -> int:
    result = deps.run_command(
        ("systemctl", "show", "-p", "MainPID", "--value", config.warehouse_service)
    )
    if _returncode(result) != 0:
        raise CheckFailure("could not inspect the warehouse process")
    raw_pid = str(getattr(result, "stdout", "")).strip()
    if not raw_pid.isdigit() or int(raw_pid) <= 0:
        raise CheckFailure("warehouse service has no usable main process")
    return int(raw_pid)


def _process_start_epoch(pid: int, config: SafetyConfig, deps: SafetyDependencies) -> float:
    stat_text = deps.read_text(config.process_environ_dir / str(pid) / "stat")
    closing = stat_text.rfind(")")
    if closing < 0:
        raise CheckFailure("warehouse process stat is malformed")
    fields_after_comm = stat_text[closing + 1 :].split()
    # /proc/<pid>/stat field 22 is process start ticks. fields_after_comm
    # begins at field 3, so the zero-based index is 19.
    if len(fields_after_comm) <= 19 or not fields_after_comm[19].isdigit():
        raise CheckFailure("warehouse process start time is unavailable")
    uptime_text = deps.read_text(config.process_environ_dir / "uptime").split()
    if not uptime_text or deps.clock_ticks <= 0:
        raise CheckFailure("system uptime is unavailable")
    uptime = float(uptime_text[0])
    start_since_boot = int(fields_after_comm[19]) / deps.clock_ticks
    return deps.now() - uptime + start_since_boot


def _check_process_loaded_source(config: SafetyConfig, deps: SafetyDependencies) -> str:
    pid = _pid_for_service(config, deps)
    process_start = _process_start_epoch(pid, config, deps)
    source_mtime = deps.stat_mtime(config.shopify_source_path)
    # Filesystems and process clocks can differ by a fraction of a second.
    if process_start + 1.0 < source_mtime:
        raise CheckFailure("warehouse process predates the hardened Shopify source")
    return "warehouse process started after the hardened Shopify source was installed"


def _check_runtime_flags(config: SafetyConfig, deps: SafetyDependencies) -> str:
    pid = _pid_for_service(config, deps)
    environ_path = config.process_environ_dir / str(pid) / "environ"
    environ = deps.read_bytes(environ_path)
    values: dict[str, bytes] = {}
    for item in environ.split(b"\0"):
        key, separator, value = item.partition(b"=")
        decoded_key = key.decode("ascii", "ignore")
        if separator and decoded_key in {_WRITE_FLAG, _MUTATION_FLAG}:
            values[decoded_key] = value
    for key in (_WRITE_FLAG, _MUTATION_FLAG):
        enabled = values.get(key, b"").decode("utf-8", "replace").strip().lower()
        if enabled in _TRUE_VALUES:
            raise CheckFailure("a Shopify write flag is enabled in the running warehouse process")
    return "content and central mutation flags are absent or disabled"


def _check_warehouse_https(config: SafetyConfig, deps: SafetyDependencies) -> str:
    health_url = _public_endpoint(config.warehouse_public_base_url, "/api/health")
    webhook_url = _public_endpoint(
        config.warehouse_public_base_url,
        "/api/shopify/webhook/products-update",
    )
    health = deps.http_get(health_url, config.timeout_seconds)
    if health.status != 401:
        raise CheckFailure("warehouse health endpoint is not protected by Basic Auth")
    if not _header(health.headers, "www-authenticate").lower().startswith("basic"):
        raise CheckFailure("warehouse health endpoint lacks a Basic Auth challenge")

    webhook = deps.http_get(webhook_url, config.timeout_seconds)
    if webhook.status not in {404, 405}:
        raise CheckFailure("warehouse webhook route did not bypass the public auth gate")
    reached_app = _header(webhook.headers, "x-powered-by").lower() == "express"
    reached_app = reached_app or "cannot get" in webhook.body.lower()
    if not reached_app:
        raise CheckFailure("warehouse webhook probe did not reach the application")
    return "warehouse HTTPS is valid; health is protected and webhook path reaches the app"


def _expected_webhooks(base_url: str) -> dict[str, str]:
    base = base_url.rstrip("/")
    return {
        "PRODUCTS_CREATE": f"{base}/api/shopify/webhook/products-create",
        "PRODUCTS_UPDATE": f"{base}/api/shopify/webhook/products-update",
        "PRODUCTS_DELETE": f"{base}/api/shopify/webhook/products-delete",
    }


def _check_webhooks(config: SafetyConfig, deps: SafetyDependencies) -> str:
    endpoint = _local_endpoint(config.webhook_endpoint)
    _validate_url(config.warehouse_public_base_url, scheme="https")
    response = deps.http_get(endpoint, config.timeout_seconds)
    if response.status != 200:
        raise CheckFailure("local webhook inspection endpoint did not return 200")
    try:
        payload = json.loads(response.body)
    except (TypeError, ValueError):
        raise CheckFailure("local webhook inspection response was not JSON")
    live = payload.get("live") if isinstance(payload, dict) else None
    if not isinstance(live, list):
        raise CheckFailure("local webhook inspection response has no live list")

    expected = _expected_webhooks(config.warehouse_public_base_url)
    actual: dict[str, str] = {}
    for item in live:
        if not isinstance(item, dict):
            raise CheckFailure("local webhook inspection response contains an invalid entry")
        topic = item.get("topic")
        url = item.get("url")
        if not isinstance(topic, str) or not isinstance(url, str) or topic in actual:
            raise CheckFailure("live webhook subscriptions are not uniquely shaped")
        actual[topic] = url

    if actual != expected:
        raise CheckFailure("live product webhooks are not the exact CREATE/UPDATE/DELETE set")
    return "exact CREATE, UPDATE, and DELETE product webhooks are registered"


def _check_support_health(config: SafetyConfig, deps: SafetyDependencies) -> str:
    url = _public_endpoint(config.support_public_base_url, "/health")
    response = deps.http_get(url, config.timeout_seconds)
    if response.status != 200:
        raise CheckFailure("support health endpoint did not return 200")
    return "support health endpoint returns 200"


def run_monitor(
    config: SafetyConfig | None = None,
    deps: SafetyDependencies | None = None,
) -> SafetyReport:
    """Run every independent check and return a fail-closed report."""

    config = config or SafetyConfig()
    deps = deps or default_dependencies()
    checks = (
        _run_check("services.active", lambda: _check_services(config, deps)),
        _run_check("caddy.config_manifest", lambda: _check_caddy_manifest(config, deps)),
        _run_check(
            "warehouse.source_manifest",
            lambda: _check_warehouse_source_manifest(config, deps),
        ),
        _run_check("warehouse.port_loopback", lambda: _check_port_loopback(config, deps)),
        _run_check("shopify.write_guard_source", lambda: _check_source_guard(config, deps)),
        _run_check(
            "warehouse.process_source",
            lambda: _check_process_loaded_source(config, deps),
        ),
        _run_check("shopify.write_flags_runtime", lambda: _check_runtime_flags(config, deps)),
        _run_check("warehouse.https_route", lambda: _check_warehouse_https(config, deps)),
        _run_check("shopify.webhooks_exact", lambda: _check_webhooks(config, deps)),
        _run_check("support.health", lambda: _check_support_health(config, deps)),
    )
    return SafetyReport(checks=checks)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run read-only Buttons Bebe Shopify content-write safety checks.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="emit one machine-readable JSON report")
    parser.add_argument("--warehouse-service", default=SafetyConfig.warehouse_service)
    parser.add_argument("--caddy-service", default=SafetyConfig.caddy_service)
    parser.add_argument("--port", type=int, default=SafetyConfig.warehouse_port)
    parser.add_argument("--source-path", type=Path, default=SafetyConfig.shopify_source_path)
    parser.add_argument("--webhook-url", default=SafetyConfig.webhook_endpoint)
    parser.add_argument("--warehouse-url", default=SafetyConfig.warehouse_public_base_url)
    parser.add_argument("--support-url", default=SafetyConfig.support_public_base_url)
    parser.add_argument("--caddy-manifest", type=Path, default=SafetyConfig.caddy_manifest_path)
    parser.add_argument(
        "--warehouse-source-manifest",
        type=Path,
        default=SafetyConfig.warehouse_source_manifest_path,
    )
    parser.add_argument("--timeout", type=float, default=SafetyConfig.timeout_seconds)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = SafetyConfig(
        warehouse_service=args.warehouse_service,
        caddy_service=args.caddy_service,
        warehouse_port=args.port,
        shopify_source_path=args.source_path,
        webhook_endpoint=args.webhook_url,
        warehouse_public_base_url=args.warehouse_url,
        support_public_base_url=args.support_url,
        caddy_manifest_path=args.caddy_manifest,
        warehouse_source_manifest_path=args.warehouse_source_manifest,
        timeout_seconds=args.timeout,
    )
    report = run_monitor(config=config)
    if args.json:
        print(json.dumps(report.as_dict(), sort_keys=True))
    else:
        for check in report.checks:
            print(f"{check.status} {check.name}: {check.detail}")
        print("Safety monitor: " + ("PASS" if report.ok else "FAIL"))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
