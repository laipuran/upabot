import json
from pathlib import Path


class EmojiCatalog:
    """QQ names, not an emotion classifier or a guarantee of reaction support."""

    def __init__(self):
        self.names: dict[str, str] = json.loads(
            Path(__file__).with_name("emoji_names.json").read_text(encoding="utf-8")
        )

    def describe(self, emoji_id: str) -> str | None:
        return self.names.get(emoji_id)

    def resolve(self, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 40:
            raise ValueError("请提供表情编号或准确名称。")
        value = value.strip().removeprefix("/")
        if value in self.names:
            return value
        matches = [key for key, name in self.names.items() if name.casefold() == value.casefold()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            matches = [
                key for key, name in self.names.items() if value.casefold() in name.casefold()
            ]
        candidates = "、".join(f"{key}={self.names[key]}" for key in matches[:8])
        if candidates:
            raise ValueError("名称不唯一或不准确，请指定编号。候选：" + candidates)
        raise ValueError("未找到该编号或名称；请换用准确的 QQ 表情名称。")

    def enrich(self, facts: list[dict]) -> list[dict]:
        return [
            {
                **fact,
                **(
                    {
                        "likes": [
                            {**like, "name": self.describe(like["emoji_id"])}
                            for like in fact["likes"]
                        ]
                    }
                    if "likes" in fact
                    else {}
                ),
            }
            for fact in facts
        ]
