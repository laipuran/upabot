import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from support import FakeEvent

# support must isolate runtime directories before importing AstrBot.
# isort: split
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from plugins.astrbot_plugin_device_commands.main import DeviceCommandsPlugin
from plugins.astrbot_plugin_music_converter.main import MusicConverterPlugin
from plugins.astrbot_plugin_taboo.main import TabooPlugin

APPLE_URL = "https://music.apple.com/cn/album/test/123?i=456"


async def dispatch(plugins, event):
    """Exercise registered handler filters/order without running the bot or Agent."""
    instances = {type(plugin).__module__: plugin for plugin in plugins}
    for handler in star_handlers_registry.get_handlers_by_event_type(EventType.AdapterMessageEvent):
        if event.is_stopped():
            break
        instance = instances.get(handler.handler_module_path)
        if instance is not None and all(f.filter(event, {}) for f in handler.event_filters):
            await handler.handler(instance, event)


class PluginInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.music_client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "name": "歌曲",
                        "artist": "歌手",
                        "neteaseUrl": "https://music.163.com/1",
                    },
                )
            )
        )
        self.device_client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        )
        self.music = MusicConverterPlugin(Mock())
        self.device = DeviceCommandsPlugin(Mock())
        with patch(
            "plugins.astrbot_plugin_music_converter.main.httpx.AsyncClient",
            return_value=self.music_client,
        ):
            await self.music.initialize()
        self.addAsyncCleanup(self.music.terminate)
        with patch(
            "plugins.astrbot_plugin_device_commands.main.httpx.AsyncClient",
            return_value=self.device_client,
        ):
            await self.device.initialize()
        self.addAsyncCleanup(self.device.terminate)
        with patch(
            "plugins.astrbot_plugin_taboo.main.get_astrbot_data_path", return_value=self.temp.name
        ):
            self.taboo = TabooPlugin(Mock())
        await self.taboo.initialize()
        self.addAsyncCleanup(self.taboo.terminate)

    async def test_registered_order_notifies_before_music_consumes_message(self):
        command = FakeEvent("tb reg music.apple.com")
        command.is_at_or_wake_command = True
        # Deliberately pass music first: registered priorities must determine execution order.
        plugins = [self.music, self.device, self.taboo]
        await dispatch(plugins, command)
        self.assertIn("注册成功", command.replies[0])

        event = FakeEvent(APPLE_URL, user="101", group="200")
        event.bot.call_action.return_value = {"group_name": "测试群"}
        await dispatch(plugins, event)
        self.assertIn("网易云音乐", event.replies[0])
        sends = [
            c for c in event.bot.call_action.await_args_list if c.args == ("send_private_msg",)
        ]
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0].kwargs["user_id"], 100)
        self.assertTrue(event.call_llm)
        self.assertTrue(event.is_stopped())

    async def test_disabling_taboo_does_not_disable_music(self):
        event = FakeEvent(APPLE_URL, group="200")
        await dispatch([self.music, self.device], event)
        self.assertIn("网易云音乐", event.replies[0])
        event.bot.call_action.assert_not_awaited()

    async def test_registered_command_precedes_music_link_detection(self):
        command = FakeEvent(f"tb reg {APPLE_URL}")
        command.is_at_or_wake_command = True
        await dispatch([self.music, self.taboo], command)
        self.assertEqual(len(command.replies), 1)
        self.assertIn("注册成功", command.replies[0])

        listing = FakeEvent("tb list")
        listing.is_at_or_wake_command = True
        await dispatch([self.music, self.taboo], listing)
        self.assertIn(APPLE_URL, listing.replies[0])

    async def test_taboo_database_failure_does_not_break_other_plugins(self):
        with patch("aiosqlite.connect", side_effect=OSError("database unavailable")):
            with self.assertRaises(OSError):
                await self.taboo.initialize()
            event = FakeEvent(APPLE_URL)
            await dispatch([self.music, self.device], event)
            self.assertIn("网易云音乐", event.replies[0])

            command = FakeEvent("upa status")
            command.is_at_or_wake_command = True
            with self.assertLogs(self.device.logger, level="ERROR"):
                await dispatch([self.music, self.device], command)
            self.assertEqual(command.replies, ["查询设备状态失败，请稍后再试。"])

    async def test_unloading_music_keeps_device_client_open(self):
        await self.music.terminate()
        self.assertTrue(self.music_client.is_closed)
        self.assertFalse(self.device_client.is_closed)
