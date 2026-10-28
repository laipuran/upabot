import asyncio
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .api import ApiError, InformationProviderApi
from .chart import render_trend
from .store import Store

HELP = """电费查询（/电费 与 /elec 相同）
/elec subscribe - 交互选择并订阅房间
/elec room - 查看本对话绑定
/elec change - 重新选择房间
/elec unsubscribe - 确认后解绑本对话
/elec balance - 余额
/elec usage [YYYY-MM-DD] - 日用量，默认昨日
/elec trend [开始日期 结束日期] - 趋势，默认最近七个完整自然日
/elec refresh - 手动更新后端数据
/elec alert on <金额> - 开启低余额报警，如 /elec alert on 10
/elec alert off - 关闭报警
/elec alert - 查看报警状态
/elec cancel - 取消选择；选择中可回复“返回”或“取消”
普通查询不自动刷新；群聊共享一个绑定，不设置操作权限。"""


@dataclass
class Reply:
    text: str
    image: bytes | None = None


@dataclass
class Wizard:
    actor: str
    expires: float
    stage: str = "area"
    areas: list = field(default_factory=list)
    buildings: list = field(default_factory=list)
    rooms: list = field(default_factory=list)
    area: dict = field(default_factory=dict)
    building: dict = field(default_factory=dict)
    room: dict = field(default_factory=dict)


def menu(title: str, items: list, name: str) -> Reply:
    if not items:
        return Reply("暂无可选项，可回复“返回”或“取消”。")
    return Reply(
        title
        + "\n"
        + "\n".join(f"{i}. {item[name]}" for i, item in enumerate(items, 1))
        + "\n回复序号；返回 / 取消。"
    )


def choice(text: str, items: list):
    if not text.isascii() or not text.isdecimal() or not 1 <= int(text) <= len(items):
        raise ValueError("请选择列表中的有效序号。")
    return items[int(text) - 1]


def amount(value) -> Decimal:
    try:
        number = Decimal(str(value))
        if (
            not number.is_finite()
            or isinstance(value, bool)
            or abs(number) > Decimal("1000000000000")
        ):
            raise InvalidOperation
        return number
    except InvalidOperation as exc:
        raise ValueError("金额数据无效。") from exc


class Electricity:
    def __init__(self, client: httpx.AsyncClient, database: Path, *, today=None, clock=None):
        self.client = client
        self.database = database
        self.today = today or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")).date())
        self.clock = clock or time.monotonic
        self.api = InformationProviderApi(client)
        self.store = Store(database)
        self.wizards: dict[str, Wizard] = {}
        self.locks = defaultdict(asyncio.Lock)
        self.poll_lock = asyncio.Lock()

    async def initialize(self):
        await self.store.initialize()

    async def command(self, conversation: str, actor: str, text: str) -> Reply:
        async with self.locks[conversation]:
            try:
                return await self._command(conversation, actor, text)
            except (ApiError, ValueError) as exc:
                return Reply(str(exc))
            except (KeyError, TypeError, AttributeError):
                return Reply("电费服务返回了无效数据。")

    async def _command(self, conversation: str, actor: str, text: str) -> Reply:
        parts = text.split()
        cmd = parts[0].lower() if parts else "help"
        if cmd == "help":
            return Reply(HELP)
        wizard = self.wizards.get(conversation)
        if wizard and wizard.expires <= self.clock():
            del self.wizards[conversation]
            wizard = None
        if cmd == "cancel":
            if wizard and wizard.actor != actor:
                return Reply("另一个用户正在选房，只有发起者可以取消。")
            self.wizards.pop(conversation, None)
            return Reply("已取消交互，原绑定不变。")
        binding = await self.store.get(conversation)
        if cmd == "room":
            if not binding:
                return Reply("本对话尚未绑定房间，请使用 /elec subscribe。")
            return Reply(f"当前房间：{binding['room_name']}\nRoomId：{binding['room_id']}")
        if cmd in {"subscribe", "change", "unsubscribe"}:
            if len(parts) != 1:
                return Reply(f"使用方法：/elec {cmd}（不需要填写 RoomId）。")
            if wizard:
                return Reply("本对话正在进行选房/确认，请先由发起者完成或 /elec cancel。")
            if cmd == "subscribe" and binding:
                return Reply("本对话已经绑定房间，请使用 /elec change 更换。")
            if cmd in {"change", "unsubscribe"} and not binding:
                return Reply("本对话尚未绑定房间，请使用 /elec subscribe。")
            if cmd == "unsubscribe":
                self.wizards[conversation] = Wizard(actor, self.clock() + 300, stage="unsubscribe")
                return Reply(
                    f"确认解绑 {binding['room_name']}？回复“确认”或“取消”。\n"
                    "仅解除本对话绑定，不停止后端房间跟踪。"
                )
            areas = await self.api.areas()
            self.wizards[conversation] = Wizard(actor, self.clock() + 300, areas=areas)
            return menu("请选择区域：", areas, "areaName")
        if cmd == "alert":
            if not binding:
                return Reply("本对话尚未绑定房间，请使用 /elec subscribe。")
            if len(parts) == 1:
                threshold = binding.get("threshold")
                state = f"低于 {threshold} 元时通知" if threshold else "关闭"
                return Reply(
                    f"{binding['room_name']}\n报警：{state}\n"
                    f"最近检查：{binding.get('checked_at', '尚未检查')}\n"
                    f"状态：{binding.get('check_status', '尚未检查')}"
                )
            if parts[1:] == ["off"]:
                binding.update(threshold=None, notified=False, version=uuid.uuid4().hex)
                await self.store.save(conversation, binding)
                return Reply("低余额报警已关闭。")
            if len(parts) == 3 and parts[1] == "on":
                if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,2})?", parts[2]):
                    return Reply("报警金额必须为大于0、最多两位小数的数值（整数最多9位）。")
                threshold = amount(parts[2])
                if threshold <= 0:
                    return Reply("报警金额必须大于0。")
                # Repeating the same setting must not reset deduplication.
                if binding.get("threshold") is None or amount(binding["threshold"]) != threshold:
                    binding.update(
                        threshold=str(threshold), notified=False, version=uuid.uuid4().hex
                    )
                    await self.store.save(conversation, binding)
                return Reply(
                    f"已开启：余额低于 {threshold:.2f} 元时报警；"
                    "下次定期检查生效，持续低余额不重复通知。"
                )
            return Reply("使用方法：/elec alert [on <金额> | off]")
        if cmd in {"balance", "usage", "trend", "refresh"}:
            if not binding:
                return Reply("本对话尚未绑定房间，请使用 /elec subscribe。")
            room_id, name = binding["room_id"], binding["room_name"]
            if cmd in {"balance", "refresh"}:
                if len(parts) != 1:
                    return Reply(f"使用方法：/elec {cmd}")
                data = await (
                    self.api.balance(room_id) if cmd == "balance" else self.api.refresh(room_id)
                )
                day = data.get("date") or data.get("usage", {}).get("date", "后端当前记录")
                label = "余额" if cmd == "balance" else "已更新，余额"
                return Reply(f"{name}\n{label}：{amount(data['balance']):.2f} 元\n记录日期：{day}")
            if cmd == "usage":
                if len(parts) > 2:
                    return Reply("日期用法：/elec usage [YYYY-MM-DD]")
                day = (
                    self.parse_date(parts[1])
                    if len(parts) == 2
                    else self.today() - timedelta(days=1)
                )
                data = await self.api.usage(room_id, day.isoformat())
                return Reply(
                    f"{name}\n日期：{data['date']}\n用电量：{amount(data['usage'])} 度\n"
                    f"电费：{amount(data['cost']):.2f} 元"
                )
            if len(parts) not in {1, 3}:
                return Reply("日期用法：/elec trend [开始日期 结束日期]")
            if len(parts) == 3:
                start, end = self.parse_date(parts[1]), self.parse_date(parts[2])
            else:
                end = self.today() - timedelta(days=1)
                start = end - timedelta(days=6)
            if start > end or (end - start).days > 365:
                return Reply("日期范围无效：开始日期不得晚于结束日期，最多查询366天。")
            data = await self.api.trend(room_id, start.isoformat(), end.isoformat())
            if not any(data.get(k) for k in ("usage", "cost", "balance")):
                return Reply("该日期区间暂无数据。")
            image = await render_trend(name, start.isoformat(), end.isoformat(), data)
            return Reply(f"{name}\n{start} 至 {end} 的用电量、电费、余额趋势", image)
        return Reply("未知命令，请使用 /elec help。")

    async def check_alerts(self, send):
        """One polling pass; no balance cache and no automatic refresh/retry."""
        async with self.poll_lock:
            for key, snapshot in await self.store.all():
                if snapshot.get("threshold") is None:
                    continue
                async with self.locks[key]:
                    binding = await self.store.get(key)
                    if (
                        not binding
                        or binding.get("threshold") is None
                        or binding["version"] != snapshot["version"]
                    ):
                        continue
                    binding["checked_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
                        timespec="seconds"
                    )
                    try:
                        data = await self.api.balance(binding["room_id"])
                        balance = amount(data["balance"])
                        if str(data["roomId"]) != binding["room_id"]:
                            raise ValueError("后端返回的房间不匹配。")
                        day = self.parse_date(data["date"]).isoformat()
                        threshold = amount(binding["threshold"])
                        if balance >= threshold:
                            binding.update(notified=False, check_status="余额正常")
                        elif not binding.get("notified", False):
                            await send(
                                key,
                                f"低余额提醒：{binding['room_name']}\n"
                                f"余额：{balance:.2f} 元（低于 {threshold:.2f} 元）\n"
                                f"余额记录日期：{day}\n数据来自后端缓存，非实时余额。\n"
                                "/elec alert off 可关闭提醒。",
                            )
                            binding.update(notified=True, check_status="低余额，已通知")
                        else:
                            binding["check_status"] = "持续低余额，已通知，不重复发送"
                    except (ApiError, ValueError, KeyError, TypeError) as exc:
                        binding["check_status"] = (
                            str(exc)
                            if isinstance(exc, (ApiError, ValueError))
                            else "后端余额数据无效"
                        )
                    except Exception:
                        # Delivery failures don't consume the alert; try again next scheduled pass.
                        binding["check_status"] = "通知失败，将在下次定期检查时重试"
                    await self.store.save(key, binding)

    def parse_date(self, text: str) -> date:
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
                raise ValueError
            day = date.fromisoformat(text)
            if day > self.today():
                raise ValueError
            return day
        except ValueError as exc:
            raise ValueError("日期无效，请使用 YYYY-MM-DD，且不得查询未来日期。") from exc

    async def select(self, conversation: str, actor: str, text: str) -> Reply | None:
        text = text.strip()
        if not (
            (text.isascii() and text.isdecimal())
            or text in {"确认", "confirm", "返回", "back", "取消", "cancel"}
        ):
            return None
        async with self.locks[conversation]:
            wizard = self.wizards.get(conversation)
            if wizard is None or wizard.actor != actor:
                return None
            if wizard.expires <= self.clock():
                del self.wizards[conversation]
                return Reply("本次选择已过期，请重新 /elec subscribe 或 /elec change。")
            try:
                return await self._select(conversation, wizard, text.strip())
            except (ApiError, ValueError) as exc:
                return Reply(str(exc))
            except (KeyError, TypeError, AttributeError):
                return Reply("电费服务返回了无效数据。")

    async def _select(self, conversation: str, wizard: Wizard, text: str) -> Reply:
        wizard.expires = self.clock() + 300
        if text in {"取消", "cancel"}:
            del self.wizards[conversation]
            return Reply("已取消交互，原绑定不变。")
        if text in {"返回", "back"}:
            if wizard.stage in {"area", "building"}:
                wizard.stage = "area"
                return menu("请选择区域：", wizard.areas, "areaName")
            if wizard.stage == "floor":
                wizard.stage = "building"
                return menu("请选择楼宇：", wizard.buildings, "architectureName")
            if wizard.stage == "room":
                wizard.stage = "floor"
                return Reply(f"请输入楼层（1–{wizard.building['storyCount']}）：")
            if wizard.stage == "confirm":
                wizard.stage = "room"
                return menu("请选择房间：", wizard.rooms, "roomName")
            del self.wizards[conversation]
            return Reply("已取消解绑。")
        if wizard.stage == "unsubscribe":
            if text not in {"确认", "confirm"}:
                return Reply("请回复“确认”或“取消”。")
            await self.store.delete(conversation)
            del self.wizards[conversation]
            return Reply("已解绑本对话，后端房间跟踪不受影响。")
        if wizard.stage == "area":
            area = choice(text, wizard.areas)
            buildings = await self.api.buildings(area["areaId"])
            wizard.area, wizard.buildings, wizard.stage = area, buildings, "building"
            return menu("请选择楼宇：", buildings, "architectureName")
        if wizard.stage == "building":
            wizard.building = choice(text, wizard.buildings)
            wizard.stage = "floor"
            return Reply(f"请输入楼层（1–{wizard.building['storyCount']}）：")
        if wizard.stage == "floor":
            if (
                not text.isascii()
                or not text.isdecimal()
                or not 1 <= int(text) <= int(wizard.building["storyCount"])
            ):
                raise ValueError("请输入有效楼层。")
            rooms = await self.api.rooms(wizard.building["architectureId"], int(text))
            wizard.rooms, wizard.stage = rooms, "room"
            return menu("请选择房间：", rooms, "roomName")
        if wizard.stage == "room":
            wizard.room = choice(text, wizard.rooms)
            wizard.stage = "confirm"
            return Reply(
                f"确认订阅 {wizard.building['architectureName']}-{wizard.room['roomName']}？\n"
                f"RoomId：{wizard.room['roomNo']}\n回复“确认”，或“返回”/“取消”。"
            )
        if text not in {"确认", "confirm"}:
            raise ValueError("请回复“确认”，或“返回”/“取消”。")
        await self.api.subscribe(wizard.room["roomNo"])
        name = f"{wizard.building['architectureName']}-{wizard.room['roomName']}"
        await self.store.bind(conversation, wizard.room["roomNo"], name)
        del self.wizards[conversation]
        return Reply(f"订阅成功，本对话已绑定：{name}\n历史数据由后端回填，暂无数据时请稍后查询。")
