#!/usr/bin/env python3
"""Local, read-only operational checks. No provider calls or alert messages."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import tempfile
import urllib.request

STATUS=Path('/var/lib/buttonsbebe/ops-status.json')
BACKUP=Path('/var/lib/buttonsbebe/backup-status.json')
SERVICES=('buttonsbebe-webhook','buttonsbebe-processor','buttonsbebe-kb-mcp',
          'buttonsbebe-redo-mcp','buttonsbebe-gorgias-mcp','buttonsbebe-whatsapp-connect',
          'buttonsbebe-kb-admin','helpdesk-inbox')
TIMERS=('buttonsbebe-backup','buttonsbebe-inbox-projection','buttonsbebe-heartbeat')
PORTS={'kb_socket':8077,'redo_socket':8078,'gorgias_socket':8079,'whatsapp_socket':8085,'kb_admin_socket':8087}


def command(*args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=5)
    return result.returncode,result.stdout


def active(name):
    code,output=command('systemctl','is-active',name)
    return 'ok' if code==0 and output.strip()=='active' else 'unavailable'


def last_result(name):
    code,output=command('systemctl','show',name+'.service','-p','Result','--value')
    return 'ok' if code==0 and output.strip()=='success' else 'unavailable'


def progress():
    # Read only known loop-completion markers, never treat arbitrary error logs
    # or startup messages as evidence that work is being completed.
    code,output=command('journalctl','-u','buttonsbebe-processor','--since','10 min ago',
                        '--no-pager','-q','-o','cat','-n','50','--grep','Processor idle heartbeat|Job completed')
    if code:return 'unavailable'
    for line in output.splitlines():
        try:
            row=json.loads(line)
            if row.get('level')=='INFO' and row.get('logger') in ('__main__','orchestrator') and row.get('msg') in ('Processor idle heartbeat','Job completed'):
                return 'ok'
        except (ValueError,AttributeError):
            if re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO\s+\[(?:__main__|orchestrator)\] (?:Processor idle heartbeat|Job completed)(?: \| .*)?',line):
                return 'ok'
    return 'stale'


def tcp(port):
    with socket.create_connection(('127.0.0.1',port),timeout=2):return 'ok'


def readiness(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/ready',timeout=3) as response:
        data=json.loads(response.read(65537))
    if port==8766:
        return 'ok' if data.get('ok') is True and data.get('sendAccessEnabled') is False else 'unavailable'
    if data.get('status')!='ready':return 'unavailable'
    diagnostics=data.get('diagnostics',{})
    for count,age in (('pending_jobs','oldest_pending_seconds'),('processing_jobs','oldest_processing_seconds')):
        number=diagnostics.get(count)
        if not isinstance(number,int) or isinstance(number,bool) or number<0:return 'unavailable'
        if number>0:
            value=diagnostics.get(age)
            if value is None or not isinstance(value,(int,float)) or value>1800:return 'attention'
    return 'ok'


def backup(now):
    if not BACKUP.is_file():return 'missing'
    state=json.loads(BACKUP.read_text())
    if state.get('status')!='ok':return 'attention'
    value=state.get('last_success')
    if not value:return 'missing'
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:return 'unavailable'
    age=(now-parsed).total_seconds()
    if age < -30:return 'unavailable'
    return 'ok' if age<=8*3600 else 'stale'


def disk():
    stat=os.statvfs('/var/lib')
    free=stat.f_bavail*stat.f_frsize
    return 'ok' if free>=1024**3 and stat.f_bavail/max(1,stat.f_blocks)>=.05 else 'attention'


def safe(check):
    try:return check()
    except Exception:return 'unavailable'


def collect(now=None):
    now=now or datetime.now(timezone.utc)
    checks={name:lambda name=name:active(name+'.service') for name in SERVICES}
    checks.update({name+'_timer':lambda name=name:active(name+'.timer') for name in TIMERS})
    checks.update({name+'_result':lambda name=name:last_result(name) for name in TIMERS})
    checks.update({name:lambda port=port:tcp(port) for name,port in PORTS.items()})
    checks.update(processor_progress=progress,webhook_readiness=lambda:readiness(8000),
                  inbox_readiness=lambda:readiness(8766),backup_freshness=lambda:backup(now),disk_space=disk)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values=dict(zip(checks,pool.map(safe,checks.values())))
    return {'checked_at':now.isoformat(),'status':'ok' if all(value=='ok' for value in values.values()) else 'attention',
            'checks':values,'notification_transport':'local_only'}


def write(state):
    STATUS.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.ops-status-',dir=STATUS.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(state,stream);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,STATUS)
        fd=os.open(STATUS.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:Path(name).unlink(missing_ok=True)


def main():
    os.umask(0o077)
    try:
        state=collect();write(state)
        print(json.dumps(state))
        return 0 if state['status']=='ok' else 1
    except Exception:
        print('{"status":"unavailable","notification_transport":"local_only"}')
        return 1


if __name__=='__main__':raise SystemExit(main())
