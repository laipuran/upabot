import unittest
from unittest.mock import Mock, patch

import httpx
from support import FakeEvent

from plugins.astrbot_plugin_device_commands.main import DeviceCommandsPlugin


class DevicePluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
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
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.response))
        self.plugin = DeviceCommandsPlugin(Mock())
        with patch(
            "plugins.astrbot_plugin_device_commands.main.httpx.AsyncClient",
            return_value=self.client,
        ):
            await self.plugin.initialize()
        self.addAsyncCleanup(self.plugin.terminate)

    async def test_private_device_command_returns_status_without_agent(self):
        # AstrBot removes the wake prefix before invoking command handlers.
        event = FakeEvent("upa status")
        await self.plugin.upa(event)
        self.assertIn("CPU 温度: 42°C", event.replies[0])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.is_stopped())

    async def test_bot_self_messages_do_not_run_commands(self):
        event = FakeEvent("upa status", user="999")
        await self.plugin.upa(event)
        self.assertEqual(event.replies, [])

    async def test_group_command_rejects_private_operation_without_llm(self):
        event = FakeEvent("upa status", group="200")
        await self.plugin.upa(event)
        self.assertIn("请在私聊中使用", event.replies[0])
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

    async def test_unloading_closes_own_http_client(self):
        await self.plugin.terminate()
        self.assertTrue(self.client.is_closed)
