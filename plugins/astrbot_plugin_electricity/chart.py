import asyncio
from datetime import date
from io import BytesIO
from math import isfinite

from PIL import Image, ImageDraw, ImageFont


def draw_trend(name: str, start: str, end: str, data: dict) -> bytes:
    font = ImageFont.load_default(size=16)
    for path in (
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        try:
            font = ImageFont.truetype(path, 16)
            break
        except OSError:
            pass
    image = Image.new("RGB", (900, 740), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 12), name, font=font, fill="#222222")
    draw.text((30, 38), f"{start} — {end}（缺失日期不补零）", font=font, fill="#555555")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    days = max((last - first).days, 1)
    for index, (key, label, color) in enumerate(
        (
            ("usage", "用电量 / kWh", "#1976d2"),
            ("cost", "电费 / 元", "#ef6c00"),
            ("balance", "余额 / 元", "#388e3c"),
        )
    ):
        top = 90 + index * 215
        draw.text((30, top - 18), label, font=font, fill=color)
        left, right, bottom = 110, 850, top + 145
        draw.line((left, top + 8, left, bottom, right, bottom), fill="#999999", width=1)
        points = []
        for item in data.get(key, []):
            day, value = date.fromisoformat(item["date"]), float(item["value"])
            if first <= day <= last and isfinite(value):
                points.append((day, value))
        points.sort()
        if not points:
            draw.text((left + 200, top + 60), "暂无数据", font=font, fill="#888888")
        else:
            low, high = min(v for _, v in points), max(v for _, v in points)
            span = high - low or 1
            draw.text((30, top + 8), f"{high:.2f}", font=font, fill="#555555")
            draw.text((30, bottom - 18), f"{low:.2f}", font=font, fill="#555555")
            previous = None
            for day, value in points:
                x = left + (day - first).days / days * (right - left)
                y = bottom - 10 - (value - low) / span * 115
                if previous and (day - previous[0]).days == 1:
                    draw.line((previous[1], previous[2], x, y), fill=color, width=2)
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
                previous = (day, x, y)
        draw.text((left, bottom + 5), start, font=font, fill="#555555")
        draw.text((right - 100, bottom + 5), end, font=font, fill="#555555")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def render_trend(name: str, start: str, end: str, data: dict) -> bytes:
    return await asyncio.to_thread(draw_trend, name, start, end, data)
