from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class ChatMessage:
    user_id: str
    nickname: str
    text: str
    time: int
    is_trigger: bool = False


class MessageCache:
    def __init__(self, max_per_group: int = 50):
        self._cache: dict[str, deque[ChatMessage]] = defaultdict(
            lambda: deque(maxlen=max_per_group)
        )

    def add(self, group_id: str, msg: ChatMessage) -> None:
        self._cache[group_id].append(msg)

    def get_context(self, group_id: str, count: int = 10) -> list[ChatMessage]:
        return list(self._cache[group_id])[-count:]

    def mark_last_as_trigger(self, group_id: str) -> None:
        if self._cache[group_id]:
            self._cache[group_id][-1].is_trigger = True
