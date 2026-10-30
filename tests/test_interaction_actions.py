import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from support import FakeEvent

# isort: split
from astrbot.api.message_components import At, Reply

from plugins.astrbot_plugin_qq_reaction_listener.actions import AttemptLimits, InteractionActions
from plugins.astrbot_plugin_qq_reaction_listener.catalog import EmojiCatalog
from plugins.astrbot_plugin_qq_reaction_listener.tools import build_tools


class InteractionActionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.allowed = AsyncMock(return_value=True)
        self.catalog = EmojiCatalog()
        self.limits = AttemptLimits(clock=lambda: self.now)
        self.actions = InteractionActions(self.catalog, self.allowed, self.limits)

    def event(self, message_id=1, group="200"):
        event = FakeEvent("聊天", group=group)
        event.message_obj.message_id = str(message_id)
        event.message_obj.raw_message = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 999,
            "group_id": int(group),
            "user_id": 100,
            "message_id": message_id,
        }
        event.bot.call_action.return_value = {"result": 0}
        return event

    async def test_bound_targets_current_quoted_and_mentioned(self):
        for target in ("sender", "mentioned", "quoted_sender"):
            with self.subTest(target=target):
                self.limits.clear()
                event = self.event()
                if target == "mentioned":
                    event.message_obj.message += [At(qq="999"), At(qq="101")]
                elif target == "quoted_sender":
                    event.message_obj.message.append(Reply(id="-123"))
                    event.bot.call_action.side_effect = lambda action, **_: (
                        {"group_id": 200, "sender": {"user_id": 101}}
                        if action == "get_msg"
                        else {"result": 0}
                    )
                result = await self.actions.run(event, "poke", target=target)
                self.assertTrue(result.sent)
                self.assertEqual(event.bot.call_action.await_args.args, ("group_poke",))
                self.assertEqual(
                    event.bot.call_action.await_args.kwargs,
                    {
                        "self_id": "999",
                        "group_id": 200,
                        "user_id": 100 if target == "sender" else 101,
                    },
                )

    async def test_emoji_names_ids_and_quoted_group_verification(self):
        for target, emoji in (("current", "捂脸"), ("quoted", "0")):
            self.limits.clear()
            event = self.event()
            if target == "quoted":
                event.message_obj.message.append(Reply(id="-123"))
                event.bot.call_action.side_effect = lambda action, **_: (
                    {"group_id": 200} if action == "get_msg" else {"result": 0}
                )
            result = await self.actions.run(event, "react", target=target, emoji=emoji)
            self.assertTrue(result.sent)
            self.assertEqual(
                event.bot.call_action.await_args.kwargs,
                {
                    "self_id": "999",
                    "message_id": 1 if target == "current" else -123,
                    "emoji_id": "264" if emoji == "捂脸" else "0",
                    "set": True,
                },
            )

    async def test_unknown_or_ambiguous_name_returns_only_small_candidates_without_sending(self):
        for emoji in ("咖啡", "未知表情", "999999", "好"):
            event = self.event()
            result = await self.actions.run(event, "react", target="current", emoji=emoji)
            self.assertFalse(result.sent)
            self.assertLess(len(result.message), 250)
            event.bot.call_action.assert_not_awaited()
        event = self.event()
        result = await self.actions.run(event, "react", target="current", emoji="60")
        self.assertTrue(result.sent)  # Failed name lookup did not consume send quota.

    async def test_unbound_multiple_or_cross_group_targets_never_send(self):
        for mode in ("qq", "multiple", "all", "cross_group", "unknown_author"):
            self.limits.clear()
            event = self.event()
            target = "mentioned"
            if mode == "qq":
                target = "123456"
            elif mode == "multiple":
                event.message_obj.message += [At(qq="101"), At(qq="102")]
            elif mode == "all":
                event.message_obj.message.append(At(qq="all"))
            else:
                target = "quoted_sender"
                event.message_obj.message.append(Reply(id="-123"))
                event.bot.call_action.return_value = {
                    "group_id": 201 if mode == "cross_group" else 200,
                    "sender": {},
                }
            result = await self.actions.run(event, "poke", target=target)
            self.assertFalse(result.sent)
            self.assertFalse(
                any(call.args == ("group_poke",) for call in event.bot.call_action.await_args_list)
            )

    async def test_both_tools_share_turn_dedup_cooldown_and_failure_budget(self):
        event = self.event()
        event.bot.call_action.side_effect = RuntimeError("secret token")
        failed = await self.actions.run(event, "poke", target="sender")
        self.assertFalse(failed.sent)
        self.assertNotIn("secret", failed.message)
        self.now += 3
        duplicate = await self.actions.run(event, "react", target="current", emoji="264")
        self.assertFalse(duplicate.sent)
        event.bot.call_action.assert_awaited_once()
        next_event = self.event(2)
        self.assertTrue((await self.actions.run(next_event, "poke", target="sender")).sent)
        throttled = self.event(3)
        self.assertFalse((await self.actions.run(throttled, "poke", target="sender")).sent)
        throttled.bot.call_action.assert_not_awaited()

    async def test_concurrent_tool_calls_reserve_before_sending(self):
        started, release = asyncio.Event(), asyncio.Event()
        event = self.event()

        async def send(*_, **__):
            started.set()
            await release.wait()
            return {"result": 0}

        event.bot.call_action.side_effect = send
        first = asyncio.create_task(self.actions.run(event, "poke", target="sender"))
        await asyncio.wait_for(started.wait(), 1)
        second = await self.actions.run(event, "react", target="current", emoji="264")
        release.set()
        self.assertTrue((await first).sent)
        self.assertFalse(second.sent)
        event.bot.call_action.assert_awaited_once()

    async def test_switch_change_or_event_mutation_during_quote_lookup_cannot_redirect(self):
        for mutate in (False, True):
            self.allowed.return_value = True
            event = self.event()
            event.message_obj.message.append(Reply(id="-123"))

            async def lookup(*_, **__):
                if mutate:
                    event.message_obj.group_id = "201"
                else:
                    self.allowed.return_value = False
                return {"group_id": 200, "sender": {"user_id": 101}}

            event.bot.call_action.side_effect = lookup
            self.assertFalse((await self.actions.run(event, "poke", target="quoted_sender")).sent)
            event.bot.call_action.assert_awaited_once_with(
                "get_msg", message_id=-123, self_id="999"
            )

    async def test_notice_private_self_and_invalid_message_id_cannot_use_tools(self):
        for mode in ("notice", "private", "self", "uuid", "other"):
            event = self.event()
            if mode == "notice":
                event.message_obj.raw_message["post_type"] = "notice"
            elif mode == "private":
                event.message_obj.group_id = ""
            elif mode == "self":
                event.message_obj.sender.user_id = "999"
                event.message_obj.raw_message["user_id"] = 999
            elif mode == "uuid":
                event.message_obj.message_id = "not-a-real-message-id"
            else:
                event.platform_meta.name = "other"
            self.assertFalse((await self.actions.run(event, "poke", target="sender")).sent)
            event.bot.call_action.assert_not_awaited()

    async def test_sdk_tools_finish_success_without_second_reply_but_can_continue_real_answer(self):
        async def poke(event, target="sender"):
            return await self.actions.run(event, "poke", target=target)

        async def react(event, emoji, target="current"):
            return await self.actions.run(event, "react", target=target, emoji=emoji)

        tools = build_tools(poke, react)
        event = self.event()
        wrapper = SimpleNamespace(context=SimpleNamespace(event=event))
        self.assertIsNone(await tools[0].call(wrapper))  # SDK's end-turn sentinel.
        duplicate = await tools[1].call(wrapper, emoji="264")
        self.assertIsInstance(duplicate, str)
        self.now += 3
        wrapper.context.event = self.event(2)
        result = await tools[1].call(wrapper, emoji="264", finish_turn=False)
        self.assertIn("不要再用文字播报", result)
        schema_text = json.dumps([tool.parameters for tool in tools], ensure_ascii=False)
        self.assertLess(len(schema_text), 1600)
        self.assertNotIn("放大招", schema_text)  # No catalog in tools either.
        for bad in ({"user_id": "123"}, {"target": "123"}, {"times": 3}, {"finish_turn": "true"}):
            wrapper.context.event = self.event(3)
            self.assertIsInstance(await tools[0].call(wrapper, **bad), str)
            wrapper.context.event.bot.call_action.assert_not_awaited()

        tools[0].active = False
        wrapper.context.event = self.event(4)
        self.assertIsInstance(await tools[0].call(wrapper), str)
        wrapper.context.event.bot.call_action.assert_not_awaited()

    def test_limits_count_attempts_bound_groups_and_recover_without_resetting_turn_dedup(self):
        limits = AttemptLimits(cooldown=0, per_minute=2, clock=lambda: self.now)
        group = ("qq", "999", "200")
        self.assertTrue(limits.reserve(group, (*group, 1)))
        self.assertTrue(limits.reserve(group, (*group, 2)))
        self.assertFalse(limits.reserve(group, (*group, 3)))
        self.now += 60
        self.assertFalse(limits.reserve(group, (*group, 1)))
        self.assertTrue(limits.reserve(group, (*group, 3)))
        limits.clear()
        self.assertEqual(sum(limits.reserve(("qq", "999", str(i)), (i,)) for i in range(100)), 60)

    def test_catalog_snapshot_and_small_lookup_preserve_actual_names_not_emotions(self):
        self.assertEqual(len(self.catalog.names), 537)
        for emoji_id, name in (
            ("0", "惊讶"),
            ("264", "捂脸"),
            ("448", "放大招"),
            ("128522", "嘿嘿"),
        ):
            self.assertEqual(self.catalog.describe(emoji_id), name)
        self.assertIsNone(self.catalog.describe("999999"))
        self.assertEqual(self.catalog.resolve("捂脸"), "264")
        with self.assertRaisesRegex(ValueError, "60=咖啡"):
            self.catalog.resolve("咖啡")
