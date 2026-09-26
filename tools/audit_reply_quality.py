"""Read-only export of the next 100 terminal draft results after a release.

Outputs a private review packet, not an automatic quality score. Neither queued
work nor provider actions are changed. Device delivery is never inferred.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3


def collect(db_path, after_attempt_id):
    if after_attempt_id < 0:
        raise ValueError('A nonnegative release attempt marker is required')
    uri=Path(db_path).resolve().as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        rows=db.execute("""SELECT a.*,oa.status AS notification_status
            FROM draft_generation_attempts a LEFT JOIN owner_alert_attempts oa ON oa.job_id=a.job_id
            WHERE a.id>? AND a.outcome IN ('ready','needs_review','failed','no_reply')
            ORDER BY a.id LIMIT 100""",(after_attempt_id,)).fetchall()
    counts=Counter(row['outcome'] for row in rows)
    generated=counts['ready']+counts['needs_review']
    attempted=generated+counts['failed']
    records=[]
    for row in rows:
        payload=json.loads(row['result_json'] or '{}').get('result',{})
        facts=payload.get('missing_facts') or []
        if isinstance(facts,str):
            facts=json.loads(facts)
        records.append(dict(attempt_id=row['id'],job_id=row['job_id'],ticket_id=row['ticket_id'],
            outcome=row['outcome'],finished_at=row['finished_at'],error_category=row['error_code'],
            priority=payload.get('priority'),draft_text=payload.get('draft_text',''),
            missing_facts=facts,staff_next_step=payload.get('staff_next_step',''),
            notification_transport='accepted' if row['notification_status']=='accepted' else
                'uncertain' if row['notification_status'] else 'not_attempted',
            owner_delivery='unconfirmed',review=dict(usable_answer=None,incorrect_escalation=None,defects=[])))
    percent=round(100*generated/attempted,2) if attempted else None
    return dict(after_attempt_id=after_attempt_id,target_results=100,observed_results=len(rows),
        collection_complete=len(rows)==100,generation_success_percent=percent,
        generation_target_percent=95,generation_target_met=percent>=95 if percent is not None else None,
        outcomes=dict(counts),quality_review='pending; assess every case individually',records=records)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--after-attempt-id',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    data=collect(args.db,args.after_attempt_id)
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as handle:json.dump(data,handle,indent=2)
    print(json.dumps({k:v for k,v in data.items() if k!='records'}))
