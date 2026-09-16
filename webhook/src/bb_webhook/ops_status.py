"""Sanitized authenticated view of the separate local operations monitor."""
from datetime import datetime, timezone
import json
from pathlib import Path

STATUS=Path('/var/lib/buttonsbebe/ops-status.json')
CHECKS={'buttonsbebe-webhook','buttonsbebe-processor','buttonsbebe-kb-mcp',
        'buttonsbebe-redo-mcp','buttonsbebe-gorgias-mcp','buttonsbebe-whatsapp-connect',
        'buttonsbebe-kb-admin','helpdesk-inbox','buttonsbebe-backup_timer',
        'buttonsbebe-inbox-projection_timer','buttonsbebe-heartbeat_timer',
        'buttonsbebe-backup_result','buttonsbebe-inbox-projection_result',
        'buttonsbebe-heartbeat_result','kb_socket','redo_socket','gorgias_socket',
        'whatsapp_socket','kb_admin_socket','processor_progress','webhook_readiness',
        'inbox_readiness','backup_freshness','disk_space'}
VALUES={'ok','attention','missing','stale','unavailable'}


def summary(now=None):
    unavailable={'status':'unavailable','checks':{},'notification_transport':'local_only'}
    try:
        if not STATUS.is_file():return {**unavailable,'status':'missing'}
        if STATUS.is_symlink() or STATUS.stat().st_size>16384:return unavailable
        data=json.loads(STATUS.read_text())
        when=datetime.fromisoformat(data['checked_at'])
        if when.tzinfo is None:return unavailable
        age=((now or datetime.now(timezone.utc))-when).total_seconds()
        if age < -30:return unavailable
        checks={name:value for name,value in data['checks'].items() if name in CHECKS and isinstance(value,str) and value in VALUES}
        complete=set(checks)==CHECKS
        status='stale' if age>180 else 'ok' if complete and all(value=='ok' for value in checks.values()) else 'attention'
        return {'status':status,'checked_at':when.isoformat(),'age_seconds':max(0,int(age)),
                'checks':checks,'notification_transport':'local_only'}
    except Exception:return unavailable
