import atexit
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

# AstrBot initializes its runtime directories when imported. Keep them out of the repo.
_temp_base = "/tmp/opencode" if Path("/tmp/opencode").is_dir() else None
_runtime = tempfile.TemporaryDirectory(prefix="duckbot-tests-", dir=_temp_base)
atexit.register(_runtime.cleanup)
os.environ["ASTRBOT_ROOT"] = _runtime.name

from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.message_components import Plain  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402


class FakeEvent(AstrMessageEvent):
    """Real AstrBot event with only outbound QQ I/O replaced."""

    def __init__(self, text, user="100", group="", platform="qq", self_id="999"):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        message.self_id = self_id
        message.group_id = group
        message.sender = MessageMember(user, "测试用户")
        message.message = [Plain(text)]
        message.message_str = text
        message.message_id = "1"
        message.raw_message = {}
        super().__init__(
            text,
            message,
            PlatformMetadata("aiocqhttp", "test", platform),
            group or user,
        )
        self.bot = AsyncMock()
        self.sent = []

    async def send(self, message):
        self.sent.append(message)
        self._has_send_oper = True

    @property
    def replies(self):
        return ["".join(c.text for c in m.chain if isinstance(c, Plain)) for m in self.sent]
