"""Separate QA profile and transport harness around the production runner."""
from __future__ import annotations
import ast
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from qa_safety import GROUPS, TOOLS, UTILITY_NAMES, policy_files, redact, scenario_fixture
from qa_metadata import prove_metadata
from qa_catalog import load_manifest
from qa_receipt import NO_BUNDLED_SKILLS_MARKER, instruction_identity, seed_instructions

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "w") as handle:
        json.dump(value,handle,indent=2,ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary,path)


def profile_config(model: dict, ports: dict) -> dict:
    if not isinstance(model,dict) or not set(model).issubset({"default","provider","base_url","api_key"}):
        raise ValueError("Expected a model-only configuration block")
    if not all(isinstance(model.get(k),str) and model[k] for k in ("default","provider")):
        raise ValueError("Model name and provider are required")
    if model.get("base_url") and not model["base_url"].startswith("https://"):
        raise ValueError("Model provider must use HTTPS")
    return {
        "model": model,
        "agent": {"max_turns":30,"verbose":False,"disabled_toolsets":["terminal","file","code_execution","browser","computer_use","web","memory","skills","cronjob","delegation","session_search","search","todo"]},
        "platform_toolsets":{"cli":[]},
        "memory":{"memory_enabled":False,"user_profile_enabled":False},
        "mcp_servers":{group:{"url":f"http://127.0.0.1:{ports[group]}/mcp","enabled":True,"connect_timeout":15,
                              "trust":"untrusted","tools":{"include":sorted(TOOLS[group]),"resources":True,"prompts":True}} for group in GROUPS},
    }


ALLOWED_ENV = frozenset({"HOME","HERMES_HOME","HERMES_CONFIG","PATH","LANG","PYTHONUNBUFFERED","DEMO_MODE","OLLAMA_API_KEY"})


def require_clean_env(env: dict, home: Path) -> None:
    """Refuse TERMINAL_CWD, HERMES_IGNORE_RULES, HERMES_BUNDLED_SKILLS or any other inherited override."""
    if (set(env) - ALLOWED_ENV or env.get("HOME") != str(home) or env.get("HERMES_HOME") != str(home/".hermes")
            or env.get("HERMES_CONFIG") != str(home/".hermes"/"config.yaml")):
        raise ValueError("Inherited Hermes context or working-directory overrides are refused")


def minimal_environment(home: Path) -> dict:
    # No inherited commerce credentials, root HOME, active profile or MCP config.
    return {"HOME":str(home),"HERMES_HOME":str(home / ".hermes"),"HERMES_CONFIG":str(home / ".hermes" / "config.yaml"),
            "PATH":"/usr/local/bin:/usr/bin:/bin","LANG":"C.UTF-8","PYTHONUNBUFFERED":"1","DEMO_MODE":"1"}


def isolated_run(command, *, timeout, env, cwd, **ignored):
    """Use the exact production helper without importing credential config.

    Loading this self-contained file directly avoids hermes_runner.__init__ and
    processor.config import-time dotenv behavior during QA preflight.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "qa_production_process", REPO / "processor/hermes_runner/process.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_bounded(command, timeout=timeout, env=env, cwd=cwd)


def unpack(result):
    if result.isError:
        raise ValueError("QA MCP operation failed")
    if result.structuredContent is not None:
        return result.structuredContent
    blocks = [item.text for item in result.content if getattr(item,"type",None)=="text"]
    if len(blocks) != 1:
        raise ValueError("Unexpected MCP content")
    return json.loads(blocks[0])


async def endpoint_preflight(ports, fixture, schemas=None):
    receipt = {}
    for group in GROUPS:
        async with streamablehttp_client(f"http://127.0.0.1:{ports[group]}/mcp",timeout=5,sse_read_timeout=30) as (read,write,_):
            async with ClientSession(read,write) as session:
                await session.initialize()
                listing = await session.list_tools()
                if len(listing.tools) != len(TOOLS[group]) or {tool.name for tool in listing.tools} != TOOLS[group] or any(not tool.annotations or tool.annotations.readOnlyHint is not True for tool in listing.tools):
                    raise ValueError("QA MCP tool contract mismatch")
                for catalog,field in ((await session.list_resources(),'resources'),
                                      (await session.list_resource_templates(),'resourceTemplates'),
                                      (await session.list_prompts(),'prompts')):
                    if getattr(catalog,field) != [] or catalog.nextCursor:
                        raise ValueError("QA metadata catalogs must remain empty")
                if schemas is not None:
                    schemas.update({f"mcp__{group}__{tool.name}": tool.inputSchema for tool in listing.tools})
                if group == "buttonsbebe_gorgias":
                    result = unpack(await session.call_tool("get_ticket",{"ticket_id":fixture["ticket"]["id"]}))
                    if result.get("id") != fixture["ticket"]["id"] or result.get("qa_fixture") is not True:
                        raise ValueError("Gorgias fixture sentinel mismatch")
                elif group == "buttonsbebe_redo":
                    result = unpack(await session.call_tool("get_return",{"return_id":"unknown-qa-sentinel"}))
                    if result.get("error") != "qa_fixture_not_found":
                        raise ValueError("Redo fixture sentinel mismatch")
                else:
                    unpack(await session.call_tool("search_kb",{"query":"return policy","k":2}))
                receipt[group] = sorted(TOOLS[group])
    return receipt


def prove_hermes_bindings(hermes_python, hermes_source, env, home, timeout, run):
    expected = sorted(f"mcp__{group}__{tool}" for group in GROUPS for tool in TOOLS[group] | UTILITY_NAMES)
    code = """
import sys,json
sys.path.insert(0,sys.argv[1])
from model_tools import get_tool_definitions
try:
 from tools.mcp_tool_discovery import discover_mcp_tools
except ImportError:
 from tools.mcp_tool import discover_mcp_tools
try:
 from tools.mcp_tool_lifecycle import shutdown_mcp_servers
except ImportError:
 from tools.mcp_tool import shutdown_mcp_servers
try:
 discover_mcp_tools()
 groups=json.loads(sys.argv[2])
 raw=get_tool_definitions(enabled_toolsets=groups,quiet_mode=True,skip_tool_search_assembly=True)
 actual=get_tool_definitions(enabled_toolsets=groups,quiet_mode=True)
 bindings={'raw':sorted(x['function']['name'] for x in raw),'actual':sorted(x['function']['name'] for x in actual)}
 if set(bindings['actual'])=={'tool_call','tool_describe','tool_search'}:
  from tools.tool_search import scoped_deferrable_names
  from model_tools import handle_function_call
  from tools.registry import registry
  from agent.tool_executor import _tool_search_scoped_names
  from types import SimpleNamespace
  from unittest.mock import patch
  reachable=sorted(scoped_deferrable_names(raw))
  executor_scope=sorted(_tool_search_scoped_names(SimpleNamespace(enabled_toolsets=groups,disabled_toolsets=[])))
  with patch.object(registry,'dispatch',side_effect=AssertionError('unexpected QA dispatch')) as dispatch:
   unknown=json.loads(handle_function_call('tool_call',{'name':'mcp__qa_forbidden__never','arguments':{}},enabled_toolsets=groups))
   outside=json.loads(handle_function_call('tool_call',{'name':'mcp__buttonsbebe_gorgias__get_ticket','arguments':{'ticket_id':-1}},enabled_toolsets=['buttonsbebe_kb']))
   rejected=bool(unknown.get('error')) and bool(outside.get('error')) and not dispatch.called
  bindings['bridge_scope']={'reachable':reachable,'executor_scope':executor_scope,'rejected_outside_without_dispatch':rejected}
 print('QA_BINDINGS='+json.dumps(bindings))
finally:
 shutdown_mcp_servers()
"""
    result = run([str(hermes_python),"-c",code,str(hermes_source),json.dumps(GROUPS)],timeout=timeout,env=env,cwd=home)
    matches = [line[len("QA_BINDINGS="):] for line in result.stdout.splitlines() if line.startswith("QA_BINDINGS=")]
    if result.returncode or len(matches)!=1:
        raise ValueError("Hermes tool-binding preflight failed; no scenarios run")
    actual=json.loads(matches[0])
    direct = {"raw":expected,"actual":expected}
    bridged = {"raw":expected,"actual":["tool_call","tool_describe","tool_search"],
               "bridge_scope":{"reachable":expected,"executor_scope":expected,
                               "rejected_outside_without_dispatch":True}}
    if actual not in (direct, bridged):
        raise ValueError("Hermes exposes missing or extra tools; no scenarios run")
    return actual


LAUNCH_PROBE = """
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from hermes_cli._launchers import runtime_command
print('QA_LAUNCH='+json.dumps(runtime_command(Path(sys.argv[1]),python=sys.argv[2],code=json.loads(sys.argv[3]))))
"""
# Production home=None form: the private HERMES_HOME comes from the environment, so every shard's
# launch (and launch_sha256) is identical. A home baked into the command would differ per shard.
DEFAULT_HOME = "os.environ['HERMES_HOME'] = os.environ.get('HERMES_HOME') or str(__import__('hermes_constants').get_default_hermes_root()); "


def resolve_launch(hermes_python, hermes_source, env, home, code=None, timeout=60):
    """The pinned source's own production launcher (-I bootstrap) bound to the exact chosen interpreter."""
    require_clean_env(env, home)
    result = isolated_run([str(hermes_python),"-I","-c",LAUNCH_PROBE,str(hermes_source),str(hermes_python),json.dumps(code)],
                          timeout=timeout,env=env,cwd=home)
    matches = [line[len("QA_LAUNCH="):] for line in result.stdout.splitlines() if line.startswith("QA_LAUNCH=")]
    launch = json.loads(matches[0]) if not result.returncode and len(matches)==1 else None
    entry = f"exec({code!r})" if code is not None else (
        "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)")
    expected_bootstrap = ("import os, sys, runpy; "
        "os.environ.pop('PYTHONHOME', None); os.environ.pop('PYTHONPATH', None); "
        "os.environ.pop('VIRTUAL_ENV', None); "
        f"sys.path.insert(0, {str(hermes_source)!r}); " + DEFAULT_HOME +
        "import hermes_bootstrap; " + entry)
    try:
        supported_bootstrap = (isinstance(launch, list) and len(launch) == 4
            and isinstance(launch[3], str)
            and ast.dump(ast.parse(launch[3])) == ast.dump(ast.parse(expected_bootstrap)))
    except (SyntaxError, ValueError, TypeError):
        supported_bootstrap = False
    if (not isinstance(launch,list) or len(launch)!=4 or not all(isinstance(part,str) for part in launch)
            or launch[:3]!=[str(hermes_python),"-I","-c"] or not supported_bootstrap):
        raise ValueError("Hermes source lacks the supported runtime_command launcher contract; no scenarios run")
    return launch


SEED_PROBE = """
import json
from agent.skill_utils import ESSENTIAL_SKILLS
from tools.skills_sync import _discover_bundled_skills, _get_bundled_dir, sync_skills
result = sync_skills(quiet=True)
bundled = _get_bundled_dir()
print('QA_SEED=' + json.dumps({'opt_out': result.get('skipped_opt_out') is True, 'bundled': str(bundled.resolve()),
    'essentials': {name: src.relative_to(bundled).as_posix() for name, src in _discover_bundled_skills(bundled) if name in ESSENTIAL_SKILLS}}))
"""


def seed_essentials(hermes_source, home, run):
    """Write the explicit essential-only opt-out, then run the source's own sync synchronously
    before any model run, so Hermes startup sync finds nothing new to install."""
    marker = home/".hermes"/NO_BUNDLED_SKILLS_MARKER
    os.close(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600))
    result = run(["python","-c",SEED_PROBE],timeout=120,env=None,cwd=home)
    matches = [line[len("QA_SEED="):] for line in result.stdout.splitlines() if line.startswith("QA_SEED=")]
    seeded = json.loads(matches[0]) if not result.returncode and len(matches)==1 else {}
    if (seeded.get("opt_out") is not True or seeded.get("bundled") != str(Path(hermes_source).resolve()/"skills")
            or not isinstance(seeded.get("essentials"),dict) or not seeded["essentials"]):
        raise ValueError("Hermes essential-only skill seeding failed; no scenarios run")
    return seeded["essentials"]


class Harness:
    def __init__(self, *, output: Path, model_config: Path, hermes_python: Path, hermes_source: Path, kb_mode: str, timeout: int, base_port: int, product_manifest: Path | None = None, product_manifest_sha256: str | None = None, policy_overlay: Path | None = None, policy_overlay_sha256: str | None = None):
        if not model_config.is_file() or model_config.is_symlink() or model_config.stat().st_mode & 0o077:
            raise ValueError("Model-only config must be a private regular file")
        if model_config.stat().st_size > 16384:
            raise ValueError("Oversized model configuration")
        model = json.loads(model_config.read_text())
        if not isinstance(model,dict) or set(model) not in ({"model"},{"model","access_token"}):
            raise ValueError("Only a model block is accepted; do not supply production config")
        self.output = output.resolve()
        if self.output.is_relative_to(REPO):
            raise ValueError("QA output must be outside the application source tree")
        self.output.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.output.is_symlink() or self.output.stat().st_mode & 0o077:
            raise ValueError("QA output must be private")
        self.home=self.output / "home"
        self.home.mkdir(mode=0o700,exist_ok=True)
        (self.home / ".hermes").mkdir(parents=True,exist_ok=True,mode=0o700)
        seed_instructions(self.home / ".hermes")
        self.ports={group:base_port+i for i,group in enumerate(GROUPS)}
        self.config=profile_config(model["model"],self.ports)
        atomic_json(self.home/".hermes"/"config.yaml",self.config) # JSON is valid YAML.
        if "access_token" in model:
            if model["model"].get("provider") != "openai-codex" or not isinstance(model["access_token"],str) or not model["access_token"]:
                raise ValueError("Access-token input requires the Codex provider")
            atomic_json(self.home/".hermes"/"auth.json", {"credential_pool":{"openai-codex":[{"id":"qa-access-only","label":"QA access only","source":"manual:qa","auth_type":"oauth","priority":0,"access_token":model["access_token"]}]}})
        self.env=minimal_environment(self.home)
        # Env-key providers (e.g. ollama-cloud) read credentials from the
        # environment, not the model block; export the provider key into the
        # isolated env so stub reads are authorized. Existing secret redaction
        # covers outputs; nothing else inherits this env.
        provider_key = model["model"].get("api_key")
        if isinstance(provider_key, str) and provider_key:
            provider = model["model"].get("provider")
            if provider == "ollama-cloud":
                self.env["OLLAMA_API_KEY"] = provider_key
        self.hermes_python=hermes_python.absolute();self.hermes_source=hermes_source.resolve()
        if not self.hermes_python.is_file() or not (self.hermes_source/"model_tools.py").is_file():
            raise ValueError("Explicit Hermes interpreter/source paths are required")
        self.launch=resolve_launch(self.hermes_python,self.hermes_source,self.env,self.home)
        self.essentials=seed_essentials(self.hermes_source,self.home,self.bootstrapped_run)
        instruction_identity(self.home,self.essentials,self.hermes_source)
        self.fixture_path=self.output/"active-fixture.json"
        self.audit_path=self.output/"tool-audit.jsonl"
        self.allowlist=self.output/"policy-allowlist.json"
        if (product_manifest is None) != (product_manifest_sha256 is None):
            raise ValueError("Product manifest and reviewed hash must be provided together")
        extra = load_manifest(product_manifest,product_manifest_sha256) if product_manifest is not None else set()
        self.catalog_receipt = {"sha256":product_manifest_sha256,"count":len(extra)}
        atomic_json(self.allowlist,sorted(policy_files(REPO) | extra))
        if (policy_overlay is None) != (policy_overlay_sha256 is None):
            raise ValueError('Policy snapshot and reviewed hash must be supplied together')
        self.policy_overlay = None
        if policy_overlay is not None:
            if kb_mode != 'policies-only':
                raise ValueError('Proposed content requires the policies-only projection')
            from qa_policy_overlay import load_overlay
            self.policy_overlay = self.output/'policy-overlay.json'
            atomic_json(self.policy_overlay, load_overlay(policy_overlay, policy_overlay_sha256, REPO))
        self.policy_overlay_sha256 = policy_overlay_sha256
        self.kb_mode=kb_mode;self.timeout=timeout;self.children=[]
        self.secret_values=([model["access_token"]] if "access_token" in model else []) + [value for key,value in model["model"].items() if key=="api_key" and isinstance(value,str) and value]

    def bootstrapped_run(self, command, *, timeout, env=None, cwd=None, **ignored):
        """Run a [python, -c, code, *args] probe through the pinned runtime_command bootstrap
        (same -I interpreter, dependency selection and HERMES_HOME path as production)."""
        if len(command) < 3 or command[1] != "-c":
            raise ValueError("Unexpected QA probe command")
        require_clean_env(self.env, self.home)
        launch = resolve_launch(self.hermes_python,self.hermes_source,self.env,self.home,code=command[2])
        return isolated_run([*launch,*command[3:]],timeout=timeout,env=self.env,cwd=self.home)

    def start(self, first_fixture):
        atomic_json(self.fixture_path,{**first_fixture,"scenario_id":"QA-PREFLIGHT"})
        for group,port in self.ports.items():
            with socket.socket() as probe:
                # Match the MCP server's reuse behavior: a completed serial run
                # can leave TIME_WAIT sockets, but an active listener still fails.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1",port))
        for group,port in self.ports.items():
            command=[sys.executable,str(HERE/"qa_mcp_server.py"),"--group",group,"--port",str(port),"--fixture",str(self.fixture_path),"--audit",str(self.audit_path),"--allowlist",str(self.allowlist),"--kb-mode",self.kb_mode]
            if self.policy_overlay:
                command.extend(['--policy-overlay', str(self.policy_overlay), '--policy-overlay-sha256',
                                hashlib.sha256(self.policy_overlay.read_bytes()).hexdigest()])
            self.children.append(subprocess.Popen(command,env=self.env,cwd=self.output,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True))
        schemas={}
        deadline=time.monotonic()+20
        while True:
            try:
                receipt=asyncio.run(endpoint_preflight(self.ports,first_fixture,schemas))
                break
            except Exception:
                if time.monotonic()>=deadline or any(child.poll() is not None for child in self.children):
                    raise ValueError("QA MCP preflight failed") from None
                time.sleep(0.2)
        metadata=prove_metadata(self.hermes_python,self.hermes_source,self.env,self.home,schemas,GROUPS,self.bootstrapped_run)
        bindings=prove_hermes_bindings(self.hermes_python,self.hermes_source,self.env,self.home,90,self.bootstrapped_run)
        receipt={"endpoints":receipt,"hermes_bindings":bindings,"hermes_metadata":metadata,"kb_mode":self.kb_mode,
                 "production_prompt_sha256":hashlib.sha256((REPO/"processor/hermes_runner/prompt.py").read_bytes()).hexdigest(),
                 "production_extract_sha256":hashlib.sha256((REPO/"processor/hermes_runner/extract.py").read_bytes()).hexdigest(),
                 "profile_sha256":hashlib.sha256(json.dumps(self.config,sort_keys=True).encode()).hexdigest(),
                 "product_catalog_manifest":self.catalog_receipt,"policy_allowlist_sha256":hashlib.sha256(self.allowlist.read_bytes()).hexdigest(),
                 "proposed_policy_snapshot_sha256":self.policy_overlay_sha256}
        atomic_json(self.output/"preflight.json",receipt)
        self.assert_no_fatal_audit()

    def assert_no_fatal_audit(self):
        if self.audit_path.exists() and any(json.loads(line).get("fatal") for line in self.audit_path.read_text().splitlines()):
            raise ValueError("QA policy proxy refused a response; run stopped")

    def run(self, scenario, ordinal):
        fixture=scenario_fixture(scenario,ordinal)
        atomic_json(self.fixture_path,fixture)
        # Suppress processor/config.py's import-time dotenv loading. QA settings
        # then replace get_settings; production code and environment are untouched.
        os.environ["DEMO_MODE"]="1"
        sys.path.insert(0,str(REPO))
        sys.path.insert(0,str(REPO/"processor"))
        from hermes_runner import runner
        from hermes_runner.extract import _extract_draft_details, _valid_verdicts
        from hermes_runner.constants import _make_run_token
        token=_make_run_token()
        captured={}
        settings=SimpleNamespace(hermes_bin="hermes",hermes_profile="",hermes_ignore_rules=False,hermes_skip_approval=False,
                                 hermes_toolsets=",".join(GROUPS),hermes_home=str(self.home),job_timeout=self.timeout,support_store_name="Buttons Bebe")
        def execute(command,**kwargs):
            if "--yolo" in command or "--ignore-rules" in command or "HERMES_IGNORE_RULES" in self.env or command[command.index("-t")+1] != ",".join(GROUPS) or command[0]!="hermes":
                raise ValueError("Unexpected QA command or tool authorization")
            require_clean_env(self.env,self.home)
            before=instruction_identity(self.home,self.essentials,self.hermes_source)
            captured["attempted"]=True
            result=isolated_run([*self.launch,*command[1:]],timeout=self.timeout,env=self.env,cwd=self.home)
            if instruction_identity(self.home,self.essentials,self.hermes_source)!=before:
                raise ValueError("QA instructions changed during the run")
            for secret in self.secret_values:
                result.stdout=result.stdout.replace(secret,"[credential removed]")
            result.stderr=""  # Provider errors are never copied into processor logs.
            captured["result"]=result
            return result
        started=time.monotonic()
        with patch.object(runner,"get_settings",return_value=settings),patch.object(runner,"_run_environment",return_value=self.env),patch.object(runner,"_make_run_token",return_value=token),patch.object(runner,"run_bounded",side_effect=execute):
            result=runner.process_ticket_with_hermes(fixture["ticket"]["id"],scenario["message"],scenario["subject"],scenario["email"],[scenario.get("intent","")])
        self.assert_no_fatal_audit()
        process=captured.get("result")
        output=process.stdout if process and process.returncode==0 else ""
        for secret in self.secret_values:
            output=output.replace(secret,"[credential removed]")
        valid_verdicts=_valid_verdicts(output,token=token)[0] if output else []
        extraction=_extract_draft_details(output,token=token) if output else None
        return {"id":scenario["id"],"scenario":scenario,"result":result,"hermes_output":redact(output),
                "seconds":round(time.monotonic()-started,2),"run_token":token,"process_returncode":process.returncode if process else None,
                "model_called":captured.get("attempted",False),"authenticated_verdict":bool(valid_verdicts),
                "draft_extraction":vars(extraction) if extraction else None,"human_review":"pending",
                "tool_calls":[json.loads(line) for line in self.audit_path.read_text().splitlines() if json.loads(line).get("scenario_id")==scenario["id"]] if self.audit_path.exists() else []}

    def close(self):
        for child in self.children:
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL);child.wait()
