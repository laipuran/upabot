import os
from pathlib import Path

import httpx
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .image_gen import render_chat_image
from .message_cache import ChatMessage
from .services import APPLE_MUSIC_PATTERN, DeviceStatus, MusicConverter
from .taboo import Taboo


class DuckBot(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        data = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_duckbot"
        self.taboo = Taboo(data / "taboo.sqlite3")
        self.client: httpx.AsyncClient | None = None

    async def initialize(self):
        await self.taboo.initialize()
        self.client = httpx.AsyncClient(timeout=15.0)
        self.music = MusicConverter(self.client)
        self.device = DeviceStatus(
            self.client,
            os.getenv("DEVICE_STATUS_API_URL", "http://host.docker.internal:5000/DeviceStatus"),
        )

    async def terminate(self):
        if self.client is not None:
            await self.client.aclose()

    @filter.command("upa", priority=50)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
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

    @filter.command("taboo", alias={"tb"}, priority=50)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def taboo_command(self, event: AstrMessageEvent):
        if event.get_sender_id() == event.get_self_id():
            return
        event.should_call_llm(True)
        event.stop_event()
        if not event.is_private_chat():
            reply = "请在私聊中使用 Taboo 命令"
        else:
            parts = event.message_str.split(maxsplit=1)
            text = parts[1] if len(parts) > 1 else ""
            try:
                reply = await self.taboo.command(
                    event.get_platform_id(), event.get_sender_id(), text
                )
            except Exception:
                self.logger.exception("Taboo command failed")
                reply = "关键词设置失败，请稍后再试。"
        await event.send(event.plain_result(reply))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=100)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def observe_group(self, event: AstrMessageEvent):
        # Run before commands/music can consume the event. Never suppress AI here.
        if event.get_sender_id() == event.get_self_id():
            return
        try:
            notices = await self.taboo.observe(
                event.get_platform_id(),
                event.get_group_id(),
                ChatMessage(
                    user_id=event.get_sender_id(),
                    nickname=event.get_sender_name(),
                    text=event.message_str,
                    time=event.message_obj.timestamp,
                ),
            )
        except Exception:
            self.logger.exception("Taboo monitoring failed")
            return
        if not notices:
            return
        group_name = event.get_group_id()
        routing = {"self_id": int(event.get_self_id())}
        try:
            info = await event.bot.call_action(
                "get_group_info", group_id=int(event.get_group_id()), **routing
            )
            group_name = info.get("group_name") or group_name
        except Exception:
            self.logger.warning("Cannot get group name; using group ID", exc_info=True)
        for notice in notices:
            try:
                image = await render_chat_image(notice.keyword, notice.messages, group_name)
                message = [{"type": "image", "data": {"file": image}}]
            except Exception:
                self.logger.exception("Cannot render Taboo image; sending text instead")
                message = [
                    {
                        "type": "text",
                        "data": {"text": f"有人在群 {group_name} 中提到了「{notice.keyword}」"},
                    }
                ]
            try:
                await event.bot.call_action(
                    "send_private_msg", user_id=int(notice.user_id), message=message, **routing
                )
            except Exception:
                self.logger.warning("Cannot notify subscriber %s", notice.user_id, exc_info=True)

    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def convert_music(self, event: AstrMessageEvent):
        if event.get_sender_id() == event.get_self_id():
            return
        if not APPLE_MUSIC_PATTERN.search(event.message_str):
            return
        # Set before I/O, so failures cannot fall through into the default Agent.
        event.should_call_llm(True)
        event.stop_event()
        try:
            reply = await self.music.convert(event.message_str)
        except httpx.HTTPError:
            self.logger.exception("Music conversion request failed")
            reply = "转换服务暂时不可用，请稍后再试。"
        except (ValueError, TypeError, AttributeError):
            self.logger.exception("Invalid music conversion response")
            reply = "转换服务返回了无效结果，请稍后再试。"
        if reply:
            await event.send(event.plain_result(reply))
