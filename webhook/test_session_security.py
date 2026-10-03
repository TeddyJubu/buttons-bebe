"""Authenticated owner and adversarial proxy tests; no external operations."""
import base64
import asyncio
import threading
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import Request
from bb_webhook import app as app_module, database, session_store, password_executor
from bb_webhook.routers import webhook as webhook_router
from bb_webhook.console_auth import build_session_token, hash_password, safe_next_path
from bb_webhook.routers import auth, health
from bb_webhook.middleware import console_session

ORIGIN = "https://support.buttonsbebe.com"


def _slow_password_test(password, encoded):
    # Runs in an actual isolated process; never consumes a real credential.
    time.sleep(1.5)
    return False


class SessionSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        await database.init_db(self.path)
        await session_store.initialize(self.path)
        self.settings = SimpleNamespace(console_username="chaim", console_password_hash=hash_password("local-test-password"), console_session_secret="local-test-secret", demo_mode=False, db_path_absolute=self.path, gorgias_auth="local-placeholder", processor_result_secret="synthetic-result-secret-0123456789")
        for consumer in (auth, health, console_session):
            settings_patch = patch.object(consumer, "get_settings", return_value=self.settings)
            settings_patch.start()
            self.addCleanup(settings_patch.stop)
        self.login_patch = patch.object(auth, "_login_allowed", return_value=True)
        self.login_patch.start()
        self.app = app_module.create_app()

        @self.app.post("/dashboard/api/test-action")
        async def action(request: Request):
            return {"actor_id":request.state.actor_id, "actor_role":request.state.actor_role}

        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=ORIGIN)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.login_patch.stop()
        self.temp.cleanup()

    async def login(self):
        response = await self.client.post("/auth/login", headers={"origin":ORIGIN}, json={"username":"chaim", "password":"local-test-password", "next":"/inbox/?view=all"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["redirect"], "/inbox/?view=all")
        return response.cookies["bb_console_session"]

    def legacy_token(self):
        def enc(value): return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
        payload = enc(f"chaim\n{int(time.time())+3600}".encode())
        return payload + "." + enc(hmac.new(b"local-test-secret", payload.encode(), hashlib.sha256).digest())

    async def test_registered_session_is_required_and_logout_revokes_copied_cookie(self):
        unknown = build_session_token("chaim", "local-test-secret")
        response = await self.client.get("/auth/session", headers={"cookie":f"bb_console_session={unknown}"})
        self.assertEqual(response.status_code, 401)
        token = await self.login()
        self.assertEqual((await self.client.get("/auth/session")).status_code, 200)
        logout = await self.client.post("/auth/logout", headers={"origin":ORIGIN})
        self.assertEqual(logout.status_code, 200)
        replay = await self.client.get("/auth/session", headers={"cookie":f"bb_console_session={token}"})
        self.assertEqual(replay.status_code, 401)

    async def test_legacy_session_migrates_once_and_cannot_reactivate(self):
        token = self.legacy_token()
        headers = {"cookie":f"bb_console_session={token}"}
        for _ in range(2):
            self.assertEqual((await self.client.get("/auth/session",headers=headers)).status_code,200)
        self.assertEqual((await self.client.post("/auth/logout",headers={**headers,"origin":ORIGIN})).status_code,200)
        for altered in (token, token + "=", token + "!"):
            replay = await self.client.get("/auth/session",headers={"cookie":f"bb_console_session={altered}"})
            self.assertEqual(replay.status_code,401)

    async def test_dashboard_mutations_require_session_and_trusted_origin(self):
        path = "/dashboard/api/test-action"
        self.assertEqual((await self.client.post(path,headers={"origin":ORIGIN})).status_code,401)
        await self.login()
        for headers in ({}, {"origin":"https://evil.invalid"}, {"origin":ORIGIN,"sec-fetch-site":"cross-site"}, {"origin":"null"}):
            self.assertEqual((await self.client.post(path,headers=headers)).status_code,403)
        valid = await self.client.post(path,headers={"origin":ORIGIN})
        self.assertEqual(valid.json(),{"actor_id":"owner:chaim","actor_role":"owner"})

    async def test_login_and_logout_reject_cross_site_requests(self):
        for path in ("/auth/login","/auth/logout"):
            self.assertEqual((await self.client.post(path,headers={"origin":"https://evil.invalid"},json={})).status_code,403)

    async def test_processor_results_are_not_a_public_or_browser_api(self):
        # Direct local producer reaches the handler's normal payload validator.
        self.assertEqual((await self.client.post("/dashboard/api/results",headers={"authorization":"Bearer "+self.settings.processor_result_secret},json={})).status_code,400)
        await self.login()
        for headers in ({"x-forwarded-for":"127.0.0.1"}, {"forwarded":"for=127.0.0.1"}, {"origin":ORIGIN}):
            self.assertEqual((await self.client.post("/dashboard/api/results",headers=headers,json={})).status_code,403)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app,client=("203.0.113.5",123)),base_url=ORIGIN) as remote:
            self.assertEqual((await remote.post("/dashboard/api/results",json={})).status_code,403)
            self.assertEqual((await remote.get("/dashboard/api/tickets",headers={"x-forwarded-for":"127.0.0.1","x-authenticated-actor":"owner:chaim"})).status_code,401)

    async def test_result_credential_required_even_for_local_owner_session(self):
        await self.login()
        for headers in ({},{'authorization':'Bearer wrong'},{'authorization':'Basic synthetic'}):
            response=await self.client.post('/dashboard/api/results',headers=headers,json={})
            self.assertEqual(response.status_code,401)
        valid={'authorization':'Bearer '+self.settings.processor_result_secret}
        self.assertEqual((await self.client.post('/dashboard/api/results',headers={**valid,'x-forwarded-for':'127.0.0.1'},json={})).status_code,403)
        self.settings.processor_result_secret=''
        self.assertEqual((await self.client.post('/dashboard/api/results',headers=valid,json={})).status_code,503)
        ready=await self.client.get('/ready')
        self.assertEqual(ready.status_code,503)
        self.assertFalse(ready.json()['checks']['processor_result_configured'])

    async def test_forward_auth_uses_original_uri_and_method(self):
        response = await self.client.get("/auth/page-check",headers={"x-forwarded-uri":"/inbox/?view=all", "x-forwarded-method":"GET"})
        self.assertEqual(response.headers["location"],"/console/login?next=%2Finbox%2F%3Fview%3Dall")
        api = await self.client.get("/auth/page-check",headers={"x-forwarded-uri":"/inbox/console/api/helpdesk", "x-forwarded-method":"POST"})
        self.assertEqual(api.status_code,401)
        await self.login()
        self.assertEqual((await self.client.get("/auth/check",headers={"x-forwarded-method":"POST"})).status_code,403)
        self.assertEqual((await self.client.get("/auth/check",headers={"x-forwarded-method":"POST","origin":ORIGIN})).status_code,204)
        self.assertEqual((await self.client.get("/auth/check")).status_code,204)

    async def test_revocation_store_failure_does_not_claim_logout_success(self):
        await self.login()
        with patch.object(session_store,"revoke",side_effect=RuntimeError("database unavailable")):
            result = await self.client.post("/auth/logout",headers={"origin":ORIGIN})
        self.assertEqual(result.status_code,503)
        self.assertNotIn("set-cookie",result.headers)



    async def test_cancelled_login_cannot_free_a_running_password_slot(self):
        with patch.object(password_executor,"verify_password",_slow_password_test):
            first = asyncio.create_task(password_executor.verify_bounded("synthetic", "synthetic"))
            second = asyncio.create_task(password_executor.verify_bounded("synthetic", "synthetic"))
            await asyncio.sleep(0.2)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            with self.assertRaises(password_executor.PasswordVerifierBusy):
                await password_executor.verify_bounded("synthetic", "synthetic")
            await second

    async def test_password_saturation_does_not_block_webhook_intake(self):
        payload = {"username":"chaim","password":"synthetic-test-only"}
        with patch.object(password_executor,"verify_password",_slow_password_test):
            first = asyncio.create_task(self.client.post("/auth/login",headers={"origin":ORIGIN},json=payload))
            second = asyncio.create_task(self.client.post("/auth/login",headers={"origin":ORIGIN},json=payload))
            # Let both request handlers submit CPU jobs without waiting for the
            # workers to complete. The third request must be refused promptly.
            await asyncio.sleep(0.1)
            saturated = await asyncio.wait_for(self.client.post("/auth/login",headers={"origin":ORIGIN},json=payload),timeout=0.75)
            self.assertEqual(saturated.status_code,429)
            with patch.object(webhook_router,"verify_signature",return_value=False):
                intake = await asyncio.wait_for(self.client.post("/webhook/gorgias/test",content=b"{}"),timeout=0.75)
            self.assertEqual(intake.status_code,401)
            self.assertEqual(intake.json()["error"],"invalid_signature")
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            self.assertEqual([response.status_code for response in await asyncio.gather(first,second)],[401,401])

    async def test_readiness_checks_real_database_and_schema(self):
        result = await self.client.get("/ready")
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["diagnostics"]["pending_jobs"],0)
        from bb_webhook.db import Database
        await Database(self.path).execute("DROP TABLE parsed_messages")
        self.assertEqual((await self.client.get("/ready")).status_code,503)


class RedirectTests(unittest.TestCase):
    def test_unsafe_paths_are_refused(self):
        for path in ("/console/../../evil", "/inbox/%2e%2e/evil", "/inbox/%252e%252e/evil", "/console/\\evil", "/inbox/%0aevil", "https://evil.invalid", "//evil.invalid", "/inbox.evil/"):
            self.assertEqual(safe_next_path(path),"/console/",path)
        self.assertEqual(safe_next_path("/inbox/?view=all"),"/inbox/?view=all")


if __name__ == "__main__":
    unittest.main()
