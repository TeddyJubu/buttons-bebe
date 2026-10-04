"""Source-bound run receipts and sanitized human-review receipts for manual QA.

Nothing here runs a model or invents a verdict. A run receipt binds one full
suite run to working-tree content; a reviewer writes per-ID verdicts; the
combined receipt certifies only what those verdicts and hashes support.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SUITES = {"core": ("testing/scenarios.json", 48), "reliability": ("testing/reliability-scenarios.json", 10)}
VERDICTS = ("PASS", "NEEDS_WORK", "FAIL", "pending")
# First-party generation, queue, human-review and KB-search behavior plus the
# approved policy text, QA adapters and catalogs.
SOURCE_GLOBS = (
    "processor/**/*.py", "processor/**/*.yaml", "processor/**/*.json", "processor/*.sh",
    "processor/pyproject.toml", "processor/uv.lock",
    "webhook/src/**/*.py", "webhook/main.py", "webhook/run.sh", "webhook/pyproject.toml", "webhook/uv.lock",
    "console-src/index.html", "console-src/inbox/*.py", "console-src/inbox/requirements.lock",
    "console-src/inbox2/*.py", "console-src/inbox2/*.js", "console-src/inbox2/index.html",
    "kb/scripts/*.py", "kb/run_mcp.sh", "kb/requirements.lock",
    "kb/policies/*.md", "kb/faq/*.md", "kb/intents/*.md",
    "tools/*mcp*.py", "tools/_common.py", "tools/gorgias_content.py", "tools/run-gorgias.sh", "tools/run-redo.sh",
    "tools/requirements.lock", "tools/runtime-constraints.txt", "feedback/pii.py", "feedback/learning_paths.py",
    "hermes/SOUL.md", "hermes/skills/buttonsbebe/**/*",
    "testing/qa_*.py", "testing/run_live_tests.py", "testing/requirements-qa.lock",
    "testing/scenarios.json", "testing/reliability-scenarios.json",
)
REQUIRED = ("processor/hermes_runner/prompt.py", "processor/draft_cleaner.py", "processor/orchestrator.py",
            "webhook/src/bb_webhook/app.py", "kb/scripts/search_kb.py",
            "testing/scenarios.json", "testing/reliability-scenarios.json")
# Tests, runtime state, unapproved lessons, secrets and dependency copies.
SKIP_PARTS = {"__pycache__", ".git", ".venv", "venv", "node_modules", "site-packages",
              "tests", "test", "data", "learned", "notices"}
SKIP_SUFFIXES = (".db", ".sqlite", ".sqlite3", "-wal", "-shm", ".log", ".pyc")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _git(repo: Path):
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return head, bool(status.strip())


def source_fingerprint(repo: Path = REPO) -> dict:
    """Hash working-tree content, not just the commit label."""
    repo = repo.resolve()
    for name in REQUIRED:
        if not (repo / name).is_file():
            raise ValueError(f"Missing fingerprinted source: {name}")
    files = {}
    for pattern in SOURCE_GLOBS:
        for path in repo.glob(pattern):
            relative = path.relative_to(repo)
            if (path.is_file() and not SKIP_PARTS & set(relative.parts) and not path.name.endswith(SKIP_SUFFIXES)
                    and not path.name.startswith(("test_", ".env"))):
                files[relative.as_posix()] = sha256_bytes(path.read_bytes())
    head, dirty = _git(repo)
    return {"head": head, "dirty": dirty, "files": dict(sorted(files.items())), "sha256": digest(files)}


def hermes_identity(executable: Path, source: Path) -> dict:
    source = source.resolve()
    files = {path.relative_to(source).as_posix(): sha256_bytes(path.read_bytes())
             for path in sorted(source.rglob("*.py")) if not SKIP_PARTS & set(path.relative_to(source).parts)}
    if not files:
        raise ValueError("Hermes source has no Python files")
    return {"executable_sha256": sha256_bytes(executable.read_bytes()), "source_sha256": digest(files), "source_files": len(files)}


def catalog(suite: str, repo: Path = REPO) -> tuple[str, list[str]]:
    name, expected = SUITES[suite]
    raw = (repo / name).read_bytes()
    ids = [item.get("id") for item in json.loads(raw)]
    if len(ids) != expected or len(set(ids)) != expected or not all(isinstance(i, str) and i for i in ids):
        raise ValueError(f"{name} must hold exactly {expected} unique scenario IDs")
    return sha256_bytes(raw), ids


def kb_snapshot(kb_mode, product_manifest=None, product_manifest_sha256=None,
                policy_overlay=None, policy_overlay_sha256=None, repo: Path = REPO) -> dict:
    """Identity of every approved document the run may serve, re-verified on each call.

    Repository policies are in the source fingerprint; the product manifest
    pins per-file content and the overlay pins its own bytes.
    """
    if product_manifest is not None:
        from qa_catalog import load_manifest
        load_manifest(product_manifest, product_manifest_sha256)
    if policy_overlay is not None:
        from qa_policy_overlay import load_overlay
        load_overlay(policy_overlay, policy_overlay_sha256, repo)
    identity = {"mode": kb_mode, "policy_overlay_sha256": policy_overlay_sha256,
                "product_manifest_sha256": product_manifest_sha256}
    return {**identity, "sha256": digest(identity)}


def observed_kb(results: list) -> list:
    """Sorted unique (file, heading, content hash) for every KB section shown to the model."""
    return sorted({(name, heading, value) for result in results for call in result.get("tool_calls", [])
                   if call.get("tool") == "kb_projection"
                   for name, heading, value in zip(call.get("files", []), call.get("headings", []),
                                                   call.get("content_sha256", []))})


def _single_content(observed, message: str) -> None:
    sections = {}
    for name, heading, value in observed:
        if sections.setdefault((name, heading), value) != value:
            raise ValueError(f"{message}: {name} {heading!r}")


def run_receipt(suite, ids, results, before, after, repo: Path = REPO) -> dict:
    catalog_sha256, all_ids = catalog(suite, repo)
    captured = [result["id"] for result in results]
    return {"schema": 2, "suite": suite, "catalog_sha256": catalog_sha256, "ids": captured,
            "complete": captured == all_ids and list(ids) == all_ids,
            "bindings": before, "bindings_after_sha256": digest(after), "kb_observed": observed_kb(results)}


def check_run_integrity(run: dict) -> None:
    """Reject evidence whose source, Hermes or approved KB content moved during the run."""
    if digest(run["bindings"]) != run.get("bindings_after_sha256"):
        raise ValueError(f"{run['suite']}: source, Hermes or approved KB snapshot changed during the run")
    _single_content(run["kb_observed"], f"{run['suite']}: KB content changed during the run")


def judgment_template(run_path: Path) -> dict:
    run = json.loads(run_path.read_bytes())
    return {"schema": 2, "suite": run["suite"], "run_sha256": sha256_bytes(run_path.read_bytes()),
            "verdicts": {scenario_id: "pending" for scenario_id in run["ids"]}, "blocking_defects": []}


def _suite_review(suite: str, run_path: Path, judgments_path: Path, repo: Path) -> tuple[dict, dict, int]:
    raw = run_path.read_bytes()
    run, judgments = json.loads(raw), json.loads(judgments_path.read_bytes())
    catalog_sha256, all_ids = catalog(suite, repo)
    if run.get("schema") != 2 or run.get("suite") != suite or judgments.get("suite") != suite:
        raise ValueError(f"{suite}: run or judgments are not a schema 2 {suite} review")
    if not run.get("complete") or run.get("ids") != all_ids:
        raise ValueError(f"{suite}: partial run; every catalog ID must be captured")
    if run.get("catalog_sha256") != catalog_sha256:
        raise ValueError(f"{suite}: catalog changed since the run")
    check_run_integrity(run)
    if judgments.get("run_sha256") != sha256_bytes(raw):
        raise ValueError(f"{suite}: judgments are for a different run")
    verdicts = judgments.get("verdicts")
    if not isinstance(verdicts, dict) or set(verdicts) != set(all_ids) or any(v not in VERDICTS for v in verdicts.values()):
        raise ValueError(f"{suite}: one PASS, NEEDS_WORK, FAIL or pending verdict is required per ID")
    defects = judgments.get("blocking_defects")
    if not isinstance(defects, list):
        raise ValueError(f"{suite}: blocking_defects must be a list")
    summary = {"catalog_sha256": catalog_sha256, "run_sha256": sha256_bytes(raw),
               "kb_observed_documents": len(run["kb_observed"]), "kb_observed_sha256": digest(run["kb_observed"]),
               "verdicts": {i: verdicts[i] for i in all_ids},
               "counts": {v: list(verdicts.values()).count(v) for v in VERDICTS}}
    return summary, run, len(defects)


def build_receipt(core_run, core_judgments, reliability_run, reliability_judgments, repo: Path = REPO) -> dict:
    """Combine both full suites; defect text and model output stay private."""
    core, core_meta, core_defects = _suite_review("core", core_run, core_judgments, repo)
    reliability, rel_meta, rel_defects = _suite_review("reliability", reliability_run, reliability_judgments, repo)
    bindings = core_meta["bindings"]
    for name, label in (("source", "source"), ("hermes", "Hermes"), ("kb_snapshot", "approved KB snapshot")):
        if digest(bindings[name]) != digest(rel_meta["bindings"][name]):
            raise ValueError(f"Core and reliability runs used a different {label}")
    _single_content(sorted({*map(tuple, core_meta["kb_observed"]), *map(tuple, rel_meta["kb_observed"])}),
                    "KB content differs between core and reliability runs")
    receipt = {"schema": 2, **bindings, "suites": {"core": core, "reliability": reliability},
               "blocking_defects": core_defects + rel_defects}
    return {**receipt, **review_state(receipt)}


def review_state(receipt: dict) -> dict:
    verdicts = [v for suite in receipt["suites"].values() for v in suite["verdicts"].values()]
    complete = "pending" not in verdicts
    return {"review_complete": complete,
            "release_passed": complete and all(v == "PASS" for v in verdicts) and receipt["blocking_defects"] == 0}


def check_receipt(receipt: dict, repo: Path = REPO, *, release: bool = True) -> dict:
    """Recompute state against the current tree; never trust stored booleans."""
    if receipt.get("schema") != 2 or set(receipt.get("suites", {})) != set(SUITES):
        raise ValueError("Receipt must cover exactly the core and reliability suites")
    current = source_fingerprint(repo)
    if receipt["source"]["sha256"] != current["sha256"]:
        raise ValueError("Stale receipt: source content differs from the reviewed run")
    for suite, summary in receipt["suites"].items():
        catalog_sha256, all_ids = catalog(suite, repo)
        if summary["catalog_sha256"] != catalog_sha256 or list(summary["verdicts"]) != all_ids:
            raise ValueError(f"{suite}: receipt does not cover the current full catalog")
        if any(v not in VERDICTS for v in summary["verdicts"].values()):
            raise ValueError(f"{suite}: unknown verdict")
    state = review_state(receipt)
    if receipt.get("review_complete") != state["review_complete"] or receipt.get("release_passed") != state["release_passed"]:
        raise ValueError("Receipt state does not match its verdicts")
    if not state["review_complete"]:
        raise ValueError("Review incomplete: pending verdicts remain")
    if release and not state["release_passed"]:
        raise ValueError("Release not passed: NEEDS_WORK, FAIL or blocking defects recorded")
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template", help="Write all-pending judgments for a run")
    template.add_argument("--run", type=Path, required=True)
    template.add_argument("--output", type=Path, required=True)
    build = commands.add_parser("build", help="Combine reviewed core and reliability runs")
    for name in ("core-run", "core-judgments", "reliability-run", "reliability-judgments", "output"):
        build.add_argument(f"--{name}", type=Path, required=True)
    check = commands.add_parser("check", help="Validate a receipt against this checkout")
    check.add_argument("receipt", type=Path)
    check.add_argument("--review-only", action="store_true", help="Accept a complete review that did not pass")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            state = check_receipt(json.loads(args.receipt.read_bytes()), release=not args.review_only)
            print(json.dumps(state))
            return 0
        value = judgment_template(args.run) if args.command == "template" else build_receipt(
            args.core_run, args.core_judgments, args.reliability_run, args.reliability_judgments)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
        print(json.dumps({k: value[k] for k in ("review_complete", "release_passed")} if args.command == "build" else {"pending": len(value["verdicts"])}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"QA receipt rejected: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
