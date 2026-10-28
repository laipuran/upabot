import os

import httpx
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .device import DeviceStatus


class DeviceCommandsPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.client: httpx.AsyncClient | None = None

    async def initialize(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        self.device = DeviceStatus(
            self.client,
            os.getenv("DEVICE_STATUS_API_URL", "http://host.docker.internal:5000/DeviceStatus"),
        )

    async def terminate(self):
        if self.client is not None:
            await self.client.aclose()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.command("upa", priority=50)
    async def upa(self, event: AstrMessageEvent):
        if event.get_sender_id() == event.get_self_id():
            return
        event.should_call_llm(True)
        event.stop_event()
        if not event.is_private_chat():
            reply = "请在私聊中使用 Upa 命令"
        elif event.message_str.split()[1:] != ["status"]:
            reply = "使用方法: /upa status"
        else:
            try:
                reply = await self.device.status()
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                self.logger.exception("Device status request failed")
                reply = "查询设备状态失败，请稍后再试。"
        await event.send(event.plain_result(reply))
