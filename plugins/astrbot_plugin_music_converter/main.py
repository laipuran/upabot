import httpx
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .music import APPLE_MUSIC_PATTERN, MusicConverter


class MusicConverterPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.client: httpx.AsyncClient | None = None

    async def initialize(self):
        self.client = httpx.AsyncClient(timeout=15.0)
        self.music = MusicConverter(self.client)

    async def terminate(self):
        if self.client is not None:
            await self.client.aclose()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL)
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
