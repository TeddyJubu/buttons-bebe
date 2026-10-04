import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import sys,threading,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import redo_worker as worker

class RedoTransportTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append(value)
                if value['method']=='notifications/initialized':
                    self.send_response(204);self.end_headers();return
                result={} if value['method']=='initialize' else {'structuredContent':{'returns':[]}}
                payload=json.dumps({'jsonrpc':'2.0','id':value['id'],'result':result}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Mcp-Session-Id','fixture-session');self.end_headers();self.wfile.write(payload)
            def do_DELETE(self):
                owner.calls.append({'method':'session-delete'});self.send_response(204);self.end_headers()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        p=patch.object(worker,'MCP_URL',f'http://127.0.0.1:{self.server.server_port}/mcp');p.start();self.addCleanup(p.stop)
    def test_real_transport_calls_only_fixed_read_and_closes_session(self):
        self.assertEqual(worker.call_mcp('12345'),{'returns':[]})
        self.assertEqual([x['method'] for x in self.calls],['initialize','notifications/initialized','tools/call','session-delete'])
        self.assertEqual(self.calls[2]['params'],{'name':'get_returns_for_order','arguments':{'order_name':'12345'}})
    def test_mutation_tool_is_refused_before_dispatch(self):
        with worker.MCP() as client:
            with self.assertRaises(worker.RedoUnavailable):client.call('refund',{'id':1})
        self.assertNotIn('tools/call',[x['method'] for x in self.calls])
