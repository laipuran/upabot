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


class DeviceStatus:
    def __init__(self, client: httpx.AsyncClient, url: str):
        self.client = client
        self.url = url

    async def status(self) -> str:
        response = await self.client.get(self.url, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        memory = data["memory"]
        storage = data["storage"]
        return "\n".join(
            [
                "Upa 设备状态:",
                f"CPU 温度: {data['cpuTemperatureCelsius']}°C",
                f"运行时间: {data['uptime']}",
                f"设备型号: {data['model']}",
                f"系统版本: {data['osVersion']}",
                f"IP 地址: {data['ip']}",
                "",
                f"内存: {memory['usedMB']}MB / {memory['totalMB']}MB "
                f"({memory['usagePercentage']:.1f}%)",
                f"存储: {storage['usedGB']}GB / {storage['totalGB']}GB "
                f"({storage['usagePercentage']:.1f}%)",
            ]
        )
