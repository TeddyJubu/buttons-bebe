#!/usr/bin/env python3
"""Manual reviewed WhatsApp dependency switch; never modify auth state or other units."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import signal
import subprocess
import tempfile
import time
import urllib.request
from recovery_policy import open_regular,open_parent,private_directory

LIVE=Path('/root/Buttonsbebe Agent/whatsapp-connect')
CANDIDATE=Path('/opt/buttonsbebe/whatsapp-candidate-18ca775')
BACKUPS=Path('/opt/buttonsbebe/backups')
LOCK=Path('/run/lock/buttonsbebe-deploy.lock')
UNIT='buttonsbebe-whatsapp-connect.service'
FILES=('server.js','package.json','package-lock.json')
MAX_FILES=30000
MAX_BYTES=1024*1024*1024


def digest(path):
    fd=open_regular(str(path));count=0;h=hashlib.sha256()
    try:
        while block:=os.read(fd,1024*1024):
            count+=len(block)
            if count>MAX_BYTES:raise ValueError('File limit exceeded')
            h.update(block)
    finally:os.close(fd)
    return h.hexdigest()


def directory(path):
    parent,name=open_parent(str(path))
    try:fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    finally:os.close(parent)
    return fd


def tree_digest(path):
    fd=directory(path);os.close(fd)
    records=[];total=0
    for root,dirs,files in os.walk(path,followlinks=False):
        dirs.sort();files.sort()
        for name in sorted(dirs+files):
            child=Path(root)/name;st=child.lstat();relative=child.relative_to(path).as_posix()
            if stat.S_ISLNK(st.st_mode):
                target=os.readlink(child);resolved=os.path.normpath(os.path.join(child.parent,target))
                if os.path.isabs(target) or not resolved.startswith(str(path)+'/'):raise ValueError('External module symlink')
                records.append((relative,'link',target))
                if name in dirs:dirs.remove(name)
            elif stat.S_ISREG(st.st_mode):
                total+=st.st_size
                if total>MAX_BYTES:raise ValueError('Module byte limit exceeded')
                records.append((relative,'file',digest(child)))
            elif stat.S_ISDIR(st.st_mode):records.append((relative,'directory'))
            else:raise ValueError('Special module member')
            if len(records)>MAX_FILES:raise ValueError('Module member limit exceeded')
    return hashlib.sha256(json.dumps(records,separators=(',',':')).encode()).hexdigest()


def patched_server(source):
    """Redact the legacy secret-bearing startup log; pass the no-secret format through.

    The deployed server.js now logs via pino (`log.info(...%d..., PORT)`) with
    no BASE interpolation, so there is nothing to redact — but the redaction
    must survive a rollback from a live dir still on the old console.log
    format, and any source whose startup log is not one of the two known-good
    shapes (marker twice, unknown or missing log line) is still refused
    rather than guessed at.
    """
    marker=b' base=${BASE}'
    pino_startup=b'log.info("whatsapp-connect listening on 127.0.0.1:%d", PORT)'
    if marker in source:
        if source.count(marker)!=1:raise ValueError('Expected unique startup log marker')
        line=next(line for line in source.splitlines() if marker in line)
        if b'console.log(`whatsapp-connect listening on 127.0.0.1:${PORT}' not in line:raise ValueError('Startup log format changed')
        return source.replace(marker,b'',1)
    if source.count(pino_startup)==1:
        return source
    raise ValueError('Startup log format changed')


def inventory(live=LIVE,candidate=CANDIDATE):
    return {'schema':1,'live':{**{name:digest(live/name) for name in FILES},'node_modules':tree_digest(live/'node_modules')},
            'candidate':{name:digest(candidate/name) for name in ('package.json','package-lock.json')}|{'node_modules':tree_digest(candidate/'node_modules')},
            'patched_server_sha256':hashlib.sha256(patched_server((live/'server.js').read_bytes())).hexdigest()}


def validate_plan(plan):
    if not isinstance(plan,dict) or set(plan)!={'schema','live','candidate','patched_server_sha256'} or type(plan['schema']) is not int or plan['schema']!=1:raise ValueError('Invalid approved switch plan')
    for group,names in (('live',{*FILES,'node_modules'}),('candidate',{'package.json','package-lock.json','node_modules'})):
        if not isinstance(plan[group],dict) or set(plan[group])!=names:raise ValueError('Incomplete reviewed fingerprints')
        if any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in plan[group].values()):raise ValueError('Invalid reviewed fingerprint')
    if not isinstance(plan['patched_server_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',plan['patched_server_sha256']):raise ValueError('Invalid patched fingerprint')


@contextmanager
def deployment_lock(path=LOCK):
    parent,name=open_parent(str(path))
    try:fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=parent)
    finally:os.close(parent)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('Invalid deployment lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);yield
    finally:os.close(fd)


def auth_empty(live):
    fd=directory(live/'auth')
    try:
        if os.listdir(fd):raise ValueError('Auth store is no longer empty; stop for pairing review')
    finally:os.close(fd)


def system(action):
    result=subprocess.run(['systemctl',action,UNIT],capture_output=True,timeout=5 if action=='is-active' else 45)
    if action=='is-active':return result.returncode==0
    if result.returncode:raise RuntimeError('Named WhatsApp unit operation failed')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):return None


def status():
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open('http://127.0.0.1:8085/wa/status',timeout=3) as response:
        raw=response.read(128*1024+1)
        if len(raw)>128*1024:raise ValueError('Status response too large')
        value=json.loads(raw)
    return value.get('state') if isinstance(value,dict) else None


def wait_ready(service,state,attempts=20,pause=2):
    deadline=time.monotonic()+60
    for _ in range(attempts):
        if time.monotonic()>deadline:break
        try:
            if service('is-active') and state() in {'qr','connected'}:return
        except Exception:pass
        time.sleep(pause)
    raise RuntimeError('WhatsApp readiness deadline exceeded')


def atomic_file(path,content,mode):
    fd,name=tempfile.mkstemp(prefix='.wa-switch-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as output:
            os.fchmod(output.fileno(),mode);output.write(content);output.flush();os.fsync(output.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


def sync_directories(*paths):
    for path in paths:
        fd=directory(path)
        try:os.fsync(fd)
        finally:os.close(fd)


def journal(path,value):
    atomic_file(path,json.dumps(value).encode(),0o600)
    fd=directory(path.parent)
    try:os.fsync(fd)
    finally:os.close(fd)


def apply(plan,live=LIVE,candidate=CANDIDATE,backups=BACKUPS,lock=LOCK,service=system,state=status,ready=wait_ready):
    validate_plan(plan)
    with deployment_lock(lock):
        if inventory(live,candidate)!=plan:raise ValueError('Pre-reviewed file/module fingerprints changed')
        if not service('is-active') or state()!='qr':raise ValueError('Require active WhatsApp unit in QR state')
        auth_empty(live)
        before=json.loads((live/'package-lock.json').read_text());after=json.loads((candidate/'package-lock.json').read_text())
        if after['packages']['node_modules/qs']['version']!='6.16.0':raise ValueError('Candidate qs version differs from review')
        name='node_modules/@whiskeysockets/baileys'
        if before['packages'][name]['version']!=after['packages'][name]['version']:raise ValueError('Baileys version change is not approved')
        for checked in (live,candidate,backups):
            fd=directory(checked);os.close(fd)
        if len({live.stat().st_dev,candidate.stat().st_dev,backups.stat().st_dev})!=1:raise ValueError('Atomic module moves require one filesystem')
        private=Path(tempfile.mkdtemp(prefix='manual-wa-dependency-',dir=backups));os.chmod(private,0o700)
        originals={name:(live/name).read_bytes() for name in FILES}
        modes={name:stat.S_IMODE((live/name).stat().st_mode) for name in FILES}
        for name in FILES:atomic_file(private/name,originals[name],0o600)
        receipt={'schema':1,'phase':'prepared','approved':plan,'original_modes':modes,'auth_state_touched':False,
                 'rollback_server_variant':'original-with-reviewed-startup-log-redaction',
                 'rollback_server_sha256':plan['patched_server_sha256']}
        journal(private/'switch.json',receipt)
        stop_attempted=False;old_moved=False;new_moved=False
        try:
            stop_attempted=True;service('stop')
            if service('is-active'):raise RuntimeError('WhatsApp unit did not stop')
            auth_empty(live)
            if inventory(live,candidate)!=plan:raise ValueError('Fingerprints changed before mutation')
            receipt['phase']='stopped';journal(private/'switch.json',receipt)
            os.rename(live/'node_modules',private/'node_modules');old_moved=True
            os.rename(candidate/'node_modules',live/'node_modules');new_moved=True
            atomic_file(live/'server.js',patched_server(originals['server.js']),modes['server.js'])
            for name in ('package.json','package-lock.json'):atomic_file(live/name,(candidate/name).read_bytes(),modes[name])
            sync_directories(live,candidate,private)
            receipt['phase']='candidate-installed';journal(private/'switch.json',receipt)
            service('start');ready(service,state)
            receipt['phase']='verified';journal(private/'switch.json',receipt)
            return {'status':'verified','unit':UNIT,'recovery_id':private.name,'auth_state_touched':False}
        except Exception:
            try:
                if stop_attempted:service('stop')
                if digest(live/'server.js') not in {plan['live']['server.js'],plan['patched_server_sha256']}:
                    raise RuntimeError('Unexpected concurrent server edit; manual recovery required')
                for name in ('package.json','package-lock.json'):
                    if digest(live/name) not in {plan['live'][name],plan['candidate'][name]}:
                        raise RuntimeError('Unexpected concurrent package edit; manual recovery required')
                if new_moved:os.rename(live/'node_modules',private/'failed-node_modules')
                if old_moved:os.rename(private/'node_modules',live/'node_modules')
                if stop_attempted:
                    # Never reintroduce the secret-bearing startup log when
                    # restarting the original dependency set after failure.
                    atomic_file(live/'server.js',patched_server(originals['server.js']),modes['server.js'])
                    for name in ('package.json','package-lock.json'):
                        atomic_file(live/name,originals[name],modes[name])
                sync_directories(live,candidate,private)
                if stop_attempted:service('start');ready(service,state)
                receipt['phase']='rolled-back';journal(private/'switch.json',receipt)
            except Exception:
                receipt['phase']='manual-recovery-required';journal(private/'switch.json',receipt)
                raise RuntimeError('WhatsApp switch failed and rollback requires operator review') from None
            raise RuntimeError('WhatsApp switch failed; old modules/source restored and readiness verified') from None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',action='store_true')
    parser.add_argument('--approved-plan',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();os.umask(0o077)
    if os.geteuid()!=0:raise SystemExit('Run manually as root')
    def interrupted(_number,_frame):raise RuntimeError('Switch interrupted')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    try:
        if args.inventory and not args.apply:
            with deployment_lock():print(json.dumps(inventory()))
        elif args.apply and args.approved_plan and not args.inventory:
            fd=open_regular(str(args.approved_plan))
            with os.fdopen(fd) as stream:
                st=os.fstat(stream.fileno())
                if st.st_uid!=0 or st.st_mode&0o077 or st.st_size>65536:raise ValueError('Approved plan must be private and bounded')
                plan=json.load(stream)
            print(json.dumps(apply(plan)))
        else:raise ValueError('Use inventory or explicit apply with approved plan')
    except Exception as error:raise SystemExit(f'WhatsApp dependency switch failed: {type(error).__name__}; inspect private recovery journal, no values printed') from None

if __name__=='__main__':main()
