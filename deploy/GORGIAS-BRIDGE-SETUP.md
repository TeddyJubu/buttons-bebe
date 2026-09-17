# Gorgias bridge activation — `console-src/helpdesk-agent/bridge/`

The bridge is a **dormant, optional sidecar** connecting the demo helpdesk to
Gorgias: inbound webhook events become intake tickets; human-confirmed replies
route to the Gorgias API or AgentMail email. It is deliberately disabled by
default and stays that way until an owner explicitly walks this document.
Referenced from `AGENTS.md` §6 and `helpdesk-design/INTAKE.md`.

Dormancy is already paid for: `tools/verify_release.sh` runs
`console-src/helpdesk-agent/tests/test_bridge.py` on every release, so the
contract with the live helpdesk API cannot silently rot while disabled.

## 1. Env ladder (necessary, not sufficient)

Inbound (Gorgias → helpdesk):

| Var | Purpose |
|---|---|
| `GORGIAS_BRIDGE_ENABLED=1` | Master inbound switch (`bridge/config.py`) |
| `GORGIAS_SUBDOMAIN` | Gorgias instance subdomain |
| `GORGIAS_API_EMAIL` | API-credential email (outbound calls) |
| `GORGIAS_API_KEY` | API key (outbound calls) |
| `GORGIAS_BRIDGE_SECRET` | Shared secret for the inbound webhook door |

Outbound (helpdesk → customer):

| Var | Purpose |
|---|---|
| `HELPDESK_OUTBOUND_ENABLED=1` | Master outbound switch (`bridge/config.py`) |
| `HELPDESK_SEND_ALLOWLIST` | Restricts outbound email destinations |
| `AGENTMAIL_API_KEY` | Email transport credential (`bridge/email_out.py`) |

**Runtime locks stack on top:** `review_server.py` re-forces both master
switches to `"0"` in-process at startup (its module prologue), and the systemd
unit supplies `0` values as well. Env files and unit definition are part of the
reviewed activation change — an env-only edit is overwritten at boot.

## 2. The three code locks that env flags alone cannot flip

Setting every var above still leaves the bridge inert. These are deliberate
code-level safety locks; each must be edited by hand as part of a reviewed
activation change:

1. **Send lock** — `console-src/helpdesk-agent/helpdesk/send_access.py:9`
   hardcodes `SEND_ACCESS_ENABLED = False`. Every outbound path re-checks it
   (`bridge/gorgias_api.py` before each send, `bridge/email_out.py` likewise,
   and the tissue-level check in `helpdesk/tissues.py`). It is defense-in-depth
   by design: do not collapse the re-checks into one helper.
2. **Inbound HTTP door** — `console-src/inbox/review_server.py:105-106`
   (`POST /webhook/gorgias`) is a hardcoded 503 stub that never imports the
   bridge. Wiring it is step 4 below.
3. **Network isolation** — the inbox runtime runs under a systemd profile that
   denies outbound `connect()` (`deploy/INBOX-NETWORK-ISOLATION.md`). Outbound
   Gorgias calls need that profile relaxed for the bridge's exit path.

## 3. Gorgias side

Create an HTTP Integration in Gorgias pointing at a **public URL that reaches
the inbox server's `POST /webhook/gorgias` route** — not the bare `:8766`
listener, which is localhost-only by design. Today no Caddy route proxies the
integration to `127.0.0.1:8766`; adding one (or another authenticated ingress)
is part of the reviewed activation change, and it must be verified live before
Gorgias is pointed at it. Send the same header secret as `GORGIAS_BRIDGE_SECRET`
(bearer or `X-Bridge-Secret`; query-string secrets are refused by design —
`bridge/gorgias_inbound.py:verify_secret`).

Note the isolation boundary: production's Gorgias webhook receiver keeps
running on port 8000; this door serves the *demo helpdesk* intake only.

## 4. Wiring the inbound door (the snippet that does not exist yet)

`accept()` checks only the env flag — **the secret check must happen in the
HTTP route, before `accept()`**, or the activated bridge is an unauthenticated
ticket-injection door. Malformed or non-object bodies are rejected with 400
before dispatch — an unguarded `await request.json()` would 500 the route.
Minimal wiring:

```python
# review_server.py — replace the 503 stub
from bridge import gorgias_inbound  # lazy: only after activation is decided

@app.post("/webhook/gorgias")
@app.post("/webhook/gorgias/{rest:path}")
async def gorgias_bridge(request: Request):
    if not gorgias_inbound.verify_secret(dict(request.headers)):
        return JSONResponse({"ok": False, "status": "unauthorized"}, status_code=401)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "status": "bad_request"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "status": "bad_request"}, status_code=400)
    result = await run_in_threadpool(
        gorgias_inbound.accept, payload, invoke=handle_http
    )
    return JSONResponse(result, status_code=result.get("http", 200))
```

`verify_secret` is fail-closed when `GORGIAS_BRIDGE_SECRET` is unset, and
`accept()` still 503s unless `GORGIAS_BRIDGE_ENABLED=1` — a misconfigured
activation degrades to the current stub, not to an open door. The wiring is
backed by existing tests (`tests/test_bridge.py::test_verify_secret_bearer_and_header`
and `test_bridge_disabled_returns_503`).

## 5. Activation checklist

1. Owner decision + review: flip `send_access.py:9` and wire the door in the
   same reviewed change (never env-only).
2. Set the env ladder on the VPS (systemd unit for the inbox, not shell) — and
   remove the startup stomp: `review_server.py` re-forces both switches to `"0"`
   in its module prologue, and the unit supplies `0` values; both change in the
   same reviewed commit as the door wiring.
3. Relax the inbox network profile only as far as the bridge needs
   (`deploy/INBOX-NETWORK-ISOLATION.md`).
4. Add and verify the public ingress route (Caddy or equivalent authenticated
   proxy to `127.0.0.1:8766/webhook/gorgias`) before pointing Gorgias at it.
5. Re-run `bash tools/verify_release.sh` — bridge tests must stay green.
6. Send one test ticket through the Gorgias HTTP Integration and watch it land
   in the demo intake; confirm the Send lock still refuses a direct send.
