"""Keyword subscriptions, switches, and notification planning for Taboo."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from .message_cache import ChatMessage, MessageCache

HELP_TEXT = (
    "Taboo 使用说明（请在私聊中使用）:\n"
    "/taboo reg <关键词>   - 注册关键词\n"
    "/taboo unreg <关键词> - 取消关键词\n"
    "/taboo list          - 列出关键词 (/tb ls)\n"
    "/taboo on            - 开启监控\n"
    "/taboo off           - 关闭监控"
)
ALIASES = {
    "register": "reg",
    "add": "reg",
    "unregister": "unreg",
    "remove": "unreg",
    "del": "unreg",
    "delete": "unreg",
    "ls": "list",
    "enable": "on",
    "disable": "off",
    "h": "help",
}


@dataclass
class Notification:
    user_id: str
    keyword: str
    messages: list[ChatMessage]


class Taboo:
    """Persistent keyword subscriptions, isolated by AstrBot platform instance."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.cache = MessageCache()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS keywords ("
                "platform TEXT NOT NULL, keyword TEXT NOT NULL, user_id TEXT NOT NULL, "
                "PRIMARY KEY (platform, keyword))"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS settings ("
                "platform TEXT NOT NULL, user_id TEXT NOT NULL, enabled INTEGER NOT NULL, "
                "PRIMARY KEY (platform, user_id))"
            )
            await db.commit()

    async def command(self, platform: str, user_id: str, text: str) -> str:
        parts = text.strip().split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        command = ALIASES.get(command, command)
        argument = parts[1].strip() if len(parts) > 1 else ""
        async with self.lock, aiosqlite.connect(self.path) as db:
            if command in {"on", "off"}:
                enabled = command == "on"
                async with db.execute(
                    "SELECT enabled FROM settings WHERE platform = ? AND user_id = ?",
                    (platform, user_id),
                ) as cursor:
                    row = await cursor.fetchone()
                current = bool(row[0]) if row else True
                state = "开启" if enabled else "关闭"
                if enabled == current:
                    return f"监控已{state}，无需重复操作"
                await db.execute(
                    "INSERT INTO settings VALUES (?, ?, ?) "
                    "ON CONFLICT(platform, user_id) DO UPDATE SET enabled = excluded.enabled",
                    (platform, user_id, enabled),
                )
                await db.commit()
                return f"Taboo 监控已{state}"
            if command == "unreg":
                if not argument:
                    return "请指定关键词，格式: /taboo unreg <关键词>"
                cursor = await db.execute(
                    "DELETE FROM keywords WHERE platform = ? AND user_id = ? AND keyword = ?",
                    (platform, user_id, argument),
                )
                await db.commit()
                if not cursor.rowcount:
                    return f"您未注册关键词「{argument}」"
                return f"关键词「{argument}」已取消注册"
            if command == "reg":
                if not argument:
                    return "请指定关键词，格式: /taboo reg <关键词>"
                try:
                    await db.execute(
                        "INSERT INTO keywords VALUES (?, ?, ?)",
                        (platform, argument, user_id),
                    )
                    await db.commit()
                except aiosqlite.IntegrityError:
                    return f"关键词「{argument}」已被注册"
                return (
                    f"关键词「{argument}」注册成功！\n"
                    "当群聊中有人提及此关键词且监控开启时，将转发聊天记录到您的私信。"
                )
            if command == "list":
                async with db.execute(
                    "SELECT keyword FROM keywords WHERE platform = ? AND user_id = ? "
                    "ORDER BY rowid",
                    (platform, user_id),
                ) as cursor:
                    rows = await cursor.fetchall()
                if not rows:
                    return "您尚未注册任何关键词"
                return "您已注册的关键词:\n" + "\n".join(
                    f"  {index}. {row[0]}" for index, row in enumerate(rows, 1)
                )
        if command in {"", "help"}:
            return HELP_TEXT
        return f"未知命令: {command}\n\n{HELP_TEXT}"

    async def observe(
        self, platform: str, group_id: str, message: ChatMessage
    ) -> list[Notification]:
        group_key = f"{platform}:{group_id}"
        self.cache.add(group_key, message)
        context = self.cache.get_context(group_key)
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT k.keyword, k.user_id FROM keywords k "
                "LEFT JOIN settings s ON k.platform = s.platform AND k.user_id = s.user_id "
                "WHERE k.platform = ? AND COALESCE(s.enabled, 1) = 1 ORDER BY k.rowid",
                (platform,),
            ) as cursor:
                rows = await cursor.fetchall()
        recipients = {}
        for keyword, user_id in rows:
            if keyword in message.text and user_id != message.user_id:
                recipients.setdefault(user_id, keyword)
        if not recipients:
            return []
        message.is_trigger = True
        return [Notification(user, keyword, context) for user, keyword in recipients.items()]
