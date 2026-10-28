import unittest

import httpx

from plugins.astrbot_plugin_device_commands.device import DeviceStatus
from plugins.astrbot_plugin_music_converter.music import MusicConverter


class MusicConverterTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_message_does_not_request_conversion(self):
        def unexpected_request(request):
            self.fail("Regular messages must not make HTTP requests")

        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
            self.assertIsNone(await MusicConverter(client).convert("早上好"))

    async def test_missing_match_returns_conversion_failure(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
        ) as client:
            self.assertEqual(
                await MusicConverter(client).convert(APPLE_URL),
                "无法转换该 Apple Music 链接。",
            )

    async def test_shared_apple_music_link_returns_netease_link(self):
        def convert(request):
            self.assertEqual(request.url.params["url"], APPLE_URL)
            return httpx.Response(
                200,
                json={
                    "name": "晴天",
                    "artist": "周杰伦",
                    "neteaseUrl": "https://music.163.com/song?id=186016",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(convert)) as client:
            converter = MusicConverter(client)
            reply = await converter.convert(f"分享一首歌 {APPLE_URL}")

        self.assertEqual(
            reply,
            "Apple Music -> 网易云音乐\n原曲: 晴天 - 周杰伦\nhttps://music.163.com/song?id=186016",
        )


APPLE_URL = "https://music.apple.com/cn/album/晴天/123456?i=654321"


class DeviceStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_device_api_raises_http_error(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        ) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await DeviceStatus(client, "http://device.test/DeviceStatus").status()

    async def test_status_formats_device_api_response(self):
        def status(request):
            self.assertEqual(str(request.url), "http://device.test/DeviceStatus")
            return httpx.Response(
                200,
                json={
                    "cpuTemperatureCelsius": 42,
                    "uptime": "1 day",
                    "model": "Raspberry Pi",
                    "osVersion": "Debian",
                    "ip": "192.0.2.1",
                    "memory": {"usedMB": 512, "totalMB": 4096, "usagePercentage": 12.5},
                    "storage": {"usedGB": 10, "totalGB": 100, "usagePercentage": 10},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(status)) as client:
            service = DeviceStatus(client, "http://device.test/DeviceStatus")
            reply = await service.status()

        self.assertEqual(
            reply,
            "Upa 设备状态:\nCPU 温度: 42°C\n运行时间: 1 day\n设备型号: Raspberry Pi\n"
            "系统版本: Debian\nIP 地址: 192.0.2.1\n\n内存: 512MB / 4096MB (12.5%)\n"
            "存储: 10GB / 100GB (10.0%)",
        )
