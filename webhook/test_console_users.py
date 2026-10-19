"""Multi-user console accounts: bootstrap, roles, and admin CRUD."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from bb_webhook import app as app_module, database, session_store, user_store
from bb_webhook.console_auth import hash_password
from bb_webhook.middleware import console_session
from bb_webhook.routers import auth, health

ORIGIN = "https://support.buttonsbebe.com"


class ConsoleUsersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "users.sqlite3"
        await database.init_db(self.path)
        await session_store.initialize(self.path)
        self.settings = SimpleNamespace(
            console_username="chaim",
            console_password_hash=hash_password("admin-password-ok"),
            console_session_secret="users-test-secret",
            demo_mode=False,
            db_path_absolute=self.path,
            gorgias_auth="local-placeholder",
            processor_result_secret="synthetic-result-secret-0123456789",
        )
        await user_store.bootstrap_from_settings(self.settings, self.path)
        for target in (
            "bb_webhook.routers.auth.get_settings",
            "bb_webhook.routers.health.get_settings",
            "bb_webhook.middleware.console_session.get_settings",
            "bb_webhook.deps.get_settings",
        ):
            patched = patch(target, return_value=self.settings)
            patched.start()
            self.addCleanup(patched.stop)
        self.login_patch = patch.object(auth, "_login_allowed", return_value=True)
        self.login_patch.start()
        self.app = app_module.create_app()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=ORIGIN
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.login_patch.stop()
        self.temp.cleanup()

    async def login(self, username="chaim", password="admin-password-ok"):
        response = await self.client.post(
            "/auth/login",
            headers={"origin": ORIGIN},
            json={"username": username, "password": password, "next": "/console/"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response

    async def test_bootstrap_creates_admin_once(self):
        users = await user_store.list_users(self.path)
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["role"], "admin")
        await user_store.bootstrap_from_settings(self.settings, self.path)
        self.assertEqual(len(await user_store.list_users(self.path)), 1)

    async def test_staff_login_redirects_away_from_console(self):
        await user_store.create_user(
            self.path,
            username="agent",
            password_hash=hash_password("staff-password-ok"),
            role="staff",
            gorgias_email="agent@example.test",
        )
        response = await self.login("agent", "staff-password-ok")
        self.assertEqual(response.json()["redirect"], "/inbox/")
        self.assertEqual(response.json()["role"], "staff")
        session = await self.client.get("/auth/session")
        self.assertEqual(session.json()["role"], "staff")
        self.assertEqual(session.json()["gorgiasEmail"], "agent@example.test")
        denied = await self.client.get(
            "/auth/check",
            headers={"x-forwarded-uri": "/console/api/stats", "origin": ORIGIN},
        )
        self.assertEqual(denied.status_code, 403)
        allowed = await self.client.get(
            "/auth/check",
            headers={"x-forwarded-uri": "/console/api/inbox/send-access", "origin": ORIGIN},
        )
        self.assertEqual(allowed.status_code, 204)
        self.assertEqual(allowed.headers["x-operator-email"], "agent@example.test")
        self.assertEqual(allowed.headers["x-authenticated-actor"], "staff:agent")
        ticket = await self.client.get(
            "/auth/check",
            headers={"x-forwarded-uri": "/console/api/ticket/12/retry-draft", "origin": ORIGIN},
        )
        self.assertEqual(ticket.status_code, 204)
        await self.client.post("/auth/logout", headers={"origin": ORIGIN})
        # Force empty email so env-set INBOX_OPERATOR_GORGIAS_EMAIL cannot leak.
        await user_store.update_user(self.path, "chaim", gorgias_email="")
        await self.login()
        cleared = await self.client.get(
            "/auth/check",
            headers={"x-forwarded-uri": "/inbox/api/helpdesk", "origin": ORIGIN},
        )
        self.assertEqual(cleared.status_code, 204)
        self.assertEqual(cleared.headers["x-operator-email"], "-")
        await self.login("agent", "staff-password-ok")
        await user_store.update_user(self.path, "agent", disabled=True)
        self.assertEqual((await self.client.get("/auth/session")).status_code, 401)

    async def test_admin_can_manage_users_and_last_admin_is_protected(self):
        await self.login()
        created = await self.client.post(
            "/dashboard/api/users",
            headers={"origin": ORIGIN},
            json={
                "username": "helper",
                "password": "helper-password",
                "role": "staff",
                "gorgiasEmail": "helper@example.test",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        listed = await self.client.get("/dashboard/api/users")
        self.assertEqual(listed.status_code, 200)
        names = {row["username"] for row in listed.json()["users"]}
        self.assertEqual(names, {"chaim", "helper"})
        blocked = await self.client.patch(
            "/dashboard/api/users/chaim",
            headers={"origin": ORIGIN},
            json={"disabled": True},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"], "last_admin")

    async def test_user_changes_are_audited_with_actor_and_no_secrets(self):
        await self.login()
        headers = {"origin": ORIGIN}
        created = await self.client.post("/dashboard/api/users", headers=headers, json={
            "username": "helper", "password": "helper-password",
            "role": "staff", "gorgiasEmail": "helper@example.test",
        })
        self.assertEqual(created.status_code, 201, created.text)
        changed = await self.client.patch("/dashboard/api/users/helper", headers=headers,
                                          json={"role": "admin", "password": "another-password"})
        self.assertEqual(changed.status_code, 200, changed.text)
        noop = await self.client.patch("/dashboard/api/users/helper", headers=headers,
                                       json={"role": "admin"})  # no-op: not recorded
        self.assertEqual(noop.status_code, 200, noop.text)
        # Refused change leaves no trace and no change.
        before = await user_store.get_active("chaim", self.path)
        refused = await self.client.patch("/dashboard/api/users/chaim", headers=headers,
                                          json={"gorgiasEmail": "bad"})
        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertEqual(await user_store.get_active("chaim", self.path), before)
        response = await self.client.get("/dashboard/api/users/audit")
        self.assertEqual(response.status_code, 200, response.text)
        entries = response.json()["entries"]
        self.assertEqual([e["action"] for e in entries], ["update", "create", "bootstrap"])
        update, create, boot = entries
        self.assertEqual(update["actor"], "admin:chaim")
        self.assertEqual(update["target"], "helper")
        self.assertEqual(update["changes"], {"role": ["staff", "admin"], "passwordReset": True})
        self.assertEqual(create["changes"], {"role": "staff", "gorgiasEmail": "helper@example.test"})
        self.assertEqual(boot["actor"], "system:bootstrap")
        self.assertNotIn("pbkdf2", response.text)
        self.assertNotIn("password", response.text.replace("passwordReset", ""))

        from bb_webhook.routers import users as users_router
        with patch.object(users_router, "_AUDIT_PAGE", 2):
            first = (await self.client.get("/dashboard/api/users/audit")).json()
            self.assertEqual([e["action"] for e in first["entries"]], ["update", "create"])
            older = (await self.client.get(
                f"/dashboard/api/users/audit?before={first['nextBefore']}")).json()
            self.assertEqual([e["action"] for e in older["entries"]], ["bootstrap"])
            self.assertIsNone(older["nextBefore"])
        bad = await self.client.get("/dashboard/api/users/audit?before=-1")
        self.assertEqual(bad.status_code, 400)

        await self.client.post("/auth/logout", headers=headers)
        await user_store.create_user(self.path, username="viewer", password_hash=hash_password("viewer-password"),
                                     role="staff", gorgias_email="viewer@example.test")
        await self.client.post("/auth/logout", headers=headers)
        await self.login("viewer", "viewer-password")
        denied = await self.client.get("/dashboard/api/users/audit")
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
