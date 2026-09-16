"""Regression tests for dashboard replies sent through the Gorgias API."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from bb_webhook.gorgias_client import GorgiasClient


class _FakeAsyncClient:
    calls: list[tuple[str, str, dict]] = []

    def __init__(self, **kwargs: object) -> None:
        self.options = kwargs

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/api/messages"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 44,
                            "created_datetime": "2026-09-07T08:00:00Z",
                            "from_agent": False,
                            "channel": "email",
                            "source": {
                                "from": {"address": "customer@example.com"},
                                "to": [{"address": "support@buttonsbebe.com"}],
                            },
                            "sender": {"email": "customer@example.com"},
                        }
                    ]
                },
                request=httpx.Request("GET", url),
            )
        if url.endswith("/api/tickets/123/messages/9001"):
            return httpx.Response(
                200,
                json={"id": 9001, "sent_datetime": "2026-08-26T00:00:01Z"},
                request=httpx.Request("GET", url),
            )
        raise AssertionError(f"unexpected GET {url}")

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append(("POST", url, kwargs))
        return httpx.Response(
            201,
            json={"id": 9001, "sent_datetime": None},
            request=httpx.Request("POST", url),
        )


class GorgiasClientSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_public_reply_uses_current_listing_and_confirms_delivery(self) -> None:
        _FakeAsyncClient.calls = []
        with patch("bb_webhook.gorgias_client.get_settings", return_value=SimpleNamespace(demo_mode=False)), patch("bb_webhook.gorgias_client.httpx.AsyncClient", _FakeAsyncClient):
            result = await GorgiasClient(
                subdomain="buttons-bebe",
                email="agent@buttonsbebe.com",
                api_key="test-key",
                base_url="https://buttons-bebe.gorgias.com",
            ).send_public_reply(123, "Your order is on the way.")

        self.assertEqual(result["delivery_status"], "sent")
        self.assertEqual(result["message_id"], 9001)
        self.assertEqual(_FakeAsyncClient.calls[0][1], "https://buttons-bebe.gorgias.com/api/messages")
        post = next(call for call in _FakeAsyncClient.calls if call[0] == "POST")
        payload = post[2]["json"]
        self.assertEqual(payload["channel"], "email")
        self.assertEqual(payload["via"], "api")
        self.assertEqual(payload["receiver"], {"email": "customer@example.com"})
        self.assertEqual(payload["sender"], {"email": "agent@buttonsbebe.com"})
        self.assertEqual(payload["source"]["from"]["address"], "support@buttonsbebe.com")

    async def test_history_429_then_200_sends_with_two_gets(self) -> None:
        # 3.4: the extracted _request helper keeps the 429-then-success policy.
        class FlakyHistory(_FakeAsyncClient):
            def __init__(self, **kwargs: object) -> None:
                super().__init__(**kwargs)
                self.attempts = 0

            async def get(self, url: str, **kwargs: object) -> httpx.Response:
                if url.endswith("/api/messages"):
                    self.attempts += 1
                    if self.attempts == 1:
                        self.calls.append(("GET", url, kwargs))
                        return httpx.Response(429, headers={"Retry-After": "0"}, request=httpx.Request("GET", url))
                    return await _FakeAsyncClient.get(self, url, **kwargs)
                return await super().get(url, **kwargs)

        FlakyHistory.calls = []
        with patch("bb_webhook.gorgias_client.get_settings", return_value=SimpleNamespace(demo_mode=False)), patch("bb_webhook.gorgias_client.httpx.AsyncClient", FlakyHistory), patch("bb_webhook.gorgias_client.asyncio.sleep", return_value=None):
            result = await GorgiasClient(
                subdomain="buttons-bebe",
                email="agent@buttonsbebe.com",
                api_key="test-key",
                base_url="https://buttons-bebe.gorgias.com",
            ).send_public_reply(123, "Your order is on the way.")
        self.assertEqual(result["delivery_status"], "sent")
        gets = [call for call in FlakyHistory.calls if call[0] == "GET" and call[1].endswith("/api/messages")]
        self.assertEqual(len(gets), 2)

    async def test_recipient_or_source_change_fails_before_post(self):
        for expected in ({"expected_recipient": "different@example.com"}, {"expected_source_message_id": "45"}):
            _FakeAsyncClient.calls=[]
            with patch("bb_webhook.gorgias_client.get_settings", return_value=SimpleNamespace(demo_mode=False)), patch("bb_webhook.gorgias_client.httpx.AsyncClient", _FakeAsyncClient):
                result=await GorgiasClient(subdomain="buttons-bebe", email="agent@example.com", api_key="test-key",
                    base_url="https://buttons-bebe.gorgias.com").send_public_reply(123,"Reply",**expected)
            self.assertEqual(result["delivery_status"],"not_attempted")
            self.assertFalse(any(call[0]=="POST" for call in _FakeAsyncClient.calls))

    async def test_provider_page_order_prevents_stale_send_across_timezone_offsets(self):
        class OrderedPage(_FakeAsyncClient):
            async def get(self,url,**kwargs):
                if url.endswith('/api/messages'):
                    self.calls.append(('GET',url,kwargs))
                    def customer(mid,stamp):
                        return {'id':mid,'created_datetime':stamp,'from_agent':False,'channel':'email',
                                'source':{'from':{'address':'customer@example.com'},'to':[{'address':'support@buttonsbebe.com'}]}}
                    return httpx.Response(200,json={'data':[
                        customer(45,'2026-09-07T09:30:00Z'),
                        customer(44,'2026-09-07T10:00:00+02:00')]},request=httpx.Request('GET',url))
                return await super().get(url,**kwargs)
        OrderedPage.calls=[]
        with patch('bb_webhook.gorgias_client.get_settings',return_value=SimpleNamespace(demo_mode=False)),patch('bb_webhook.gorgias_client.httpx.AsyncClient',OrderedPage):
            result=await GorgiasClient(subdomain='test',email='agent@example.com',api_key='test-key',base_url='https://test.gorgias.com').send_public_reply(
                123,'Reply reviewed before the latest customer message',expected_source_message_id='44',expected_recipient='customer@example.com')
        self.assertEqual(result['delivery_status'],'not_attempted')
        self.assertEqual(result['error'],'new_customer_message_refresh_ticket')
        self.assertFalse(any(call[0]=='POST' for call in OrderedPage.calls))

    async def test_provider_ties_and_invalid_history_never_fall_back_to_older_review(self):
        for latest_stamp,latest_id,expected_error in (
            ('2026-09-07T09:30:00Z',45,'new_customer_message_refresh_ticket'),
            ('not-a-timestamp',45,'invalid_provider_message_history'),
            (None,45,'invalid_provider_message_history'),
            ('2026-09-07',45,'invalid_provider_message_history'),
            ('2026-09-07T09:30:00Z',True,'invalid_provider_message_history'),
            ('2026-09-07T09:30:00Z','045','invalid_provider_message_history'),
            ('2026-09-07T09:30:00Z',float('nan'),'invalid_provider_message_history')):
            class History(_FakeAsyncClient):
                async def get(self,url,**kwargs):
                    if url.endswith('/api/messages'):
                        self.calls.append(('GET',url,kwargs))
                        route={'from_agent':False,'channel':'email','source':{'from':{'address':'customer@example.com'},'to':[{'address':'support@buttonsbebe.com'}]}}
                        # NaN is intentionally returned by a synthetic response
                        # object rather than serialized as standards-compliant JSON.
                        response=httpx.Response(200,json={},request=httpx.Request('GET',url))
                        response.json=lambda: {'data':[{**route,'id':latest_id,'created_datetime':latest_stamp},
                            {**route,'id':44,'created_datetime':'2026-09-07T09:30:00Z'}]}
                        return response
                    return await super().get(url,**kwargs)
            History.calls=[]
            with self.subTest(timestamp=latest_stamp,id=latest_id),patch('bb_webhook.gorgias_client.get_settings',return_value=SimpleNamespace(demo_mode=False)),patch('bb_webhook.gorgias_client.httpx.AsyncClient',History):
                result=await GorgiasClient(subdomain='test',email='agent@example.com',api_key='test-key',base_url='https://test.gorgias.com').send_public_reply(123,'Old reviewed reply',expected_source_message_id='44')
                self.assertEqual(result['delivery_status'],'not_attempted')
                self.assertEqual(result['error'],expected_error)
                self.assertFalse(any(call[0]=='POST' for call in History.calls))

    async def test_invalid_reviewed_source_id_never_posts(self):
        for source_id in (True,False,0,-1,'044',float('nan'),'not-an-id'):
            _FakeAsyncClient.calls=[]
            with self.subTest(source_id=source_id),patch('bb_webhook.gorgias_client.get_settings',return_value=SimpleNamespace(demo_mode=False)),patch('bb_webhook.gorgias_client.httpx.AsyncClient',_FakeAsyncClient):
                result=await GorgiasClient(subdomain='test',email='agent@example.com',api_key='test-key',base_url='https://test.gorgias.com').send_public_reply(123,'Reply',expected_source_message_id=source_id)
                self.assertEqual(result['delivery_status'],'not_attempted')
                self.assertEqual(result['error'],'invalid_reviewed_source_message_id')
                self.assertFalse(any(call[0]=='POST' for call in _FakeAsyncClient.calls))

    async def test_bad_request_fallback_keeps_literal_html_escaped(self):
        class RejectText(_FakeAsyncClient):
            async def post(self,url,**kwargs):
                self.calls.append(("POST",url,kwargs))
                code=400 if "body_text" in kwargs["json"] else 201
                return httpx.Response(code,json={"id":9001},request=httpx.Request("POST",url))
        RejectText.calls=[]
        with patch("bb_webhook.gorgias_client.get_settings",return_value=SimpleNamespace(demo_mode=False)),patch("bb_webhook.gorgias_client.httpx.AsyncClient",RejectText):
            await GorgiasClient(subdomain="test",email="agent@example.com",api_key="test-key",base_url="https://test.gorgias.com").send_public_reply(123,'Literal <img src=x> & text')
        fallback=[call for call in RejectText.calls if call[0]=="POST"][-1][2]["json"]
        self.assertEqual(fallback['body_html'],'Literal &lt;img src=x&gt; &amp; text')

    async def test_reconciliation_rejects_wrong_message_and_prioritizes_failure(self):
        for payload,expected in (({'id':9002,'sent_datetime':'2026-08-26T00:00:01Z'},'unknown'),
            ({'id':9001,'sent_datetime':'2026-08-26T00:00:01Z','failed_datetime':'2026-08-26T00:00:02Z'},'failed'),
            ({'id':9001,'sent_datetime':True},'unknown'),
            ({'id':9001,'sent_datetime':'2026-08-26T00:00:01.123456'},'sent'),
            ({'id':9001,'sent_datetime':'2026-08-26'},'unknown')):
            class Receipt(_FakeAsyncClient):
                async def get(self,url,**kwargs):return httpx.Response(200,json=payload,request=httpx.Request('GET',url))
            with patch("bb_webhook.gorgias_client.get_settings",return_value=SimpleNamespace(demo_mode=False)),patch("bb_webhook.gorgias_client.httpx.AsyncClient",Receipt):
                result=await GorgiasClient(subdomain='test',email='agent@example.com',api_key='test-key',base_url='https://test.gorgias.com')._wait_for_delivery(123,9001)
            self.assertEqual(result['status'],expected,payload)

    async def test_malformed_created_id_never_attaches_a_different_message(self):
        for bad_id in (True,0,-1,'09001','not-an-id'):
            class BadId(_FakeAsyncClient):
                async def post(self,url,**kwargs):return httpx.Response(201,json={'id':bad_id},request=httpx.Request('POST',url))
            attached=[]
            async def capture(message_id):attached.append(message_id)
            with patch("bb_webhook.gorgias_client.get_settings",return_value=SimpleNamespace(demo_mode=False)),patch("bb_webhook.gorgias_client.httpx.AsyncClient",BadId):
                result=await GorgiasClient(subdomain='test',email='agent@example.com',api_key='test-key',base_url='https://test.gorgias.com').send_public_reply(123,'Reply',on_created=capture)
            self.assertFalse(result['ok'])
            self.assertEqual(attached,[])

    async def test_message_id_is_recorded_before_delivery_read(self):
        _FakeAsyncClient.calls=[]
        async def on_created(message_id):
            self.assertEqual(message_id,9001)
            self.assertEqual(_FakeAsyncClient.calls[-1][0],"POST")
        with patch("bb_webhook.gorgias_client.get_settings", return_value=SimpleNamespace(demo_mode=False)), patch("bb_webhook.gorgias_client.httpx.AsyncClient", _FakeAsyncClient):
            await GorgiasClient(subdomain="buttons-bebe",email="agent@example.com",api_key="test-key",
                base_url="https://buttons-bebe.gorgias.com").send_public_reply(123,"Reply",expected_recipient="customer@example.com",
                expected_source_message_id="44",on_created=on_created)


if __name__ == "__main__":
    unittest.main()
