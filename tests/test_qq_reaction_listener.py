import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.support import FakeEvent

# support isolates AstrBot's runtime before SDK imports.
# isort: split
from aiocqhttp import Event
from astrbot.api import FunctionTool, ToolSet
from astrbot.api.message_components import Reply
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context
from astrbot.core.agent.message import Message, TextPart, dump_messages_with_checkpoints
from astrbot.core.db.po import Conversation
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from plugins.astrbot_plugin_qq_reaction_listener.main import (
    CONTEXT_MARKER,
    QQReactionListenerPlugin,
)
from plugins.astrbot_plugin_qq_reaction_listener.memory import InteractionMemory
from plugins.astrbot_plugin_qq_reaction_listener.tools import POKE_TOOL, REACT_TOOL


class InteractionPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session_allowed = self.enterContext(
            patch(
                "plugins.astrbot_plugin_qq_reaction_listener.main.SessionPluginManager."
                "is_plugin_enabled_for_session",
                new_callable=AsyncMock,
                return_value=True,
            )
        )
        self.context = Mock()
        self.settings = {"provider_settings": {"enable": True}, "plugin_set": ["*"]}
        self.context.get_config.return_value = self.settings
        self.context.get_using_provider_async = AsyncMock(return_value=Mock())
        self.manager = self.context.conversation_manager = AsyncMock()
        self.manager.get_curr_conversation_id.return_value = "existing"
        self.conversation = Conversation(
            platform_id="qq", user_id="200", cid="existing", history="[]"
        )
        self.manager.get_conversation.return_value = self.conversation
        self.tool_manager = ToolSet()
        self.context.provider_manager.llm_tools = self.tool_manager
        self.context.add_llm_tools.side_effect = lambda *tools: Context.add_llm_tools(
            self.context, *tools
        )
        self.context.get_llm_tool_manager.return_value = self.tool_manager
        self.plugin = QQReactionListenerPlugin(self.context, {"enabled": True})
        self.now = 100.0
        self.plugin.memory = InteractionMemory(clock=lambda: self.now)
        self.plugin.reply_limits.clock = lambda: self.now
        self.plugin.actions.limits.clock = lambda: self.now
        self.addAsyncCleanup(self.plugin.terminate)

    def notice(self, *, poke=False, **changes):
        event = FakeEvent("", group="200")
        event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "group_msg_emoji_like",
            "self_id": 999,
            "group_id": 200,
            "user_id": 100,
            "time": 12345,
            "message_id": -123,
            "is_add": True,
            "likes": [{"emoji_id": "264", "count": 1}],
        }
        if poke:
            event.message_obj.raw_message.update(
                notice_type="notify", sub_type="poke", target_id=999
            )
            del event.message_obj.raw_message["message_id"]
        event.message_obj.raw_message.update(changes)
        event.bot.call_action.return_value = {
            "sender": {"user_id": 999},
            "group_id": 200,
            "message": [{"type": "text", "data": {"text": "原消息"}}],
        }
        return event

    def chat(self):
        event = FakeEvent("你怎么不理我", group="200")
        event.message_obj.raw_message = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 999,
            "group_id": 200,
            "user_id": 100,
            "message_id": 1,
        }
        return event

    async def collect(self, event):
        return [result async for result in self.plugin.on_notice(event)]

    async def test_real_adapter_poke_immediately_requests_current_conversation(self):
        event = self.notice(poke=True)
        event.message_obj = await AiocqhttpAdapter._convert_handle_notice_event(
            None, Event.from_payload(event.message_obj.raw_message)
        )
        results = await self.collect(event)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], ProviderRequest)
        self.assertIs(results[0].conversation, self.conversation)
        self.assertIn("文字当场回应", results[0].prompt)
        self.assertTrue(event.call_llm)  # Suppress the *default* second request.
        self.assertEqual(event.sent, [])
        event.bot.call_action.assert_not_awaited()
        req = results[0]
        req.func_tool = ToolSet(
            [
                FunctionTool(
                    name="other",
                    description="另一个工具",
                    parameters={"type": "object", "properties": {}},
                )
            ]
        )
        await self.plugin.on_llm_request(event, req)
        self.assertTrue(req.func_tool.empty())
        self.assertIn('"kind": "poke"', req.extra_user_content_parts[-1].text)
        await event.send(event.plain_result("怎么啦"))
        await self.plugin.after_message_sent(event)
        self.assertEqual(
            self.plugin.memory.context(self.plugin._scope(event))[0]["response"],
            {"action": "reply"},
        )

    async def test_native_process_stage_runs_poke_once_but_never_runs_reactions(self):
        stage = ProcessStage()
        stage.ctx = Mock(astrbot_config={"provider_settings": {"enable": True}})

        async def listeners(event):
            async for result in self.plugin.on_notice(event):
                yield result

        agent_calls = []

        async def agent(event):
            agent_calls.append(event.get_extra("provider_request"))
            yield

        stage.star_request_sub_stage = Mock(process=listeners)
        stage.agent_sub_stage = Mock(process=agent)
        for event in (self.notice(), self.notice(poke=True)):
            event.set_extra("activated_handlers", [object()])
            event.is_at_or_wake_command = True
            _ = [result async for result in stage.process(event)]
        self.assertEqual(len(agent_calls), 1)
        self.assertIsInstance(agent_calls[0], ProviderRequest)

    async def test_burst_and_replays_do_not_delay_or_duplicate_first_poke_reply(self):
        first = self.notice(poke=True)
        self.assertEqual(len(await self.collect(first)), 1)
        self.assertEqual(await self.collect(first), [])
        self.assertEqual(await self.collect(self.notice(poke=True, time=12346)), [])
        self.now += 3
        self.assertEqual(len(await self.collect(self.notice(poke=True, time=12348))), 1)
        self.assertEqual(self.manager.get_conversation.await_count, 2)

    async def test_poke_reply_drops_invalid_notice_quote_but_preserves_original_chain(self):
        event = self.notice(poke=True)
        event.message_obj.message_id = "notice-uuid"
        await self.collect(event)
        result = event.plain_result("怎么啦")
        result.chain.insert(0, Reply(id="notice-uuid"))
        await event.send(result)
        self.assertIsInstance(result.chain[0], Reply)
        self.assertFalse(any(isinstance(part, Reply) for part in event.sent[0].chain))

    async def test_disabling_feature_before_native_request_cancels_pending_reply(self):
        event = self.notice(poke=True)
        request = (await self.collect(event))[0]
        self.plugin.config["poke_reply_enabled"] = False
        await self.plugin.on_llm_request(event, request)
        self.assertTrue(event.is_stopped())

    async def test_unload_after_generation_started_still_prevents_late_poke_send(self):
        event = self.notice(poke=True)
        await self.collect(event)
        await self.plugin.terminate()
        await event.send(event.plain_result("不应送达"))
        self.assertEqual(event.sent, [])

    async def test_normal_chat_does_not_blackout_next_poke_reply(self):
        await self.plugin.on_llm_request(self.chat(), ProviderRequest(prompt="正常聊天"))
        self.assertEqual(len(await self.collect(self.notice(poke=True))), 1)

    async def test_disabled_model_session_or_reply_switch_does_not_trigger_model(self):
        for mode in ("master", "context", "reply", "provider", "session", "no_provider"):
            with self.subTest(mode=mode):
                self.plugin.config = {"enabled": True}
                self.settings["provider_settings"]["enable"] = mode != "provider"
                self.session_allowed.return_value = mode != "session"
                self.context.get_using_provider_async.return_value = (
                    None if mode == "no_provider" else Mock()
                )
                if mode in ("master", "context", "reply"):
                    key = {
                        "master": "enabled",
                        "context": "context_enabled",
                        "reply": "poke_reply_enabled",
                    }[mode]
                    self.plugin.config[key] = False
                self.plugin.memory.clear()
                self.plugin.reply_limits.clear()
                self.assertEqual(await self.collect(self.notice(poke=True)), [])
        self.manager.get_conversation.assert_not_awaited()

    async def test_context_is_small_named_temporary_and_preserves_other_request_fields(self):
        self.assertEqual(await self.collect(self.notice()), [])
        req = ProviderRequest(
            prompt="原问题", system_prompt="人设", contexts=[{"role": "user", "content": "历史"}]
        )
        req.extra_user_content_parts.append(TextPart(text="其他插件"))
        await self.plugin.on_llm_request(self.chat(), req)
        part = req.extra_user_content_parts[-1]
        self.assertIn('"name": "捂脸"', part.text)
        self.assertNotIn("537", part.text)
        self.assertNotIn("放大招", part.text)  # No complete emoji table in context.
        self.assertLess(len(part.text), 1000)
        self.assertEqual((req.prompt, req.system_prompt), ("原问题", "人设"))
        self.assertEqual(req.contexts, [{"role": "user", "content": "历史"}])
        message = Message.model_validate(await req.assemble_context())
        saved = str(dump_messages_with_checkpoints([message]))
        self.assertNotIn(CONTEXT_MARKER, saved)
        self.assertIn("其他插件", saved)
        self.assertIn("原问题", saved)
        await self.plugin.on_llm_request(self.chat(), req)
        self.assertEqual(len(req.extra_user_content_parts), 2)
        self.now += 600
        await self.plugin.on_llm_request(self.chat(), req)
        self.assertEqual(len(req.extra_user_content_parts), 1)

    async def test_unknown_emoji_keeps_id_without_guessing_and_removal_is_silent(self):
        event = self.notice(likes=[{"emoji_id": "999999", "count": 1}])
        await self.collect(event)
        req = ProviderRequest(prompt="问题")
        await self.plugin.on_llm_request(self.chat(), req)
        self.assertIn('"name": null', req.extra_user_content_parts[-1].text)
        event.message_obj.raw_message["is_add"] = False
        self.assertEqual(await self.collect(event), [])
        await self.plugin.on_llm_request(self.chat(), req)
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_invalid_sources_and_unrelated_interactions_are_ignored(self):
        for changes in (
            {"user_id": 999},
            {"user_id": 0},
            {"group_id": 201},
            {"self_id": 998},
            {"message_id": "--123"},
            {"is_add": "true"},
            {"likes": None},
            {"notice_type": "notify", "sub_type": "poke", "target_id": 101},
        ):
            event = self.notice(**changes)
            if "user_id" in changes:
                event.message_obj.sender.user_id = str(changes["user_id"])
            self.assertEqual(await self.collect(event), [])
            event.bot.call_action.assert_not_awaited()

    async def test_only_reactions_to_this_bots_group_messages_are_remembered(self):
        for change in (
            {"sender": {"user_id": 101}},
            {"group_id": 201},
            {"message_type": "private"},
        ):
            self.plugin.memory.clear()
            event = self.notice()
            event.bot.call_action.return_value.update(change)
            await self.collect(event)
            await self.collect(event)
            event.bot.call_action.assert_awaited_once()
            self.assertEqual(self.plugin.memory.context(self.plugin._scope(event)), [])

    async def test_query_failure_is_silent_and_negative_cached(self):
        event = self.notice()
        event.bot.call_action.side_effect = RuntimeError("secret")
        with self.assertLogs(self.plugin.logger, level="WARNING") as logs:
            self.assertEqual(await self.collect(event), [])
        self.assertNotIn("secret", str(logs.output))
        await self.collect(event)
        event.bot.call_action.assert_awaited_once()

    async def test_group_bot_instance_and_split_session_contexts_do_not_mix(self):
        await self.collect(self.notice())
        for field in ("group", "bot", "instance", "session", "platform"):
            event = self.chat()
            if field == "group":
                event.message_obj.group_id = "201"
                event.session_id = "201"
            elif field == "bot":
                event.message_obj.self_id = "998"
            elif field == "instance":
                event.platform_meta.id = "other"
                event.session.platform_id = "other"
            elif field == "session":
                event.session_id = "200_101"
            else:
                event.platform_meta.name = "other"
            req = ProviderRequest(prompt="问题")
            await self.plugin.on_llm_request(event, req)
            self.assertEqual(req.extra_user_content_parts, [], field)

    async def test_tools_register_only_when_enabled_and_cleanup_uses_identity(self):
        await self.plugin.initialize()
        self.assertTrue(self.tool_manager.empty())
        self.plugin.config.update(tools_enabled=True, tool_groups=["200"])
        await self.plugin.initialize()
        self.assertEqual(set(self.tool_manager.names()), {POKE_TOOL, REACT_TOOL})
        self.assertTrue(
            all(tool.handler_module_path.endswith(".main") for tool in self.tool_manager.tools)
        )
        own = list(self.tool_manager.tools)
        await self.plugin.initialize()
        self.assertEqual(len(self.tool_manager.tools), 2)
        replacement = Mock()
        replacement.name = POKE_TOOL
        self.tool_manager.tools.append(replacement)
        await self.plugin.terminate()
        self.assertEqual(self.tool_manager.tools, [replacement])
        self.assertTrue(all(not tool.active for tool in own))

    async def test_disabled_tools_hidden_per_request_without_mutating_shared_set(self):
        self.plugin.config.update(tools_enabled=True, tool_groups=["200"])
        await self.plugin.initialize()
        shared = self.tool_manager
        req = ProviderRequest(func_tool=shared)
        self.plugin.config["tools_enabled"] = False
        await self.plugin.on_llm_request(self.chat(), req)
        self.assertTrue(req.func_tool.empty())
        self.assertEqual(len(shared.tools), 2)
        self.plugin.config.update(tools_enabled=True, context_enabled=False)
        req = ProviderRequest(func_tool=shared)
        event = self.chat()
        await self.plugin.on_llm_request(event, req)
        self.assertIs(req.func_tool, shared)
        self.assertEqual(req.extra_user_content_parts, [])
        event.bot.call_action.return_value = {"result": 0}
        self.assertTrue((await self.plugin.poke(event)).sent)

    async def test_tool_runtime_enforces_group_profile_and_session_permissions(self):
        self.plugin.config.update(tools_enabled=True, tool_groups=["200"])
        for mode in ("group", "profile", "session", "master", "runner"):
            with self.subTest(mode=mode):
                self.plugin.config.update(enabled=True, tool_groups=["200"])
                self.settings.update(plugin_set=["*"], agent_runner={"runner_type": "local"})
                self.session_allowed.return_value = True
                if mode == "group":
                    self.plugin.config["tool_groups"] = []
                elif mode == "profile":
                    self.settings["plugin_set"] = ["another_plugin"]
                elif mode == "session":
                    self.session_allowed.return_value = False
                elif mode == "master":
                    self.plugin.config["enabled"] = False
                else:
                    self.settings["agent_runner"]["runner_type"] = "other"
                event = self.chat()
                self.assertFalse((await self.plugin.poke(event)).sent)
                event.bot.call_action.assert_not_awaited()

    async def test_default_off_and_unload_cannot_send_or_keep_context(self):
        self.plugin.config = {}
        self.assertEqual(await self.collect(self.notice(poke=True)), [])
        self.assertEqual(await self.collect(self.notice()), [])
        self.plugin.config["enabled"] = True
        await self.collect(self.notice())
        await self.plugin.terminate()
        req = ProviderRequest(prompt="问题")
        await self.plugin.on_llm_request(self.chat(), req)
        self.assertEqual(req.extra_user_content_parts, [])
        self.assertEqual(await self.collect(self.notice(poke=True)), [])

    def test_notice_filter_does_not_wake_ordinary_messages(self):
        handlers = [
            h
            for h in star_handlers_registry.get_handlers_by_event_type(
                EventType.AdapterMessageEvent
            )
            if h.handler_module_path == type(self.plugin).__module__
        ]
        self.assertEqual(len(handlers), 1)
        self.assertFalse(all(f.filter(self.chat(), {}) for f in handlers[0].event_filters))
