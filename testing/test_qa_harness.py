"""Offline QA boundary tests; model transport is a synthetic executable."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from qa_safety import filter_policy_results, redact, scenario_fixture, GROUPS, TOOLS, UTILITY_NAMES
from qa_harness import Harness, atomic_json, endpoint_preflight, isolated_run, minimal_environment, profile_config, prove_hermes_bindings

SCENARIO={"id":"QA-TEST","subject":"Shipping question","message":"When does order #10312 ship?","email":"qa@example.com","intent":"shipping","cat":"low"}


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

    def tearDown(self):self.temp.cleanup()

    def harness(self,binary=None):
        return Harness(output=self.root/"run",model_config=self.model,hermes=binary or Path(sys.executable),hermes_python=Path(sys.executable),hermes_source=self.source,kb_mode="fixture",timeout=10,base_port=28877)

    def test_interpreter_symlink_keeps_virtual_environment_context(self):
        interpreter = self.root / 'venv/bin/python'
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        harness = Harness(output=self.root/'interpreter-run', model_config=self.model,
                          hermes=Path(sys.executable), hermes_python=interpreter,
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

    def test_real_production_runner_prompt_and_extraction_are_used(self):
        executable=self.root/"synthetic-hermes"
        # A shebang cannot quote; the space in this repo's directory name splits
        # it. exec via /bin/sh so shlex.quote can handle the interpreter path.
        # "exec" stays the sh builtin but parses as a string expression in Python.
        executable.write_text('#!/bin/sh\n"exec" '+shlex.quote(sys.executable)+' "$0" "$@"'+'''\nimport sys,re,json
assert '--yolo' not in sys.argv
import os
assert os.environ['HERMES_HOME']==os.environ['HOME']+'/.hermes'
assert os.environ['HERMES_CONFIG']==os.environ['HERMES_HOME']+'/config.yaml'
assert os.getcwd()==os.environ['HOME']
assert sys.argv[sys.argv.index('-t')+1]=='buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias'
prompt=sys.argv[-1]
token=re.search(r'RUN TOKEN for this ticket: ([a-f0-9]+)',prompt).group(1)
print('<DRAFT:'+token+'>Thanks for reaching out. Your order is awaiting fulfillment. I can help confirm the next steps once the team has reviewed the shipping details.</DRAFT:'+token+'>')
print('JSON_RESULT['+token+']: '+json.dumps({'priority':'normal','action':'drafted','reason':'Synthetic QA fixture','notify_owner':False,'gorgias_priority_set':False,'note_posted':False}))
''')
        executable.chmod(0o700)
        harness=self.harness(executable)
        try:
            result=harness.run(SCENARIO,1)
            self.assertTrue(result["authenticated_verdict"],result)
            self.assertTrue(result["model_called"])
            self.assertIn("Thanks for reaching out",result["result"]["draft_text"])
        finally:harness.close()

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
        (self.source / 'tools').mkdir()
        (self.source / 'tools/__init__.py').write_text('')
        (self.source / 'tools/mcp_tool.py').write_text(
            "discovered=False\ndef discover_mcp_tools():\n global discovered\n discovered=True\ndef shutdown_mcp_servers(): pass\n")
        module = ("from tools import mcp_tool\n"
                  "def get_tool_definitions(**kwargs):\n"
                  f" return [{{'function':{{'name':n}}}} for n in {names!r}] if mcp_tool.discovered else []\n")
        (self.source / 'model_tools.py').write_text(module)
        result = prove_hermes_bindings(Path(sys.executable), self.source,
                                      minimal_environment(self.root), self.root, 5)
        self.assertEqual(result, {'raw': names, 'actual': names})
        (self.source / 'model_tools.py').write_text(module.replace(repr(names), repr(names + ['terminal'])))
        with self.assertRaisesRegex(ValueError, 'missing or extra'):
            prove_hermes_bindings(Path(sys.executable), self.source,
                                  minimal_environment(self.root), self.root, 5)

    def test_bridge_receipt_requires_exact_catalog_and_rejection_proof(self):
        import copy
        import qa_harness
        names = sorted(f"mcp__{g}__{t}" for g in GROUPS for t in TOOLS[g] | UTILITY_NAMES)
        receipt = {'raw':names, 'actual':['tool_call','tool_describe','tool_search'],
                   'bridge_scope':{'reachable':names, 'executor_scope':names,
                                   'rejected_outside_without_dispatch':True}}
        def verify(value):
            completed = subprocess.CompletedProcess([], 0, 'QA_BINDINGS='+json.dumps(value), '')
            with patch.object(qa_harness, 'isolated_run', return_value=completed):
                return prove_hermes_bindings(Path(sys.executable), self.source,
                                            minimal_environment(self.root), self.root, 5)
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
