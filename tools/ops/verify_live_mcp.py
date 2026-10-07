#!/usr/bin/env python3
"""Manual discovery-only proof for the paused, patched production Hermes home."""
import argparse
import hashlib
import importlib.util
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys

SOURCE = Path('/usr/local/lib/hermes-agent')
HOME = Path('/root/.hermes')
PYTHON = SOURCE / 'venv/bin/python'  # Keep lexical venv path.
PATCHED_MCP_SHA = '27e96bade42bb3c2106c41b386f7105902f29b4825f61775567d1f463f674279'
MODEL_TOOLS_SHA = '32a106d66835dc9f88f15624076086a53cbd4bb7ed80889228d0e53f62d4cfac'
CACHE_MODULE_SHA = 'e24f2bdb98a1aa11bc21f8fc800a2723b2bf2d0e97704bc5323df187c17a8c7f'
PROCESS_HELPER_SHA = 'bde46293ba6f887d66411aa638de7544a8375f8999218832f101baba4aa12aa3'
GROUPS = {
    'buttonsbebe_kb': (8077, {'search_kb'}),
    'buttonsbebe_redo': (8078, {'list_recent_returns','get_returns_for_order','get_return','get_order'}),
    'buttonsbebe_gorgias': (8079, {'list_recent_tickets','get_ticket','get_ticket_messages','get_customer','search_customer','list_inbox_tickets'}),
}
PREFIX = 'MCP_PROOF='


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def mcp_field(value, snake, camel):
    """Read SDK2 name first, then SDK1 alias, without truthiness fallback."""
    if isinstance(value, dict):
        return value[snake] if snake in value else value.get(camel)
    if hasattr(value, snake): return getattr(value, snake)
    return getattr(value, camel, None)


def endpoint_metadata(group, listing):
    names = GROUPS[group][1]
    if (mcp_field(listing, 'next_cursor', 'nextCursor') or len(listing.tools) != len(names)
            or {t.name for t in listing.tools} != names):
        raise ValueError('Endpoint tools changed')
    result = {'schemas':{}, 'readonly':{}}
    for tool in listing.tools:
        key = 'mcp__'+group+'__'+tool.name
        result['schemas'][key] = mcp_field(tool, 'input_schema', 'inputSchema')
        result['readonly'][key] = mcp_field(tool.annotations, 'read_only_hint', 'readOnlyHint') is True
    return result


def canonical_nullable_schema(node):
    # Exactly the reviewed optional string/null equivalence; never strip fields.
    if isinstance(node, list): return [canonical_nullable_schema(v) for v in node]
    if not isinstance(node, dict): return node
    out = dict(node)
    for key in ('properties','patternProperties','$defs','definitions'):
        if isinstance(out.get(key), dict):
            out[key] = {k:canonical_nullable_schema(v) for k,v in out[key].items()}
    for key in ('items','additionalProperties','anyOf','oneOf','allOf','not'):
        if key in out: out[key] = canonical_nullable_schema(out[key])
    union = out.get('anyOf')
    if (isinstance(union,list) and len(union)==2 and {'type':'string'} in union
            and {'type':'null'} in union and not {'type','nullable','oneOf','allOf'}.intersection(out)):
        del out['anyOf']; out['type']='string'; out['nullable']=True
    return out


def metadata_equal(left, right):
    def canon(value):
        return {**value, 'schemas': {k:canonical_nullable_schema(v) for k,v in value['schemas'].items()}}
    return json.dumps(canon(left),sort_keys=True)==json.dumps(canon(right),sort_keys=True)


def validate_metadata(value):
    names = {'mcp__'+g+'__'+n for g,(_,names) in GROUPS.items() for n in names}
    if set(value) != {'schemas','readonly'} or set(value['schemas']) != names or set(value['readonly']) != names:
        raise ValueError('Unexpected discovery tool set')
    if any(value['readonly'][n] is not True for n in names): raise ValueError('Readonly hint missing')
    for schema in value['schemas'].values():
        if not isinstance(schema,dict) or schema.get('type')!='object' or not isinstance(schema.get('properties'),dict) or not schema['properties']:
            raise ValueError('Empty or invalid tool schema')


def registry_metadata(raw, hints, utility_schemas):
    business = {'mcp__'+g+'__'+n for g,(_,ns) in GROUPS.items() for n in ns}
    expected_utilities = {'mcp__'+g+'__'+n for g in GROUPS
                          for n in ('list_resources','read_resource','list_prompts','get_prompt')}
    functions = [row['function'] for row in raw]
    names = [row['name'] for row in functions]
    if len(names) != 23 or len(set(names)) != 23 or set(names) != business | expected_utilities:
        raise ValueError('Unexpected business or utility definitions')
    if set(utility_schemas) != expected_utilities:
        raise ValueError('Unexpected generated utility schemas')
    for row in functions:
        if row['name'] in expected_utilities and row != utility_schemas[row['name']]:
            raise ValueError('Generated utility definition changed')
    value = {'schemas': {row['name']:row['parameters'] for row in functions if row['name'] in business},
             'readonly': {'mcp__'+g+'__'+n:v is True for g in GROUPS for n,v in hints.get(g,{}).items()}}
    validate_metadata(value)
    return value


def cached_metadata(cache):
    if set(cache) != set(GROUPS): raise ValueError('Unexpected cached groups')
    value = {'schemas':{},'readonly':{}}
    for group,(_,names) in GROUPS.items():
        entry = cache[group]
        if entry.get('cache_scope') != 'private' or type(entry.get('ttl_ms')) not in (int,float) or entry['ttl_ms'] != 0:
            raise ValueError('Private zero-TTL cache policy changed')
        rows = entry['tools']
        if len(rows) != len(names) or {row['name'] for row in rows} != names:
            raise ValueError('Unexpected cached business tools')
        for row in rows:
            key = 'mcp__'+group+'__'+row['name']
            value['schemas'][key] = mcp_field(row,'input_schema','inputSchema')
            value['readonly'][key] = mcp_field(row.get('annotations',{}),'read_only_hint','readOnlyHint') is True
    validate_metadata(value)
    return value


def require_empty_listing(listing, snake, camel):
    rows = mcp_field(listing,snake,camel)
    if rows != [] or mcp_field(listing,'next_cursor','nextCursor'):
        raise ValueError('Unexpected resource or prompt catalog')


def audit(event, args):
    if event == 'socket.connect':
        address = args[1]
        if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1') or address[1] not in (8077,8078,8079):
            raise PermissionError('Only local MCP discovery connections permitted')
    if event in ('subprocess.Popen','os.system','os.exec','os.posix_spawn','os.fork','os.forkpty'):
        raise PermissionError('Discovery cannot launch subprocesses')


def guard_request(request):
    if request.url.host not in ('127.0.0.1','::1') or request.url.port not in (8077,8078,8079) or request.url.scheme != 'http':
        raise PermissionError('Only local MCP requests permitted')
    if request.method == 'POST':
        value = json.loads(request.content)
        if not isinstance(value,dict) or value.get('method') not in (
                'initialize','notifications/initialized','tools/list','notifications/cancelled',
                'resources/list','resources/templates/list','prompts/list'):
            raise PermissionError('Only initialization and tool listing permitted')
    elif request.method not in ('GET','DELETE','HEAD'):
        raise PermissionError('Unexpected discovery HTTP method')


def guard_http_client(module):
    """Protect both httpx (SDK1/Hermes) and httpx2 (SDK2) send boundaries."""
    original = module.Client.send
    async_original = module.AsyncClient.send
    def send(self, request, *args, **kwargs):
        guard_request(request); return original(self, request, *args, **kwargs)
    async def async_send(self, request, *args, **kwargs):
        guard_request(request); return await async_original(self, request, *args, **kwargs)
    module.Client.send = send
    module.AsyncClient.send = async_send


@asynccontextmanager
async def discovery_transport(url):
    transport = importlib.import_module('mcp.client.streamable_http')
    legacy = getattr(transport, 'streamablehttp_client', None)
    if legacy is not None:
        async with legacy(url, timeout=5, sse_read_timeout=15) as streams:
            if len(streams) != 3: raise ValueError('Unexpected SDK1 transport streams')
            yield streams[0], streams[1]
    else:
        # SDK2 renamed the entry point, moved timeout configuration into its
        # httpx2 client, and returns two streams rather than the SDK1 triple.
        modern = getattr(transport, 'streamable_http_client')
        http = importlib.import_module('httpx2')
        async with http.AsyncClient(timeout=http.Timeout(5, read=15),
                                    trust_env=False, follow_redirects=False) as client:
            async with modern(url, http_client=client) as streams:
                if len(streams) != 2: raise ValueError('Unexpected SDK2 transport streams')
                yield streams[0], streams[1]


def silence_stderr():
    # Keep background-task and interpreter-shutdown errors private for the whole
    # child lifetime, including after ordinary redirect context managers exit.
    descriptor = os.open(os.devnull, os.O_WRONLY)
    try: os.dup2(descriptor, 2)
    finally: os.close(descriptor)


def child(mode):
    silence_stderr()
    # Install before Hermes/provider imports; inherited env is separately minimal.
    sys.addaudithook(audit)
    for name in ('httpx', 'httpx2'):
        try: module = importlib.import_module(name)
        except ModuleNotFoundError as error:
            if name == 'httpx2' and error.name == 'httpx2': continue
            raise
        guard_http_client(module)
    import yaml
    config = yaml.safe_load((HOME / 'config.yaml').read_text())
    servers = config.get('mcp_servers', {})
    if set(servers) != set(GROUPS): raise ValueError('Production MCP groups changed')
    for group,(port,_) in GROUPS.items():
        value = servers[group]
        if value.get('url') != f'http://127.0.0.1:{port}/mcp' or value.get('command') or value.get('enabled') is False:
            raise ValueError('Production MCP endpoint changed')
    if mode == 'endpoints':
        import asyncio
        from mcp import ClientSession
        async def inspect():
            result = {'schemas':{},'readonly':{}}
            for group,(port,names) in GROUPS.items():
                async with discovery_transport(f'http://127.0.0.1:{port}/mcp') as (read,write):
                    async with ClientSession(read,write) as session:
                        await session.initialize(); listing = await session.list_tools()
                        require_empty_listing(await session.list_resources(),'resources','resources')
                        require_empty_listing(await session.list_resource_templates(),'resource_templates','resourceTemplates')
                        require_empty_listing(await session.list_prompts(),'prompts','prompts')
                        metadata = endpoint_metadata(group, listing)
                        result['schemas'].update(metadata['schemas'])
                        result['readonly'].update(metadata['readonly'])
            return result
        value = asyncio.run(inspect())
    else:
        sys.path.insert(0,str(SOURCE))
        from model_tools import get_tool_definitions
        import tools.mcp_tool as mcp_tool
        from tools.mcp_tool import discover_mcp_tools, shutdown_mcp_servers, _tool_read_only_hints
        cached = set()
        original_cache = mcp_tool._register_from_cache_sync
        def from_cache(name, config, entry):
            value = original_cache(name, config, entry)
            cached.add(name)
            return value
        mcp_tool._register_from_cache_sync = from_cache
        try:
            discover_mcp_tools()
            raw=get_tool_definitions(enabled_toolsets=list(GROUPS),quiet_mode=True,skip_tool_search_assembly=True)
            utilities = {entry['schema']['name']:entry['schema'] for group in GROUPS
                         for entry in mcp_tool._build_utility_schemas(group)}
            value = registry_metadata(raw,_tool_read_only_hints,utilities)
            cache_value = cached_metadata(json.loads((HOME/'cache/mcp_schema_cache.json').read_text()))
            if not metadata_equal(value,cache_value): raise ValueError('Cache metadata mismatch')
            if cached: raise ValueError('Zero-TTL cache unexpectedly reused')
        finally: shutdown_mcp_servers()
    validate_metadata(value)
    print(PREFIX+json.dumps(value,sort_keys=True))


def verify(profile_sha):
    if os.geteuid()!=0: raise ValueError('Root required for production profile proof')
    expected = {SOURCE/'tools/mcp_tool.py':PATCHED_MCP_SHA, SOURCE/'model_tools.py':MODEL_TOOLS_SHA,
                HOME/'config.yaml':profile_sha, SOURCE/'tools/mcp_schema_cache.py':CACHE_MODULE_SHA}
    if any(sha(p)!=h for p,h in expected.items()): raise ValueError('Reviewed source/profile hash changed')
    # Use the reviewed bounded lifecycle helper, never a shell or model runner.
    helper = Path(__file__).resolve().parents[2]/'processor/hermes_runner/process.py'
    if sha(helper) != PROCESS_HELPER_SHA: raise ValueError('Reviewed process helper changed')
    spec=importlib.util.spec_from_file_location('discovery_process',helper)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    env={'HOME':'/root','HERMES_HOME':str(HOME),'PATH':'/usr/local/bin:/usr/bin:/bin',
         'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'}
    results=[]
    for mode in ('endpoints','hermes','cache'):
        completed=module.run_bounded([str(PYTHON),str(Path(__file__).resolve()),'--child',mode],
                                     timeout=90,env=env,cwd=HOME)
        rows=[line[len(PREFIX):] for line in completed.stdout.splitlines() if line.startswith(PREFIX)]
        if completed.returncode or len(rows)!=1: raise ValueError('Discovery subprocess failed')
        result=json.loads(rows[0]);validate_metadata(result);results.append(result)
        if any(sha(p)!=h for p,h in expected.items()): raise ValueError('Source/profile changed during proof')
    if not all(metadata_equal(results[0],v) for v in results[1:]): raise ValueError('Endpoint/cache metadata mismatch')
    return {'group_count':3,'tool_count':10,'readonly_verified':True,'endpoint_schemas_verified':True,
            'fresh_process_rediscovery_verified':True,'cache_metadata_verified':True,
            'cache_reuse_permitted':False,'cache_scope':'private','cache_ttl_ms':0,
            'generated_metadata_utility_count':12,'resource_prompt_catalogs_empty':True,
            'containment_scope':'Reviewed Python HTTP clients and audit hooks; not an OS sandbox',
            'model_calls':0,'tool_calls':0,
            'profile_sha256':profile_sha,'mcp_module_sha256':PATCHED_MCP_SHA,'model_tools_sha256':MODEL_TOOLS_SHA,
            'cache_module_sha256':CACHE_MODULE_SHA,'process_helper_sha256':PROCESS_HELPER_SHA,
            'proof_script_sha256':sha(Path(__file__)),
            'schema_sha256':hashlib.sha256(json.dumps(results[0]['schemas'],sort_keys=True).encode()).hexdigest()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-sha256')
    parser.add_argument('--child',choices=['endpoints','hermes','cache'],help=argparse.SUPPRESS)
    args=parser.parse_args()
    try:
        if args.child: child(args.child)
        elif args.profile_sha256: print(json.dumps(verify(args.profile_sha256),sort_keys=True))
        else: raise ValueError('Reviewed profile hash required')
    except Exception as error:
        print(json.dumps({'verified':False,'error_type':type(error).__name__}))
        raise SystemExit(1) from None
