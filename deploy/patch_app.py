#!/usr/bin/env python3
"""Idempotently add the feedback review endpoints + console route to app.py.

Backs up the original, inserts a sys.path shim and the routes, and py_compiles
the result. Safe to run twice (no-ops if already patched).
"""
import datetime
import pathlib
import py_compile
import sys

APP = pathlib.Path("/root/Buttonsbebe Agent/webhook/src/bb_webhook/app.py")

SHIM = '''import sys as _sys
_AGENT_ROOT = str(Path(__file__).resolve().parents[3])
if _AGENT_ROOT not in _sys.path:
    _sys.path.insert(0, _AGENT_ROOT)
'''

ROUTES = '''
# ── Feedback review console (added by deploy) ──────────────
_REVIEW_HTML = (Path(__file__).parent / "review_console.html").read_text("utf-8")


@app.get("/dashboard/review", response_class=HTMLResponse)
async def review_console() -> HTMLResponse:
    return HTMLResponse(content=_REVIEW_HTML)


@app.get("/dashboard/api/review/list")
async def review_list() -> JSONResponse:
    from feedback import review as _rv
    return JSONResponse(content={"pending": _rv.list_pending()})


@app.get("/dashboard/api/review/packet/{ticket_id}")
async def review_packet(ticket_id: str) -> JSONResponse:
    from feedback import review as _rv
    p = _rv.get_packet(ticket_id)
    if not p:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return JSONResponse(content={
        "ticket_id": p["ticket_id"], "front": p["front"],
        "situation_masked": p["situation_masked"],
        "reply_masked": p["reply_masked"], "reply_raw": p["reply"],
        "pii_reply": p["pii_reply"],
    })


@app.post("/dashboard/api/review/approve/{ticket_id}")
async def review_approve(ticket_id: str, request: Request) -> JSONResponse:
    from feedback import review as _rv
    try:
        body = await request.json()
    except Exception:
        body = {}
    r = _rv.approve(ticket_id, pii_cleared=bool(body.get("pii_cleared")),
                    note=str(body.get("note", "")), why=str(body.get("why", "")))
    return JSONResponse(content=r, status_code=200 if r.get("ok") else 400)


@app.post("/dashboard/api/review/reject/{ticket_id}")
async def review_reject(ticket_id: str, purge: bool = False) -> JSONResponse:
    from feedback import review as _rv
    return JSONResponse(content=_rv.reject(ticket_id, purge=purge))


@app.post("/dashboard/api/review/reindex")
async def review_reindex() -> JSONResponse:
    from feedback import review as _rv
    return JSONResponse(content=_rv.reindex())


'''

ANCHOR = '@app.post("/webhook/gorgias/{tenant_id}")'
PATH_IMPORT = "from pathlib import Path\n"


def main() -> int:
    src = APP.read_text(encoding="utf-8")
    if "/dashboard/api/review/list" in src:
        print("already patched — nothing to do")
        return 0
    if PATH_IMPORT not in src:
        print("ERROR: could not find 'from pathlib import Path' import anchor")
        return 1
    if ANCHOR not in src:
        print("ERROR: could not find webhook-receiver anchor")
        return 1

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = APP.with_suffix(f".py.bak-review-{stamp}")
    backup.write_text(src, encoding="utf-8")

    src = src.replace(PATH_IMPORT, PATH_IMPORT + SHIM, 1)
    src = src.replace(ANCHOR, ROUTES + ANCHOR, 1)
    APP.write_text(src, encoding="utf-8")

    try:
        py_compile.compile(str(APP), doraise=True)
    except py_compile.PyCompileError as e:
        APP.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
        print("ERROR: patched file failed to compile — reverted.\n", e)
        return 1

    print(f"patched OK. backup: {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
