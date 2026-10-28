import httpx


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class InformationProviderApi:
    """No caching, automatic refresh, or retries: the backend owns data freshness."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def request(self, method: str, path: str, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(0, "电费服务暂时不可用，请稍后重试。") from exc
        if not response.is_success:
            messages = {
                400: "请求参数无效，请检查输入。",
                404: "暂无数据，请先订阅房间，或稍后手动更新。",
                409: "该房间已订阅。",
                429: "后端限流，请稍后重试。",
                503: "后端功能暂不可用或已关闭。",
            }
            raise ApiError(
                response.status_code, messages.get(response.status_code, "电费服务响应异常。")
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(0, "电费服务返回了无效数据。") from exc

    async def areas(self):
        return await self.request("GET", "api/areas")

    async def buildings(self, area_id: str):
        return await self.request("GET", f"api/areas/{area_id}/buildings")

    async def rooms(self, building_id: str, floor: int):
        return await self.request(
            "GET", f"api/buildings/{building_id}/rooms", params={"floor": floor}
        )

    async def subscribe(self, room_id: str):
        try:
            await self.request("POST", "api/rooms", json={"roomId": room_id})
        except ApiError as exc:
            if exc.status != 409:
                raise

    async def balance(self, room_id: str):
        return await self.request("GET", f"api/rooms/{room_id}/balance")

    async def usage(self, room_id: str, day: str):
        return await self.request("GET", f"api/rooms/{room_id}/daily-usage", params={"date": day})

    async def trend(self, room_id: str, start: str, end: str):
        return await self.request(
            "GET", f"api/rooms/{room_id}/trend", params={"startDate": start, "endDate": end}
        )

    async def refresh(self, room_id: str):
        return await self.request("POST", f"api/rooms/{room_id}/refresh")
