from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SUITES = {"core": ("testing/scenarios.json", 48), "reliability": ("testing/reliability-scenarios.json", 10)}
VERDICTS = ("PASS", "NEEDS_WORK", "FAIL", "pending")
RECEIPT_SCHEMA = 4
# Production Hermes reads these from ~/.hermes (HERMES_IGNORE_RULES unset). QA seeds exactly these bytes.
INSTRUCTION_FILES = ("SOUL.md", "skills/buttonsbebe/support-agent/SKILL.md", "skills/buttonsbebe/ticket-processor/SKILL.md")
SOURCE_FINGERPRINT_GROUPS: dict[str, tuple[str, ...]] = {
    "intake": ("intake/__init__.py", "intake/message_content.py"),
    "processor": (
        "processor/**/*.py", "processor/**/*.yaml", "processor/**/*.json", "processor/*.sh",
        "processor/pyproject.toml", "processor/uv.lock",
    ),
    "webhook": (
        "webhook/src/**/*.py", "webhook/main.py", "webhook/run.sh", "webhook/pyproject.toml", "webhook/uv.lock",
    ),
    "console_inbox": (
        "console-src/index.html", "console-src/inbox/*.py", "console-src/inbox/requirements.lock",
        "console-src/inbox2/*.py", "console-src/inbox2/*.js", "console-src/inbox2/index.html",
    ),
    "knowledge_base": (
        "kb/scripts/*.py", "kb/run_mcp.sh", "kb/requirements.lock",
        "kb/policies/*.md", "kb/faq/*.md", "kb/intents/*.md",
    ),
    "tools": (
        "tools/*mcp*.py", "tools/_common.py", "tools/gorgias_content.py", "tools/run-gorgias.sh", "tools/run-redo.sh",
        "tools/requirements.lock", "tools/runtime-constraints.txt",
    ),
    "feedback": ("feedback/pii.py", "feedback/learning_paths.py"),
    "hermes": ("hermes/SOUL.md", "hermes/skills/buttonsbebe/**/*"),
    "qa": (
        "testing/qa_*.py", "testing/run_live_tests.py", "testing/requirements-qa.lock",
        "testing/scenarios.json", "testing/reliability-scenarios.json",
    ),
}
REQUIRED = ("processor/hermes_runner/prompt.py", "processor/draft_cleaner.py", "processor/orchestrator.py",
            "intake/message_content.py",
            "webhook/src/bb_webhook/app.py", "kb/scripts/search_kb.py",
            "testing/scenarios.json", "testing/reliability-scenarios.json")
SOURCE_FINGERPRINT_EXCLUDED_PATH_PARTS = frozenset({"__pycache__", ".git", ".venv", "venv", "node_modules", "site-packages",
                                                      "tests", "test", "data", "learned", "notices"})
SOURCE_FINGERPRINT_EXCLUDED_SUFFIXES = (".db", ".sqlite", ".sqlite3", "-wal", "-shm", ".log", ".pyc")


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
    repo = repo.resolve()
    for name in REQUIRED:
        if not (repo / name).is_file():
            raise ValueError(f"Missing fingerprinted source: {name}")
    files = {}
    for source_patterns in SOURCE_FINGERPRINT_GROUPS.values():
        for pattern in source_patterns:
            for path in repo.glob(pattern):
                relative = path.relative_to(repo)
                if (path.is_file()
                        and not SOURCE_FINGERPRINT_EXCLUDED_PATH_PARTS.intersection(relative.parts)
                        and not path.name.endswith(SOURCE_FINGERPRINT_EXCLUDED_SUFFIXES)
                        and not path.name.startswith(("test_", ".env"))):
                    files[relative.as_posix()] = sha256_bytes(path.read_bytes())
    head, dirty = _git(repo)
    return {"head": head, "dirty": dirty, "files": dict(sorted(files.items())), "sha256": digest(files)}


def hermes_identity(launch: list[str], source: Path) -> dict:
    source = source.resolve()
    files = {path.relative_to(source).as_posix(): sha256_bytes(path.read_bytes())
             for path in sorted(source.rglob("*.py"))
             if not SOURCE_FINGERPRINT_EXCLUDED_PATH_PARTS.intersection(path.relative_to(source).parts)}
    if not files:
        raise ValueError("Hermes source has no Python files")
    # The launch is the exact interpreter + Hermes runtime_command bootstrap the QA child runs.
    return {"launch_sha256": digest(launch), "source_sha256": digest(files), "source_files": len(files)}


def _safe_url(value: str, *, local: bool = False) -> str:
    # URLs may carry credentials even when an API key is stored separately.
    # Refuse those forms without echoing or hashing their secret contents.
    try:
        parts = urlsplit(value)
        port = parts.port
        if (not isinstance(value, str) or any(c.isspace() for c in value)
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or not parts.hostname):
            raise ValueError
        if local:
            if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not port or parts.path != "/mcp":
                raise ValueError
            return "http://127.0.0.1/mcp"  # Each isolated shard chooses its own port.
        if parts.scheme != "https":
            raise ValueError
        host = parts.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        authority = host if port in (None, 443) else f"{host}:{port}"
        return urlunsplit(("https", authority, parts.path, "", ""))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("QA profile URL must be a credential-free endpoint without query or fragment") from None


def model_runtime_identity(profile: Path, interpreter: Path) -> dict:
    """Read the actual isolated profile, never the original config or auth file.

    Only the supported, nonsecret configuration shape enters the identity.
    Model API keys are ignored before hashing; unknown configuration fails closed.
    """
    if profile.is_symlink() or not profile.is_file() or profile.stat().st_size > 16384:
        raise ValueError("Invalid isolated QA profile")
    try:
        config = json.loads(profile.read_bytes())
        if not isinstance(config, dict) or set(config) != {"model", "agent", "memory", "platform_toolsets", "mcp_servers"}:
            raise ValueError
        model = config["model"]
        if not isinstance(model, dict) or not set(model) <= {"default", "provider", "base_url", "api_key"}:
            raise ValueError
        if any(not isinstance(model.get(key), str) or not model[key].strip() for key in ("default", "provider")):
            raise ValueError
        safe_model = {key: model[key] for key in ("default", "provider")}
        if model.get("base_url"):
            safe_model["base_url"] = _safe_url(model["base_url"])
        agent = config["agent"]
        if (not isinstance(agent, dict) or set(agent) != {"max_turns", "verbose", "disabled_toolsets"}
                or type(agent["max_turns"]) is not int or not 1 <= agent["max_turns"] <= 1000
                or type(agent["verbose"]) is not bool
                or not isinstance(agent["disabled_toolsets"], list)
                or any(not isinstance(name, str) or not name.isidentifier() for name in agent["disabled_toolsets"])):
            raise ValueError
        memory = config["memory"]
        if (not isinstance(memory, dict) or set(memory) != {"memory_enabled", "user_profile_enabled"}
                or any(type(value) is not bool for value in memory.values())
                or config["platform_toolsets"] != {"cli": []}):
            raise ValueError
        servers = config["mcp_servers"]
        if not isinstance(servers, dict) or set(servers) != {"buttonsbebe_kb", "buttonsbebe_redo", "buttonsbebe_gorgias"}:
            raise ValueError
        safe_servers = {}
        for name, server in servers.items():
            if (not isinstance(server, dict) or set(server) != {"url", "enabled", "connect_timeout", "trust", "tools"}
                    or server["enabled"] is not True or type(server["connect_timeout"]) is not int
                    or server["trust"] != "untrusted" or not isinstance(server["tools"], dict)
                    or set(server["tools"]) != {"include", "resources", "prompts"}
                    or any(type(server["tools"][key]) is not bool for key in ("resources", "prompts"))
                    or not isinstance(server["tools"]["include"], list)
                    or any(not isinstance(tool, str) or not tool.isidentifier() for tool in server["tools"]["include"])):
                raise ValueError
            safe_servers[name] = {**server, "url": _safe_url(server["url"], local=True),
                                  "tools": {**server["tools"], "include": sorted(set(server["tools"]["include"]))}}
        safe = {"model": safe_model, "agent": {**agent, "disabled_toolsets": sorted(set(agent["disabled_toolsets"]))},
                "memory": memory, "platform_toolsets": {"cli": []}, "mcp_servers": safe_servers,
                "interpreter_sha256": sha256_bytes(interpreter.read_bytes())}
        return {**safe, "sha256": digest(safe)}
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("Unsupported isolated QA model/runtime profile; rerun with a supported configuration") from None


CONTEXT_FILE_NAMES = frozenset({".hermes.md", "hermes.md", "agents.override.md", "agents.md", "claude.md", ".cursorrules", ".cursor"})


def _regular_bytes(base: Path, name: str) -> bytes:
    """Read base/name, refusing a symlink anywhere below base (O_NOFOLLOW only covers the leaf)."""
    path = base
    if base.is_symlink():
        raise ValueError(f"QA instruction path must not traverse links: {base.name}")
    for part in Path(name).parts:
        path = path / part
        if part in ("..", ".") or path.is_symlink():
            raise ValueError(f"QA instruction path must not traverse links: {name}")
    if not path.is_file():
        raise ValueError(f"QA instruction file must be a regular file: {name}")
    return path.read_bytes()


def seed_instructions(hermes_home: Path, repo: Path = REPO) -> None:
    if hermes_home.parent.is_symlink():
        raise ValueError("QA instruction home must not be a link")
    for name in INSTRUCTION_FILES:
        data = _regular_bytes(repo / "hermes", name)
        directory = hermes_home.parent
        for part in (hermes_home.name, *Path(name).parts[:-1]):
            directory = directory / part
            if directory.is_symlink():
                raise ValueError(f"QA instruction directory must not be a link: {name}")
            directory.mkdir(exist_ok=True, mode=0o700)
        with open(os.open(directory / Path(name).name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "wb") as handle:
            handle.write(data)


def _present(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True  # Unknown counts as present: fail closed.


def context_directories(cwd: Path) -> list[Path]:
    """Directories Hermes project-context discovery reads (agent/prompt_builder.py, fae9e567):
    every directory from cwd up to the nearest ancestor holding .git, or cwd alone without one."""
    cwd = cwd.resolve()
    chain = [cwd, *cwd.parents]
    root = next((index for index, directory in enumerate(chain) if _present(directory / ".git")), 0)
    return chain[:root + 1]


NO_BUNDLED_SKILLS_MARKER = ".no-bundled-skills"  # Hermes essential-only opt-out (tools/skills_sync.py).


def _skill_tree(root: Path) -> dict:
    """Regular files under root keyed by relative path; links refused, runtime caches skipped."""
    files = {}
    if root.is_symlink():
        raise ValueError("QA skill tree must not contain links at its root")
    if not root.is_dir():
        raise ValueError("QA skill tree root must be a directory")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"QA skill tree must not contain links: {relative.as_posix()}")
        if path.is_file() and "__pycache__" not in relative.parts and path.suffix not in (".pyc", ".pyo"):
            files[relative.as_posix()] = path.read_bytes()
    return files


def _md5_dir(files: dict) -> str:
    # Mirrors Hermes tools/skills_sync._dir_hash (manifest origin hash) over the same file set.
    hasher = hashlib.md5()
    for name in sorted(files, key=lambda item: Path(item)):
        hasher.update(str(Path(name)).encode("utf-8"))
        hasher.update(files[name])
    return hasher.hexdigest()


def _valid_essentials(essentials) -> bool:
    if not isinstance(essentials, dict) or not essentials:
        return False
    for name, relative in essentials.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}",name)
                or not isinstance(relative, str) or not relative or len(relative)>512 or "\\" in relative):
            return False
        parts=Path(relative).parts
        if (not parts or Path(relative).is_absolute() or Path(relative).as_posix()!=relative
                or len(parts)>10 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",part) for part in parts)):
            return False
    return True


def _valid_sha256(value) -> bool:
    return isinstance(value,str) and re.fullmatch(r"[0-9a-f]{64}",value) is not None


def essential_expectation(hermes_source: Path, essentials: dict) -> tuple[dict, str]:
    """Exact bytes Hermes essential-only sync installs from the pinned source: each essential
    skill, its category DESCRIPTION.md, and the v2 manifest. No full bundled-index parity."""
    if not _valid_essentials(essentials):
        raise ValueError("Invalid essential skill locations")
    bundled = hermes_source.resolve() / "skills"
    if bundled.is_symlink():
        raise ValueError("QA essential source must not contain links")
    expected, manifest = {}, []
    for name, relative in sorted(essentials.items()):
        parts = Path(relative).parts
        _regular_bytes(bundled, f"{relative}/SKILL.md")
        tree = _skill_tree(bundled / relative)
        if "SKILL.md" not in tree:
            raise ValueError(f"Essential skill missing from the pinned source: {name}")
        expected.update({f"skills/{relative}/{key}": value for key, value in tree.items()})
        manifest.append(f"{name}:{_md5_dir(tree)}\n")
        description = bundled / Path(relative).parent / "DESCRIPTION.md"
        if description.is_symlink():
            raise ValueError("QA essential source must not contain links")
        if len(parts) > 1 and description.is_file() and not description.is_symlink():
            expected[f"skills/{Path(relative).parent.as_posix()}/DESCRIPTION.md"] = description.read_bytes()
    expected["skills/.bundled_manifest"] = "".join(sorted(manifest)).encode()
    return expected, digest({key: sha256_bytes(value) for key, value in expected.items()})


def instruction_identity(home: Path, essentials: dict, hermes_source: Path, repo: Path = REPO) -> dict:
    """Hash the instruction bytes Hermes will actually load from the private HOME (also the QA cwd).

    Approved SOUL/skills plus the source-pinned essential skills seeded under the explicit
    .no-bundled-skills opt-out. Fails closed on missing, modified, linked or extra
    context/skill/memory files, including approved files.
    """
    hermes_home = home / ".hermes"
    if home.is_symlink() or hermes_home.is_symlink():
        raise ValueError("QA instruction home must not traverse links")
    files = {}
    for name in INSTRUCTION_FILES:
        data = _regular_bytes(hermes_home, name)
        if data != _regular_bytes(repo / "hermes", name):
            raise ValueError(f"QA instruction file differs from the reviewed source: {name}")
        files[name] = sha256_bytes(data)
    marker = hermes_home / NO_BUNDLED_SKILLS_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("QA requires the explicit essential-only skills opt-out marker")
    expected, source_sha256 = essential_expectation(hermes_source, essentials)
    essential_keys = sorted(expected)
    expected.update({name: _regular_bytes(repo / "hermes", name) for name in INSTRUCTION_FILES[1:]})
    installed = {f"skills/{key}": value for key, value in _skill_tree(hermes_home / "skills").items()}
    if set(installed) != set(expected):
        raise ValueError("Unexpected or missing QA skill files")
    if any(installed[key] != value for key, value in expected.items()):
        raise ValueError("QA skill file differs from the pinned source")
    memories = hermes_home / "memories"
    if memories.exists() and (memories.is_symlink() or any(memories.iterdir())):
        raise ValueError("QA memory must stay empty")
    for directory in context_directories(home):
        found = sorted(entry.name for entry in directory.iterdir() if entry.name.lower() in CONTEXT_FILE_NAMES)
        if found:
            raise ValueError(f"Hermes would load project context from {directory}: {', '.join(found)}")
    essential = {"opt_out_marker": NO_BUNDLED_SKILLS_MARKER, "skills": dict(sorted(essentials.items())),
                 "source_sha256": source_sha256,
                 "installed_sha256": digest({key: sha256_bytes(installed[key]) for key in essential_keys})}
    identity = {"ignore_rules": False, "files": files, "essential_skills": essential}
    return {**identity, "sha256": digest(identity)}


def _require_instructions(bindings: dict) -> None:
    identity = bindings.get("instructions")
    source = bindings.get("source", {}).get("files", {})
    essential = identity.get("essential_skills") if isinstance(identity, dict) else None
    if (not isinstance(identity, dict) or set(identity) != {"ignore_rules", "files", "essential_skills", "sha256"}
            or identity["ignore_rules"] is not False or not isinstance(identity["files"], dict)
            or list(identity["files"]) != list(INSTRUCTION_FILES)
            or any(not _valid_sha256(value) for value in identity["files"].values())
            or any(source.get(f"hermes/{name}") != value for name, value in identity["files"].items())
            or not isinstance(essential, dict)
            or set(essential) != {"opt_out_marker", "skills", "source_sha256", "installed_sha256"}
            or essential["opt_out_marker"] != NO_BUNDLED_SKILLS_MARKER
            or not _valid_essentials(essential["skills"])
            or not _valid_sha256(essential["source_sha256"])
            or not _valid_sha256(essential["installed_sha256"])
            or essential["source_sha256"] != essential["installed_sha256"]
            or identity["sha256"] != digest({key: value for key, value in identity.items() if key != "sha256"})):
        raise ValueError("Missing or invalid Hermes instruction parity; rerun QA with the reviewed SOUL, skills and pinned essentials")


def _require_model_runtime(bindings: dict) -> None:
    hermes = bindings.get("hermes")
    if (not isinstance(hermes, dict) or set(hermes) != {"launch_sha256", "source_sha256", "source_files"}
            or not _valid_sha256(hermes["launch_sha256"])
            or not _valid_sha256(hermes["source_sha256"])
            or type(hermes["source_files"]) is not int or not 1 <= hermes["source_files"] <= 100_000):
        raise ValueError("Missing or invalid Hermes launch/source identity; rerun QA with a pinned runtime")
    identity = bindings.get("model_runtime")
    expected = {"model", "agent", "memory", "platform_toolsets", "mcp_servers", "interpreter_sha256", "sha256"}
    if (not isinstance(identity, dict) or set(identity) != expected
            or not isinstance(identity["model"], dict)
            or not {"default", "provider"} <= set(identity["model"]) <= {"default", "provider", "base_url"}
            or any(not isinstance(value, str) or not value.strip() for value in identity["model"].values())
            or not _valid_sha256(identity["interpreter_sha256"])
            or identity.get("sha256") != digest({key: value for key, value in identity.items() if key != "sha256"})):
        raise ValueError("Missing or invalid model/runtime identity; legacy QA evidence must be rerun")
    _require_instructions(bindings)


def catalog(suite: str, repo: Path = REPO) -> tuple[str, list[str]]:
    name, expected = SUITES[suite]
    raw = (repo / name).read_bytes()
    ids = [item.get("id") for item in json.loads(raw)]
    if len(ids) != expected or len(set(ids)) != expected or not all(isinstance(i, str) and i for i in ids):
        raise ValueError(f"{name} must hold exactly {expected} unique scenario IDs")
    return sha256_bytes(raw), ids


def kb_snapshot(kb_mode, product_manifest=None, product_manifest_sha256=None,
                policy_overlay=None, policy_overlay_sha256=None, repo: Path = REPO) -> dict:
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
    _require_model_runtime(before)
    _require_model_runtime(after)
    return {"schema": RECEIPT_SCHEMA, "suite": suite, "catalog_sha256": catalog_sha256, "ids": captured,
            "complete": captured == all_ids and list(ids) == all_ids,
            "bindings": before, "bindings_after_sha256": digest(after), "kb_observed": observed_kb(results)}


def check_run_integrity(run: dict) -> None:
    if run.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("Legacy QA run lacks model/runtime and instruction-parity evidence; rerun the suites")
    _require_model_runtime(run["bindings"])
    if digest(run["bindings"]) != run.get("bindings_after_sha256"):
        raise ValueError(f"{run['suite']}: source, Hermes, model/runtime or approved KB snapshot changed during the run")
    _single_content(run["kb_observed"], f"{run['suite']}: KB content changed during the run")


def judgment_template(run_path: Path) -> dict:
    run = json.loads(run_path.read_bytes())
    check_run_integrity(run)
    return {"schema": RECEIPT_SCHEMA, "suite": run["suite"], "run_sha256": sha256_bytes(run_path.read_bytes()),
            "verdicts": {scenario_id: "pending" for scenario_id in run["ids"]}, "blocking_defects": []}


def _suite_review(suite: str, run_path: Path, judgments_path: Path, repo: Path) -> tuple[dict, dict, int]:
    raw = run_path.read_bytes()
    run, judgments = json.loads(raw), json.loads(judgments_path.read_bytes())
    catalog_sha256, all_ids = catalog(suite, repo)
    if (run.get("schema") != RECEIPT_SCHEMA or judgments.get("schema") != RECEIPT_SCHEMA
            or run.get("suite") != suite or judgments.get("suite") != suite):
        raise ValueError(f"{suite}: schema 4 model/runtime and instruction-parity evidence required; rerun legacy suites")
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
    core, core_meta, core_defects = _suite_review("core", core_run, core_judgments, repo)
    reliability, rel_meta, rel_defects = _suite_review("reliability", reliability_run, reliability_judgments, repo)
    bindings = core_meta["bindings"]
    for name, label in (("source", "source"), ("hermes", "Hermes"), ("model_runtime", "model/runtime"), ("instructions", "Hermes instructions"),
                        ("kb_snapshot", "approved KB snapshot")):
        if digest(bindings[name]) != digest(rel_meta["bindings"][name]):
            raise ValueError(f"Core and reliability runs used a different {label}")
    _single_content(sorted({*map(tuple, core_meta["kb_observed"]), *map(tuple, rel_meta["kb_observed"])}),
                    "KB content differs between core and reliability runs")
    receipt = {"schema": RECEIPT_SCHEMA, **bindings, "suites": {"core": core, "reliability": reliability},
               "blocking_defects": core_defects + rel_defects}
    return {**receipt, **review_state(receipt)}


def review_state(receipt: dict) -> dict:
    verdicts = [v for suite in receipt["suites"].values() for v in suite["verdicts"].values()]
    complete = "pending" not in verdicts
    return {"review_complete": complete,
            "release_passed": complete and all(v == "PASS" for v in verdicts) and receipt["blocking_defects"] == 0}


def check_receipt(receipt: dict, repo: Path = REPO, *, release: bool = True) -> dict:
    if receipt.get("schema") != RECEIPT_SCHEMA or set(receipt.get("suites", {})) != set(SUITES):
        raise ValueError("Schema 4 receipt must cover both suites with model/runtime and instruction-parity evidence; rerun legacy suites")
    _require_model_runtime(receipt)
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
    parser = argparse.ArgumentParser(
        description=(
            "Source-bound run receipts and sanitized human-review receipts for manual QA.\n\n"
            "Nothing here runs a model or invents a verdict. A run receipt binds one full\n"
            "suite run to working-tree content; a reviewer writes per-ID verdicts; the\n"
            "combined receipt certifies only what those verdicts and hashes support."
        )
    )
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
