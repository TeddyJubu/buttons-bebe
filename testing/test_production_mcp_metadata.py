"""Actual FastMCP discovery with credentials/search/providers isolated at import."""
import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from qa_mcp_server import create_server
from qa_safety import GROUPS,TOOLS

ROOT=Path(__file__).resolve().parents[1]
PROBE='''
import asyncio,importlib.util,json,sys,types
from pathlib import Path
root=Path(sys.argv[1]);relative=sys.argv[2]
common=types.ModuleType('_common');common.load_env=lambda:{};common._clean=lambda v:v.strip().strip(chr(34)+chr(39)).replace(chr(13),'')
requests=types.ModuleType('requests')
def forbidden(*a,**k):raise AssertionError('No provider call allowed in discovery test')
requests.get=forbidden;requests.post=forbidden
search=types.ModuleType('search_kb');search.search=forbidden
kb=types.ModuleType('kb_lib');kb.CATEGORY_WEIGHT={'policies':1};kb.CONTENT_FOLDERS=['policies'];kb._get_model=forbidden
sys.modules.update({'_common':common,'requests':requests,'search_kb':search,'kb_lib':kb})
spec=importlib.util.spec_from_file_location('qa_actual_mcp',root/relative)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
async def inspect():
 tools=await module.mcp.list_tools()
 print(json.dumps([t.model_dump(by_alias=True) for t in tools]))
asyncio.run(inspect())
'''


def schema_contract(schema):
    if isinstance(schema,dict):
        return {k:schema_contract(v) for k,v in schema.items() if k not in {'title','description'}}
    if isinstance(schema,list):return [schema_contract(v) for v in schema]
    return schema


class ProductionDiscoveryTests(unittest.TestCase):
    def test_all_production_tools_are_readonly_and_qa_input_schemas_match(self):
        modules={'buttonsbebe_kb':'kb/scripts/kb_mcp_server.py',
                 'buttonsbebe_redo':'tools/redo_mcp.py','buttonsbebe_gorgias':'tools/gorgias_mcp.py'}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            for group in GROUPS:
                with self.subTest(group=group):
                    result=subprocess.run([sys.executable,'-c',PROBE,str(ROOT),modules[group]],capture_output=True,text=True,timeout=15)
                    self.assertEqual(result.returncode,0,result.stderr)
                    actual={x['name']:x for x in json.loads(result.stdout)}
                    self.assertEqual(set(actual),TOOLS[group])
                    fixture=create_server(group,18877,path/'fixture',path/'audit',path/'allow','fixture')
                    qa={x.name:x.model_dump(by_alias=True) for x in asyncio.run(fixture.list_tools())}
                    for name,tool in actual.items():
                        self.assertEqual(tool['annotations'],{'title':None,'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':True})
                        self.assertEqual(schema_contract(tool['inputSchema']),schema_contract(qa[name]['inputSchema']),name)
                        self.assertTrue(tool['inputSchema']['properties'],name)

    def test_qa_cursor_never_expands_single_fixture_page(self):
        from qa_safety import scenario_fixture
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);fixture=path/'fixture'
            value=scenario_fixture({'id':'QA-CURSOR','message':'Order #1','subject':'Order','email':'qa@example.com'},1)
            fixture.write_text(json.dumps(value))
            server=create_server('buttonsbebe_gorgias',18877,fixture,path/'audit',path/'allow','fixture')
            result=asyncio.run(server.call_tool('get_ticket_messages',{'ticket_id':value['ticket']['id'],'cursor':'unknown'}))
            self.assertIn('qa_fixture_not_found',str(result))
            for args in ({'ticket_id':value['ticket']['id'],'cursor':'x'*2049},
                         {'ticket_id':value['ticket']['id'],'limit':0},
                         {'ticket_id':True}):
                with self.assertRaises(Exception):asyncio.run(server.call_tool('get_ticket_messages',args))
            result=asyncio.run(server.call_tool('list_inbox_tickets',{'cursor':'unknown'}))
            self.assertIn('qa_fixture_not_found',str(result))
            for args in ({'cursor':'x'*2049}, {'cursor':''}, {'limit':0}, {'limit':True}):
                with self.assertRaises(Exception):asyncio.run(server.call_tool('list_inbox_tickets',args))
