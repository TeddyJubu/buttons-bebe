import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import sys,threading,time,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import redo_worker as worker

class RedoTransportTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.headers=[];self.mode='json';self.delays={};owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,value):
                owner.calls.append(value);owner.headers.append(dict(self.headers))
                method=value['method'];time.sleep(owner.delays.get(method,0))
                if method in ('notifications/initialized','session-delete'):
                    self.send_response(500 if owner.mode=='notification-error' and method=='notifications/initialized' else 204);self.end_headers();return
                result={} if method=='initialize' else {'structuredContent':{'returns':[]}}
                if owner.mode=='tool-error' and method=='tools/call':result={'isError':True,'content':[{'type':'text','text':'synthetic error'}]}
                if owner.mode=='api-error' and method=='tools/call':result={'structuredContent':{'error':'synthetic unavailable'}}
                envelope={'jsonrpc':'2.0','id':value['id'],'result':result}
                if owner.mode=='init-error' and method=='initialize':envelope={'jsonrpc':'2.0','id':value['id'],'error':{'code':-1}}
                if owner.mode=='wrong-id' and method=='tools/call':envelope['id']=999
                if owner.mode=='json-oversized' and method=='tools/call':
                    envelope['result']['structuredContent']['padding']='x'*worker.MAX_RESPONSE_BYTES
                payload=json.dumps(envelope).encode()
                if owner.mode=='header-drip' and method=='tools/call':
                    for byte in b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n'+payload:
                        self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.015)
                    return
                sse=owner.mode.startswith('sse') and method=='tools/call'
                self.send_response(200);self.send_header('Content-Type','text/event-stream' if sse else 'application/json');self.send_header('Mcp-Session-Id','fixture-session');self.end_headers()
                if sse:
                    if owner.mode=='sse-oversized':payload=b'data: '+b'x'*(worker.MAX_RESPONSE_BYTES+1)+b'\n\n'
                    elif owner.mode=='sse-wrong-only':payload=b'data: '+json.dumps({'jsonrpc':'2.0','id':999,'result':{}}).encode()+b'\n\n'
                    else:
                        # Ignore a different event ID, join multiline data, accept CRLF/comments.
                        wrong=b'data: '+json.dumps({'jsonrpc':'2.0','id':999,'result':{}}).encode()+b'\n\n'
                        parts=payload.split(b',',1)
                        payload=b': keepalive\r\n'+wrong+b'data: '+parts[0]+b',\r\ndata: '+parts[1]+b'\r\n\r\n'
                    if owner.mode=='sse-drip':
                        for byte in payload:self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.015)
                        return
                self.wfile.write(payload)
            def do_POST(self):
                try:self.reply(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                except (BrokenPipeError,ConnectionResetError):pass
            def do_DELETE(self):
                try:self.reply({'method':'session-delete'})
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        p=patch.object(worker,'MCP_URL',f'http://127.0.0.1:{self.server.server_port}/mcp');p.start();self.addCleanup(p.stop)

    def assert_session_headers(self):
        self.assertNotIn('Mcp-Session-Id',self.headers[0])
        for headers in self.headers[1:]:self.assertEqual(headers.get('Mcp-Session-Id'),'fixture-session')

    def test_real_transport_calls_only_fixed_read_and_closes_session(self):
        self.assertEqual(worker.call_mcp('12345'),{'returns':[]})
        self.assertEqual([x['method'] for x in self.calls],['initialize','notifications/initialized','tools/call','session-delete'])
        self.assertEqual(self.calls[2]['params'],{'name':'get_returns_for_order','arguments':{'order_name':'12345'}})
        self.assert_session_headers()

    def test_mutation_tool_is_refused_before_dispatch(self):
        with worker.MCP() as client:
            with self.assertRaises(worker.RedoUnavailable):client.call('refund',{'id':1})
        self.assertNotIn('tools/call',[x['method'] for x in self.calls])

    def test_sse_multiline_and_mismatched_event_ids(self):
        self.mode='sse';self.assertEqual(worker.call_mcp('12345'),{'returns':[]});self.assert_session_headers()

    def test_json_sse_oversized_and_missing_matching_result_are_refused(self):
        for mode in ('json-oversized','sse-oversized','sse-wrong-only'):
            with self.subTest(mode):
                self.calls.clear();self.headers.clear();self.mode=mode
                with self.assertRaises(worker.RedoUnavailable):worker.call_mcp('12345')
                self.assertEqual(self.calls[-1]['method'],'session-delete');self.assert_session_headers()

    def test_tool_api_and_json_id_errors_close_the_bound_session(self):
        for mode in ('tool-error','api-error','wrong-id','init-error','notification-error'):
            with self.subTest(mode):
                self.calls.clear();self.headers.clear();self.mode=mode
                with self.assertRaises(worker.RedoUnavailable):worker.call_mcp('12345')
                self.assertEqual(self.calls[-1]['method'],'session-delete');self.assert_session_headers()

    def test_timeout_is_shared_across_all_rpc_phases(self):
        self.delays={'initialize':.06,'notifications/initialized':.06,'tools/call':.06}
        start=time.monotonic()
        with self.assertRaises(worker.RedoUnavailable):worker.call_mcp('12345',timeout=.16)
        self.assertLess(time.monotonic()-start,.28)
        self.assertEqual([c['method'] for c in self.calls],['initialize','notifications/initialized','tools/call'])
        self.delays={};self.assertEqual(worker.call_mcp('12345'),{'returns':[]}) # Capacity released after timeout.

    def test_deadline_interrupts_slow_body_and_open_header_drips(self):
        for mode in ('sse-drip','header-drip'):
            with self.subTest(mode):
                self.mode=mode;start=time.monotonic()
                with self.assertRaises(worker.RedoUnavailable):worker.call_mcp('12345',timeout=.13)
                self.assertLess(time.monotonic()-start,.25)
                self.mode='json';self.assertEqual(worker.call_mcp('12345'),{'returns':[]})

    def test_cleanup_uses_remaining_session_deadline(self):
        self.delays={'session-delete':.4};start=time.monotonic()
        self.assertEqual(worker.call_mcp('12345',timeout=.12),{'returns':[]})
        self.assertLess(time.monotonic()-start,.24)
        self.assertEqual(self.calls[-1]['method'],'session-delete');self.assert_session_headers()

    def test_timeout_validation_and_capacity_wait_are_bounded(self):
        for timeout in (0,-1,26,float('inf'),float('nan'),True,'1'):
            with self.subTest(timeout):
                with self.assertRaises(ValueError):worker.MCP(timeout)
        worker.CAPACITY.acquire();self.addCleanup(worker.CAPACITY.release)
        start=time.monotonic()
        with self.assertRaises(worker.RedoUnavailable):worker.call_mcp('12345',timeout=.04)
        self.assertLess(time.monotonic()-start,.15);self.assertEqual(self.calls,[])
