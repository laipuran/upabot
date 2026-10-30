"""Bounded, short-lived interaction memory; no AstrBot or sending dependencies."""

import asyncio
import re
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

Scope = tuple[str, str, str]  # platform instance, bot QQ, AstrBot session origin
MessageLookup = Callable[[int], Awaitable[dict]]


@dataclass(frozen=True)
class InteractionChange:
    kind: str
    actor: str
    message_id: str
    revision: int
    added: bool


def numeric_id(value: object, *, signed: bool = False, allow_zero: bool = False) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    value = str(value)
    pattern = r"-?[0-9]{1,20}" if signed else r"[0-9]{1,20}"
    return value if re.fullmatch(pattern, value) and (allow_zero or int(value) != 0) else ""


@dataclass
class _Record:
    kind: str
    actor: str
    updated: float
    message_id: str = ""
    text: str = ""
    count: int = 1
    emojis: dict[str, int] = field(default_factory=dict)
    revision: int = 0
    response: dict | None = None


@dataclass
class _Session:
    updated: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    records: OrderedDict = field(default_factory=OrderedDict)
    pokes_seen: OrderedDict = field(default_factory=OrderedDict)
    reactions_seen: OrderedDict = field(default_factory=OrderedDict)
    messages: OrderedDict = field(default_factory=OrderedDict)
    lookups: deque = field(default_factory=deque)


class InteractionMemory:
    """Observe notices and return current facts, never a request to act.

    All ages/expiry use an injected monotonic clock. Message lookup is the only
    external dependency. Calls for one scope are serialized; scopes are isolated.
    There are no timers, background tasks, model calls or persistent storage.
    """

    def __init__(
        self,
        *,
        ttl: float = 600,
        clock: Callable[[], float] = time.monotonic,
        max_sessions: int = 128,
        max_records: int = 32,
        merge_window: float = 3,
    ):
        self.ttl = ttl
        self.clock = clock
        self.max_sessions = max_sessions
        self.max_records = max_records
        self.merge_window = merge_window
        self._sessions: OrderedDict[Scope, _Session] = OrderedDict()
        self._revision = 0

    def clear(self):
        self._sessions.clear()

    def _prune(self, now: float):
        for scope, session in list(self._sessions.items()):
            if now - session.updated >= self.ttl:
                del self._sessions[scope]
                continue
            for key, record in list(session.records.items()):
                if now - record.updated >= self.ttl:
                    del session.records[key]
            for mapping in (session.pokes_seen, session.reactions_seen, session.messages):
                for key, value in list(mapping.items()):
                    if now - value[0] >= self.ttl:
                        del mapping[key]

    @staticmethod
    def _limit(mapping: OrderedDict, size: int):
        while len(mapping) > size:
            mapping.popitem(last=False)

    async def observe(
        self, scope: Scope, raw: dict, lookup: MessageLookup
    ) -> InteractionChange | None:
        """Remember a valid group interaction involving this bot, or ignore it.

        ``raw`` is a OneBot notice already matched to the receiving event's group,
        actor and self ID by the AstrBot adapter. Failed lookups propagate to that
        adapter for logging; they never manufacture an interaction.
        """
        bot = scope[1]
        actor = numeric_id(raw.get("user_id"))
        if (
            raw.get("post_type") != "notice"
            or not numeric_id(raw.get("group_id"))
            or not actor
            or actor == bot
            or str(raw.get("self_id")) != bot
        ):
            return
        poke = raw.get("notice_type") == "notify" and raw.get("sub_type") == "poke"
        reaction = raw.get("notice_type") == "group_msg_emoji_like"
        if not (poke or reaction) or (poke and str(raw.get("target_id")) != bot):
            return
        if reaction and (type(raw.get("is_add")) is not bool):
            return

        now = self.clock()
        self._prune(now)
        session = self._sessions.get(scope)
        if session is None:
            session = self._sessions[scope] = _Session(now)
        self._sessions.move_to_end(scope)
        self._limit(self._sessions, self.max_sessions)
        async with session.lock:
            if self._sessions.get(scope) is not session:
                return
            now = self.clock()
            try:
                if poke:
                    record = self._poke(session, actor, raw, now)
                else:
                    record = await self._reaction(session, actor, bot, raw, lookup, now)
                if record is None or self._sessions.get(scope) is not session:
                    return None
                self._revision += 1
                record.revision = self._revision
                record.response = None
                return InteractionChange(
                    record.kind,
                    actor,
                    record.message_id,
                    record.revision,
                    poke or raw["is_add"],
                )
            finally:
                session.updated = self.clock()
                self._limit(session.records, self.max_records)
                self._limit(session.pokes_seen, 128)
                self._limit(session.reactions_seen, 256)
                self._limit(session.messages, 32)

    def _poke(self, session: _Session, actor: str, raw: dict, now: float):
        # Without an upstream identifier/time, two real pokes cannot be
        # distinguished from a replay. Do not invent a UUID from the adapter.
        event_id = numeric_id(raw.get("message_id"), signed=True)
        timestamp = raw.get("time")
        token = (actor, "id", event_id) if event_id else (actor, "time", timestamp)
        if event_id or (isinstance(timestamp, int) and not isinstance(timestamp, bool)):
            if token in session.pokes_seen:
                return
            session.pokes_seen[token] = (now,)
        key = ("poke", actor)
        record = session.records.get(key)
        if record and now - record.updated <= self.merge_window:
            record.count += 1
            record.updated = now
        else:
            session.records[key] = _Record("poke", actor, now)
        session.records.move_to_end(key)
        return session.records[key]

    async def _reaction(self, session, actor, bot, raw, lookup, now):
        message_id = numeric_id(raw.get("message_id"), signed=True)
        likes = raw.get("likes")
        if not message_id or not isinstance(likes, list):
            return
        emojis = {}
        for like in likes[:20]:
            if not isinstance(like, dict):
                continue
            emoji = numeric_id(like.get("emoji_id"), allow_zero=True)
            count = like.get("count", 1)
            if emoji and type(count) is int and 0 <= count <= 1000000:
                if raw["is_add"] and count == 0:
                    continue
                emojis[emoji] = count
        if not emojis:
            return
        added = raw["is_add"]
        timestamp = raw.get("time")
        timestamp = timestamp if type(timestamp) is int else None
        changes = {}
        for emoji, count in emojis.items():
            previous = session.reactions_seen.get((actor, message_id, emoji))
            if previous:
                _, was_added, last_time, last_count = previous
                if timestamp is not None and last_time is not None and timestamp < last_time:
                    continue
                if was_added == added and (not added or count == last_count):
                    if timestamp is not None and (last_time is None or timestamp > last_time):
                        # Track the latest source ordering without making a
                        # duplicate interaction appear new in chat context.
                        session.reactions_seen[(actor, message_id, emoji)] = (
                            previous[0],
                            was_added,
                            timestamp,
                            last_count,
                        )
                    continue
            changes[emoji] = count
        if not changes:
            return
        key = ("reaction", actor, message_id)
        record = session.records.get(key)
        if added:
            text = await self._message(session, bot, raw, message_id, lookup, now)
            if text is None:
                return
            if record is None:
                record = session.records[key] = _Record(
                    "reaction", actor, now, message_id=message_id, text=text
                )
            record.emojis.update(changes)
            while len(record.emojis) > 20:
                record.emojis.pop(next(iter(record.emojis)))
            record.updated = now
            session.records.move_to_end(key)
        elif record:
            for emoji in changes:
                record.emojis.pop(emoji, None)
            if not record.emojis:
                del session.records[key]
        for emoji, count in changes.items():
            state_key = (actor, message_id, emoji)
            session.reactions_seen[state_key] = (now, added, timestamp, count)
            session.reactions_seen.move_to_end(state_key)
        return record or _Record("reaction", actor, now, message_id=message_id)

    async def _message(self, session, bot, raw, message_id, lookup, now):
        cached = session.messages.get(message_id)
        if cached:
            return cached[1]
        while session.lookups and now - session.lookups[0] >= 60:
            session.lookups.popleft()
        if len(session.lookups) >= 8:
            return None
        session.lookups.append(now)
        # Negative-cache failures too, so malformed/unknown messages cannot
        # repeatedly force a read. The global per-scope lookup budget still applies.
        session.messages[message_id] = (now, None)
        message = await lookup(int(message_id))
        if not isinstance(message, dict):
            return None
        sender = message.get("sender")
        if not isinstance(sender, dict) or str(sender.get("user_id")) != bot:
            return None
        if str(message.get("group_id", raw["group_id"])) != str(raw["group_id"]):
            return None
        if message.get("message_type", "group") != "group":
            return None
        segments = message.get("message", [])
        text = ""
        if isinstance(segments, list):
            for segment in segments[:100]:
                if not isinstance(segment, dict) or segment.get("type") != "text":
                    continue
                data = segment.get("data")
                if isinstance(data, dict) and isinstance(data.get("text"), str):
                    text = (text + data["text"][:300])[:300]
        session.messages[message_id] = (now, text)
        return text

    def context(self, scope: Scope) -> list[dict]:
        """Return at most eight current facts, without consuming or refreshing them."""
        now = self.clock()
        self._prune(now)
        session = self._sessions.get(scope)
        if not session:
            return []
        return [self._fact(record, now) for record in list(session.records.values())[-8:]]

    def current(self, scope: Scope, change: InteractionChange) -> dict | None:
        """Return the exact still-current revision, not a later replacement."""
        self.context(scope)  # Apply the same expiry rules as normal chat context.
        session = self._sessions.get(scope)
        if not session:
            return None
        key = (
            ("poke", change.actor)
            if change.kind == "poke"
            else ("reaction", change.actor, change.message_id)
        )
        record = session.records.get(key)
        if record and record.revision == change.revision:
            return self._fact(record, self.clock())
        return None

    def handled(self, scope: Scope, change: InteractionChange, action: str, text: str = ""):
        if self.current(scope, change) is None:
            return
        session = self._sessions[scope]
        key = (
            ("poke", change.actor)
            if change.kind == "poke"
            else ("reaction", change.actor, change.message_id)
        )
        session.records[key].response = {"action": action}
        if text:
            session.records[key].response["text"] = text[:120]

    @staticmethod
    def _fact(record: _Record, now: float) -> dict:
        fact = {
            "kind": record.kind,
            "actor_qq": record.actor,
            "seconds_ago": max(0, int(now - record.updated)),
        }
        if record.kind == "poke":
            fact["poke_count"] = record.count
        else:
            fact.update(
                message_id=record.message_id,
                your_message_text=record.text,
                likes=[
                    {"emoji_id": emoji, "reported_count": count}
                    for emoji, count in list(record.emojis.items())[:20]
                ],
            )
        if record.response is not None:
            fact["response"] = dict(record.response)
        return fact
