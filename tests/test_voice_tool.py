import asyncio
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from astrbot.api.message_components import Record
from astrbot.core.astr_agent_tool_exec import (
    FunctionTool,
    FunctionToolExecutor,
    call_local_llm_tool,
)
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.provider.func_tool_manager import FunctionToolManager
from astrbot.core.provider.register import llm_tools
from support import FakeEvent

from plugins.astrbot_plugin_voice_tool.main import VoiceToolPlugin

MODULE = "plugins.astrbot_plugin_voice_tool.main"


class VoiceToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp/opencode")
        self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / "voice.wav"
        self.audio.write_bytes(b"test audio")
        self.provider = Mock(get_audio=AsyncMock(return_value=str(self.audio)))
        self.context = Mock(get_using_tts_provider_async=AsyncMock(return_value=self.provider))
        self.plugin = VoiceToolPlugin(self.context, {})
        self.event = FakeEvent("请说你好")

    async def collect(self, text="你好"):
        return [value async for value in self.plugin.speak(self.event, text)]

    async def test_registered_tool_schema(self):
        tool = next(tool for tool in llm_tools.func_list if tool.name == "speak")
        self.assertIsInstance(tool, FunctionTool)
        self.assertEqual(tool.description, "把一段话合成语音发给用户，想发声时用")
        self.assertEqual(tool.parameters["properties"]["text"]["type"], "string")
        self.assertEqual(set(tool.parameters["properties"]), {"text"})

    async def test_session_provider_and_cleanup_after_send(self):
        generator = self.plugin.speak(self.event, "你好")
        result = await anext(generator)
        self.assertIn("语音已发送", result)
        self.assertIsInstance(self.event.sent[0].chain[0], Record)
        self.assertEqual(self.event.sent[0].chain[0].file, str(self.audio))
        self.assertFalse(self.audio.exists())
        self.context.get_using_tts_provider_async.assert_awaited_once_with(
            self.event.unified_msg_origin
        )
        self.provider.get_audio.assert_awaited_once_with("你好")
        with self.assertRaises(StopAsyncIteration):
            await anext(generator)
        self.assertFalse(self.audio.exists())

    async def test_real_sdk_wrapper_does_not_delete_before_send(self):
        context = SimpleNamespace(context=SimpleNamespace(event=self.event))
        wrapper = call_local_llm_tool(
            context=context,
            handler=self.plugin.speak,
            method_name="decorator_handler",
            text="你好",
        )
        async for result in wrapper:
            self.assertIn("语音已发送", result)
        self.assertEqual(len(self.event.sent), 1)
        self.assertFalse(self.audio.exists())

    async def test_cleanup_disabled(self):
        self.plugin.config["cleanup"] = False
        await self.collect()
        self.assertTrue(self.audio.exists())

    async def test_real_executor_sends_before_cleanup(self):
        context = SimpleNamespace(context=SimpleNamespace(event=self.event), tool_call_timeout=60)
        registered = next(tool for tool in llm_tools.func_list if tool.name == "speak")
        tool = FunctionTool(
            name="speak",
            description=registered.description,
            parameters=registered.parameters,
            handler=self.plugin.speak,
        )

        async def send(message):
            self.assertTrue(self.audio.exists())
            self.event.sent.append(message)

        self.event.send = send
        self.plugin.config["max_chars"] = 2
        async for _ in FunctionToolExecutor._execute_local(tool, context, text="你好呀"):
            pass
        self.assertEqual(len(self.event.sent), 2)
        self.assertIsInstance(self.event.sent[1].chain[0], Record)
        self.assertFalse(self.audio.exists())

    async def test_real_executor_returns_failure_to_model(self):
        context = SimpleNamespace(context=SimpleNamespace(event=self.event), tool_call_timeout=60)
        tool = FunctionTool(
            name="speak", description="test", parameters={}, handler=self.plugin.speak
        )
        self.context.get_using_tts_provider_async.return_value = None
        results = [
            result
            async for result in FunctionToolExecutor._execute_local(tool, context, text="你好")
        ]
        self.assertIn("文字回复", results[0].content[0].text)
        self.assertEqual(self.event.sent, [])

    async def test_permission_guarded_executor_sends_real_audio_before_cleanup(self):
        with wave.open(str(self.audio), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b"\0\0" * 2400)
        context = SimpleNamespace(context=SimpleNamespace(event=self.event), tool_call_timeout=60)
        manager = FunctionToolManager()
        manager.func_list.append(
            FunctionTool(name="speak", description="test", parameters={}, handler=self.plugin.speak)
        )
        tool = manager.get_full_tool_set().get_tool("speak")
        failures = []

        async def send(message):
            try:
                await AiocqhttpMessageEvent._parse_onebot_json(message)
                self.event.sent.append(message)
            except Exception as error:
                failures.append(type(error).__name__)
                raise

        self.event.send = send
        self.plugin.config["max_chars"] = 2
        with patch.object(manager, "_check_tool_permission", AsyncMock(return_value=None)):
            with patch("astrbot.core.astr_agent_tool_exec.logger.error"):
                async for _ in FunctionToolExecutor._execute_local(tool, context, text="你好呀"):
                    pass
        self.assertEqual(failures, [])
        self.assertEqual(len(self.event.sent), 2)
        self.assertIsInstance(self.event.sent[1].chain[0], Record)
        self.assertFalse(self.audio.exists())

    async def test_closing_generator_cleans_file(self):
        generator = self.plugin.speak(self.event, "你好")
        await anext(generator)
        await generator.aclose()
        self.assertFalse(self.audio.exists())

    async def test_disabled_tool_falls_back_without_provider_call(self):
        self.plugin.config["enabled"] = False
        self.assertIn("文字回复", (await self.collect())[0])
        self.context.get_using_tts_provider_async.assert_not_awaited()

    async def test_empty_text_falls_back(self):
        self.assertIn("文字回复", (await self.collect("  "))[0])
        self.provider.get_audio.assert_not_awaited()

    async def test_missing_provider_falls_back(self):
        self.context.get_using_tts_provider_async.return_value = None
        self.assertIn("未配置", (await self.collect())[0])
        self.provider.get_audio.assert_not_awaited()

    async def test_empty_path_falls_back(self):
        for path in (None, "", "  "):
            with self.subTest(path=path):
                self.provider.get_audio.return_value = path
                self.assertIn("文字回复", (await self.collect())[0])

    async def test_missing_file_falls_back(self):
        self.provider.get_audio.return_value = str(self.audio.parent / "missing.wav")
        self.assertIn("文字回复", (await self.collect())[0])

    async def test_synthesis_timeout_falls_back(self):
        async def slow(_):
            await asyncio.sleep(60)

        self.provider.get_audio.side_effect = slow
        with patch(f"{MODULE}.SYNTHESIS_TIMEOUT_SECONDS", 0.01):
            self.assertIn("超时", (await self.collect())[0])

    async def test_provider_errors_fall_back_without_exposing_details(self):
        for operation in (self.context.get_using_tts_provider_async, self.provider.get_audio):
            with self.subTest(operation=operation):
                operation.side_effect = RuntimeError("secret provider address")
                with self.assertLogs(self.plugin.logger, level="ERROR"):
                    values = await self.collect()
                self.assertIn("文字回复", values[0])
                self.assertNotIn("secret", values[0])
                operation.side_effect = None

    async def test_truncation_sends_notice_and_only_synthesizes_prefix(self):
        await self.collect("鸭" * 121)
        self.provider.get_audio.assert_awaited_once_with("鸭" * 120)
        self.assertIn("120", self.event.sent[0].chain[0].text)
        self.assertIsInstance(self.event.sent[1].chain[0], Record)

    async def test_custom_limit(self):
        self.plugin.config["max_chars"] = 2
        await self.collect("你好呀")
        self.provider.get_audio.assert_awaited_once_with("你好")

    async def test_cleanup_failure_does_not_fail_tool(self):
        with patch.object(Path, "unlink", side_effect=PermissionError):
            with self.assertLogs(self.plugin.logger, level="WARNING"):
                values = await self.collect()
        self.assertIn("语音已发送", values[0])

    async def test_send_failure_cleans_audio_and_returns_fallback(self):
        self.event.send = AsyncMock(side_effect=OSError("private audio path"))
        with self.assertLogs(self.plugin.logger, level="ERROR"):
            values = await self.collect()
        self.assertIn("语音发送失败", values[0])
        self.assertNotIn("private", values[0])
        self.assertFalse(self.audio.exists())

    async def test_cancellation_during_send_cleans_audio(self):
        self.event.send = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.collect()
        self.assertFalse(self.audio.exists())

    async def test_cancellation_is_not_swallowed(self):
        self.provider.get_audio.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.collect()
