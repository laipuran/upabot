from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .image_gen import render_chat_image
from .message_cache import ChatMessage
from .taboo import Taboo


class TabooPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # Keep the original data directory so splitting the plugin preserves subscriptions.
        data = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_duckbot"
        self.taboo = Taboo(data / "taboo.sqlite3")

    async def initialize(self):
        await self.taboo.initialize()

    # AstrBot records priority on first registration: keep this filter innermost.
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.command("taboo", alias={"tb"}, priority=50)
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

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=100)
    async def observe_group(self, event: AstrMessageEvent):
        # Run before other plugins' commands/music consume the event. Never suppress AI here.
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
