import unittest
from unittest.mock import Mock, patch

import httpx

from tests.support import FakeEvent

# isort: split

from plugins.astrbot_plugin_music_converter.main import MusicConverterPlugin

APPLE_URL = "https://music.apple.com/cn/album/test/123?i=456"


class MusicPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.response = httpx.Response(
            200, json={"name": "歌曲", "artist": "歌手", "neteaseUrl": "https://music.163.com/1"}
        )
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.response))
        self.plugin = MusicConverterPlugin(Mock())
        with patch(
            "plugins.astrbot_plugin_music_converter.main.httpx.AsyncClient",
            return_value=self.client,
        ):
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
        self.assertTrue(event.call_llm)

    async def test_regular_message_does_not_suppress_ai_chat(self):
        event = FakeEvent("你好", group="200")
        await self.plugin.convert_music(event)
        self.assertEqual(event.replies, [])
        self.assertFalse(event.call_llm)
        self.assertFalse(event.is_stopped())

    async def test_conversion_error_does_not_fall_through_to_agent(self):
        self.response = httpx.Response(503)
        event = FakeEvent(APPLE_URL)
        with self.assertLogs(self.plugin.logger, level="ERROR"):
            await self.plugin.convert_music(event)
        self.assertEqual(event.replies, ["转换服务暂时不可用，请稍后再试。"])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.is_stopped())

    async def test_bot_self_messages_do_not_convert_music(self):
        event = FakeEvent(APPLE_URL, user="999")
        await self.plugin.convert_music(event)
        self.assertEqual(event.replies, [])

    async def test_unloading_closes_own_http_client(self):
        await self.plugin.terminate()
        self.assertTrue(self.client.is_closed)
