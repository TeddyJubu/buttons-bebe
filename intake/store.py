"""Transactional sandbox storage; only the offline entry point opens this store."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

from .policy import Conflict, Invalid
from . import delivery_context, auth
from .records import STATUSES, PRIORITIES, canonical, choice, digest, email, now, text, uid

APPLICATION_ID = 0x4242494E


class Store:
    def __init__(self, directory):
        self.path = Path(directory) / "intake.sqlite3"
        if self.path.is_symlink():
            raise Invalid("Sandbox database cannot be a symbolic link.")
        existed = self.path.exists()
        if existed and self.path.stat().st_nlink != 1:
            raise Invalid("Sandbox database cannot be a hard link.")
        with self.connection() as db:
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if existed and (app_id != APPLICATION_ID or version not in (1, 2, 3, 4, 5, 6, 7)):
                raise Invalid("Refusing an unrecognized database. Create a new sandbox workspace.")
            db.executescript(Path(__file__).with_name("schema.sql").read_text())
            # Additive local-only migration. Existing IDs, source records and
            # original proof workspaces are preserved when explicitly opened.
            columns = {row[1] for row in db.execute("PRAGMA table_info(tickets)")}
            for name, definition in (("queue", "TEXT NOT NULL DEFAULT 'inbox'"),
                                     ("review_reason", "TEXT NOT NULL DEFAULT ''"),
                                     ("status_changed_at", "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    db.execute(f"ALTER TABLE tickets ADD COLUMN {name} {definition}")
            db.execute("UPDATE tickets SET status_changed_at=updated_at WHERE status_changed_at=''")
            review_columns = {row[1] for row in db.execute('PRAGMA table_info(delivery_reviews)')}
            if 'generation' not in review_columns:
                db.execute("ALTER TABLE delivery_reviews ADD COLUMN generation TEXT NOT NULL DEFAULT ''")
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            db.execute("PRAGMA user_version=7")
            db.execute("INSERT OR IGNORE INTO sandbox_meta VALUES ('mode', 'offline_sandbox')")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            if auth.current.get():
                auth.require(db)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, ticket_id, kind, detail, actor="Sandbox operator"):
        identity = auth.current.get()
        if identity:
            actor = identity['name']
            detail = {**detail, 'actor_user_id': identity['id']}
        db.execute("INSERT INTO events(ticket_id,kind,actor,created_at,detail_json) VALUES (?,?,?,?,?)",
                   (ticket_id, kind, actor, now(), canonical(detail)))

    def operation(self, kind, data, apply, validate=None):
        operation_id = text(data.get("operation_id"), "operation_id", 128, True)
        if auth.current.get():
            operation_id = "user:" + auth.current.get()["id"] + ":" + operation_id
        fingerprint = digest(data)
        with self.connection(write=True) as db:
            if validate:
                validate(db)
            old = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if old:
                if old["kind"] != kind or old["digest"] != fingerprint:
                    raise Conflict("This operation ID was already used for a different action.")
                return json.loads(old["result_json"])
            result = apply(db)
            db.execute("INSERT INTO operations VALUES (?,?,?,?)",
                       (operation_id, kind, fingerprint, canonical(result)))
            return result

    def create_ticket(self, data):
        subject = text(data.get("subject"), "subject", 2000, True)
        body = text(data.get("body"), "description", 100_000, True)
        name = text(data.get("name") or "Unknown contact", "name")
        address = email(data.get("email") or "")

        def apply(db):
            tid, cid, mid, at = uid(), uid(), uid(), now()
            db.execute("INSERT INTO contacts VALUES (?,?,?,?,?)", (cid, "manual:" + cid, name, address, "{}"))
            db.execute("""INSERT INTO tickets(id,subject,contact_id,status,priority,assignee,channel,origin,created_at,updated_at)
                        VALUES (?,?,?,'open','normal','','manual','manual_test',?,?)""", (tid, subject, cid, at, at))
            db.execute("UPDATE tickets SET status_changed_at=? WHERE id=?", (at, tid))
            # Staff descriptions are private notes, never fabricated customer messages.
            db.execute("""INSERT INTO messages(id,ticket_id,kind,author_name,author_email,body,created_at,channel,origin)
                        VALUES (?,?,'note',?,'',?,?,'internal','manual_test')""", (mid, tid, auth.actor_name(), body, at))
            self.event(db, tid, "ticket_created", {"message_id": mid})
            return {"id": tid}

        return self.operation("create_ticket", data, apply)

    def update_ticket(self, tid, data):
        status = choice(data.get("status"), STATUSES, "status")
        priority = choice(data.get("priority"), PRIORITIES, "priority")
        assignee = text(data.get("assignee", ""), "assignee")
        if auth.current.get() and 'assignee' in data:
            raise Invalid('Use a local assignee_id; source assignment is preserved.')

        def apply(db):
            old = self.require_revision(db, tid, data.get("revision"))
            if auth.current.get():
                assignee_value = old['assignee']
                if 'assignee_id' in data:
                    from .team import assign
                    assign(self, db, tid, data['assignee_id'])
            else:
                assignee_value = assignee
            at = now()
            barrier = at if status != old["status"] else old["status_changed_at"]
            db.execute("UPDATE tickets SET status=?,priority=?,assignee=?,revision=revision+1,updated_at=?,status_changed_at=? WHERE id=?",
                       (status, priority, assignee_value, at, barrier, tid))
            self.event(db, tid, "ticket_updated", {
                "before": {k: old[k] for k in ("status", "priority", "assignee")},
                "after": {"status": status, "priority": priority, "assignee": assignee_value},
            })
            return {"id": tid, "revision": old["revision"] + 1}

        return self.operation("update_ticket:" + tid, data, apply)

    def add_note(self, tid, data):
        body = text(data.get("body"), "note", 100_000, True)

        def apply(db):
            self.require_revision(db, tid, data.get("revision"))
            mid, at = uid(), now()
            db.execute("""INSERT INTO messages(id,ticket_id,kind,author_name,author_email,body,created_at,channel,origin)
                        VALUES (?,?,'note',?,'',?,?,'internal','manual_test')""", (mid, tid, auth.actor_name(), body, at))
            db.execute("UPDATE tickets SET revision=revision+1,updated_at=? WHERE id=?", (at, tid))
            self.event(db, tid, "note_added", {"message_id": mid})
            return {"id": tid, "message_id": mid}

        return self.operation("add_note:" + tid, data, apply)

    @staticmethod
    def require_revision(db, tid, revision):
        row = db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
        if not row:
            raise Invalid("Ticket not found.")
        if type(revision) is not int or revision != row["revision"]:
            raise Conflict("This ticket changed. Refresh it before saving your change.")
        return row

    def list_tickets(self, query="", status="", offset=0, queue="work", view="all"):
        query = text(query, "query", 500)
        if status:
            choice(status, STATUSES, "status")
        if type(offset) is not int or offset < 0:
            raise Invalid("Invalid page offset.")
        where, args = [], []
        choice(queue, ("work", "all", "inbox", "review", "spam", "automatic"), "queue")
        if queue == "work":
            where.append("t.queue IN ('inbox','review')")
        elif queue != "all":
            where.append("t.queue=?")
            args.append(queue)
        if status:
            where.append("t.status=?")
            args.append(status)
        if query:
            where.append("""(instr(lower(t.subject || ' ' || c.name || ' ' || c.email || ' BB-' || t.number),lower(?))>0
                OR EXISTS (SELECT 1 FROM messages m WHERE m.ticket_id=t.id AND instr(lower(m.body),lower(?))>0))""")
            args.extend([query, query])
        from .team import filter_view
        filter_view(view, where, args)
        condition = " WHERE " + " AND ".join(where) if where else ""
        join = " FROM tickets t LEFT JOIN contacts c ON c.id=t.contact_id"
        with self.connection() as db:
            total = db.execute("SELECT count(*)" + join + condition, args).fetchone()[0]
            result = db.execute("SELECT t.*, c.name, c.email" + join + condition +
                                " ORDER BY rtrim(t.updated_at,'Z') DESC,t.number DESC LIMIT 50 OFFSET ?", (*args, offset)).fetchall()
            from .team import decorate
            return {"tickets": [decorate(db, dict(row)) for row in result], "total": total,
                    "nextOffset": offset + 50 if offset + 50 < total else None}

    def get_ticket(self, tid):
        with self.connection() as db:
            db.execute("BEGIN")
            row = db.execute("""SELECT t.*,c.name,c.email FROM tickets t
                             LEFT JOIN contacts c ON c.id=t.contact_id WHERE t.id=?""", (tid,)).fetchone()
            if not row:
                raise Invalid("Ticket not found.")
            result = dict(row)
            result["messages"] = [dict(m) for m in db.execute(
                "SELECT * FROM messages WHERE ticket_id=? ORDER BY rtrim(created_at,'Z'),sequence", (tid,))]
            for message in result["messages"]:
                message["attachments"] = [json.loads(a[0]) for a in db.execute(
                    "SELECT metadata_json FROM attachments WHERE message_id=?", (message["id"],))]
                message['attachment_records'] = [dict(a) for a in db.execute(
                    '''SELECT a.id,a.metadata_json,CASE WHEN f.attachment_id IS NULL THEN 'metadata_only'
                       ELSE 'private_copy' END AS availability,b.byte_size
                       FROM attachments a LEFT JOIN attachment_files f ON f.attachment_id=a.id
                       LEFT JOIN attachment_blobs b ON b.sha256=f.sha256 WHERE a.message_id=?''', (message['id'],))]
            result["sources"] = [dict(s) for s in db.execute(
                "SELECT account,external_id FROM source_records WHERE ticket_id=? AND kind='ticket'", (tid,))]
            result["events"] = [dict(e) for e in db.execute(
                "SELECT kind,actor,created_at,detail_json FROM events WHERE ticket_id=? ORDER BY sequence DESC", (tid,))]
            result['reply_context'] = delivery_context.context(db, tid)
            result['deliveries'] = delivery_context.history(db, tid)
            from . import assistance
            result['assistance'] = assistance.view(db, tid)
            from .team import decorate
            return decorate(db, result)

    def import_history(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,account,created_at,report_json FROM import_batches ORDER BY created_at DESC LIMIT 50")]
