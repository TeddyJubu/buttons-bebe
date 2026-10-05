"""Internal results carry a dedicated credential only to the exact local API."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import orchestrator
from hermes_runner.runner import _run_environment

SECRET='synthetic-result-secret-0123456789'


class ResultAuthTests(unittest.TestCase):
    def test_credential_header_and_no_redirect_handler(self):
        response=Mock(status=200);response.read.return_value=b'{"status":"ok","generation_state":"ready"}'
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=response
        with patch.object(orchestrator,'get_settings',return_value=SimpleNamespace(processor_result_secret=SECRET)),patch('urllib.request.build_opener',return_value=opener) as builder,patch.dict(os.environ,{'DASHBOARD_RESULT_URL':'http://127.0.0.1:8000/dashboard/api/results'}):
            orchestrator._save_result_to_webhook(123,'synthetic',1,dict(generation_attempt_id=1,generation_state='ready'))
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'),'Bearer '+SECRET)
        self.assertEqual(builder.call_args.args[0].proxies,{})
        self.assertIsNone(builder.call_args.args[1].redirect_request(None,None,None,None,None,None))

    def test_ambient_proxy_cannot_intercept_synthetic_loopback_request(self):
        import urllib.request
        import threading
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.headers.get('Authorization'))
                self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ok","generation_state":"ready"}')
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        actual_builder=urllib.request.build_opener
        response=Mock(status=200);response.read.return_value=b'{"status":"ok","generation_state":"ready"}'
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        mocked=Mock();mocked.open.return_value=response
        with patch.object(orchestrator,'get_settings',return_value=SimpleNamespace(processor_result_secret=SECRET)),patch('urllib.request.build_opener',return_value=mocked) as builder,patch.dict(os.environ,{'DASHBOARD_RESULT_URL':'http://127.0.0.1:8000/dashboard/api/results'}):
            orchestrator._save_result_to_webhook(123,'synthetic',1,dict(generation_attempt_id=1,generation_state='ready'))
        with patch.dict(os.environ,{'http_proxy':'http://127.0.0.1:1','HTTP_PROXY':'http://127.0.0.1:1','no_proxy':'','NO_PROXY':''}):
            opener=actual_builder(*builder.call_args.args)
            request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/dashboard/api/results',data=b'{}',headers={'Authorization':'Bearer '+SECRET})
            with opener.open(request,timeout=2) as result:self.assertEqual(result.status,200)
        self.assertEqual(received,['Bearer '+SECRET])

    def test_missing_or_invalid_secret_fails_before_network(self):
        for value in ('','short','x'*32+'\n'):
            with patch.object(orchestrator,'get_settings',return_value=SimpleNamespace(processor_result_secret=value)),patch('urllib.request.build_opener') as transport,patch.dict(os.environ,{'DASHBOARD_RESULT_URL':'http://127.0.0.1:8000/dashboard/api/results'}):
                with self.assertRaises(RuntimeError):orchestrator._save_result_to_webhook(123,'synthetic',1,dict(generation_attempt_id=1,generation_state='ready'))
                transport.assert_not_called()

    def test_credential_cannot_be_sent_to_remote_or_redirect_destination(self):
        for url in ('https://evil.invalid/dashboard/api/results','http://127.0.0.1:8766/dashboard/api/results','http://127.0.0.1:8000/dashboard/api/results?secret=unused'):
            with patch.dict(os.environ,{'DASHBOARD_RESULT_URL':url}),patch('urllib.request.build_opener') as transport:
                with self.assertRaises(RuntimeError):orchestrator._save_result_to_webhook(123,'synthetic',1,dict(generation_attempt_id=1,generation_state='ready'))
                transport.assert_not_called()

    def test_missing_attempt_or_state_fails_before_transport(self):
        for result in ({}, {'generation_state':'ready'}, {'generation_attempt_id':1}):
            with patch.object(orchestrator, 'get_settings', return_value=SimpleNamespace(processor_result_secret=SECRET)), patch('urllib.request.build_opener') as transport, patch.dict(os.environ, {'DASHBOARD_RESULT_URL':'http://127.0.0.1:8000/dashboard/api/results'}):
                with self.assertRaises(RuntimeError):
                    orchestrator._save_result_to_webhook(123, 'synthetic', 1, result)
                transport.assert_not_called()

    def test_hermes_child_never_inherits_internal_result_credential(self):
        with patch.dict(os.environ,{'PROCESSOR_RESULT_SECRET':SECRET},clear=True):
            child=_run_environment(SimpleNamespace(hermes_home='/synthetic',hermes_path='/usr/bin'))
        self.assertNotIn('PROCESSOR_RESULT_SECRET',child)
        self.assertNotIn(SECRET,child.values())


if __name__=='__main__':unittest.main()
