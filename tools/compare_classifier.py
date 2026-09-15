#!/usr/bin/env python3
"""Offline parity harness for the pre-split and current classifiers.

The T-FIX-3 split is intended to be structural.  This harness makes that
claim executable without importing both implementations into one interpreter:

* the parent creates deterministic, synthetic payloads;
* each classifier is loaded in a fresh child process;
* only ``(priority, sensitive, should_notify_owner)`` crosses the process
  boundary; and
* a shim supplied as ``--old`` is replaced with the newest non-shim
  ``processor/classifier.py`` found in local Git history (pre-rename path;
  the working-tree shim is now ``processor/classifier_shim.py``).

The Git lookup is deliberately limited to local ``log`` and ``show`` reads.
The classifier workers receive a scrubbed environment, run with ``DEMO_MODE=1``
so dotenv loaders cannot read a production ``.env``, and install audit hooks
that reject network, subprocess, and credential-file access.  This script is
not a live or VPS test.

The plan's command works from the repository root::

    python tools/compare_classifier.py \
      --old processor/classifier_shim.py \
      --new processor/classifier/__init__.py \
      --samples 10000
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


DEFAULT_SAMPLES = 10_000
DEFAULT_SEED = 20260823
_WORKER_FLAG = "--worker"
_SHIM_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+classifier(?:\.[A-Za-z_]\w*)*\s+import|"
    r"import\s+classifier(?:\s+as|\s*$))",
    re.MULTILINE,
)


class ParityError(RuntimeError):
    """An operational or contract failure in the parity harness."""


class ParityMismatch(ParityError):
    """The old and new classifiers returned different verdict tuples."""


@dataclass(frozen=True)
class SourceSpec:
    """A classifier source file and the provenance shown in the report."""

    path: Path
    origin: str


@dataclass(frozen=True)
class ComparisonResult:
    """Successful comparison metadata."""

    samples: int
    seed: int
    old_origin: str
    new_path: Path


# Keep the corpus small and explicit.  The seeded selection and stable index
# make the exact payload stream reproducible across Python versions; no Faker,
# clock, network, store, or customer data is involved.
_MESSAGE_CASES: tuple[str, ...] = (
    "I need a refund for order #{order}.",
    "I am filing a chargeback with my bank for order #{order}.",
    "The item arrived damaged and the seam is torn.",
    "My order never arrived; tracking stopped moving.",
    "You sent a different item than the one I ordered.",
    "This is a scam and you stole my money.",
    "I want to speak to a manager right now.",
    "Please change my shipping address before dispatch.",
    "I need the parcel by Friday because of a deadline.",
    "Can you tell me which size fits a two-year-old?",
    "What fabric is this romper made from?",
    "Where is my order? It has been waiting for two weeks.",
    "Could you resend the discount code from my email?",
    "I would like to exchange this for another size.",
    "The package arrived today and everything is lovely, thank you!",
    "Do you ship to Canada and how long does delivery take?",
    "I am furious about this unacceptable service!!!",
    "THIS IS A JOKE WHERE IS MY ORDER",
    "I received the wrong colour, not what I ordered.",
    "There is a hole in the sleeve and a stain on the collar.",
    "I changed my mind and want my money back.",
    "Is the tear-away label meant to be removed?",
    "Can I order the romper without the bow?",
    "Please ignore this test message; I only need product dimensions.",
    "Were this missing from my order?",
    "Were that lost in my parcel?",
)

_SUBJECT_CASES: tuple[str, ...] = (
    "Order question",
    "Where is my parcel?",
    "Refund request",
    "Sizing help",
    "",
    "FLASH SALE!!!",
    "Support follow-up",
)

_INTENT_CASES: tuple[Any, ...] = (
    [],
    [{"name": "refund"}],
    [{"name": "shipping"}],
    [{"name": "order_status"}],
    ["chargeback"],
    [{"name": "sizing"}, {"name": "product_question"}],
    [{"name": "cancellation"}],
)

_KB_CASES: tuple[Any, ...] = (
    None,
    [],
    [{"sensitive": False, "source": "synthetic-kb"}],
    [{"sensitive": True, "source": "synthetic-kb"}],
    [{"sensitive": False}, {"sensitive": True}],
)

_NOISE_CASES: tuple[str, ...] = (
    "",
    " Please help.",
    "\n\nThanks for reaching out.\n\n",
    "\n> Previous synthetic message\n",
    " — synthetic parity probe",
    "\nI have checked the order details.",
)


def synthetic_payloads(samples: int, seed: int = DEFAULT_SEED) -> list[tuple[dict[str, Any], Any]]:
    """Return a deterministic list of ``(payload, kb_results)`` pairs."""

    if samples <= 0:
        raise ValueError("samples must be a positive integer")

    rng = random.Random(seed)
    payloads: list[tuple[dict[str, Any], Any]] = []
    for index in range(samples):
        message_template = _MESSAGE_CASES[index % len(_MESSAGE_CASES)]
        message = message_template.format(order=10_000 + index)

        # These mutations are deterministic but exercise the same input-shape
        # edges that the split must preserve: smart punctuation, quoted text,
        # boilerplate, casing, and harmless extra context.
        if index % 17 == 0:
            message = message.replace("'", "’")
        if index % 19 == 0:
            message = message.upper()
        message += _NOISE_CASES[rng.randrange(len(_NOISE_CASES))]

        subject = _SUBJECT_CASES[rng.randrange(len(_SUBJECT_CASES))]
        intents = _INTENT_CASES[index % len(_INTENT_CASES)]
        kb_results = _KB_CASES[index % len(_KB_CASES)]
        payload = {
            "ticket_id": f"parity-{index:05d}",
            "message_id": f"synthetic-message-{index:05d}",
            "ticket_subject": subject,
            "message_text": message,
            "customer_email": f"customer-{index:05d}@example.test",
            "intents": intents,
            "synthetic": True,
        }
        payloads.append((payload, kb_results))
    return payloads


def _looks_like_shim(source: str) -> bool:
    """Recognize the temporary ``classifier.py`` compatibility shim."""

    implementation_markers = (
        "_MAIN_IMMEDIATE_KEYWORDS",
        "_SELFTEST_CASES",
        "def _selftest(",
        "def _find_matches(",
    )
    if any(marker in source for marker in implementation_markers):
        return False
    if _SHIM_IMPORT_RE.search(source):
        return True
    lines = [line for line in source.splitlines() if line.strip()]
    return len(lines) <= 24 and "def classify(" not in source


def _git_environment() -> dict[str, str]:
    """Return an environment for local history reads, with no credentials."""

    return {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }


def _run_git(repo_root: Path, args: Sequence[str]) -> str:
    """Run a bounded local ``git log``/``git show`` read."""

    command = ["git", *args]
    completed = subprocess.run(
        command,
        cwd=str(repo_root),
        env=_git_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or "no diagnostic"
        raise ParityError(f"local Git command failed ({' '.join(command)}): {detail}")
    return completed.stdout


def _find_repo_root(start: Path) -> Path:
    """Find the nearest checkout root without invoking Git."""

    start = start.resolve()
    candidates = (start, *start.parents)
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate
    raise ParityError(f"could not find a repository root above {start}")


def _historical_classifier(
    shim_path: Path,
    repo_root: Path,
    materialize_dir: Path,
) -> SourceSpec:
    """Find the newest non-shim classifier source in local history."""

    try:
        relative_path = shim_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise ParityError(
            f"--old shim must be inside --repo for local history lookup: {shim_path}"
        ) from exc

    # Follow renames so the pre-rename processor/classifier.py history is
    # found even when the working-tree shim has a new name.
    commits = _run_git(
        repo_root,
        ["log", "--format=%H", "--follow", "--find-renames=40%", "--", relative_path],
    ).splitlines()
    if not commits:
        raise ParityError(f"no local Git history found for {relative_path}")

    checked_paths = [relative_path]
    parent = str(Path(relative_path).parent)
    for candidate in (f"{parent}/classifier.py", "processor/classifier.py"):
        if candidate not in checked_paths:
            checked_paths.append(candidate)
    for commit in commits:
        source = None
        for candidate in checked_paths:
            try:
                source = _run_git(
                    repo_root,
                    ["show", "--no-ext-diff", "--no-textconv", f"{commit}:{candidate}"],
                )
                if source:
                    break
            except ParityError:
                # A rename or deletion can leave a path in the log that is not
                # present in that particular tree. Continue to the next revision.
                continue
        if not source:
            continue
        if _looks_like_shim(source) or "def classify(" not in source:
            continue
        materialized = materialize_dir / "classifier.py"
        materialized.write_text(source, encoding="utf-8")
        return SourceSpec(materialized, f"Git {commit[:12]}:{relative_path}")

    raise ParityError(
        f"local Git history for {relative_path} has no pre-split classifier implementation"
    )


def resolve_old_source(old_path: Path, repo_root: Path, materialize_dir: Path) -> SourceSpec:
    """Use ``old_path`` directly, or resolve a shim through local history."""

    old_path = old_path.resolve()
    if old_path.is_file():
        source = old_path.read_text(encoding="utf-8")
        if not _looks_like_shim(source):
            return SourceSpec(old_path, f"working tree: {old_path}")
    return _historical_classifier(old_path, repo_root, materialize_dir)


def _safe_worker_environment(repo_root: Path) -> dict[str, str]:
    """Build a minimal, demo-bound environment for classifier workers."""

    processor = repo_root / "processor"
    webhook_src = repo_root / "webhook" / "src"
    demo_db = repo_root / "webhook" / "data" / "cute-things-demo-webhook.db"
    return {
        "PATH": os.defpath,
        "PYTHONPATH": os.pathsep.join((str(processor), str(webhook_src))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        # Both config loaders skip dotenv in demo mode. The values below are
        # harmless local-demo placeholders for any import-time settings check.
        "DEMO_MODE": "1",
        "SHOPIFY_SHOP": "yznyc1-ez.myshopify.com",
        "GORGIAS_SUBDOMAIN": "cute-things-demo",
        "GORGIAS_BASE_URL": "http://127.0.0.1:8190",
        "WEBHOOK_DB_PATH": str(demo_db),
        "KB_MCP_URL": "http://127.0.0.1:8177/mcp",
        "HERMES_PROFILE": "cutethingsdemo",
        "HERMES_TOOLSETS": "buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias",
        "HERMES_REWRITE_TOOLSETS": "todo",
        "HERMES_IGNORE_RULES": "1",
        "HERMES_SKIP_APPROVAL": "0",
        "SUPPORT_STORE_NAME": "Cute Things",
        "WEBHOOK_HOST": "127.0.0.1",
        "WEBHOOK_PORT": "8100",
        "FEEDBACK_KB_ROOT": str(repo_root / "demo" / "data" / "kb"),
        "WEBHOOK_SECRET": "offline-parity-placeholder",
        "LOG_LEVEL": "CRITICAL",
        "LOG_FORMAT": "json",
    }


def _install_worker_guards() -> None:
    """Reject network/process/credential access from a classifier worker."""

    protected_markers = (
        ".env",
        "/.hermes/",
        "auth.json",
        "credentials",
        ".sqlite",
        ".db",
    )
    blocked_events = {
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.sendmsg",
        "socket.sendto",
        "subprocess.Popen",
        "os.system",
    }

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event in blocked_events:
            raise PermissionError(f"parity worker blocked {event}")
        if event == "open" and args and isinstance(args[0], str):
            candidate = args[0].replace("\\", "/").lower()
            if any(marker in candidate for marker in protected_markers):
                raise PermissionError("parity worker blocked protected-file access")

    sys.addaudithook(audit)


def _load_classifier(module_path: Path, project_root: Path) -> Any:
    """Load one classifier as the sole ``classifier`` module in this worker."""

    module_path = module_path.resolve()
    search_paths = [module_path.parent, project_root / "processor", project_root / "webhook" / "src"]
    if module_path.name == "__init__.py":
        search_paths.insert(0, module_path.parent.parent)
    for search_path in reversed(search_paths):
        value = str(search_path)
        if value not in sys.path:
            sys.path.insert(0, value)

    if module_path.name == "__init__.py":
        spec = importlib.util.spec_from_file_location(
            "classifier",
            module_path,
            submodule_search_locations=[str(module_path.parent)],
        )
    else:
        spec = importlib.util.spec_from_file_location("classifier", module_path)
    if spec is None or spec.loader is None:
        raise ParityError(f"could not create an import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["classifier"] = module
    spec.loader.exec_module(module)
    classify = getattr(module, "classify", None)
    if not callable(classify):
        raise ParityError(f"{module_path} does not expose callable classify()")
    return classify


def _verdict(result: Any) -> list[Any]:
    """Project and validate the exact parity contract."""

    if not isinstance(result, dict):
        raise ParityError(f"classify() returned {type(result).__name__}, expected dict")
    required = ("priority", "sensitive", "should_notify_owner")
    missing = [key for key in required if key not in result]
    if missing:
        raise ParityError(f"classify() result is missing required keys: {', '.join(missing)}")
    priority = result["priority"]
    sensitive = result["sensitive"]
    notify = result["should_notify_owner"]
    if not isinstance(priority, str) or type(sensitive) is not bool or type(notify) is not bool:
        raise ParityError(
            "classify() parity tuple must be (str, bool, bool), got "
            f"({type(priority).__name__}, {type(sensitive).__name__}, {type(notify).__name__})"
        )
    return [priority, sensitive, notify]


def _worker_main(module_path: Path, project_root: Path) -> int:
    """Read JSONL requests and emit one validated tuple per request."""

    _install_worker_guards()
    # Classification logs are not part of the protocol and may contain the
    # synthetic message text. Disable them before importing either module.
    import logging

    logging.disable(logging.CRITICAL)
    classify = _load_classifier(module_path, project_root)
    for index, line in enumerate(sys.stdin):
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            result = classify(request["payload"], request.get("kb_results"))
            response = {"index": index, "verdict": _verdict(result)}
        except Exception as exc:  # pragma: no cover - exercised through parent process
            response = {"index": index, "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(response, ensure_ascii=False), flush=True)
            return 2
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


def _worker_command(module_path: Path, project_root: Path) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        _WORKER_FLAG,
        "--module-path",
        str(module_path),
        "--project-root",
        str(project_root),
    ]


def _run_worker(
    module_path: Path,
    project_root: Path,
    payloads: Sequence[tuple[dict[str, Any], Any]],
    timeout_seconds: float,
) -> list[tuple[str, bool, bool]]:
    """Run one classifier child and decode its tuple-only JSONL output."""

    request_text = "".join(
        json.dumps(
            {"payload": payload, "kb_results": kb_results},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
        for payload, kb_results in payloads
    )
    try:
        completed = subprocess.run(
            _worker_command(module_path, project_root),
            cwd=str(project_root),
            env=_safe_worker_environment(project_root),
            input=request_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParityError(f"classifier worker timed out after {timeout_seconds:g}s: {module_path}") from exc

    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        if len(detail) > 4_000:
            detail = detail[-4_000:]
        raise ParityError(
            f"classifier worker failed for {module_path} (exit {completed.returncode}): {detail}"
        )

    responses = completed.stdout.splitlines()
    if len(responses) != len(payloads):
        raise ParityError(
            f"classifier worker returned {len(responses)} rows for {len(payloads)} payloads: "
            f"{module_path}"
        )

    verdicts: list[tuple[str, bool, bool]] = []
    for expected_index, line in enumerate(responses):
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ParityError(f"invalid worker JSON at row {expected_index}: {line!r}") from exc
        if response.get("index") != expected_index:
            raise ParityError(
                f"worker row index {response.get('index')!r} does not match {expected_index}"
            )
        if "error" in response:
            raise ParityError(f"classifier worker rejected row {expected_index}: {response['error']}")
        tuple_value = response.get("verdict")
        if (
            not isinstance(tuple_value, list)
            or len(tuple_value) != 3
            or not isinstance(tuple_value[0], str)
            or type(tuple_value[1]) is not bool
            or type(tuple_value[2]) is not bool
        ):
            raise ParityError(f"invalid parity tuple at worker row {expected_index}: {tuple_value!r}")
        verdicts.append((tuple_value[0], tuple_value[1], tuple_value[2]))
    return verdicts


def compare(
    old_path: Path,
    new_path: Path,
    *,
    repo_root: Path,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    timeout_seconds: float = 300.0,
) -> ComparisonResult:
    """Compare two classifier paths and raise on the first parity failure."""

    if old_path.resolve() == new_path.resolve():
        raise ParityError("--old and --new must identify different classifier paths")
    if not new_path.is_file():
        raise ParityError(f"--new classifier path does not exist: {new_path}")
    if samples <= 0:
        raise ParityError("--samples must be a positive integer")
    if timeout_seconds <= 0:
        raise ParityError("--timeout-seconds must be positive")

    repo_root = repo_root.resolve()
    payloads = synthetic_payloads(samples, seed)
    with tempfile.TemporaryDirectory(prefix="classifier-parity-") as temp_name:
        old_source = resolve_old_source(old_path, repo_root, Path(temp_name))
        old_verdicts = _run_worker(
            old_source.path, repo_root, payloads, timeout_seconds
        )
        new_verdicts = _run_worker(
            new_path.resolve(), repo_root, payloads, timeout_seconds
        )

    mismatches: list[str] = []
    for index, (old_verdict, new_verdict) in enumerate(zip(old_verdicts, new_verdicts)):
        if old_verdict == new_verdict:
            continue
        payload, kb_results = payloads[index]
        mismatches.append(
            json.dumps(
                {
                    "index": index,
                    "old": old_verdict,
                    "new": new_verdict,
                    "message_text": payload.get("message_text"),
                    "ticket_subject": payload.get("ticket_subject"),
                    "intents": payload.get("intents"),
                    "kb_sensitive": any(
                        isinstance(item, dict) and item.get("sensitive")
                        for item in (kb_results or [])
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if len(mismatches) >= 20:
            break
    if mismatches:
        raise ParityMismatch(
            f"classifier parity failed: {len(mismatches)} mismatch(es) shown; "
            f"old={old_source.origin}; new={new_path}\n" + "\n".join(mismatches)
        )
    return ComparisonResult(samples, seed, old_source.origin, new_path.resolve())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare old and new classifier verdict tuples offline.",
        epilog=(
            "The default corpus is deterministic and synthetic. A shim passed "
            "as --old is resolved from local Git history; no network or VPS "
            "access is performed."
        ),
    )
    parser.add_argument("--old", help="old classifier file, usually processor/classifier_shim.py")
    parser.add_argument("--new", help="new classifier package entry point")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--repo", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(_WORKER_FLAG, action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--module-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--project-root", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.worker:
        if args.module_path is None or args.project_root is None:
            raise SystemExit("worker requires --module-path and --project-root")
        return _worker_main(args.module_path, args.project_root)
    if not args.old or not args.new:
        _parser().error("--old and --new are required")

    old_path = Path(args.old).resolve()
    new_path = Path(args.new).resolve()
    repo_root = args.repo.resolve() if args.repo else _find_repo_root(old_path.parent)
    try:
        result = compare(
            old_path,
            new_path,
            repo_root=repo_root,
            samples=args.samples,
            seed=args.seed,
            timeout_seconds=args.timeout_seconds,
        )
    except ParityError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "classifier parity PASS: "
        f"{result.samples:,} deterministic synthetic payloads; "
        f"seed={result.seed}; old={result.old_origin}; new={result.new_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
