import asyncio
import base64
import math
import os
from pathlib import Path

import httpx
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .electricity import Electricity, Reply


class ElectricityPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.database = (
            Path(get_astrbot_data_path())
            / "plugin_data"
            / "astrbot_plugin_electricity"
            / "electricity.sqlite3"
        )
        self.client: httpx.AsyncClient | None = None
        self.poller: asyncio.Task | None = None

    async def initialize(self):
        self.interval = float(os.getenv("ELEC_ALERT_INTERVAL_SECONDS", "3600"))
        if not math.isfinite(self.interval) or self.interval < 60:
            raise ValueError("ELEC_ALERT_INTERVAL_SECONDS 必须为不小于60的秒数")
        self.client = httpx.AsyncClient(
            base_url=os.getenv("ELEC_API_BASE_URL", "http://host.docker.internal:5051").rstrip("/")
            + "/",
            timeout=60.0,
        )
        try:
            self.app = Electricity(self.client, self.database)
            await self.app.initialize()
        except BaseException:
            await self.client.aclose()
            raise
        self.poller = asyncio.create_task(self.poll_alerts(), name="electricity-alerts")

    async def terminate(self):
        if self.poller is not None:
            self.poller.cancel()
            try:
                await self.poller
            except asyncio.CancelledError:
                pass
        if self.client is not None:
            await self.client.aclose()

    @staticmethod
    def conversation(event: AstrMessageEvent) -> str:
        group = event.get_group_id()
        return str(
            MessageSession(
                event.get_platform_id(),
                MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE,
                group or event.get_sender_id(),
            )
        )

    async def respond(self, event: AstrMessageEvent, reply: Reply):
        event.should_call_llm(True)
        event.stop_event()
        chain = [Plain(reply.text)]
        if reply.image is not None:
            chain.append(Image.fromBase64(base64.b64encode(reply.image).decode()))
        await event.send(event.chain_result(chain))

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.command("电费", alias={"elec"}, priority=50)
    async def electricity(self, event: AstrMessageEvent):
        if event.get_sender_id() == event.get_self_id():
            return
        event.should_call_llm(True)
        event.stop_event()
        parts = event.message_str.split(maxsplit=1)
        try:
            reply = await self.app.command(
                self.conversation(event),
                event.get_sender_id(),
                parts[1] if len(parts) > 1 else "help",
            )
        except Exception:
            self.logger.exception("Electricity command failed")
            reply = Reply("电费功能暂时不可用，请稍后重试。")
        await self.respond(event, reply)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL, priority=40)
    async def selection(self, event: AstrMessageEvent):
        if event.get_sender_id() == event.get_self_id():
            return
        try:
            reply = await self.app.select(
                self.conversation(event), event.get_sender_id(), event.message_str
            )
        except Exception:
            self.logger.exception("Electricity room selection failed")
            reply = Reply("选房暂时失败，原绑定不变，请稍后重试或 /elec cancel。")
        if reply is not None:
            await self.respond(event, reply)

    async def send_alert(self, conversation: str, text: str):
        found = await self.context.send_message(conversation, MessageChain().message(text))
        if not found:
            raise RuntimeError("Electricity alert platform unavailable")

    async def poll_alerts(self):
        # First check runs after one interval; only enabled, bound conversations are queried.
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.app.check_alerts(self.send_alert)
            except Exception:
                self.logger.exception("Electricity alert polling failed")
