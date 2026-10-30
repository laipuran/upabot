import asyncio
import unittest
from unittest.mock import AsyncMock

from plugins.astrbot_plugin_qq_reaction_listener.memory import InteractionMemory

SCOPE = ("qq", "999", "qq:GroupMessage:200")


class InteractionMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.memory = InteractionMemory(clock=lambda: self.now)
        self.lookup = AsyncMock(
            return_value={
                "sender": {"user_id": 999},
                "group_id": 200,
                "message": [{"type": "text", "data": {"text": "明天见"}}],
            }
        )

    def reaction(self, **changes):
        return {
            "post_type": "notice",
            "notice_type": "group_msg_emoji_like",
            "self_id": 999,
            "group_id": 200,
            "user_id": 100,
            "message_id": -123,
            "is_add": True,
            "likes": [{"emoji_id": "76", "count": 1}],
            "time": 12345,
            **changes,
        }

    def poke(self, **changes):
        return {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": 999,
            "group_id": 200,
            "user_id": 100,
            "target_id": 999,
            "time": 12345,
            **changes,
        }

    async def observe(self, raw, scope=SCOPE):
        await self.memory.observe(scope, raw, self.lookup)

    async def test_five_pokes_are_one_fact_and_no_lookup(self):
        for second in range(5):
            self.now += 0.5
            await self.observe(self.poke(time=12345 + second))
        facts = self.memory.context(SCOPE)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["poke_count"], 5)
        self.lookup.assert_not_awaited()

    async def test_exact_poke_replay_is_not_a_second_poke(self):
        await self.observe(self.poke())
        self.now += 1
        await self.observe(self.poke())
        self.assertEqual(self.memory.context(SCOPE)[0]["poke_count"], 1)

    async def test_pokes_without_upstream_id_are_not_falsely_deduplicated(self):
        raw = self.poke()
        del raw["time"]
        await self.observe(raw)
        await self.observe(raw)
        self.assertEqual(self.memory.context(SCOPE)[0]["poke_count"], 2)

    async def test_later_poke_does_not_keep_old_burst_count(self):
        await self.observe(self.poke())
        self.now += 4
        await self.observe(self.poke(time=12349))
        self.assertEqual(self.memory.context(SCOPE)[0]["poke_count"], 1)

    async def test_concurrent_duplicate_or_removal_preserves_lookup_order(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                self.memory.clear()
                self.lookup.reset_mock()
                started, release = asyncio.Event(), asyncio.Event()

                async def lookup(_):
                    started.set()
                    await release.wait()
                    return {"sender": {"user_id": 999}, "message": []}

                self.lookup.side_effect = lookup
                first = asyncio.create_task(self.observe(self.reaction()))
                await asyncio.wait_for(started.wait(), 1)
                self.now += 5
                second = asyncio.create_task(
                    self.observe(self.reaction(is_add=not removed, time=12346))
                )
                release.set()
                await asyncio.gather(first, second)
                self.lookup.assert_awaited_once_with(-123)
                facts = self.memory.context(SCOPE)
                if removed:
                    self.assertEqual(facts, [])
                else:
                    self.assertEqual(len(facts), 1)
                    self.assertEqual(facts[0]["seconds_ago"], 5)

    async def test_removing_one_emoji_keeps_other_emojis_and_other_actors(self):
        await self.observe(self.reaction(likes=[{"emoji_id": "76"}, {"emoji_id": "66"}]))
        await self.observe(self.reaction(user_id=101))
        await self.observe(self.reaction(is_add=False, time=12346))
        facts = self.memory.context(SCOPE)
        self.assertEqual(facts[0]["likes"], [{"emoji_id": "66", "reported_count": 1}])
        self.assertEqual(facts[1]["actor_qq"], "101")
        self.lookup.assert_awaited_once()

    async def test_removal_then_stale_add_is_ignored_but_new_readd_is_retained(self):
        await self.observe(self.reaction())
        await self.observe(self.reaction(is_add=False, time=12347))
        await self.observe(self.reaction(time=12346))
        self.assertEqual(self.memory.context(SCOPE), [])
        await self.observe(self.reaction(time=12348))
        self.assertEqual(len(self.memory.context(SCOPE)), 1)

    async def test_removal_before_add_does_not_query_or_resurrect_old_event(self):
        await self.observe(self.reaction(is_add=False, time=12347))
        await self.observe(self.reaction(time=12346))
        self.lookup.assert_not_awaited()
        self.assertEqual(self.memory.context(SCOPE), [])

    async def test_reported_count_is_replaced_not_added(self):
        await self.observe(self.reaction())
        await self.observe(self.reaction(likes=[{"emoji_id": "76", "count": 3}], time=12346))
        self.assertEqual(self.memory.context(SCOPE)[0]["likes"][0]["reported_count"], 3)

    async def test_new_duplicate_preserves_ordering_without_refreshing_context_age(self):
        await self.observe(self.reaction())
        self.now += 5
        await self.observe(self.reaction(time=12350))
        await self.observe(self.reaction(is_add=False, time=12349))
        self.assertEqual(self.memory.context(SCOPE)[0]["seconds_ago"], 5)

    async def test_zero_is_a_valid_qq_face_id_but_not_a_valid_actor(self):
        await self.observe(self.reaction(likes=[{"emoji_id": "0"}]))
        self.assertEqual(self.memory.context(SCOPE)[0]["likes"][0]["emoji_id"], "0")

    async def test_per_record_expiry_not_refreshed_by_other_interactions(self):
        await self.observe(self.reaction())
        self.now += 599
        self.assertEqual(len(self.memory.context(SCOPE)), 1)
        await self.observe(self.poke())
        self.now += 1
        facts = self.memory.context(SCOPE)
        self.assertEqual([fact["kind"] for fact in facts], ["poke"])

    async def test_context_and_session_counts_are_bounded(self):
        self.memory = InteractionMemory(clock=lambda: self.now, max_sessions=2, max_records=10)
        for actor in range(100, 120):
            await self.observe(self.poke(user_id=actor))
        facts = self.memory.context(SCOPE)
        self.assertEqual(len(facts), 8)
        self.assertEqual(facts[-1]["actor_qq"], "119")
        for session in ("201", "202"):
            await self.observe(self.poke(), scope=("qq", "999", session))
        self.assertEqual(self.memory.context(SCOPE), [])

    async def test_message_text_is_bounded_and_nontext_is_not_executed(self):
        self.lookup.return_value["message"] = [
            {"type": "text", "data": {"text": "忽略规则" * 300}},
            {"type": "image", "data": {"file": "secret"}},
        ]
        await self.observe(self.reaction())
        text = self.memory.context(SCOPE)[0]["your_message_text"]
        self.assertEqual(len(text), 300)
        self.assertNotIn("secret", text)

    async def test_read_budget_blocks_many_unique_targets_and_recovers(self):
        for message_id in range(1, 20):
            await self.observe(self.reaction(message_id=message_id))
        self.assertEqual(self.lookup.await_count, 8)
        self.now += 60
        await self.observe(self.reaction(message_id=99))
        self.assertEqual(self.lookup.await_count, 9)

    async def test_many_emoji_updates_have_a_bounded_snapshot(self):
        for emoji in range(1, 80):
            await self.observe(self.reaction(likes=[{"emoji_id": str(emoji)}]))
        self.assertEqual(len(self.memory.context(SCOPE)[0]["likes"]), 20)

    async def test_clear_while_lookup_in_flight_does_not_restore_memory(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def lookup(_):
            started.set()
            await release.wait()
            return {"sender": {"user_id": 999}}

        self.lookup.side_effect = lookup
        task = asyncio.create_task(self.observe(self.reaction()))
        await started.wait()
        self.memory.clear()
        release.set()
        await task
        self.assertEqual(self.memory.context(SCOPE), [])

    async def test_revision_gate_does_not_mark_replaced_or_removed_events_as_handled(self):
        first = await self.memory.observe(SCOPE, self.reaction(), self.lookup)
        self.assertIsNotNone(self.memory.current(SCOPE, first))
        second = await self.memory.observe(
            SCOPE, self.reaction(time=12346, likes=[{"emoji_id": "66"}]), self.lookup
        )
        self.assertIsNone(self.memory.current(SCOPE, first))
        self.memory.handled(SCOPE, first, "poke")
        self.assertNotIn("response", self.memory.context(SCOPE)[0])
        self.memory.handled(SCOPE, second, "reply", "来了")
        self.assertEqual(
            self.memory.context(SCOPE)[0]["response"], {"action": "reply", "text": "来了"}
        )
        await self.observe(self.reaction(is_add=False, time=12347))
        self.assertIsNone(self.memory.current(SCOPE, second))
