"""Route-coverage checklist (TESTING-STRATEGY §3).

Every route documented in API-CONTRACT.md is hit by at least one request here
and asserted to return a non-5xx status. Emulator routes are covered in their
own contract test files; this focuses on the Fable server's native + compat API.
"""


def test_every_documented_server_route_responds(env):
    # seed one ticket + customer + draft so id-bearing routes have a target
    tid = env.intake_email("emma.wilson@example.com",
                           "Where is my order #BB1015?", from_name="Emma Wilson").json()["ticket_id"]
    env.run_pipeline()
    cust_id = env.ticket(tid)["customer"]["id"]
    # a chat session for the long-poll route
    env.intake_chat("route-sess", "hello")

    checks = [
        # native health
        ("GET", "/fable/api/health", None),
        ("GET", "/health", None),
        # tickets
        ("GET", "/fable/api/tickets?status=all&channel=all&limit=50", None),
        ("GET", f"/fable/api/tickets/{tid}", None),
        ("PATCH", f"/fable/api/tickets/{tid}", {"tags": ["x"]}),
        # actions
        ("POST", f"/fable/api/tickets/{tid}/rewrite", {"instruction": "shorter"}),
        ("POST", f"/fable/api/tickets/{tid}/note", {"text": "n"}),
        # intake (all three)
        ("POST", "/fable/api/intake/email",
         {"from_email": "r@example.com", "body_text": "hi"}),
        ("POST", "/fable/api/intake/chat", {"session_id": "rc", "body_text": "hi"}),
        ("POST", "/fable/api/intake/whatsapp", {"phone": "+15550001111", "body_text": "hi"}),
        # chat long-poll
        ("GET", "/fable/api/chat/route-sess/messages?after=0", None),
        # customers
        ("GET", f"/fable/api/customers/{cust_id}", None),
        ("GET", "/fable/api/customers?email=emma.wilson@example.com", None),
        ("GET", "/fable/api/customers?q=emma", None),
        # stats / audit / macros
        ("GET", "/fable/api/stats", None),
        ("GET", "/fable/api/audit?limit=10", None),
        ("GET", "/fable/api/macros", None),
        # gorgias-compat
        ("GET", "/api/tickets?limit=30", None),
        ("GET", f"/api/tickets/{tid}", None),
        ("GET", f"/api/tickets/{tid}/messages?limit=30", None),
        ("POST", f"/api/tickets/{tid}/messages", {"channel": "internal", "body_text": "note"}),
        ("GET", f"/api/customers/{cust_id}", None),
        ("GET", "/api/customers?email=emma.wilson@example.com", None),
    ]

    # send is covered last so the earlier note/rewrite drafts still exist
    checks.append(("POST", f"/fable/api/tickets/{tid}/send", {"text": "final reply"}))

    failures = []
    for method, path, body in checks:
        r = env.client.request(method, path, json=body)
        if r.status_code >= 500:
            failures.append((method, path, r.status_code))
    assert not failures, f"routes returned 5xx: {failures}"


def test_macros_stub_shape(env):
    assert env.client.get("/fable/api/macros").json() == {"macros": []}


def test_health_reports_brain_and_queue(env):
    h = env.client.get("/fable/api/health").json()
    assert h["ok"] is True
    # Health reports the *configured* brain (FABLE_BRAIN). Default is "mock",
    # but the suite must also stay green under FABLE_BRAIN=anthropic (GATE 2,
    # TESTING-READINESS §3) — so assert against the config, not a literal.
    assert h["brain"] == env.config.BRAIN
    assert h["brain"] in ("mock", "anthropic", "hermes")
    assert "queue_depth" in h
