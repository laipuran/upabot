import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from support import FakeEvent

from plugins.astrbot_plugin_taboo.main import TabooPlugin
from plugins.astrbot_plugin_taboo.taboo import Taboo


class TabooPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch(
            "plugins.astrbot_plugin_taboo.main.get_astrbot_data_path", return_value=self.temp.name
        ):
            self.plugin = TabooPlugin(Mock())
        await self.plugin.initialize()
        self.addAsyncCleanup(self.plugin.terminate)

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
        self.assertFalse(event.is_stopped())

    async def test_bot_self_messages_do_not_run_commands(self):
        event = FakeEvent("tb reg 鸭鸭", user="999")
        await self.plugin.taboo_command(event)
        self.assertEqual(event.replies, [])

    async def test_group_command_rejects_private_operation_without_llm(self):
        event = FakeEvent("tb reg 鸭鸭", group="200")
        await self.plugin.taboo_command(event)
        self.assertIn("请在私聊中使用", event.replies[0])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.is_stopped())

    async def test_regular_message_does_not_suppress_ai_chat(self):
        event = FakeEvent("你好", group="200")
        await self.plugin.observe_group(event)
        self.assertEqual(event.replies, [])
        self.assertFalse(event.call_llm)
        self.assertFalse(event.is_stopped())
        event.bot.call_action.assert_not_awaited()

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

    async def test_existing_combined_plugin_subscriptions_and_switches_are_reused(self):
        legacy = Taboo(
            Path(self.temp.name) / "plugin_data" / "astrbot_plugin_duckbot" / "taboo.sqlite3"
        )
        await legacy.initialize()
        await legacy.command("qq", "100", "reg existing")
        await legacy.command("qq", "100", "off")
        with patch(
            "plugins.astrbot_plugin_taboo.main.get_astrbot_data_path", return_value=self.temp.name
        ):
            reopened = TabooPlugin(Mock())
        await reopened.initialize()
        self.addAsyncCleanup(reopened.terminate)
        listing = FakeEvent("tb list")
        await reopened.taboo_command(listing)
        self.assertIn("existing", listing.replies[0])

        muted = FakeEvent("existing", user="101", group="200")
        await reopened.observe_group(muted)
        muted.bot.call_action.assert_not_awaited()

        await reopened.taboo_command(FakeEvent("tb on"))
        event = FakeEvent("existing", user="101", group="200")
        event.bot.call_action.return_value = {"group_name": "测试群"}
        await reopened.observe_group(event)
        sends = [
            c for c in event.bot.call_action.await_args_list if c.args == ("send_private_msg",)
        ]
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0].kwargs["user_id"], 100)
