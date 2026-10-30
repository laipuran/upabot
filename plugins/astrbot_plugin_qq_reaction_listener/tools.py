from collections.abc import Awaitable, Callable

import jsonschema
from astrbot.api import FunctionTool
from pydantic.dataclasses import dataclass

from .actions import ActionResult

POKE_TOOL = "qq_interaction_poke"
REACT_TOOL = "qq_interaction_react"
TOOL_NAMES = {POKE_TOOL, REACT_TOOL}


@dataclass
class InteractionTool(FunctionTool):
    impl: Callable[..., Awaitable[ActionResult]] | None = None

    async def call(self, context, **kwargs):
        if not self.active:
            return "该互动工具已停用，未执行操作。"
        try:
            jsonschema.validate(kwargs, self.parameters)
        except jsonschema.ValidationError:
            return "工具参数无效；不能指定任意 QQ、消息 ID 或次数。"
        finish_turn = kwargs.pop("finish_turn", True)
        result = await self.impl(context.context.event, **kwargs)
        if result.sent and finish_turn:
            # AstrBot ends this agent turn without a second model response.
            # Unlike stop_event(), this does not abort sibling tool calls.
            return None
        return result.message


def build_tools(poke, react) -> list[InteractionTool]:
    finish = {
        "type": "boolean",
        "description": "成功后结束本轮，默认true。还须回答问题时设false，不播报动作。",
    }
    return [
        InteractionTool(
            name=POKE_TOOL,
            description="在当前 QQ 群戳一个人一次。先调用，不要播报动作；默认成功后结束本轮。",
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "finish_turn": finish,
                    "target": {
                        "type": "string",
                        "enum": ["sender", "quoted_sender", "mentioned"],
                        "description": "sender=发言人，quoted_sender=引用作者，mentioned=唯一@者。",
                    },
                },
            },
            impl=poke,
        ),
        InteractionTool(
            name=REACT_TOOL,
            description="给当前或引用消息贴 QQ 表情回应。先调用，不播报动作；默认成功后结束本轮。",
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "finish_turn": finish,
                    "target": {
                        "type": "string",
                        "enum": ["current", "quoted"],
                        "description": "current=当前消息（默认）；quoted=用户引用的消息。",
                    },
                    "emoji": {
                        "type": "string",
                        "maxLength": 40,
                        "description": "表情编号或名称，如264或捂脸。重名返回少量候选，不猜编号。",
                    },
                },
                "required": ["emoji"],
            },
            impl=react,
        ),
    ]
