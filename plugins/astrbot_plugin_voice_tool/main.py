import asyncio
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Record
from astrbot.api.star import Context, Star

SYNTHESIS_TIMEOUT_SECONDS = 30


class VoiceToolPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.llm_tool(name="speak")
    async def speak(self, event: AstrMessageEvent, text: str):
        """把一段话合成语音发给用户，想发声时用

        Args:
            text(string): 要合成语音的文字。
        """
        if not self.config.get("enabled", True):
            yield "语音工具已关闭，请改用文字回复用户。"
            return
        if not isinstance(text, str) or not text.strip():
            yield "语音文字为空，请改用文字回复用户。"
            return

        try:
            max_chars = max(1, int(self.config.get("max_chars", 120)))
        except (TypeError, ValueError, OverflowError):
            max_chars = 120
        text = text.strip()
        truncated = len(text) > max_chars
        text = text[:max_chars]

        try:
            async with asyncio.timeout(SYNTHESIS_TIMEOUT_SECONDS):
                provider = await self.context.get_using_tts_provider_async(event.unified_msg_origin)
                path = await provider.get_audio(text) if provider is not None else None
        except TimeoutError:
            yield "语音合成超时，请改用文字回复用户。"
            return
        except Exception:
            self.logger.exception("Voice synthesis failed")
            yield "语音合成失败，请改用文字回复用户。"
            return

        if provider is None:
            yield "当前会话未配置 TTS 提供商，请改用文字回复用户。"
            return
        if not isinstance(path, str) or not path.strip():
            yield "语音合成未返回音频路径，请改用文字回复用户。"
            return

        audio = Path(path)
        try:
            if not audio.is_file():
                yield "语音文件不可用，请改用文字回复用户。"
                return
            if truncated:
                yield event.plain_result(f"文字过长，本次语音仅朗读前 {max_chars} 字。")
            yield event.chain_result([Record(file=path)])
        finally:
            # AstrBot consumes each yielded message before advancing the generator.
            if self.config.get("cleanup", True):
                try:
                    audio.unlink(missing_ok=True)
                except OSError:
                    self.logger.warning("Voice temporary file cleanup failed", exc_info=True)
