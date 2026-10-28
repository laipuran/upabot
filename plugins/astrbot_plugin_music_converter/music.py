import re

import httpx

APPLE_MUSIC_PATTERN = re.compile(
    r"https?://music\.apple\.com/\w{2}/(?:album|song)/[^\s?/]+(?:/[^\s?/]+(?:\?[^\s]*)?)?"
)
CONVERT_API = "https://duckran.top/api/music/convert"


class MusicConverter:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def convert(self, text: str) -> str | None:
        match = APPLE_MUSIC_PATTERN.search(text)
        if not match:
            return None
        response = await self.client.get(CONVERT_API, params={"url": match.group(0)})
        response.raise_for_status()
        data = response.json()
        if not data or not data.get("neteaseUrl"):
            return "无法转换该 Apple Music 链接。"
        return (
            "Apple Music -> 网易云音乐\n"
            f"原曲: {data.get('name', '未知歌曲')} - {data.get('artist', '')}\n"
            f"{data['neteaseUrl']}"
        )
