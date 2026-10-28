import json
import uuid
from pathlib import Path

import aiosqlite


class Store:
    """Only conversation settings/state are persisted, never backend balance data."""

    def __init__(self, path: Path):
        self.path = path

    async def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS bindings "
                "(conversation TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            await db.commit()

    async def get(self, conversation: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT data FROM bindings WHERE conversation = ?", (conversation,)
            ) as cursor:
                row = await cursor.fetchone()
        return json.loads(row[0]) if row else None

    async def save(self, conversation: str, data: dict):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO bindings VALUES (?, ?) "
                "ON CONFLICT(conversation) DO UPDATE SET data = excluded.data",
                (conversation, json.dumps(data, ensure_ascii=False)),
            )
            await db.commit()

    async def bind(self, conversation: str, room_id: str, room_name: str):
        old = await self.get(conversation)
        await self.save(
            conversation,
            {
                "room_id": room_id,
                "room_name": room_name,
                "version": uuid.uuid4().hex,
                "threshold": old.get("threshold") if old else None,
                "notified": False,
            },
        )

    async def delete(self, conversation: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM bindings WHERE conversation = ?", (conversation,))
            await db.commit()

    async def all(self) -> list[tuple[str, dict]]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT conversation, data FROM bindings") as cursor:
                rows = await cursor.fetchall()
        return [(key, json.loads(data)) for key, data in rows]
