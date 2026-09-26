"""Sanitized, freshness-checked route diagnostics; never contacts the bridge."""
import json
from datetime import datetime, timezone
from .db import Database


async def read_health(db_path=None):
    try:
        rows = await Database(db_path).fetch("SELECT value FROM app_settings WHERE key='notification_route_health'")
        data = json.loads(rows[0]['value']) if rows else {}
        checked = datetime.fromisoformat(data['checked_at'])
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        status = data.get('status')
        if status not in {'ok','unconfigured','invalid_route','invalid_response','route_mismatch','authentication_failed','unavailable'}:
            return {'status': 'unavailable'}
        return {'status': 'stale' if age > 600 or age < -30 else status,
                'checked_at': checked.isoformat(), 'connected': data.get('connected') is True,
                'destination_configured': data.get('destination_configured') is True}
    except Exception:
        return {'status': 'unavailable'}
