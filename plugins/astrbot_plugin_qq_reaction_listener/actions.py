"""The only outbound interaction path: resolve bound targets and reserve once."""

import asyncio
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Reply

from .catalog import EmojiCatalog
from .memory import numeric_id


@dataclass(frozen=True)
class ActionResult:
    sent: bool
    message: str


class AttemptLimits:
    """Atomic, bounded attempt reservations, including uncertain/failed delivery."""

    def __init__(self, *, cooldown=3, per_minute=12, clock=time.monotonic):
        self.cooldown, self.per_minute, self.clock = cooldown, per_minute, clock
        self._groups: OrderedDict[tuple, deque] = OrderedDict()
        self._turns: OrderedDict[tuple, float] = OrderedDict()
        self._global: deque = deque()

    def clear(self):
        self._groups.clear()
        self._turns.clear()
        self._global.clear()

    def reserve(self, group: tuple, turn: tuple) -> bool:
        now = self.clock()
        for key, at in list(self._turns.items()):
            if now - at >= 600:
                del self._turns[key]
        while self._global and now - self._global[0] >= 60:
            self._global.popleft()
        recent = self._groups.setdefault(group, deque())
        while recent and now - recent[0] >= 60:
            recent.popleft()
        self._groups.move_to_end(group)
        while len(self._groups) > 128:
            self._groups.popitem(last=False)
        if (
            turn in self._turns
            or (recent and now - recent[-1] < self.cooldown)
            or len(recent) >= self.per_minute
            or len(self._global) >= 60
        ):
            return False
        self._turns[turn] = now
        while len(self._turns) > 2048:
            self._turns.popitem(last=False)
        recent.append(now)
        self._global.append(now)
        return True


class InteractionActions:
    def __init__(
        self,
        catalog: EmojiCatalog,
        allowed: Callable[[AstrMessageEvent], Awaitable[bool]],
        limits: AttemptLimits,
    ):
        self.catalog, self.allowed, self.limits = catalog, allowed, limits

    @staticmethod
    def source(event: AstrMessageEvent) -> tuple:
        raw = event.message_obj.raw_message
        if (
            event.get_platform_name() != "aiocqhttp"
            or not isinstance(raw, dict)
            or raw.get("post_type") != "message"
            or raw.get("message_type") != "group"
            or str(raw.get("group_id")) != event.get_group_id()
            or str(raw.get("self_id")) != event.get_self_id()
            or str(raw.get("user_id")) != event.get_sender_id()
            or event.get_sender_id() == event.get_self_id()
        ):
            raise ValueError("只能对当前 QQ 群聊天使用互动工具。")
        ids = [
            numeric_id(event.get_self_id()),
            numeric_id(event.get_group_id()),
            numeric_id(event.get_sender_id()),
            numeric_id(raw.get("message_id"), signed=True),
        ]
        if not all(ids) or ids[-1] != str(event.message_obj.message_id):
            raise ValueError("无法确认当前消息来源，未执行操作。")
        return event.get_platform_id(), *ids

    async def run(
        self, event: AstrMessageEvent, action: str, *, target: str, emoji: str = ""
    ) -> ActionResult:
        try:
            source = self.source(event)
            bot = event.bot
            if not await self.allowed(event):
                return ActionResult(False, "当前会话未开启互动工具，未执行操作。")
            _, self_id, group_id, actor, message_id = source

            async def quoted():
                replies = [part for part in event.get_messages() if isinstance(part, Reply)]
                if len(replies) != 1 or not numeric_id(replies[0].id, signed=True):
                    raise ValueError("请先引用一条当前群消息。")
                reply_id = numeric_id(replies[0].id, signed=True)
                async with asyncio.timeout(10):
                    result = await bot.call_action(
                        "get_msg", message_id=int(reply_id), self_id=self_id
                    )
                if (
                    not isinstance(result, dict)
                    or str(result.get("group_id")) != group_id
                    or result.get("message_type", "group") != "group"
                ):
                    raise ValueError("无法确认引用消息属于当前群，未执行操作。")
                return reply_id, result

            if action == "poke":
                if target == "sender":
                    user_id = actor
                elif target == "quoted_sender":
                    _, message = await quoted()
                    sender = message.get("sender")
                    user_id = numeric_id(sender.get("user_id")) if isinstance(sender, dict) else ""
                elif target == "mentioned":
                    users = {
                        numeric_id(part.qq)
                        for part in event.get_messages()
                        if isinstance(part, At) and str(part.qq) not in (self_id, "all")
                    }
                    if len(users) != 1 or "" in users:
                        raise ValueError("需要在当前消息中明确 @ 唯一一个人，不能猜测目标。")
                    user_id = users.pop()
                else:
                    raise ValueError("戳的目标只能是 sender、quoted_sender 或 mentioned。")
                if not user_id or user_id == self_id:
                    raise ValueError("无法确定有效的非机器人目标，未执行操作。")
                action_name = "group_poke"
                params = {"group_id": int(group_id), "user_id": int(user_id)}
                success = "已戳目标一次，不需要再重复调用。"
            elif action == "react":
                emoji_id = self.catalog.resolve(emoji)
                if target == "quoted":
                    message_id, _ = await quoted()
                elif target != "current":
                    raise ValueError("贴表情的目标只能是 current 或 quoted。")
                action_name = "set_msg_emoji_like"
                params = {"message_id": int(message_id), "emoji_id": emoji_id, "set": True}
                success = f"已贴表情 {emoji_id}（{self.catalog.describe(emoji_id)}），勿重复调用。"
            else:
                raise ValueError("不支持该互动动作。")

            # An async quote/permission lookup must not redirect an accepted event.
            if (
                self.source(event) != source
                or event.bot is not bot
                or not await self.allowed(event)
            ):
                return ActionResult(False, "消息来源或开关已变化，未执行操作。")
            # Recheck after the permission await, then reserve before network I/O.
            if self.source(event) != source or event.bot is not bot:
                return ActionResult(False, "消息来源已变化，未执行操作。")
            group = source[:3]
            if not self.limits.reserve(group, (*group, source[-1])):
                return ActionResult(
                    False, "本条消息已使用互动动作，或已达到发送限额；请勿换工具重试。"
                )
            async with asyncio.timeout(10):
                result = await bot.call_action(action_name, self_id=self_id, **params)
            if isinstance(result, dict):
                code = result.get("result")
                if (
                    result.get("errMsg")
                    or code is False
                    or (code is not True and code not in (None, 0))
                ):
                    return ActionResult(False, "平台拒绝了互动，未确认成功；不要自动换编号或重试。")
            return ActionResult(True, success + "动作已经真实发生，不要再用文字播报或描述该动作。")
        except ValueError as error:
            return ActionResult(False, str(error))
        except Exception:
            return ActionResult(False, "互动查询或发送失败，投递状态可能未知；不要自动重试。")
