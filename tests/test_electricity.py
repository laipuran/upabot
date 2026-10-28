import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from plugins.astrbot_plugin_electricity.electricity import Electricity


class ElectricityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "electricity.sqlite3"
        self.responses = {}
        self.requests = []
        self.now = 0.0

        def handle(request):
            self.requests.append(request)
            response = self.responses.get((request.method, request.url.path))
            if response is None:
                raise AssertionError(
                    f"Unexpected HTTP request: {request.method} {request.url.path}"
                )
            return httpx.Response(response[0], json=response[1])

        self.client = httpx.AsyncClient(
            base_url="http://information.test/", transport=httpx.MockTransport(handle)
        )
        self.addAsyncCleanup(self.client.aclose)
        self.app = Electricity(
            self.client, self.path, today=lambda: date(2026, 10, 28), clock=lambda: self.now
        )
        await self.app.initialize()

    def room_endpoints(self, room_id="50081022", room_name="603"):
        self.responses.update(
            {
                ("GET", "/api/areas"): (200, [{"areaId": "0025", "areaName": "信息学部学生宿舍"}]),
                ("GET", "/api/areas/0025/buildings"): (
                    200,
                    [
                        {
                            "architectureId": "000267",
                            "architectureName": "信息学部19舍(西区C3)",
                            "storyCount": 7,
                        }
                    ],
                ),
                ("GET", "/api/buildings/000267/rooms"): (
                    200,
                    [
                        {
                            "roomNo": room_id,
                            "roomName": room_name,
                        }
                    ],
                ),
                ("POST", "/api/rooms"): (200, {"roomId": room_id, "roomName": room_name}),
            }
        )

    async def bind(self, conversation="qq:FriendMessage:100", actor="100", action="subscribe"):
        self.room_endpoints()
        await self.app.command(conversation, actor, action)
        for answer in ("1", "1", "6", "1"):
            await self.app.select(conversation, actor, answer)
        return await self.app.select(conversation, actor, "确认")

    async def test_help_describes_interactive_subscription_and_alerts_without_http(self):
        reply = await self.app.command("qq:FriendMessage:100", "100", "help")
        self.assertIn("/elec subscribe", reply.text)
        self.assertIn("/elec alert on", reply.text)
        self.assertEqual(self.requests, [])

    async def test_interactive_room_subscription_is_confirmed_and_persists(self):
        self.room_endpoints()
        key = "qq:FriendMessage:100"
        self.assertIn("选择区域", (await self.app.command(key, "100", "subscribe")).text)
        self.assertIn("19舍", (await self.app.select(key, "100", "1")).text)
        self.assertIn("楼层", (await self.app.select(key, "100", "1")).text)
        self.assertIn("603", (await self.app.select(key, "100", "6")).text)
        self.assertIn("确认", (await self.app.select(key, "100", "1")).text)
        self.assertFalse(any(r.method == "POST" for r in self.requests))
        self.assertIn("订阅成功", (await self.app.select(key, "100", "确认")).text)
        reopened = Electricity(self.client, self.path)
        await reopened.initialize()
        self.assertIn("信息学部19舍(西区C3)-603", (await reopened.command(key, "100", "room")).text)
        self.assertIn("50081022", (await reopened.command(key, "100", "room")).text)

    async def test_wizard_only_accepts_initiator_choices_and_expires(self):
        self.room_endpoints()
        key = "qq:GroupMessage:200"
        await self.app.command(key, "100", "subscribe")
        self.assertIsNone(await self.app.select(key, "101", "1"))
        self.assertIsNone(await self.app.select(key, "100", "普通聊天"))
        self.assertIn("正在", (await self.app.command(key, "101", "subscribe")).text)
        self.now = 301
        self.assertIn("过期", (await self.app.select(key, "100", "1")).text)
        self.assertIn("尚未绑定", (await self.app.command(key, "101", "room")).text)

    async def test_back_cancel_and_failed_change_preserve_shared_binding(self):
        key = "qq:GroupMessage:200"
        await self.bind(key)
        self.assertIn("603", (await self.app.command(key, "101", "room")).text)
        self.assertIn("更换", (await self.app.command(key, "101", "subscribe")).text)
        await self.app.command(key, "101", "change")
        await self.app.select(key, "101", "1")
        self.assertIn("选择区域", (await self.app.select(key, "101", "返回")).text)
        await self.app.command(key, "101", "cancel")
        self.assertIn("603", (await self.app.command(key, "100", "room")).text)
        await self.bind(key, "101", "change")
        self.responses[("POST", "/api/rooms")] = (503, {"error": "unavailable"})
        await self.app.command(key, "101", "change")
        for answer in ("1", "1", "6", "1"):
            await self.app.select(key, "101", answer)
        self.assertIn("不可用", (await self.app.select(key, "101", "确认")).text)
        self.assertIn("603", (await self.app.command(key, "100", "room")).text)

    async def test_unsubscribe_requires_confirmation_and_does_not_delete_backend_room(self):
        key = "qq:FriendMessage:100"
        await self.bind(key)
        self.assertIn("确认", (await self.app.command(key, "100", "unsubscribe")).text)
        self.assertIn("603", (await self.app.command(key, "100", "room")).text)
        self.assertIn("解绑", (await self.app.select(key, "100", "确认")).text)
        self.assertIn("尚未绑定", (await self.app.command(key, "100", "room")).text)
        self.assertFalse(any(r.method == "DELETE" for r in self.requests))

    async def test_bound_queries_use_backend_each_time_without_implicit_refresh(self):
        key = "qq:FriendMessage:100"
        await self.bind(key)
        self.responses[("GET", "/api/rooms/50081022/balance")] = (
            200,
            {
                "roomId": "50081022",
                "balance": 8.5,
                "date": "2026-10-27",
            },
        )
        self.responses[("GET", "/api/rooms/50081022/daily-usage")] = (
            200,
            {
                "roomId": "50081022",
                "date": "2026-10-27",
                "usage": 12.5,
                "cost": 6.75,
            },
        )
        self.assertIn("8.50 元", (await self.app.command(key, "100", "balance")).text)
        await self.app.command(key, "100", "balance")
        usage = await self.app.command(key, "100", "usage")
        self.assertIn("12.5", usage.text)
        self.assertIn("6.75 元", usage.text)
        self.assertEqual(self.requests[-1].url.params["date"], "2026-10-27")
        self.assertEqual(sum(r.url.path.endswith("/balance") for r in self.requests), 2)
        self.responses[("GET", "/api/rooms/50081022/balance")] = (404, {"error": "none"})
        self.assertIn("暂无数据", (await self.app.command(key, "100", "balance")).text)
        self.assertFalse(any(r.url.path.endswith("/refresh") for r in self.requests))

    async def test_trend_renders_three_series_and_defaults_to_last_seven_complete_days(self):
        key = "qq:FriendMessage:100"
        await self.bind(key)
        self.responses[("GET", "/api/rooms/50081022/trend")] = (
            200,
            {
                "roomId": "50081022",
                "startDate": "2026-10-21",
                "endDate": "2026-10-27",
                "usage": [{"date": "2026-10-21", "value": 12.5}],
                "cost": [{"date": "2026-10-21", "value": 6.75}],
                "balance": [],
            },
        )
        reply = await self.app.command(key, "100", "trend")
        self.assertTrue(reply.image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(self.requests[-1].url.params["startDate"], "2026-10-21")
        self.assertEqual(self.requests[-1].url.params["endDate"], "2026-10-27")

    async def test_refresh_is_explicit_and_dates_are_validated_before_requests(self):
        key = "qq:FriendMessage:100"
        await self.bind(key)
        count = len(self.requests)
        for text in (
            "usage 2026-10-29",
            "usage 2026-02-30",
            "usage 2026-1-1",
            "trend 2026-10-01",
            "trend 2026-10-27 2026-10-21",
        ):
            with self.subTest(text=text):
                self.assertIn("日期", (await self.app.command(key, "100", text)).text)
        self.assertEqual(len(self.requests), count)
        self.responses[("POST", "/api/rooms/50081022/refresh")] = (
            200,
            {
                "roomId": "50081022",
                "balance": 20,
                "usage": {"date": "2026-10-28", "usage": 1, "cost": 0.54},
            },
        )
        self.assertIn("20.00 元", (await self.app.command(key, "100", "refresh")).text)
        self.assertEqual(self.requests[-1].method, "POST")

    async def test_backend_already_subscribed_still_binds_and_conversations_are_isolated(self):
        self.room_endpoints()
        self.responses[("POST", "/api/rooms")] = (409, {"error": "already subscribed"})
        key = "qq:GroupMessage:200"
        await self.app.command(key, "100", "subscribe")
        for answer in ("1", "1", "6", "1", "确认"):
            reply = await self.app.select(key, "100", answer)
        self.assertIn("订阅成功", reply.text)
        for other in ("qq:FriendMessage:200", "qq:GroupMessage:201", "other:GroupMessage:200"):
            self.assertIn("尚未绑定", (await self.app.command(other, "100", "room")).text)

    async def test_low_balance_alert_deduplicates_across_restart_and_rearms_after_recovery(self):
        key = "qq:GroupMessage:200"
        await self.bind(key)
        self.assertIn("10", (await self.app.command(key, "101", "alert on 10")).text)
        self.responses[("GET", "/api/rooms/50081022/balance")] = (
            200,
            {
                "roomId": "50081022",
                "balance": 8.5,
                "date": "2026-10-27",
            },
        )
        send = AsyncMock()
        await self.app.check_alerts(send)
        self.assertEqual(send.await_count, 1)
        self.assertEqual(send.await_args.args[0], key)
        self.assertIn("8.50 元", send.await_args.args[1])
        self.assertIn("2026-10-27", send.await_args.args[1])
        reopened = Electricity(self.client, self.path)
        await reopened.initialize()
        await reopened.check_alerts(send)
        self.assertEqual(send.await_count, 1)
        self.responses[("GET", "/api/rooms/50081022/balance")][1]["balance"] = 10
        await reopened.check_alerts(send)
        self.responses[("GET", "/api/rooms/50081022/balance")][1]["balance"] = 5
        await reopened.check_alerts(send)
        self.assertEqual(send.await_count, 2)
        self.assertIn("最近检查", (await reopened.command(key, "102", "alert")).text)

    async def test_failed_queries_and_failed_delivery_do_not_reset_or_consume_alert(self):
        key = "qq:FriendMessage:100"
        await self.bind(key)
        await self.app.command(key, "100", "alert on 10")
        send = AsyncMock()
        for status in (404, 429, 503):
            self.responses[("GET", "/api/rooms/50081022/balance")] = (status, {})
            await self.app.check_alerts(send)
        send.assert_not_awaited()
        self.responses[("GET", "/api/rooms/50081022/balance")] = (
            200,
            {
                "roomId": "50081022",
                "balance": 1,
                "date": "2026-10-27",
            },
        )
        send.side_effect = OSError("QQ unavailable")
        await self.app.check_alerts(send)
        send.side_effect = None
        await self.app.check_alerts(send)
        self.assertEqual(send.await_count, 2)
        self.responses[("GET", "/api/rooms/50081022/balance")] = (503, {})
        await self.app.check_alerts(send)
        self.responses[("GET", "/api/rooms/50081022/balance")] = (
            200,
            {
                "roomId": "50081022",
                "balance": 1,
                "date": "2026-10-27",
            },
        )
        await self.app.check_alerts(send)
        self.assertEqual(send.await_count, 2)

    async def test_alert_settings_validate_threshold_and_off_stops_checks(self):
        key = "qq:FriendMessage:100"
        self.assertIn("尚未绑定", (await self.app.command(key, "100", "alert on 10")).text)
        await self.bind(key)
        for value in ("0", "-1", "NaN", "Infinity", "abc", "1e1000", "1.001"):
            self.assertIn("金额", (await self.app.command(key, "100", "alert on " + value)).text)
        await self.app.command(key, "100", "alert on 10")
        self.assertIn("关闭", (await self.app.command(key, "100", "alert off")).text)
        await self.app.check_alerts(AsyncMock())
        self.assertFalse(any(r.url.path.endswith("/balance") for r in self.requests))

    async def test_async_subscription_and_leading_zero_room_id_are_supported(self):
        key = "qq:FriendMessage:100"
        self.room_endpoints("000603")
        self.responses[("POST", "/api/rooms")] = (
            202,
            {"roomId": "000603", "backfillStatus": "pending"},
        )
        await self.app.command(key, "100", "subscribe")
        for answer in ("1", "1", "6", "1", "确认"):
            reply = await self.app.select(key, "100", answer)
        self.assertIn("订阅成功", reply.text)
        self.assertIn("回填", reply.text)
        self.assertIn('"000603"', self.requests[-1].content.decode())
        self.assertIn("000603", (await self.app.command(key, "100", "room")).text)

    async def test_repeating_setting_and_invalid_balance_do_not_reset_notification(self):
        key = "qq:FriendMessage:100"
        await self.bind(key)
        await self.app.command(key, "100", "alert on 10")
        data = {"roomId": "50081022", "balance": 1, "date": "2026-10-27"}
        self.responses[("GET", "/api/rooms/50081022/balance")] = (200, data)
        send = AsyncMock()
        await self.app.check_alerts(send)
        await self.app.command(key, "100", "alert on 10.00")
        for invalid in (None, "NaN", "Infinity", True):
            data["balance"] = invalid
            await self.app.check_alerts(send)
        data["balance"] = 1
        await self.app.check_alerts(send)
        self.assertEqual(send.await_count, 1)

    async def test_change_preserves_threshold_but_resets_old_room_alarm(self):
        key = "qq:FriendMessage:100"
        await self.bind(key)
        await self.app.command(key, "100", "alert on 10")
        self.responses[("GET", "/api/rooms/50081022/balance")] = (
            200,
            {
                "roomId": "50081022",
                "balance": 1,
                "date": "2026-10-27",
            },
        )
        send = AsyncMock()
        await self.app.check_alerts(send)
        self.room_endpoints("50081023", "604")
        await self.app.command(key, "100", "change")
        for answer in ("1", "1", "6", "1", "确认"):
            await self.app.select(key, "100", answer)
        self.responses[("GET", "/api/rooms/50081023/balance")] = (
            200,
            {
                "roomId": "50081023",
                "balance": 2,
                "date": "2026-10-27",
            },
        )
        await self.app.check_alerts(send)
        self.assertEqual(send.await_count, 2)
        self.assertIn("604", send.await_args.args[1])
        await self.app.command(key, "100", "unsubscribe")
        await self.app.select(key, "100", "确认")
        requests = len(self.requests)
        await self.app.check_alerts(send)
        self.assertEqual(len(self.requests), requests)

    async def test_empty_trend_and_transport_errors_are_not_fabricated_as_data(self):
        key = "qq:FriendMessage:100"
        await self.bind(key)
        self.responses[("GET", "/api/rooms/50081022/trend")] = (
            200,
            {"usage": [], "cost": [], "balance": []},
        )
        reply = await self.app.command(key, "100", "trend")
        self.assertIn("暂无数据", reply.text)
        self.assertIsNone(reply.image)
        with patch.object(
            self.client, "request", side_effect=httpx.ConnectError("secret.internal:5051")
        ):
            reply = await self.app.command(key, "100", "balance")
        self.assertIn("暂时不可用", reply.text)
        self.assertNotIn("secret.internal", reply.text)
