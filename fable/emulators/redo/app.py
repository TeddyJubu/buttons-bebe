"""Redo (returns/refunds) API emulator (Fable) — port 9602.

Bearer auth (REDO_API_KEY). Serves the read surface tools/redo_mcp.py needs:
  GET /v2.2/stores/{store}/returns?limit=
  GET /v2.2/stores/{store}/returns/{id}
  GET /v2.2/stores/{store}/returns?order_name=%23BB1022
8 seeded returns tied to real Shopify-emulator order names (incl. #BB1022 = approved).

List responses: {"returns":[...], "meta":{...}}. Single: {"return":{...}}.
Only stdlib + fastapi/uvicorn. Binds 127.0.0.1.
"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

REDO_API_KEY = os.environ.get("REDO_API_KEY", "test-redo-key")
REDO_STORE_ID = os.environ.get("REDO_STORE_ID", "bb-store-1")

app = FastAPI(title="Fable Redo Emulator")

ERR_401 = {"error": "Unauthorized", "message": "Invalid or missing Bearer token"}
ERR_404 = {"error": "Not Found"}


def _seed():
    return [
        {"id": "ret_1001", "order_name": "#BB1022", "status": "approved",
         "items": [{"title": "Waffle Knit Two-Piece Set - 6-12M", "qty": 1, "reason": "wrong size"}],
         "created_at": "2026-06-28T10:12:00-04:00", "refund_amount": "42.00",
         "resolution": "refund", "customer_email": "sophie.martin@example.com"},
        {"id": "ret_1002", "order_name": "#BB1005", "status": "requested",
         "items": [{"title": "Chunky Knit Cardigan - 3-6M", "qty": 1, "reason": "changed mind"}],
         "created_at": "2026-07-02T14:40:00-04:00", "refund_amount": "48.00",
         "resolution": "refund", "customer_email": "ethan.clark@example.com"},
        {"id": "ret_1003", "order_name": "#BB1033", "status": "refunded",
         "items": [{"title": "Puff Sleeve Knit Dress - 12-18M", "qty": 1, "reason": "defective seam"}],
         "created_at": "2026-06-18T09:05:00-04:00", "refund_amount": "68.00",
         "resolution": "refund", "customer_email": "amelia.hall@example.com"},
        {"id": "ret_1004", "order_name": "#BB1010", "status": "in_transit",
         "items": [{"title": "Fleece Zip Footie - 0-3M", "qty": 2, "reason": "too small"}],
         "created_at": "2026-07-05T16:20:00-04:00", "refund_amount": "64.00",
         "resolution": "exchange", "customer_email": "mia.gonzalez@example.com"},
        {"id": "ret_1005", "order_name": "#BB1034", "status": "refunded",
         "items": [{"title": "Velour Tracksuit Set - 6-12M", "qty": 1, "reason": "damaged in transit"}],
         "created_at": "2026-06-22T11:30:00-04:00", "refund_amount": "58.00",
         "resolution": "refund", "customer_email": "harper.young@example.com"},
        {"id": "ret_1006", "order_name": "#BB1015", "status": "rejected",
         "items": [{"title": "Cable Knit Sweater - 3-6M", "qty": 1, "reason": "outside return window"}],
         "created_at": "2026-07-01T08:15:00-04:00", "refund_amount": "0.00",
         "resolution": "denied", "customer_email": "emma.wilson@example.com"},
        {"id": "ret_1007", "order_name": "#BB1040", "status": "approved",
         "items": [{"title": "Cozy Sherpa Jacket - 12-18M", "qty": 1, "reason": "didn't fit"}],
         "created_at": "2026-07-07T13:00:00-04:00", "refund_amount": "62.00",
         "resolution": "exchange", "customer_email": "jack.mitchell@example.com"},
        {"id": "ret_1008", "order_name": "#BB1028", "status": "requested",
         "items": [{"title": "Smocked Party Dress - 12-18M", "qty": 1, "reason": "color not as pictured"}],
         "created_at": "2026-07-08T17:45:00-04:00", "refund_amount": "52.00",
         "resolution": "refund", "customer_email": "elijah.king@example.com"},
    ]


STATE = {"returns": _seed()}


def _auth(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return auth.split(" ", 1)[1].strip() == REDO_API_KEY


@app.get("/v2.2/stores/{store}/returns")
async def list_returns(store: str, request: Request):
    if not _auth(request):
        return JSONResponse(ERR_401, status_code=401)
    q = request.query_params
    items = STATE["returns"]
    order_name = q.get("order_name")
    if order_name:
        want = order_name.lstrip("#").lower()
        items = [r for r in items if r["order_name"].lstrip("#").lower() == want]
    status = q.get("status")
    if status:
        items = [r for r in items if r["status"] == status]
    email = q.get("email")
    if email:
        items = [r for r in items if r.get("customer_email", "").lower() == email.lower()]
    limit = q.get("limit")
    if limit:
        try:
            items = items[: int(limit)]
        except ValueError:
            pass
    return JSONResponse({"returns": items, "meta": {"total_resources": len(items), "store": store}})


@app.get("/v2.2/stores/{store}/returns/{rid}")
async def get_return(store: str, rid: str, request: Request):
    if not _auth(request):
        return JSONResponse(ERR_401, status_code=401)
    r = next((x for x in STATE["returns"] if x["id"] == rid), None)
    if not r:
        return JSONResponse(ERR_404, status_code=404)
    return JSONResponse({"return": r})


@app.post("/emulator/reset")
async def reset():
    STATE["returns"] = _seed()
    return {"ok": True, "returns": len(STATE["returns"])}


@app.get("/emulator/state")
async def state():
    return {"returns": len(STATE["returns"]), "store": REDO_STORE_ID}


@app.get("/health")
async def health():
    return {"ok": True, "service": "redo", "returns": len(STATE["returns"])}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9602, log_level="warning")
