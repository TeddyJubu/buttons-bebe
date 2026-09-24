"""Fail QA before model use when Hermes loses MCP schemas or readonly hints."""
import json

PROBE = '''
import sys,json
sys.path.insert(0,sys.argv[1])
from model_tools import get_tool_definitions
try:
 from tools.mcp_tool_discovery import discover_mcp_tools
 from tools.mcp_tool_lifecycle import shutdown_mcp_servers
 from tools.mcp_tool_schema import _build_utility_schemas
 from tools.mcp_tool_scope import _server_key
 from tools.mcp_tool import _tool_read_only_hints
except ImportError:
 from tools.mcp_tool import discover_mcp_tools,shutdown_mcp_servers,_tool_read_only_hints,_build_utility_schemas
 try:
  from tools.mcp_tool import _server_key
 except ImportError:
  def _server_key(name,scope=None,current=True):
   return name
try:
 from tools.mcp_tool_common import _core
 _scope=_core._mcp_registry_scope()
except Exception:
 _scope=None
def _norm(value):
 if isinstance(value,list):
  return [_norm(item) for item in value]
 if not isinstance(value,dict):
  return value
 out={key:_norm(item) for key,item in value.items() if not (key=='required' and item==[])}
 if 'properties' in out and isinstance(out['properties'],dict):
  out['properties']={name:_norm(item) for name,item in out['properties'].items()}
 if 'parameters' in out:
  out['parameters']=_norm(out['parameters'])
 return out
try:
 groups=json.loads(sys.argv[2])
 discover_mcp_tools()
 raw=get_tool_definitions(enabled_toolsets=groups,quiet_mode=True,skip_tool_search_assembly=True)
 utilities={item['schema']['name']:_norm(item['schema']) for group in groups for item in _build_utility_schemas(group)}
 names=[item['function']['name'] for item in raw]
 expected_utilities={'mcp__'+group+'__'+name for group in groups for name in ('list_resources','read_resource','list_prompts','get_prompt')}
 if set(utilities)!=expected_utilities or len(names)!=len(set(names)):
  raise ValueError('Unexpected or duplicate utility definitions')
 selected={item['function']['name']:_norm(item['function']) for item in raw if item['function']['name'] in utilities}
 if selected!=utilities: raise ValueError('Missing or modified metadata utility')
 schemas={item['function']['name']:item['function']['parameters'] for item in raw if item['function']['name'] not in utilities}
 hints={}
 for group in groups:
  table=_tool_read_only_hints.get(_server_key(group,scope=_scope,current=False),{})
  if not table:
   table=_tool_read_only_hints.get(group,{})
  for name,value in table.items():
   hints['mcp__'+group+'__'+name]=value is True
 print('QA_METADATA='+json.dumps({'schemas':schemas,'readonly':hints,'metadata_utilities_verified':True},sort_keys=True))
finally:
 shutdown_mcp_servers()
'''


def canonical_nullable_schema(node):
    """Accept only the observed Hermes optional-string normalization.

    Installed Hermes _normalize_mcp_input_schema delegates to
    strip_nullable_unions(..., keep_nullable_hint=True). A two-branch
    string/null union becomes type=string, nullable=true. Preserve every other
    field exactly, including default, enum, required and length constraints.
    Other union shapes are deliberately not treated as equivalent.
    """
    if isinstance(node, list):
        return [canonical_nullable_schema(value) for value in node]
    if not isinstance(node, dict):
        return node
    out = dict(node)
    if out.get('required') == []:
        del out['required']
    # Recurse through schema-valued keywords plus whole-value parameters blocks.
    # Defaults, enum/const values and extension metadata are instance data,
    # even when they resemble schemas.
    if isinstance(out.get('properties'), dict):
        out['properties'] = {name: canonical_nullable_schema(value) for name, value in out['properties'].items()}
    for key in ('patternProperties', '$defs', 'definitions'):
        if isinstance(out.get(key), dict):
            out[key] = {name: canonical_nullable_schema(value) for name, value in out[key].items()}
    for key in ('items', 'additionalProperties', 'anyOf', 'oneOf', 'allOf', 'not', 'parameters'):
        if key in out:
            out[key] = canonical_nullable_schema(out[key])
    union = out.get('anyOf')
    if (isinstance(union, list) and len(union) == 2
            and {'type': 'string'} in union and {'type': 'null'} in union
            and not {'type', 'nullable', 'oneOf', 'allOf'}.intersection(out)):
        del out['anyOf']
        out['type'] = 'string'
        out['nullable'] = True
    return out


def metadata_equal(actual, expected):
    def canonical_metadata(value):
        if not isinstance(value, dict) or not isinstance(value.get('schemas'), dict):
            return value
        return {**value, 'schemas': {name: canonical_nullable_schema(schema) for name, schema in value['schemas'].items()}}
    # JSON serialization keeps false different from 0 and null from missing.
    return json.dumps(canonical_metadata(actual), sort_keys=True) == json.dumps(canonical_metadata(expected), sort_keys=True)


def prove_metadata(python, source, env, home, expected_schemas, groups, run):
    if not expected_schemas:
        raise ValueError('QA requires endpoint schema evidence')
    expected = {'schemas': expected_schemas,
                'readonly': {name: True for name in expected_schemas}, 'metadata_utilities_verified':True}
    for phase in ('initial', 'fresh-process-rediscovery'):
        result = run([str(python), '-c', PROBE, str(source), json.dumps(groups)],
                     timeout=90, env=env, cwd=home)
        rows = [line[len('QA_METADATA='):] for line in result.stdout.splitlines()
                if line.startswith('QA_METADATA=')]
        if result.returncode or len(rows) != 1 or not metadata_equal(json.loads(rows[0]), expected):
            raise ValueError('Hermes readonly metadata/schema mismatch during ' + phase + '; no model calls permitted')
    return {'readonly_verified': True, 'endpoint_schemas_verified': True,
            'fresh_process_rediscovery_verified': True, 'metadata_utility_count': 4 * len(groups), 'nullable_comparison': 'exact-string-null-union-only', 'tool_count': len(expected_schemas)}
