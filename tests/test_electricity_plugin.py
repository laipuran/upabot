import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from support import FakeEvent

from plugins.astrbot_plugin_electricity.main import ElectricityPlugin


class ElectricityPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = httpx.AsyncClient(
            base_url="http://information.test/",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json=[{"areaId": "0025", "areaName": "信息学部学生宿舍"}]
                )
            ),
        )
        self.context = Mock()
        self.context.send_message = AsyncMock(return_value=True)
        with patch(
            "plugins.astrbot_plugin_electricity.main.get_astrbot_data_path",
            return_value=self.temp.name,
        ):
            self.plugin = ElectricityPlugin(self.context)
        with patch(
            "plugins.astrbot_plugin_electricity.main.httpx.AsyncClient", return_value=self.client
        ):
            await self.plugin.initialize()
        self.addAsyncCleanup(self.plugin.terminate)

    async def test_command_is_direct_and_plain_chat_or_other_users_are_not_consumed(self):
        command = FakeEvent("elec subscribe", group="200")
        await self.plugin.electricity(command)
        self.assertIn("信息学部", command.replies[0])
        self.assertTrue(command.is_stopped())
        self.assertTrue(command.call_llm)
        for event in (FakeEvent("1", user="101", group="200"), FakeEvent("普通聊天", group="200")):
            await self.plugin.selection(event)
            self.assertEqual(event.replies, [])
            self.assertFalse(event.is_stopped())

    async def test_conversation_ignores_llm_group_member_isolation(self):
        first = FakeEvent("elec subscribe", group="200")
        first.session_id = "200_100"
        await self.plugin.electricity(first)
        second = FakeEvent("elec subscribe", user="101", group="200")
        second.session_id = "200_101"
        await self.plugin.electricity(second)
        self.assertIn("正在", second.replies[0])
        private = FakeEvent("elec room", user="200")
        await self.plugin.electricity(private)
        self.assertIn("尚未绑定", private.replies[0])

    async def test_self_messages_are_ignored_and_unload_closes_client(self):
        event = FakeEvent("elec help", user="999")
        await self.plugin.electricity(event)
        self.assertEqual(event.replies, [])
        await self.plugin.terminate()
        self.assertTrue(self.client.is_closed)

    async def test_chinese_and_english_aliases_are_registered_and_disable_agent(self):
        from test_plugin_interactions import dispatch

        for root in ("elec", "电费"):
            event = FakeEvent(root + " help", group="200")
            event.is_at_or_wake_command = True
            await dispatch([self.plugin], event)
            self.assertIn("/elec subscribe", event.replies[0])
            self.assertTrue(event.call_llm)

    async def test_valid_selection_is_consumed_without_agent(self):
        await self.plugin.electricity(FakeEvent("elec subscribe", group="200"))
        event = FakeEvent("999", group="200")
        await self.plugin.selection(event)
        self.assertIn("有效序号", event.replies[0])
        self.assertTrue(event.is_stopped())
        self.assertTrue(event.call_llm)

    async def test_proactive_notification_uses_conversation_target_and_failed_delivery_raises(self):
        key = "qq:GroupMessage:200"
        await self.plugin.send_alert(key, "低余额提醒")
        self.assertEqual(self.context.send_message.await_args.args[0], key)
        self.context.send_message.return_value = False
        with self.assertRaises(RuntimeError):
            await self.plugin.send_alert(key, "低余额提醒")
