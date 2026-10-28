import httpx


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
