import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from support import FakeEvent

from plugins.astrbot_plugin_duckbot.main import DuckBot

APPLE_URL = "https://music.apple.com/cn/album/test/123?i=456"


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.response = httpx.Response(
            200, json={"name": "歌曲", "artist": "歌手", "neteaseUrl": "https://music.163.com/1"}
        )
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.response))
        with (
            patch(
                "plugins.astrbot_plugin_duckbot.main.get_astrbot_data_path",
                return_value=self.temp.name,
            ),
            patch(
                "plugins.astrbot_plugin_duckbot.main.httpx.AsyncClient", return_value=self.client
            ),
        ):
            self.plugin = DuckBot(Mock())
            await self.plugin.initialize()
        self.addAsyncCleanup(self.plugin.terminate)

    async def test_music_conversion_replies_without_default_llm(self):
        event = FakeEvent(APPLE_URL, group="200")
        event.is_at_or_wake_command = True
        await self.plugin.convert_music(event)

        self.assertEqual(
            event.replies,
            ["Apple Music -> 网易云音乐\n原曲: 歌曲 - 歌手\nhttps://music.163.com/1"],
        )
        self.assertTrue(event.is_stopped())
        self.assertTrue(event.call_llm)  # AstrBot's flag means suppress the default request.

    async def test_private_device_command_returns_status_without_agent(self):
        self.response = httpx.Response(
            200,
            json={
                "cpuTemperatureCelsius": 42,
                "uptime": "1 day",
                "model": "Pi",
                "osVersion": "Debian",
                "ip": "192.0.2.1",
                "memory": {"usedMB": 512, "totalMB": 4096, "usagePercentage": 12.5},
                "storage": {"usedGB": 10, "totalGB": 100, "usagePercentage": 10},
            },
        )
        # AstrBot removes the configured wake prefix before invoking command handlers.
        event = FakeEvent("upa status")
        await self.plugin.upa(event)
        self.assertIn("CPU 温度: 42°C", event.replies[0])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.is_stopped())

    async def test_keyword_command_then_group_mention_sends_private_image(self):
        command = FakeEvent("tb reg 鸭鸭")
        await self.plugin.taboo_command(command)
        self.assertIn("注册成功", command.replies[0])
        self.assertTrue(command.call_llm)
        self.assertTrue(command.is_stopped())

        event = FakeEvent("鸭鸭在吗", user="101", group="200")
        event.bot.call_action.return_value = {"group_name": "测试群"}
        await self.plugin.observe_group(event)

        event.bot.call_action.assert_any_await("get_group_info", group_id=200, self_id=999)
        sends = [
            c for c in event.bot.call_action.await_args_list if c.args == ("send_private_msg",)
        ]
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0].kwargs["user_id"], 100)
        self.assertEqual(sends[0].kwargs["self_id"], 999)
        image = sends[0].kwargs["message"][0]
        self.assertEqual(image["type"], "image")
        self.assertTrue(image["data"]["file"].startswith("base64://iVBOR"))
        self.assertEqual(event.replies, [])
        self.assertFalse(event.is_stopped())  # Monitoring must not consume group messages.

    async def test_bot_self_messages_do_not_run_commands(self):
        for handler, text in [
            (self.plugin.upa, "upa status"),
            (self.plugin.taboo_command, "tb reg 鸭鸭"),
        ]:
            with self.subTest(text=text):
                event = FakeEvent(text, user="999")
                await handler(event)
                self.assertEqual(event.replies, [])

    async def test_group_commands_reject_private_operations_without_llm(self):
        for handler, text in [
            (self.plugin.upa, "upa status"),
            (self.plugin.taboo_command, "tb reg 鸭鸭"),
        ]:
            with self.subTest(text=text):
                event = FakeEvent(text, group="200")
                await handler(event)
                self.assertIn("请在私聊中使用", event.replies[0])
                self.assertTrue(event.call_llm)
                self.assertTrue(event.is_stopped())

    async def test_regular_message_does_not_suppress_ai_chat(self):
        event = FakeEvent("你好", group="200")
        await self.plugin.observe_group(event)
        await self.plugin.convert_music(event)
        self.assertEqual(event.replies, [])
        self.assertFalse(event.call_llm)
        self.assertFalse(event.is_stopped())
        event.bot.call_action.assert_not_awaited()

    async def test_conversion_error_does_not_fall_through_to_agent(self):
        self.response = httpx.Response(503)
        event = FakeEvent(APPLE_URL)
        with self.assertLogs(self.plugin.logger, level="ERROR"):
            await self.plugin.convert_music(event)
        self.assertEqual(event.replies, ["转换服务暂时不可用，请稍后再试。"])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.is_stopped())

    async def test_failed_device_request_does_not_reveal_internal_url(self):
        self.response = httpx.Response(503)
        event = FakeEvent("upa status")
        with self.assertLogs(self.plugin.logger, level="ERROR"):
            await self.plugin.upa(event)
        self.assertEqual(event.replies, ["查询设备状态失败，请稍后再试。"])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.is_stopped())

    async def test_image_failure_notifies_subscriber_not_group_sender(self):
        await self.plugin.taboo_command(FakeEvent("taboo reg 鸭鸭"))
        event = FakeEvent("鸭鸭", user="101", group="200")
        event.bot.call_action.return_value = {"group_name": "测试群"}
        with (
            patch("PIL.Image.new", side_effect=OSError("image unavailable")),
            self.assertLogs(self.plugin.logger, level="ERROR"),
        ):
            await self.plugin.observe_group(event)
        event.bot.call_action.assert_any_await(
            "send_private_msg",
            user_id=100,
            self_id=999,
            message=[{"type": "text", "data": {"text": "有人在群 测试群 中提到了「鸭鸭」"}}],
        )

    async def test_keyword_notifications_do_not_prevent_music_conversion(self):
        await self.plugin.taboo_command(FakeEvent("tb reg music.apple.com"))
        event = FakeEvent(APPLE_URL, user="101", group="200")
        event.bot.call_action.return_value = {"group_name": "测试群"}
        await self.plugin.observe_group(event)
        await self.plugin.convert_music(event)
        self.assertIn("网易云音乐", event.replies[0])
        self.assertEqual(
            sum(c.args == ("send_private_msg",) for c in event.bot.call_action.await_args_list), 1
        )
        self.assertTrue(event.call_llm)
