"""Render Taboo notifications as self-contained base64 images."""

import asyncio
import base64
from datetime import datetime
from functools import partial
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .message_cache import ChatMessage

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

TITLE_SIZE = 18
SENDER_SIZE = 14
TEXT_SIZE = 14
TIME_SIZE = 11
FOOTER_SIZE = 12

CARD_WIDTH = 480
PADDING = 16
MSG_GAP = 6


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_text(draw: ImageDraw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for ch in text:
        test = current + ch
        w, _ = _text_size(draw, test, font)
        if w > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def _render_sync(
    keyword: str,
    messages: list[ChatMessage],
    group_name: str,
) -> str:
    title_font = _load_font(TITLE_SIZE)
    sender_font = _load_font(SENDER_SIZE)
    text_font = _load_font(TEXT_SIZE)
    time_font = _load_font(TIME_SIZE)
    footer_font = _load_font(FOOTER_SIZE)

    tmp_img = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(tmp_img)

    text_width = CARD_WIDTH - PADDING * 2

    title_text = group_name or "Taboo 通知"
    _, th = _text_size(draw, title_text, title_font)
    _, sh = _text_size(draw, "Ag", sender_font)
    _, fh = _text_size(draw, "Ag", footer_font)

    header_h = PADDING + th + 12

    msg_heights: list[int] = []
    for msg in messages:
        lines = _wrap_text(draw, msg.text or "(无文字内容)", text_font, text_width)
        h = sh + 4 + len(lines) * (_text_size(draw, "Ag", text_font)[1] + 2)
        if msg.is_trigger:
            h += _text_size(draw, "Ag", time_font)[1] + 4
        msg_heights.append(h)

    footer_h = 8 + fh + PADDING

    total_h = int(header_h + sum(msg_heights) + (len(messages) - 1) * MSG_GAP + footer_h)

    img = Image.new("RGB", (CARD_WIDTH, total_h), "#f0f2f5")
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        [8, 8, CARD_WIDTH - 8, total_h - 8],
        radius=12,
        fill="#ffffff",
    )

    draw.text((PADDING, PADDING), title_text, fill="#1a1a1a", font=title_font)
    draw.line(
        [(PADDING, PADDING + th + 8), (CARD_WIDTH - PADDING, PADDING + th + 8)],
        fill="#e8e8e8",
        width=1,
    )

    y = int(header_h)

    for i, msg in enumerate(messages):
        h = msg_heights[i]
        msg_top = y
        msg_bottom = y + h

        if msg.is_trigger:
            draw.rounded_rectangle(
                [PADDING - 6, msg_top - 3, CARD_WIDTH - PADDING + 6, msg_bottom + 3],
                radius=8,
                fill="#fff8e1",
            )
            draw.line(
                [
                    (PADDING - 2, msg_top + 8),
                    (PADDING - 2, msg_bottom - 8),
                ],
                fill="#ff9800",
                width=3,
            )

        nickname = msg.nickname or str(msg.user_id)
        draw.text((PADDING, y), nickname, fill="#1a73e8", font=sender_font)

        time_str = datetime.fromtimestamp(msg.time).strftime("%H:%M")
        tw, _ = _text_size(draw, time_str, time_font)
        draw.text(
            (CARD_WIDTH - PADDING - tw, y + 2),
            time_str,
            fill="#999999",
            font=time_font,
        )

        y += sh + 4
        lines = _wrap_text(draw, msg.text or "(无文字内容)", text_font, text_width)
        for line in lines:
            draw.text((PADDING, y), line, fill="#333333", font=text_font)
            y += _text_size(draw, line, text_font)[1] + 2

        if msg.is_trigger:
            kw_text = f"触发了关键词「{keyword}」"
            tw, _ = _text_size(draw, kw_text, time_font)
            draw.rounded_rectangle(
                [PADDING, y - 1, PADDING + tw + 8, y + _text_size(draw, "Ag", time_font)[1] + 3],
                radius=4,
                fill="#e53935",
            )
            draw.text(
                (PADDING + 4, y + 1),
                kw_text,
                fill="#ffffff",
                font=time_font,
            )
            y += _text_size(draw, "Ag", time_font)[1] + 6

        y += MSG_GAP

    draw.text(
        (PADDING, y + 4),
        "发送 /taboo off 可关闭监控",
        fill="#999999",
        font=footer_font,
    )

    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"base64://{b64}"


async def render_chat_image(
    keyword: str,
    messages: list[ChatMessage],
    group_name: str = "",
) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(_render_sync, keyword=keyword, messages=messages, group_name=group_name),
    )
