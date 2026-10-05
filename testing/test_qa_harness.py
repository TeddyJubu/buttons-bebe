"""Offline QA boundary tests; model transport is a synthetic executable."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from qa_safety import filter_policy_results, redact, scenario_fixture, GROUPS, TOOLS, UTILITY_NAMES
import qa_harness
from qa_harness import Harness, atomic_json, endpoint_preflight, isolated_run, minimal_environment, profile_config, prove_hermes_bindings

SCENARIO={"id":"QA-TEST","subject":"Shipping question","message":"When does order #10312 ship?","email":"qa@example.com","intent":"shipping","cat":"low"}


def free_loopback_port(excluded=()):
    for _ in range(10):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port >= 1024 and port not in excluded:
            return port
    raise RuntimeError("Could not allocate a synthetic QA port")


def wait_for_loopback(process, port):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("Synthetic MCP process exited before becoming ready")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("Synthetic MCP process did not open its loopback port")


def stop_process_group(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


class PolicyBoundaryTests(unittest.TestCase):
    def row(self,**kwargs):
        return {"category":"policies","file":"policies/shipping.md","status":"confirmed","title":"Shipping","heading":"Timing","text":"Policy text",**kwargs}

    def test_customer_categories_are_dropped_and_unknown_paths_abort(self):
        rows=[self.row(),self.row(category="tickets",file="tickets/customer.md",text="PRIVATE CUSTOMER TEXT")]
        safe,count=filter_policy_results(rows,{"policies/shipping.md"})
        self.assertEqual(count,1)
        self.assertNotIn("PRIVATE",json.dumps(safe))
        for row in (self.row(file="/root/.env"),self.row(file="policies/../tickets/customer.md"),self.row(category="unknown"),self.row(file="policies/unreviewed.md")):
            with self.assertRaises(ValueError):filter_policy_results([row],{"policies/shipping.md"})

    def test_redaction_runs_before_safe_tool_result(self):
        safe,_=filter_policy_results([self.row(text="Person test.person@example.com at +1 (555) 123-4567 and 123 Example Street")],{"policies/shipping.md"})
        content=json.dumps(safe)
        for forbidden in ("test.person@example.com","123-4567","123 Example Street"):
            self.assertNotIn(forbidden,content)

    def test_profile_is_exact_and_environment_does_not_inherit_credentials(self):
        profile=profile_config({"default":"test-model","provider":"custom"},{g:19000+i for i,g in enumerate(GROUPS)})
        self.assertEqual(set(profile["mcp_servers"]),set(GROUPS))
        self.assertEqual(profile["platform_toolsets"]["cli"],[])
        for group in GROUPS:
            self.assertEqual(profile['mcp_servers'][group]['tools'],{'include':sorted(TOOLS[group]),'resources':True,'prompts':True})
        with patch.dict(os.environ,{"GORGIAS_API_KEY":"not-a-real-secret","HERMES_SKIP_APPROVAL":"1"}):
            env=minimal_environment(Path("/private/qa"))
        self.assertNotIn("GORGIAS_API_KEY",env)
        self.assertNotIn("HERMES_SKIP_APPROVAL",env)
        self.assertEqual(env["HERMES_HOME"],"/private/qa/.hermes")
        with self.assertRaises(ValueError):profile_config({"default":"x","provider":"custom","mcp_servers":{}},{})

    def test_scenarios_are_exactly_48_and_synthetic(self):
        rows=json.loads((Path(__file__).parent/"scenarios.json").read_text())
        self.assertEqual(len(rows),48)
        self.assertEqual(len({r["id"] for r in rows}),48)
        for index,row in enumerate(rows,1):scenario_fixture(row,index)
        with self.assertRaises(ValueError):scenario_fixture({**SCENARIO,"email":"person@customer.invalid"},1)

    def test_reliability_regressions_are_separate_synthetic_cases(self):
        rows=json.loads((Path(__file__).parent/'reliability-scenarios.json').read_text())
        self.assertEqual(len(rows),10)
        self.assertEqual(len({r['id'] for r in rows}),10)
        for index,row in enumerate(rows,1):scenario_fixture(row,index)


LAUNCHERS = '''from pathlib import Path
def runtime_command(repo_root, args=(), *, module="hermes_cli.main", code=None, python=None, home=None):
    root = Path(repo_root).resolve()
    bootstrap = ("import os, sys, runpy; "
        "os.environ.pop('PYTHONHOME', None); os.environ.pop('PYTHONPATH', None); "
        "os.environ.pop('VIRTUAL_ENV', None); "
        f"sys.path.insert(0, {str(root)!r}); "
        f"os.environ['HERMES_HOME'] = os.environ.get('HERMES_HOME') or {default_home}; "
        "import hermes_bootstrap; "
        + entry)
    return [str(python), "-I", "-c", bootstrap, *args]
'''.replace("    root = Path(repo_root).resolve()\n", """    root = Path(repo_root).resolve()
    entry = f"exec({code!r})" if code is not None else (
        f"runpy.run_module({module!r}, run_name='__main__', alter_sys=True)")
    default_home = (f"{str(home)!r}" if home is not None else
                    "str(__import__('hermes_constants').get_default_hermes_root())")
""")

# Literal stand-in for Hermes tools/skills_sync essential-only seeding (real run: evidence qa-startup-r4).
SKILLS_SYNC = '''import hashlib, os, shutil
from pathlib import Path
from agent.skill_utils import ESSENTIAL_SKILLS
def _get_bundled_dir():
    return Path(__file__).resolve().parent.parent / 'skills'
def _discover_bundled_skills(bundled):
    return [(md.parent.name, md.parent) for md in sorted(bundled.rglob('SKILL.md'))]
def _hash(directory):
    md5 = hashlib.md5()
    for path in sorted(directory.rglob('*')):
        if path.is_file():
            md5.update(str(path.relative_to(directory)).encode()); md5.update(path.read_bytes())
    return md5.hexdigest()
def sync_skills(quiet=False):
    home = Path(os.environ['HERMES_HOME']); opt_out = (home / '.no-bundled-skills').exists()
    bundled = _get_bundled_dir(); lines = []
    for name, source in _discover_bundled_skills(bundled):
        if opt_out and name not in ESSENTIAL_SKILLS:
            continue
        dest = home / 'skills' / source.relative_to(bundled)
        if not dest.exists():
            shutil.copytree(source, dest)
        lines.append(f'{name}:{_hash(source)}\\n')
        if (source.parent / 'DESCRIPTION.md').exists() and not (dest.parent / 'DESCRIPTION.md').exists():
            shutil.copy2(source.parent / 'DESCRIPTION.md', dest.parent / 'DESCRIPTION.md')
    (home / 'skills' / '.bundled_manifest').write_text(''.join(sorted(lines)))
    return {'skipped_opt_out': opt_out}
'''


class RuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.model=self.root/"model.json"
        self.model.write_text(json.dumps({"model":{"default":"test","provider":"custom","api_key":"test-only-model-key"}}))
        self.model.chmod(0o600)
        self.source=self.root/"hermes-source"
        self.source.mkdir()
        (self.source/"model_tools.py").write_text("")
        (self.source/"hermes_bootstrap.py").write_text("import os\nos.environ['QA_BOOTSTRAPPED']='1'\n")
        (self.source/"hermes_constants.py").write_text("def get_default_hermes_root():\n    return '/root/.hermes'\n")
        for name,text in {"agent/__init__.py":"","agent/skill_utils.py":"ESSENTIAL_SKILLS=frozenset({'hermes-agent'})\n",
                          "tools/__init__.py":"","tools/skills_sync.py":SKILLS_SYNC,
                          "skills/autonomous-ai-agents/DESCRIPTION.md":"agents\n",
                          "skills/autonomous-ai-agents/hermes-agent/SKILL.md":"essential\n",
                          "skills/autonomous-ai-agents/hermes-agent/references/a.md":"ref\n",
                          "skills/creative/DESCRIPTION.md":"creative\n",
                          "skills/creative/not-essential/SKILL.md":"never seeded\n"}.items():
            (self.source/name).parent.mkdir(parents=True,exist_ok=True)
            (self.source/name).write_text(text)
        (self.source/"hermes_cli").mkdir()
        (self.source/"hermes_cli/__init__.py").write_text("")
        # Same contract as Hermes' hermes_cli/_launchers.py runtime_command (pinned fae9e567).
        (self.source/"hermes_cli/_launchers.py").write_text(LAUNCHERS)

    def tearDown(self):self.temp.cleanup()

    def harness(self,interpreter=None):
        return Harness(output=self.root/"run",model_config=self.model,hermes_python=interpreter or Path(sys.executable),hermes_source=self.source,kb_mode="fixture",timeout=10,base_port=28877)

    def test_interpreter_symlink_keeps_virtual_environment_context(self):
        interpreter = self.root / 'venv/bin/python'
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        harness = Harness(output=self.root/'interpreter-run', model_config=self.model,
                          hermes_python=interpreter,
                          hermes_source=self.source, kb_mode='fixture', timeout=10,
                          base_port=28877)
        self.assertEqual(harness.hermes_python, interpreter.absolute())
        self.assertNotEqual(harness.hermes_python, interpreter.resolve())

    def test_codex_access_only_profile_never_contains_refresh_credentials(self):
        self.model.write_text(json.dumps({"model":{"default":"test","provider":"openai-codex"},"access_token":"synthetic-access-only"}))
        harness=self.harness()
        auth_path=harness.home/".hermes"/"auth.json"
        auth=json.loads(auth_path.read_text())
        entry=auth["credential_pool"]["openai-codex"][0]
        self.assertEqual(entry["access_token"],"synthetic-access-only")
        self.assertNotIn("refresh_token",entry)
        self.assertNotIn("providers",auth)
        self.assertEqual(auth_path.stat().st_mode & 0o077,0)
        self.assertFalse(harness.config["memory"]["memory_enabled"])
        self.model.write_text(json.dumps({"model":{"default":"test","provider":"openai-codex"},"refresh_token":"forbidden"}))
        with self.assertRaises(ValueError):self.harness()

    def test_cli_receipt_reads_actual_profile_again_after_last_scenario(self):
        import run_live_tests
        from qa_receipt import check_run_integrity
        for mutate in (False, True):
            output = self.root / ("cli-mutated" if mutate else "cli-stable")
            def capture(harness, scenario, ordinal):
                if mutate:
                    profile = harness.home / ".hermes" / "config.yaml"
                    config = json.loads(profile.read_text())
                    config["model"]["default"] = "changed-after-model-call"
                    atomic_json(profile, config)
                return {"id": scenario["id"], "tool_calls": []}
            argv = ["run_live_tests.py", "--hermes-python", sys.executable,
                    "--hermes-source", str(self.source), "--model-config", str(self.model),
                    "--output", str(output), "--limit", "1", "--timeout", "10"]
            # Only network/model entry points are replaced. Profile writing,
            # both identity reads, and receipt integrity use the real code.
            with patch.object(sys, "argv", argv), patch.object(Harness, "start"), \
                    patch.object(Harness, "run", new=capture), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = run_live_tests.main()
            self.assertEqual(status, 1 if mutate else 0)
            run = json.loads((output / "run.json").read_text())
            self.assertEqual(run["bindings"]["model_runtime"]["model"], {"default": "test", "provider": "custom"})
            self.assertNotIn("test-only-model-key", json.dumps(run))
            if mutate:
                with self.assertRaisesRegex(ValueError, "model/runtime.*changed during"):
                    check_run_integrity(run)
            else:
                check_run_integrity(run)

    def test_real_production_runner_prompt_and_extraction_are_used(self):
        interpreter=self.root/"chosen/bin/python3"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        body='''import sys,re,json
assert '--yolo' not in sys.argv
assert '--ignore-rules' not in sys.argv
import os
assert os.path.samefile(sys.executable,CHOSEN) and sys.flags.isolated, (sys.executable, sys.flags.isolated)
assert sys.argv[0]==SOURCE+'/hermes_cli/main.py' and sys.path[0]==SOURCE
assert not {'PYTHONPATH','VIRTUAL_ENV'} & set(os.environ) and '/qa-injected' not in sys.path
assert 'HERMES_IGNORE_RULES' not in os.environ
for name in ('SOUL.md','skills/buttonsbebe/support-agent/SKILL.md','skills/buttonsbebe/ticket-processor/SKILL.md'):
    assert open(os.environ['HOME']+'/.hermes/'+name,'rb').read()==open(REPO+'/hermes/'+name,'rb').read(), name
assert sorted(os.listdir(os.environ['HOME']+'/.hermes/skills/buttonsbebe'))==['support-agent','ticket-processor']
assert os.environ.get('QA_BOOTSTRAPPED')=='1'
from tools.skills_sync import sync_skills
sync_skills(quiet=True)  # Hermes normal startup sync: must be a no-op after QA seeding.
assert os.listdir(os.environ['HOME'])==['.hermes']
if MUTATE:
    open(os.environ['HOME']+'/.hermes/SOUL.md','ab').write(b'mutated by child')
assert os.environ['HERMES_HOME']==os.environ['HOME']+'/.hermes'
assert os.environ['HERMES_CONFIG']==os.environ['HERMES_HOME']+'/config.yaml'
assert os.getcwd()==os.environ['HOME']
assert sys.argv[sys.argv.index('-t')+1]=='buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias'
prompt=sys.argv[-1]
token=re.search(r'RUN TOKEN for this ticket: ([a-f0-9]+)',prompt).group(1)
print('<DRAFT:'+token+'>Thanks for reaching out. Your order is awaiting fulfillment. I can help confirm the next steps once the team has reviewed the shipping details.</DRAFT:'+token+'>')
print('JSON_RESULT['+token+']: '+json.dumps({'priority':'normal','action':'drafted','reason':'Synthetic QA fixture','notify_owner':False,'gorgias_priority_set':False,'note_posted':False}))
'''
        repo=str(Path(__file__).resolve().parent.parent)
        for mutate in (False,True):
            (self.source/"hermes_cli/main.py").write_text(
                f"REPO={repo!r}\nMUTATE={mutate}\nCHOSEN={str(interpreter)!r}\nSOURCE={str(self.source.resolve())!r}\n"+body)
            harness=self.harness(interpreter)
            try:
                if mutate:
                    # The runner turns the refusal into its fallback result.
                    with patch("qa_harness.isolated_run",wraps=isolated_run) as spy:
                        result=harness.run(SCENARIO,1)
                    self.assertEqual(spy.call_count,1)
                    self.assertFalse(result["authenticated_verdict"])
                    # Child ran cleanly, but the post-run instruction check refused its output.
                    self.assertTrue(result["model_called"])
                    self.assertIsNone(result["process_returncode"])
                    continue
                with patch("qa_harness.isolated_run",wraps=isolated_run) as spy:
                    result=harness.run(SCENARIO,1)
                self.assertEqual(spy.call_count,1)
                self.assertEqual(spy.call_args.args[0][:3],[str(interpreter),'-I','-c'])
                self.assertTrue(result["authenticated_verdict"],result)
                self.assertTrue(result["model_called"])
                self.assertIn("Thanks for reaching out",result["result"]["draft_text"])
            finally:
                harness.close()
                import shutil;shutil.rmtree(harness.output)

    def test_source_without_runtime_launcher_or_bound_interpreter_is_refused(self):
        variants={"missing":None,
                  "per-shard home":LAUNCHERS.replace("if home is not None else","if True else"),
                  "other interpreter":LAUNCHERS.replace("return [str(python),","return ['/usr/bin/python3',"),
                  "no isolation":LAUNCHERS.replace('"-I", ',""),
                  "console script":"def runtime_command(*a, **k):\n    return ['/root/.hermes/bin/hermes']\n"}
        for label,text in variants.items():
            with self.subTest(label):
                launchers=self.source/"hermes_cli/_launchers.py"
                launchers.unlink()
                if text:launchers.write_text(text)
                with self.assertRaisesRegex(ValueError,"runtime_command launcher contract"):
                    self.harness()
                launchers.write_text(LAUNCHERS)
        self.assertEqual(self.harness().launch[:3],[sys.executable,"-I","-c"])

    def test_shards_share_one_canonical_launch_and_instruction_identity(self):
        from qa_receipt import hermes_identity, instruction_identity
        first=Harness(output=self.root/"shard-a",model_config=self.model,hermes_python=Path(sys.executable),hermes_source=self.source,kb_mode="fixture",timeout=10,base_port=28877)
        second=Harness(output=self.root/"shard-b",model_config=self.model,hermes_python=Path(sys.executable),hermes_source=self.source,kb_mode="fixture",timeout=10,base_port=29877)
        self.assertNotEqual(first.home,second.home)
        self.assertEqual(first.launch,second.launch)
        self.assertNotIn(str(first.home),first.launch[3])
        self.assertEqual(hermes_identity(first.launch,self.source),hermes_identity(second.launch,self.source))
        self.assertEqual(instruction_identity(first.home,first.essentials,self.source),
                         instruction_identity(second.home,second.essentials,self.source))

    def test_essentials_seed_before_model_run_idempotently_and_mutations_reject(self):
        from qa_receipt import instruction_identity
        harness=self.harness()
        hermes_home=harness.home/".hermes"
        self.assertEqual(harness.essentials,{"hermes-agent":"autonomous-ai-agents/hermes-agent"})
        self.assertTrue((hermes_home/".no-bundled-skills").is_file())
        self.assertFalse((hermes_home/"skills/creative").exists())  # non-essential never seeded
        identity=instruction_identity(harness.home,harness.essentials,self.source)
        qa_harness.seed_essentials(self.source,harness.home,harness.bootstrapped_run)  # = startup sync
        self.assertEqual(instruction_identity(harness.home,harness.essentials,self.source),identity)
        essential=hermes_home/"skills/autonomous-ai-agents/hermes-agent/SKILL.md"
        for label,mutate,undo in (
                ("pinned source",lambda:essential.write_text("x"),lambda:essential.write_text("essential\n")),
                ("Unexpected",lambda:(hermes_home/"skills/extra.md").write_text("x"),lambda:(hermes_home/"skills/extra.md").unlink()),
                ("opt-out",lambda:(hermes_home/".no-bundled-skills").unlink(),lambda:(hermes_home/".no-bundled-skills").write_text(""))):
            with self.subTest(label):
                mutate()
                with self.assertRaisesRegex(ValueError,label):
                    instruction_identity(harness.home,harness.essentials,self.source)
                undo()
        # A sync that ignores the opt-out (full bundled install) fails before any scenario.
        (self.source/"tools/skills_sync.py").write_text(SKILLS_SYNC.replace("(home / '.no-bundled-skills').exists()","False"))
        with self.assertRaisesRegex(ValueError,"seeding failed"):
            Harness(output=self.root/"full-sync",model_config=self.model,hermes_python=Path(sys.executable),hermes_source=self.source,kb_mode="fixture",timeout=10,base_port=28877)

    def test_preflight_probes_use_pinned_bootstrap_and_refuse_context_overrides(self):
        import inspect
        harness=self.harness()
        probe="import os,sys,json;print('QA_PROBE='+json.dumps([sys.flags.isolated,sys.executable,sys.path[0],os.environ.get('QA_BOOTSTRAPPED'),os.environ['HERMES_HOME'],sys.argv[1:]]))"
        result=harness.bootstrapped_run([sys.executable,"-c",probe,"a","b"],timeout=30)
        row=json.loads(result.stdout.split("QA_PROBE=")[1])
        self.assertEqual(row,[1,sys.executable,str(self.source.resolve()),"1",str(harness.home/".hermes"),["a","b"]])
        source=inspect.getsource(Harness.start)
        self.assertIn("GROUPS,self.bootstrapped_run)",source)
        self.assertIn("90,self.bootstrapped_run)",source)
        for key,value in (("TERMINAL_CWD","/srv"),("HERMES_IGNORE_RULES","1"),("HERMES_BUNDLED_SKILLS","/x"),("PYTHONPATH","/x")):
            with self.subTest(key):
                harness.env[key]=value
                with self.assertRaisesRegex(ValueError,"overrides are refused"):
                    harness.bootstrapped_run([sys.executable,"-c","pass"],timeout=10)
                del harness.env[key]

    def test_fast_final_output_overflow_is_rejected(self):
        for stream, size in ((1, 1100000), (2, 140000)):
            with self.subTest(stream=stream), self.assertRaises(RuntimeError):
                isolated_run([sys.executable, "-c", f"import os; os.write({stream}, b'x'*{size})"],
                             timeout=2, env=minimal_environment(self.root), cwd=self.root)

    def test_isolated_helper_cleans_descendants_after_success_nonzero_and_cancel(self):
        import signal
        import time
        for mode in ("success", "nonzero", "cancel"):
            marker = self.root / ("survivor-" + mode)
            descendant = f"import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(.8); pathlib.Path({str(marker)!r}).write_text('bad')"
            command = [sys.executable, "-c", f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{descendant!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(.1); " +
                       ("time.sleep(20)" if mode == "cancel" else "sys.exit(3)" if mode == "nonzero" else "sys.exit(0)")]
            if mode == "cancel":
                def interrupt(*args):
                    raise KeyboardInterrupt()
                previous = signal.signal(signal.SIGALRM, interrupt)
                signal.setitimer(signal.ITIMER_REAL, .25)
                try:
                    with self.assertRaises(KeyboardInterrupt):
                        isolated_run(command, timeout=2, env=minimal_environment(self.root), cwd=self.root)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, previous)
            else:
                outcome = isolated_run(command, timeout=2, env=minimal_environment(self.root), cwd=self.root)
                self.assertEqual(outcome.returncode, 3 if mode == "nonzero" else 0)
            time.sleep(.9)
            self.assertFalse(marker.exists())

    def test_binding_preflight_discovers_before_exact_allowlist_check(self):
        names = sorted(f"mcp__{g}__{t}" for g in GROUPS for t in TOOLS[g] | UTILITY_NAMES)
        (self.source / 'tools/mcp_tool.py').write_text(
            "discovered=False\ndef discover_mcp_tools():\n global discovered\n discovered=True\ndef shutdown_mcp_servers(): pass\n")
        module = ("from tools import mcp_tool\n"
                  "def get_tool_definitions(**kwargs):\n"
                  f" return [{{'function':{{'name':n}}}} for n in {names!r}] if mcp_tool.discovered else []\n")
        (self.source / 'model_tools.py').write_text(module)
        result = prove_hermes_bindings(Path(sys.executable), self.source,
                                      minimal_environment(self.root), self.root, 5, isolated_run)
        self.assertEqual(result, {'raw': names, 'actual': names})
        (self.source / 'model_tools.py').write_text(module.replace(repr(names), repr(names + ['terminal'])))
        with self.assertRaisesRegex(ValueError, 'missing or extra'):
            prove_hermes_bindings(Path(sys.executable), self.source,
                                  minimal_environment(self.root), self.root, 5, isolated_run)

    def test_bridge_receipt_requires_exact_catalog_and_rejection_proof(self):
        import copy
        import qa_harness
        names = sorted(f"mcp__{g}__{t}" for g in GROUPS for t in TOOLS[g] | UTILITY_NAMES)
        receipt = {'raw':names, 'actual':['tool_call','tool_describe','tool_search'],
                   'bridge_scope':{'reachable':names, 'executor_scope':names,
                                   'rejected_outside_without_dispatch':True}}
        def verify(value):
            completed = subprocess.CompletedProcess([], 0, 'QA_BINDINGS='+json.dumps(value), '')
            return prove_hermes_bindings(Path(sys.executable), self.source,
                                        minimal_environment(self.root), self.root, 5, lambda *a, **k: completed)
        self.assertEqual(verify(receipt), receipt)
        for key, value in [('reachable', names+['terminal']), ('executor_scope', names[:-1]),
                           ('rejected_outside_without_dispatch', False)]:
            broken=copy.deepcopy(receipt); broken['bridge_scope'][key]=value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'missing or extra'):
                verify(broken)

    def test_timeout_terminates_process_group(self):
        marker=self.root/"should-not-exist"
        command=[sys.executable,"-c",f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',\"import time,pathlib;time.sleep(1);pathlib.Path({str(marker)!r}).write_text('bad')\"]);time.sleep(5)"]
        with self.assertRaises(subprocess.TimeoutExpired):isolated_run(command,timeout=0.2,env=minimal_environment(self.root),cwd=self.root)
        import time
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_live_local_mcp_endpoints_expose_only_readonly_fixture_contracts(self):
        fixture=self.root/"fixture.json";audit=self.root/"audit.jsonl";allowlist=self.root/"allow.json"
        atomic_json(fixture,scenario_fixture(SCENARIO,1));atomic_json(allowlist,[])
        ports={}
        for group in GROUPS:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0));ports[group]=sock.getsockname()[1]
        children=[]
        try:
            for group in GROUPS:
                children.append(subprocess.Popen([sys.executable,str(Path(__file__).parent/"qa_mcp_server.py"),"--group",group,"--port",str(ports[group]),"--fixture",str(fixture),"--audit",str(audit),"--allowlist",str(allowlist),"--kb-mode","fixture"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            import time
            deadline=time.monotonic()+10
            while True:
                try:receipt=asyncio.run(endpoint_preflight(ports,scenario_fixture(SCENARIO,1)));break
                except Exception:
                    if time.monotonic()>deadline:raise
                    time.sleep(0.1)
            self.assertEqual(receipt,{g:sorted(TOOLS[g]) for g in GROUPS})
        finally:
            for child in children:
                child.terminate();child.wait(timeout=5)


class PolicyAuditIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "private-home"
        self.home.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.fixture_path = self.root / "fixture.json"
        self.audit_path = self.root / "audit.jsonl"
        self.allowlist_path = self.root / "allowlist.json"
        self.rows_path = self.root / "synthetic-policy-rows.json"
        atomic_json(self.fixture_path, scenario_fixture(SCENARIO, 1))
        atomic_json(self.allowlist_path, ["policies/shipping.md", "faq/secondary.md"])
        self.proxy_helper = self.root / "synthetic_policy_proxy.py"
        self.proxy_helper.write_text(
            """\
import json
import sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP

rows_path = Path(sys.argv[1])
proxy = FastMCP('synthetic-policy-proxy', host='127.0.0.1', port=int(sys.argv[2]),
                log_level='ERROR', stateless_http=True, json_response=True)

@proxy.tool()
def search_kb(query: str, k: int = 25) -> dict:
    return json.loads(rows_path.read_text(encoding='utf-8'))

proxy.run(transport='streamable-http')
""",
            encoding="utf-8",
        )

        proxy_port = free_loopback_port()
        self.proxy = subprocess.Popen(
            [sys.executable, str(self.proxy_helper), str(self.rows_path), str(proxy_port)],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.addCleanup(stop_process_group, self.proxy)
        wait_for_loopback(self.proxy, proxy_port)
        self.policy_endpoint = f"http://127.0.0.1:{proxy_port}/mcp"

        qa_port = free_loopback_port({proxy_port})
        self.qa = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("qa_mcp_server.py")),
                "--group", "buttonsbebe_kb",
                "--port", str(qa_port),
                "--fixture", str(self.fixture_path),
                "--audit", str(self.audit_path),
                "--allowlist", str(self.allowlist_path),
                "--kb-mode", "policies-only",
                "--policy-endpoint", self.policy_endpoint,
            ],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.addCleanup(stop_process_group, self.qa)
        wait_for_loopback(self.qa, qa_port)
        self.qa_port = qa_port

    async def _search(self, k=1):
        async with streamablehttp_client(
            f"http://127.0.0.1:{self.qa_port}/mcp", timeout=5, sse_read_timeout=10
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(
                    "search_kb", {"query": "Synthetic shipping policy facts", "k": k}
                )

    def _returned_envelope(self, result):
        self.assertFalse(result.isError)
        if result.structuredContent is not None:
            value = result.structuredContent
        else:
            blocks = [item.text for item in result.content if getattr(item, "type", None) == "text"]
            self.assertEqual(len(blocks), 1)
            value = json.loads(blocks[0])
        if isinstance(value, dict) and set(value) == {"result"}:
            value = value["result"]
        self.assertIsInstance(value, dict)
        return value

    def _projection_audits(self):
        entries = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        return [entry for entry in entries if entry.get("tool") == "kb_projection"]

    def _health_envelope(self, state):
        notice = {"state": "healthy", "active_count": 0, "codes": [], "operator_action": ""}
        index = {"state": "healthy", "codes": []}
        status = "healthy"
        if state == "degraded":
            index = {"state": "degraded", "codes": ["keyword_lookup_failed"]}
            status = "degraded"
        elif state == "unavailable":
            notice = {"state": "unavailable", "active_count": None,
                      "codes": ["notice_read_failed"],
                      "operator_action": "Inspect the Notice Board. Active owner overrides may still exist."}
            index = {"state": "unavailable", "codes": ["index_open_failed"]}
            status = "unavailable"
        safe_source = (
            "Shipping facts: contact privacy.person@example.com, call +1 (555) 123-4567, "
            "or visit 12 Example Street. " + "x" * 10050
        )
        return {
            "status": status,
            "notice_board": notice,
            "index": index,
            "results": [
                {
                    "file": "policies/shipping.md", "category": "policies", "status": "confirmed",
                    "title": "S" * 350, "heading": "H" * 350, "text": safe_source,
                },
                {
                    "file": "faq/secondary.md", "category": "faq", "status": "confirmed",
                    "title": "Second safe fact", "heading": "K truncation", "text": "K-TRUNCATED SYNTHETIC POLICY SENTINEL",
                },
                {
                    "file": "tickets/private.md", "category": "tickets", "status": "confirmed",
                    "title": "Synthetic private ticket", "heading": "Customer details",
                    "text": "PRIVATE SYNTHETIC TICKET SENTINEL",
                },
            ],
        }

    def test_policy_audit_matches_health_envelope_after_filter_redaction_and_limit(self):
        for state in ("healthy", "degraded", "unavailable"):
            with self.subTest(state=state):
                atomic_json(self.rows_path, self._health_envelope(state))
                result = asyncio.run(self._search(k=1))
                returned = self._returned_envelope(result)
                audits = self._projection_audits()
                audit = audits[-1]
                self.assertEqual(audit["returned_envelope"], returned)
                self.assertEqual(audit["returned"], 1)
                self.assertEqual(audit["filtered"], 1)
                self.assertEqual(audit["files"], ["policies/shipping.md"])
                self.assertEqual(returned["status"], state)
                self.assertEqual(returned["notice_board"], self._health_envelope(state)["notice_board"])
                self.assertEqual(returned["index"], self._health_envelope(state)["index"])
                self.assertEqual(len(returned["results"]), 1)
                snippet = returned["results"][0]
                self.assertLessEqual(len(snippet["title"]), 300)
                self.assertLessEqual(len(snippet["heading"]), 300)
                self.assertEqual(len(snippet["text"]), 10000)
                self.assertIn("[email removed]", snippet["text"])
                self.assertIn("[phone or identifier removed]", snippet["text"])
                self.assertIn("[address removed]", snippet["text"])
                receipt = json.dumps({"returned": returned, "audit": audit})
                for marker in (
                    "privacy.person@example.com", "+1 (555) 123-4567", "12 Example Street",
                    "PRIVATE SYNTHETIC TICKET SENTINEL", "K-TRUNCATED SYNTHETIC POLICY SENTINEL",
                ):
                    self.assertNotIn(marker, receipt)
                self.assertEqual(stat.S_IMODE(self.audit_path.stat().st_mode) & 0o077, 0)

        atomic_json(self.rows_path, {
            **self._health_envelope("healthy"),
            "results": [{
                "file": "policies/shipping.md", "category": "unknown", "status": "confirmed",
                "title": "UNKNOWN CATEGORY PRIVATE SENTINEL", "heading": "Unexpected",
                "text": "UNKNOWN CATEGORY PRIVATE SENTINEL",
            }],
        })
        rejected = asyncio.run(self._search(k=1))
        self.assertTrue(rejected.isError)
        audits = self._projection_audits()
        self.assertEqual(len(audits), 4)
        self.assertTrue(audits[-1]["fatal"])
        self.assertNotIn("returned_envelope", audits[-1])
        audit_text = self.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("UNKNOWN CATEGORY PRIVATE SENTINEL", audit_text)
        self.assertNotIn("privacy.person@example.com", audit_text)

    def test_policy_endpoint_rejects_non_loopback_addresses(self):
        from qa_mcp_server import _validate_policy_endpoint

        for endpoint in (
            "https://127.0.0.1:8077/mcp",
            "http://192.0.2.1:8077/mcp",
            "http://user:password@127.0.0.1:8077/mcp",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                _validate_policy_endpoint(endpoint)



if __name__=="__main__":unittest.main()

class InputBoundaryTests(unittest.TestCase):
    def test_policy_allowlist_rejects_directory_links_and_never_admits_local_products(self):
        from qa_safety import policy_files
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();repo=root/'repo';(repo/'kb/policies').mkdir(parents=True)
            (repo/'kb/policies/allowed.md').write_text('policy')
            (repo/'kb/products').mkdir();(repo/'kb/products/product-unreviewed.md').write_text('catalog')
            self.assertEqual(policy_files(repo),{'policies/allowed.md'})
            outside=root/'outside';outside.mkdir();(outside/'secret.md').write_text('synthetic-private')
            (repo/'kb/faq').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(ValueError):policy_files(repo)
            (repo/'kb/faq').unlink()
            (repo/'kb/policies/link.md').symlink_to(outside/'secret.md')
            with self.assertRaises(ValueError):policy_files(repo)
            (repo/'kb/policies/link.md').unlink()
            (repo/'kb').rename(repo/'original-kb');(repo/'kb').symlink_to(repo/'original-kb',target_is_directory=True)
            with self.assertRaises(ValueError):policy_files(repo)

    def test_fixture_requires_exact_synthetic_shape_at_server_read_boundary(self):
        from qa_safety import validate_fixture
        import copy
        fixture=scenario_fixture(SCENARIO,1)
        self.assertEqual(validate_fixture(fixture),fixture)
        for change in ('marker','real-email','real-id','body-url','extra-customer','order-status','oversized'):
            value=copy.deepcopy(fixture)
            if change=='marker':value['ticket']['qa_fixture']=False
            elif change=='real-email':value['customer']['email']='customer@real.invalid'
            elif change=='real-id':value['ticket']['id']=123
            elif change=='body-url':value['messages'][0]['body_url']='https://example.invalid/private'
            elif change=='extra-customer':value['customer']['phone']='123456789'
            elif change=='order-status':value['orders'][0]['fulfillment_status']='delivered'
            else:value['messages'][0]['body_text']='x'*100001
            with self.subTest(change=change),self.assertRaises(ValueError):validate_fixture(value)
        from qa_mcp_server import create_server
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'fixture.json';path.write_text(json.dumps({'ticket':{'id':123}}))
            server=create_server('buttonsbebe_gorgias',19079,path,root/'audit',root/'allowlist','fixture')
            from mcp.server.fastmcp.exceptions import ToolError
            with self.assertRaisesRegex(ToolError,'Invalid synthetic fixture'):
                asyncio.run(server.call_tool('list_recent_tickets',{}))
            self.assertFalse((root/'audit').exists())
