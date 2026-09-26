"""Replay captured synthetic drafts through the real console policy, without I/O.

This is post-model policy evidence, not queue/database/provider integration proof.
Run against an immutable source archive; raw result records remain untouched.
"""
from __future__ import annotations
import argparse
import asyncio
import copy
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parent.parent
DATABASE_FUNCTIONS = ('claim_job claim_owner_alert finish_owner_alert get_job_result complete_job fail_job get_next_pending_job get_pending_job_window get_job_stats init_db requeue_stale_jobs set_setting').split()


def forbidden(*args, **kwargs):
    raise RuntimeError('Postprocess replay attempted forbidden side effect')


def worker(input_path):
    records = json.loads(Path(input_path).read_text())
    if not isinstance(records, list) or len(records) > 48:
        raise ValueError('Expected at most 48 captured QA records')
    # Installed before production imports: no config/credential loader or DB module.
    config = types.ModuleType('config'); config.get_settings = forbidden
    db = types.ModuleType('bb_webhook.database')
    for name in DATABASE_FUNCTIONS: setattr(db, name, forbidden)
    sys.modules['config'] = config
    sys.modules['bb_webhook.config'] = config
    sys.modules['bb_webhook.database'] = db
    def audit(event, args):
        if event in {'socket.connect', 'socket.connect_ex', 'socket.bind', 'socket.getaddrinfo', 'socket.sendto', 'socket.sendmsg', 'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn', 'os.fork', 'sqlite3.connect'}:
            forbidden()
        if event == 'open':
            path = args[0]
            if isinstance(path, (str, bytes)):
                p = Path(os.fsdecode(path))
                if p.name in {'.env', 'auth.json', 'config.yaml'} or p.suffix in {'.sqlite3', '.db'}:
                    forbidden()
    sys.addaudithook(audit)
    sys.path.insert(0, str(REPO/'processor'))
    logging.disable(logging.CRITICAL)
    orchestrator = importlib.import_module('orchestrator')
    output = []
    for index, record in enumerate(records, 1):
        scenario = record['scenario']
        if not scenario['email'].endswith('@example.com') or record['id'] != scenario['id']:
            raise ValueError('Only matched synthetic scenarios may be replayed')
        captured = copy.deepcopy(record['result'])
        payload = {'ticket_id': 900000000+index, 'message_id': 'qa-message-'+record['id'],
                   'message_text': scenario['message'], 'ticket_subject': scenario['subject'],
                   'customer_email': scenario['email'], 'intents': [{'name': scenario.get('intent', '')}]}
        save = Mock()
        with patch.object(orchestrator, 'process_ticket_with_hermes', return_value=captured) as model, \
             patch.object(orchestrator, '_save_result_to_webhook', save), \
             patch.object(orchestrator, 'send_whatsapp', forbidden):
            summary = asyncio.run(orchestrator.process_customer_message({'id':index,'payload':json.dumps(payload)}))
        model.assert_called_once(); save.assert_called_once()
        saved = save.call_args.kwargs
        output.append({'id':record['id'], 'console_result':saved['hermes_result'],
                       'console_draft':saved['draft_text'], 'summary':summary,
                       'captured_persistence_calls':1,
                       'payload_scope':{'synthetic':True, 'intent_names':[scenario.get('intent','')]}})
    modules = [m for m in sys.modules.values() if getattr(m, '__file__', None)]
    hashes = {}
    for module in modules:
        p = Path(module.__file__).resolve()
        if p.is_relative_to(REPO) and p.suffix == '.py':
            hashes[str(p.relative_to(REPO))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {'scope':'captured runner replay through real orchestrator classifier and console policy; no database/network/alerts/model',
            'source_sha256':hashes, 'records':output}


def replay(records):
    # Fresh process prevents module/environment contamination from model harness.
    from qa_harness import isolated_run, minimal_environment
    with tempfile.TemporaryDirectory(prefix='bb-qa-postprocess-') as temp:
        root = Path(temp); source = root/'input.json'
        source.write_text(json.dumps(records)); source.chmod(0o600)
        result = isolated_run([sys.executable, '-I', str(Path(__file__).resolve()), '--worker', str(source)],
                              timeout=30, env=minimal_environment(root), cwd=root)
        if result.returncode: raise RuntimeError('Isolated postprocess failed')
        return json.loads(result.stdout)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args=parser.parse_args()
    if args.worker:
        try: print(json.dumps(worker(args.worker)))
        except Exception: raise SystemExit('Postprocess failed safely') from None
    else:
        if not args.input or not args.output or args.output.exists(): parser.error('New output and existing input required')
        from qa_harness import atomic_json
        data=replay(json.loads(args.input.read_text()))
        atomic_json(args.output, data)
        print(json.dumps({'postprocessed':len(data['records'])}))
