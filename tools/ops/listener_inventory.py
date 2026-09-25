#!/usr/bin/env python3
"""Count-only/identity-only listener inspection; no cmdlines, URLs or env output."""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess

APP_PORTS = {8000, 8077, 8078, 8079, 8085, 8087, 8767, 9119, 8099, 4100, 3210}
KNOWN_UNITS = {'buttonsbebe-hermes-dashboard.service', 'exchange-proxy.service',
               'helpdesk-inbox2.service', 'buttonsbebe-webhook.service',
               'buttonsbebe-kb-mcp.service', 'buttonsbebe-redo-mcp.service',
               'buttonsbebe-gorgias-mcp.service', 'buttonsbebe-whatsapp-connect.service',
               'buttonsbebe-kb-admin.service'}


def inspect():
    result = subprocess.run(['ss', '-H', '-ltnp'], capture_output=True, text=True, check=True)
    records = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4: continue
        address = fields[3]
        try: port = int(address.rsplit(':', 1)[1])
        except (IndexError, ValueError): continue
        if port not in APP_PORTS: continue
        host = address.rsplit(':', 1)[0].strip('[]')
        record = {'port': port, 'loopback': host in ('127.0.0.1', '::1'), 'owners': []}
        for pid in re.findall(r'pid=(\d+)', line):
            try:
                proc = Path('/proc') / pid
                cmd = (proc / 'cmdline').read_bytes().split(b'\0')
                cgroups = (proc / 'cgroup').read_text()
                units = re.findall(r'/([a-zA-Z0-9_.@-]+\.service)', cgroups)
                record['owners'].append({'pid': int(pid),
                    'unit': next((unit for unit in units if unit in KNOWN_UNITS), 'other-or-unmanaged'),
                    'python_preview': b'http.server' in cmd,
                    'cwd_deleted': str((proc / 'cwd').readlink()).endswith(' (deleted)')})
            except (OSError, ProcessLookupError):
                record['owners'].append({'pid': int(pid), 'changed_during_snapshot': True})
        records.append(record)
    return records


if __name__ == '__main__':
    print(json.dumps(inspect(), indent=2))
