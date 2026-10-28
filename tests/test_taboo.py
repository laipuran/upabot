import tempfile
import unittest
from pathlib import Path

from plugins.astrbot_plugin_duckbot.message_cache import ChatMessage
from plugins.astrbot_plugin_duckbot.taboo import Taboo


class TabooTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "taboo.sqlite3"
        self.taboo = Taboo(self.path)
        await self.taboo.initialize()

    async def test_registered_keywords_are_listed_after_reopening(self):
        reply = await self.taboo.command("qq", "100", "reg 鸭鸭")
        self.assertIn("注册成功", reply)

        reopened = Taboo(self.path)
        await reopened.initialize()
        self.assertEqual(
            await reopened.command("qq", "100", "list"),
            "您已注册的关键词:\n  1. 鸭鸭",
        )

    async def test_group_mentions_notify_subscriber_with_recent_context_once(self):
        await self.taboo.command("qq", "100", "reg 鸭鸭")
        await self.taboo.command("qq", "100", "reg duck")
        await self.taboo.observe("qq", "200", ChatMessage("101", "小明", "早上好", 1))
        notices = await self.taboo.observe(
            "qq", "200", ChatMessage("102", "小红", "鸭鸭 duck 在吗", 2)
        )

        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].user_id, "100")
        self.assertEqual(notices[0].keyword, "鸭鸭")
        self.assertEqual([m.text for m in notices[0].messages], ["早上好", "鸭鸭 duck 在吗"])
        self.assertTrue(notices[0].messages[-1].is_trigger)

    async def test_off_persists_and_on_restores_notifications(self):
        await self.taboo.command("qq", "100", "reg 鸭鸭")
        self.assertEqual(await self.taboo.command("qq", "100", "off"), "Taboo 监控已关闭")
        reopened = Taboo(self.path)
        await reopened.initialize()
        message = ChatMessage("101", "小明", "鸭鸭", 1)
        self.assertEqual(await reopened.observe("qq", "200", message), [])
        self.assertEqual(await reopened.command("qq", "100", "on"), "Taboo 监控已开启")
        self.assertEqual(len(await reopened.observe("qq", "200", message)), 1)

    async def test_aliases_keep_keyword_ownership_and_allow_owner_to_unregister(self):
        await self.taboo.command("qq", "100", "add 鸭 鸭")
        self.assertEqual(
            await self.taboo.command("qq", "101", "unreg 鸭 鸭"), "您未注册关键词「鸭 鸭」"
        )
        self.assertIn("已被注册", await self.taboo.command("qq", "101", "register 鸭 鸭"))
        self.assertIn("鸭 鸭", await self.taboo.command("qq", "100", "ls"))
        self.assertEqual(
            await self.taboo.command("qq", "100", "delete 鸭 鸭"), "关键词「鸭 鸭」已取消注册"
        )
        self.assertEqual(await self.taboo.command("qq", "100", "list"), "您尚未注册任何关键词")

    async def test_self_mentions_and_other_platforms_do_not_notify(self):
        await self.taboo.command("qq", "100", "reg 鸭鸭")
        self.assertEqual(
            await self.taboo.observe("qq", "200", ChatMessage("100", "本人", "鸭鸭", 1)), []
        )
        self.assertEqual(
            await self.taboo.observe("other", "200", ChatMessage("101", "别人", "鸭鸭", 1)), []
        )
        self.assertEqual(await self.taboo.command("other", "100", "list"), "您尚未注册任何关键词")

    async def test_notification_context_is_bounded_and_group_scoped(self):
        await self.taboo.command("qq", "100", "reg 鸭鸭")
        await self.taboo.observe("qq", "other", ChatMessage("101", "别人", "秘密", 1))
        for index in range(15):
            await self.taboo.observe("qq", "200", ChatMessage("101", "别人", str(index), index))
        notices = await self.taboo.observe("qq", "200", ChatMessage("101", "别人", "鸭鸭", 20))
        self.assertEqual(
            [m.text for m in notices[0].messages],
            ["6", "7", "8", "9", "10", "11", "12", "13", "14", "鸭鸭"],
        )

    async def test_help_and_missing_arguments_do_not_create_subscriptions(self):
        self.assertIn("Taboo 使用说明", await self.taboo.command("qq", "100", ""))
        self.assertIn("Taboo 使用说明", await self.taboo.command("qq", "100", "h"))
        self.assertIn("未知命令", await self.taboo.command("qq", "100", "wrong"))
        self.assertIn("请指定关键词", await self.taboo.command("qq", "100", "reg"))
        self.assertIn("请指定关键词", await self.taboo.command("qq", "100", "unreg"))
        self.assertEqual(await self.taboo.command("qq", "100", "list"), "您尚未注册任何关键词")
