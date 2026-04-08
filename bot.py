# -*- coding: utf-8 -*-
import os
import html
import time
import logging
from io import BytesIO
from typing import List, Dict, Tuple

import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
from dotenv import load_dotenv

load_dotenv()

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL = os.getenv("CHANNEL_USERNAME", "").strip()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if CHANNEL and not CHANNEL.startswith("@"):
    CHANNEL = "@" + CHANNEL

# Размеры
STANDARD_W, STANDARD_H = 750, 938
STORY_W, STORY_H = 720, 1280

# Константы для МН
MN_TITLE_ZONE_PCT = 0.23
MN_BASE_FONT_SIZE = 103  # 11% от 938px
MN_MARGIN_X_PCT = 0.06
MN_MARGIN_TOP_PCT = 0.06
MN_MARGIN_BOTTOM_PCT = 0.07
MN_LINE_SPACING = 8
MN_FOOTER_SIZE_PCT = 0.034

# Константы для ЧП ВМ
CHP_GRADIENT_PCT = 0.48
CHP_BASE_FONT_SIZE = 103
CHP_MARGIN_X_PCT = 0.06
CHP_MARGIN_TOP_PCT = 0.08
CHP_MARGIN_BOTTOM_PCT = 0.08
CHP_LINE_SPACING = 8

# Константы для АМ
AM_TOP_BLUR_PCT = 0.20
AM_BLUR_RADIUS = 25
AM_BLUR_BLEND = 0.50
AM_BASE_FONT_SIZE = 56
AM_MARGIN_X_PCT = 0.055
AM_TEXT_ZONE_MARGIN_PCT = 0.12
AM_LINE_SPACING = 6

# Константы для Сторис ФДР
STORY_PHOTO_H = 410
STORY_HEADER_H = 220
STORY_PURPLE_COLOR = (122, 58, 240)
STORY_PADDING = 34
STORY_TITLE_FONT_MIN = 28
STORY_TITLE_FONT_MAX = 54
STORY_BODY_FONT_MIN = 14
STORY_BODY_FONT_MAX = 30

# Шрифты
FONT_MN = "CaviarDreams.ttf"
FONT_CHP = "Montserrat-Black.ttf"
FONT_AM = "IntroInline.ttf"
FONT_MONTSERRAT = "Montserrat-Regular.ttf"

FOOTER_TEXT = "MINSK NEWS"
TEXT_POSITION_TOP = "top"
TEXT_POSITION_BOTTOM = "bottom"

# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =========================
# Bot init
# =========================
bot = telebot.TeleBot(TOKEN)
user_state: Dict[int, Dict] = {}

# =========================
# Helper functions
# =========================
def ensure_fonts():
    fonts = [FONT_MN, FONT_CHP, FONT_AM, FONT_MONTSERRAT]
    for font in fonts:
        if not os.path.exists(font):
            logger.warning(f"Font not found: {font}")

def clear_state(user_id: int):
    if user_id in user_state:
        user_state[user_id] = {"step": "idle"}

def text_width(draw: ImageDraw.ImageDraw, s: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), s, font=font)
    return bbox[2] - bbox[0]

def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
              max_width: int, max_lines: int = 6) -> List[str]:
    """Перенос текста"""
    if not text:
        return [""]
    
    words = text.split()
    if not words:
        return [""]
    
    lines = []
    current_line = words[0]
    
    for word in words[1:]:
        test_line = current_line + " " + word
        if text_width(draw, test_line, font) <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
            if len(lines) >= max_lines:
                dots = "..."
                if text_width(draw, lines[-1] + dots, font) <= max_width:
                    lines[-1] = lines[-1] + dots
                return lines
    
    if current_line:
        if len(lines) < max_lines:
            lines.append(current_line)
        else:
            dots = "..."
            if text_width(draw, lines[-1] + dots, font) <= max_width:
                lines[-1] = lines[-1] + dots
    
    return lines

def crop_to_4x5(img: Image.Image) -> Image.Image:
    w, h = img.size
    target_ratio = 4 / 5
    cur_ratio = w / h
    if cur_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))

def apply_top_gradient(img: Image.Image, height_pct: float, max_alpha: int = 165) -> Image.Image:
    w, h = img.size
    gh = int(h * height_pct)
    if gh <= 0:
        return img
    
    overlay_alpha = Image.new("L", (w, h), 0)
    grad = Image.new("L", (1, gh), 0)
    for y in range(gh):
        a = int(max_alpha * (1 - y / max(1, gh - 1)))
        grad.putpixel((0, y), a)
    grad = grad.resize((w, gh))
    overlay_alpha.paste(grad, (0, 0))
    
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base = img.convert("RGBA")
    overlay = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
    out = Image.alpha_composite(base, overlay)
    return out.convert("RGB")

def apply_bottom_gradient(img: Image.Image, height_pct: float, max_alpha: int = 220) -> Image.Image:
    w, h = img.size
    gh = int(h * height_pct)
    if gh <= 0:
        return img
    
    overlay_alpha = Image.new("L", (w, h), 0)
    grad = Image.new("L", (1, gh), 0)
    for y in range(gh):
        a = int(max_alpha * (y / max(1, gh - 1)))
        grad.putpixel((0, y), a)
    grad = grad.resize((w, gh))
    overlay_alpha.paste(grad, (0, h - gh))
    
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base = img.convert("RGBA")
    overlay = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
    out = Image.alpha_composite(base, overlay)
    return out.convert("RGB")

def apply_bottom_gradient_soft(img: Image.Image, height_pct: float, max_alpha: int = 165) -> Image.Image:
    w, h = img.size
    gh = int(h * height_pct)
    if gh <= 0:
        return img
    
    overlay_alpha = Image.new("L", (w, h), 0)
    grad = Image.new("L", (1, gh), 0)
    for y in range(gh):
        a = int(max_alpha * (y / max(1, gh - 1)))
        grad.putpixel((0, y), a)
    grad = grad.resize((w, gh))
    overlay_alpha.paste(grad, (0, h - gh))
    
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base = img.convert("RGBA")
    overlay = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
    out = Image.alpha_composite(base, overlay)
    return out.convert("RGB")

def apply_top_blur_band(img: Image.Image, band_pct: float = AM_TOP_BLUR_PCT,
                        radius: int = AM_BLUR_RADIUS, blend: float = AM_BLUR_BLEND) -> Image.Image:
    w, h = img.size
    band_h = max(1, int(h * band_pct))
    base = img.convert("RGB")
    
    top = base.crop((0, 0, w, band_h))
    blurred = top.filter(ImageFilter.GaussianBlur(radius=radius))
    mixed = Image.blend(top, blurred, blend)
    
    overlay = Image.new("RGBA", (w, band_h), (0, 0, 0, 95))
    mixed_rgba = mixed.convert("RGBA")
    final_band = Image.alpha_composite(mixed_rgba, overlay).convert("RGB")
    
    out = base.copy()
    out.paste(final_band, (0, 0))
    return out

# =========================
# Keyboard для регулировки шрифта
# =========================
def font_size_kb(current_multiplier: float = 1.0):
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🔽 60%", callback_data="font_size:0.6"),
        InlineKeyboardButton("70%", callback_data="font_size:0.7"),
        InlineKeyboardButton("80%", callback_data="font_size:0.8"),
    )
    kb.add(
        InlineKeyboardButton("90%", callback_data="font_size:0.9"),
        InlineKeyboardButton("100%", callback_data="font_size:1.0"),
        InlineKeyboardButton("110%", callback_data="font_size:1.1"),
    )
    kb.add(
        InlineKeyboardButton("120%", callback_data="font_size:1.2"),
        InlineKeyboardButton("130%", callback_data="font_size:1.3"),
        InlineKeyboardButton("🔼 140%", callback_data="font_size:1.4"),
    )
    kb.add(InlineKeyboardButton("✅ Готово", callback_data="font_size:done"))
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="font_size:cancel"))
    return kb

# =========================
# Шаблон "МН" (ПРИНУДИТЕЛЬНЫЙ РАЗМЕР ШРИФТА)
# =========================
def make_card_mn(photo_bytes: bytes, title_text: str,
                 text_position: str = TEXT_POSITION_TOP,
                 font_size_multiplier: float = 1.0) -> BytesIO:
    ensure_fonts()
    
    logger.info(f"=== make_card_mn: multiplier = {font_size_multiplier} ===")
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((STANDARD_W, STANDARD_H), Image.Resampling.LANCZOS)
    
    img = ImageEnhance.Brightness(img).enhance(0.55)
    
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    else:
        img = apply_bottom_gradient_soft(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    
    draw = ImageDraw.Draw(img)
    
    margin_x = int(STANDARD_W * MN_MARGIN_X_PCT)
    margin_top = int(STANDARD_H * MN_MARGIN_TOP_PCT)
    margin_bottom = int(STANDARD_H * MN_MARGIN_BOTTOM_PCT)
    safe_w = STANDARD_W - 2 * margin_x
    
    footer_size = max(24, int(STANDARD_H * MN_FOOTER_SIZE_PCT))
    footer_font = ImageFont.truetype(FONT_MN, footer_size)
    fb = draw.textbbox((0, 0), FOOTER_TEXT, font=footer_font)
    footer_w = fb[2] - fb[0]
    footer_h = fb[3] - fb[1]
    
    text = (title_text or "").strip().upper()
    if not text:
        text = " "
    
    # === ПРИНУДИТЕЛЬНЫЙ РАЗМЕР ШРИФТА ===
    # Расчёт размера: базовый 103px * множитель
    target_size = int(MN_BASE_FONT_SIZE * font_size_multiplier)
    # Ограничиваем только минимальным значением, максимальное НЕ ограничиваем
    target_size = max(40, target_size)
    
    logger.info(f"Target font size: {target_size}px (multiplier: {font_size_multiplier})")
    
    # СОЗДАЁМ ШРИФТ С ТОЧНЫМ РАЗМЕРОМ
    font = ImageFont.truetype(FONT_MN, target_size)
    
    # Разбиваем текст на строки
    lines = wrap_text(draw, text, font, safe_w, max_lines=5)
    
    # Вычисляем высоту
    bbox = draw.textbbox((0, 0), "A", font=font)
    line_height = bbox[3] - bbox[1]
    total_text_height = len(lines) * line_height + (len(lines) - 1) * MN_LINE_SPACING
    
    logger.info(f"Lines: {len(lines)}, Line height: {line_height}, Total: {total_text_height}")
    
    # НЕ ПРОВЕРЯЕМ, ВЛЕЗАЕТ ЛИ ТЕКСТ - ПРИНУДИТЕЛЬНО РИСУЕМ
    if text_position == TEXT_POSITION_TOP:
        title_y = margin_top
        footer_y = STANDARD_H - margin_bottom + (margin_bottom - footer_h) // 2
    else:
        title_y = STANDARD_H - margin_bottom - total_text_height
        footer_y = margin_top
    
    # Текст по левому краю
    y = title_y
    x = margin_x
    
    for line in lines:
        draw.text((x, y), line, font=font, fill="white")
        y += line_height + MN_LINE_SPACING
    
    footer_x = (STANDARD_W - footer_w) // 2
    draw.text((footer_x, footer_y), FOOTER_TEXT, font=footer_font, fill="white")
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

# =========================
# Шаблон "ЧП ВМ"
# =========================
def make_card_chp(photo_bytes: bytes, title_text: str,
                  text_position: str = TEXT_POSITION_TOP) -> BytesIO:
    ensure_fonts()
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((STANDARD_W, STANDARD_H), Image.Resampling.LANCZOS)
    
    img = ImageEnhance.Brightness(img).enhance(0.85)
    
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
    else:
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
    
    draw = ImageDraw.Draw(img)
    
    margin_x = int(STANDARD_W * CHP_MARGIN_X_PCT)
    margin_top = int(STANDARD_H * CHP_MARGIN_TOP_PCT)
    margin_bottom = int(STANDARD_H * CHP_MARGIN_BOTTOM_PCT)
    safe_w = STANDARD_W - 2 * margin_x
    
    text = (title_text or "").strip().upper()
    title_max_h = int(STANDARD_H * MN_TITLE_ZONE_PCT)
    
    # Подбор шрифта для ЧП ВМ
    font = None
    lines = []
    line_height = 0
    total_h = 0
    
    for size in range(CHP_BASE_FONT_SIZE, 30, -2):
        test_font = ImageFont.truetype(FONT_CHP, size)
        test_lines = wrap_text(draw, text, test_font, safe_w, max_lines=6)
        
        if not test_lines:
            continue
        
        bbox = draw.textbbox((0, 0), "A", font=test_font)
        test_line_height = bbox[3] - bbox[1]
        test_total_h = len(test_lines) * test_line_height + (len(test_lines) - 1) * CHP_LINE_SPACING
        
        if test_total_h <= title_max_h:
            font = test_font
            lines = test_lines
            line_height = test_line_height
            total_h = test_total_h
            break
    
    if font is None:
        font = ImageFont.truetype(FONT_CHP, 40)
        lines = wrap_text(draw, text, font, safe_w, max_lines=6)
        bbox = draw.textbbox((0, 0), "A", font=font)
        line_height = bbox[3] - bbox[1]
        total_h = len(lines) * line_height + (len(lines) - 1) * CHP_LINE_SPACING
    
    if text_position == TEXT_POSITION_TOP:
        y = margin_top
    else:
        y = STANDARD_H - margin_bottom - total_h
    
    for line in lines:
        draw.text((margin_x, y), line, font=font, fill="white")
        y += line_height + CHP_LINE_SPACING
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

# =========================
# Шаблон "АМ"
# =========================
def make_card_am(photo_bytes: bytes, title_text: str) -> BytesIO:
    ensure_fonts()
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((STANDARD_W, STANDARD_H), Image.Resampling.LANCZOS)
    
    img = apply_top_blur_band(img)
    
    draw = ImageDraw.Draw(img)
    
    margin_x = int(STANDARD_W * AM_MARGIN_X_PCT)
    band_h = int(STANDARD_H * AM_TOP_BLUR_PCT)
    safe_w = STANDARD_W - 2 * margin_x
    
    text = (title_text or "").strip().upper()
    
    text_zone_top = int(band_h * AM_TEXT_ZONE_MARGIN_PCT)
    text_zone_bottom = int(band_h * AM_TEXT_ZONE_MARGIN_PCT)
    text_zone_h = max(1, band_h - text_zone_top - text_zone_bottom)
    
    font = None
    lines = []
    line_height = 0
    total_h = 0
    
    for size in range(AM_BASE_FONT_SIZE, 20, -2):
        test_font = ImageFont.truetype(FONT_AM, size)
        test_lines = wrap_text(draw, text, test_font, safe_w, max_lines=3)
        
        if not test_lines:
            continue
        
        bbox = draw.textbbox((0, 0), "A", font=test_font)
        test_line_height = bbox[3] - bbox[1]
        test_total_h = len(test_lines) * test_line_height + (len(test_lines) - 1) * AM_LINE_SPACING
        
        if test_total_h <= text_zone_h:
            font = test_font
            lines = test_lines
            line_height = test_line_height
            total_h = test_total_h
            break
    
    if font is None:
        font = ImageFont.truetype(FONT_AM, 30)
        lines = wrap_text(draw, text, font, safe_w, max_lines=3)
        bbox = draw.textbbox((0, 0), "A", font=font)
        line_height = bbox[3] - bbox[1]
        total_h = len(lines) * line_height + (len(lines) - 1) * AM_LINE_SPACING
    
    y = text_zone_top + max(0, (text_zone_h - total_h) // 2)
    
    for line in lines:
        line_w = text_width(draw, line, font)
        x = (STANDARD_W - line_w) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height + AM_LINE_SPACING
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

# =========================
# Шаблон "Сторис ФДР"
# =========================
def make_card_fdr_story(photo_bytes: bytes, title: str, body_text: str) -> BytesIO:
    ensure_fonts()
    
    canvas = Image.new("RGB", (STORY_W, STORY_H), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    
    photo = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    def fit_cover(im: Image.Image, target_w: int, target_h: int) -> Image.Image:
        src_w, src_h = im.size
        scale = max(target_w / src_w, target_h / src_h)
        nw, nh = int(src_w * scale), int(src_h * scale)
        resized = im.resize((nw, nh), Image.LANCZOS)
        left = max(0, (nw - target_w) // 2)
        top = max(0, (nh - target_h) // 2)
        return resized.crop((left, top, left + target_w, top + target_h))
    
    story_photo = fit_cover(photo, STORY_W, STORY_PHOTO_H)
    canvas.paste(story_photo, (0, 0))
    
    canvas.paste(Image.new("RGB", (STORY_W, STORY_HEADER_H), STORY_PURPLE_COLOR), (0, STORY_PHOTO_H))
    draw.rectangle([0, STORY_PHOTO_H + STORY_HEADER_H, STORY_W, STORY_H], fill=(0, 0, 0))
    
    # Заголовок
    title_font_size = STORY_TITLE_FONT_MAX
    title_font = ImageFont.truetype(FONT_MONTSERRAT, title_font_size)
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]
    
    while (title_w > STORY_W - 2 * STORY_PADDING or title_h > STORY_HEADER_H - 2 * STORY_PADDING) and title_font_size > STORY_TITLE_FONT_MIN:
        title_font_size -= 2
        title_font = ImageFont.truetype(FONT_MONTSERRAT, title_font_size)
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_w = title_bbox[2] - title_bbox[0]
        title_h = title_bbox[3] - title_bbox[1]
    
    title_x = (STORY_W - title_w) // 2
    title_y = STORY_PHOTO_H + (STORY_HEADER_H - title_h) // 2
    draw.text((title_x, title_y), title, font=title_font, fill="white")
    
    # Основной текст
    body_font_size = STORY_BODY_FONT_MAX
    body_font = ImageFont.truetype(FONT_MONTSERRAT, body_font_size)
    
    words = body_text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = current_line + " " + word if current_line else word
        test_bbox = draw.textbbox((0, 0), test_line, font=body_font)
        if test_bbox[2] - test_bbox[0] <= STORY_W - 2 * STORY_PADDING:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    
    line_height = body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1]
    line_spacing = 8
    max_body_h = STORY_H - STORY_PHOTO_H - STORY_HEADER_H - 2 * STORY_PADDING
    
    while len(lines) * (line_height + line_spacing) > max_body_h and body_font_size > STORY_BODY_FONT_MIN:
        body_font_size -= 2
        body_font = ImageFont.truetype(FONT_MONTSERRAT, body_font_size)
        line_height = body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1]
        
        lines = []
        current_line = ""
        for word in words:
            test_line = current_line + " " + word if current_line else word
            test_bbox = draw.textbbox((0, 0), test_line, font=body_font)
            if test_bbox[2] - test_bbox[0] <= STORY_W - 2 * STORY_PADDING:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
    
    y = STORY_PHOTO_H + STORY_HEADER_H + STORY_PADDING
    for line in lines[:15]:
        draw.text((STORY_PADDING, y), line, font=body_font, fill="white")
        y += line_height + line_spacing
    
    out = BytesIO()
    canvas.save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    return out

# =========================
# Основная функция make_card
# =========================
def make_card(photo_bytes: bytes, title_text: str, template: str,
              body_text: str = "", text_position: str = TEXT_POSITION_TOP,
              font_size_multiplier: float = 1.0) -> BytesIO:
    if template == "CHP":
        return make_card_chp(photo_bytes, title_text, text_position)
    elif template == "AM":
        return make_card_am(photo_bytes, title_text)
    elif template == "FDR_STORY":
        return make_card_fdr_story(photo_bytes, title_text, body_text)
    else:
        return make_card_mn(photo_bytes, title_text, text_position, font_size_multiplier)

# =========================
# Keyboards
# =========================
def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📝 Оформить пост"))
    return kb

def template_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📰 МН", callback_data="tpl:MN"),
        InlineKeyboardButton("🚨 ЧП ВМ", callback_data="tpl:CHP"),
        InlineKeyboardButton("✨ АМ", callback_data="tpl:AM"),
        InlineKeyboardButton("📱 Сторис ФДР", callback_data="tpl:FDR_STORY")
    )
    return kb

def text_position_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⬆️ Текст сверху", callback_data="text_pos:top"),
        InlineKeyboardButton("⬇️ Текст снизу", callback_data="text_pos:bottom")
    )
    return kb

def preview_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
        InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_body"),
        InlineKeyboardButton("✏️ Редактировать заголовок", callback_data="edit_title"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    )
    return kb

def channel_kb():
    kb = InlineKeyboardMarkup()
    return kb

def build_caption_html(title: str, body: str) -> str:
    return f"<b>📰 {html.escape(title)}</b>\n\n{html.escape(body)}".strip()

# =========================
# Callback handlers
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("font_size:"))
def on_font_size(c):
    uid = c.from_user.id
    action = c.data.split(":")[1]
    st = user_state.get(uid) or {}
    
    logger.info(f"Font size callback: action={action}")
    
    if action == "cancel":
        st.pop("step", None)
        user_state[uid] = st
        bot.answer_callback_query(c.id, "Отменено ❌")
        bot.edit_message_text("❌ Настройка отменена", c.message.chat.id, c.message.message_id)
        bot.send_message(c.message.chat.id, "📝 Выбери другой шаблон", reply_markup=template_kb())
        return
    
    if action == "done":
        multiplier = st.get("font_size_multiplier", 1.0)
        st["step"] = "waiting_text_position"
        user_state[uid] = st
        bot.answer_callback_query(c.id, f"Размер шрифта: {int(multiplier*100)}% ✅")
        bot.edit_message_text(
            f"✅ Размер шрифта установлен: {int(multiplier*100)}%\n\n📐 Теперь выбери расположение текста:",
            c.message.chat.id, c.message.message_id,
            reply_markup=text_position_kb()
        )
        return
    
    # Обработка числового значения (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4)
    try:
        new_mult = float(action)
        new_mult = max(0.6, min(1.4, new_mult))
    except:
        bot.answer_callback_query(c.id)
        return
    
    st["font_size_multiplier"] = new_mult
    user_state[uid] = st
    
    bot.answer_callback_query(c.id, f"Размер: {int(new_mult*100)}%")
    bot.edit_message_text(
        f"📰 Выбран шаблон <b>МН</b>\n\n"
        f"🔤 Выбран размер шрифта: <b>{int(new_mult*100)}%</b>\n\n"
        f"Нажми «Готово» для продолжения или выбери другой размер:",
        c.message.chat.id, c.message.message_id,
        parse_mode="HTML", reply_markup=font_size_kb(new_mult)
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("tpl:"))
def on_template_select(c):
    uid = c.from_user.id
    template = c.data.split(":", 1)[1]
    st = user_state.get(uid) or {}
    st["template"] = template
    user_state[uid] = st
    
    names = {"MN": "МН", "CHP": "ЧП ВМ", "AM": "АМ", "FDR_STORY": "Сторис ФДР"}
    name = names.get(template, template)
    
    if template == "MN":
        st["step"] = "waiting_font_size"
        st["font_size_multiplier"] = 1.0
        user_state[uid] = st
        bot.answer_callback_query(c.id, f"Шаблон {name} выбран ✅")
        bot.edit_message_text(
            f"📰 Выбран шаблон <b>{name}</b>\n\n"
            f"🔤 Выбери размер шрифта:\n\n"
            f"60% - мелкий\n"
            f"100% - стандартный\n"
            f"140% - крупный",
            c.message.chat.id, c.message.message_id,
            parse_mode="HTML", reply_markup=font_size_kb(1.0)
        )
    elif template in ["CHP"]:
        st["step"] = "waiting_text_position"
        user_state[uid] = st
        bot.answer_callback_query(c.id, f"Шаблон {name} выбран ✅")
        bot.edit_message_text(
            f"📰 Выбран шаблон <b>{name}</b>\n\nГде разместить текст?",
            c.message.chat.id, c.message.message_id,
            parse_mode="HTML", reply_markup=text_position_kb()
        )
    elif template == "FDR_STORY":
        st["step"] = "waiting_photo_fdr_story"
        user_state[uid] = st
        bot.answer_callback_query(c.id, f"Шаблон {name} выбран ✅")
        bot.edit_message_text(
            f"📱 Выбран шаблон <b>{name}</b>\n\n📸 Пришли фото для сторис.\n\n<i>Дальше:</i>\n1️⃣ Заголовок\n2️⃣ Текст",
            c.message.chat.id, c.message.message_id, parse_mode="HTML"
        )
    else:  # AM
        st["step"] = "waiting_photo"
        user_state[uid] = st
        bot.answer_callback_query(c.id, f"Шаблон {name} выбран ✅")
        bot.edit_message_text(
            f"✨ Выбран шаблон <b>{name}</b>\n\nТеперь пришли фото 📷",
            c.message.chat.id, c.message.message_id, parse_mode="HTML"
        )

@bot.callback_query_handler(func=lambda c: c.data.startswith("text_pos:"))
def on_text_position(c):
    uid = c.from_user.id
    position = c.data.split(":", 1)[1]
    st = user_state.get(uid) or {}
    st["text_position"] = position
    st["step"] = "waiting_photo"
    user_state[uid] = st
    
    pos_text = "сверху" if position == "top" else "снизу"
    bot.answer_callback_query(c.id, f"Текст будет {pos_text} ✅")
    bot.edit_message_text(
        f"✅ Текст будет расположен <b>{pos_text}</b> фотографии.\n\nТеперь пришли фото 📷",
        c.message.chat.id, c.message.message_id, parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda c: c.data in ["publish", "edit_body", "edit_title", "cancel"])
def on_action(call):
    uid = call.from_user.id
    st = user_state.get(uid)
    
    if not st or st.get("step") != "waiting_action":
        bot.answer_callback_query(call.id, "Нет активного превью")
        return
    
    if call.data == "publish":
        try:
            if st.get("template") == "FDR_STORY":
                caption = f"<b>📱 {html.escape(st.get('title', ''))}</b>\n\n{html.escape(st.get('body_raw', ''))}"
            else:
                caption = build_caption_html(st.get("title", ""), st.get("body_raw", ""))
            
            if CHANNEL:
                bot.send_photo(CHANNEL, BytesIO(st["card_bytes"]), caption=caption, parse_mode="HTML", reply_markup=channel_kb())
                bot.answer_callback_query(call.id, "Опубликовано ✅")
                bot.send_message(call.message.chat.id, "✅ Готово!", reply_markup=main_menu_kb())
            else:
                bot.answer_callback_query(call.id, "❌ CHANNEL_USERNAME не задан")
            clear_state(uid)
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ Ошибка: {e}")
    
    elif call.data == "edit_body":
        if st.get("template") == "FDR_STORY":
            st["step"] = "waiting_body_fdr"
            user_state[uid] = st
            bot.answer_callback_query(call.id, "✏️ Введи новый текст")
            bot.send_message(call.message.chat.id, "📝 Пришли новый ОСНОВНОЙ ТЕКСТ:", reply_markup=main_menu_kb())
        else:
            st["step"] = "waiting_body"
            user_state[uid] = st
            bot.answer_callback_query(call.id, "✏️ Введи новый текст")
            bot.send_message(call.message.chat.id, "📝 Пришли новый ОСНОВНОЙ ТЕКСТ:", reply_markup=main_menu_kb())
    
    elif call.data == "edit_title":
        if st.get("template") == "FDR_STORY":
            st["step"] = "waiting_title_fdr"
            user_state[uid] = st
            bot.answer_callback_query(call.id, "✏️ Введи новый заголовок")
            bot.send_message(call.message.chat.id, "📝 Пришли новый ЗАГОЛОВОК:", reply_markup=main_menu_kb())
        else:
            st["step"] = "waiting_title"
            user_state[uid] = st
            bot.answer_callback_query(call.id, "✏️ Введи новый заголовок")
            bot.send_message(call.message.chat.id, "📝 Пришли новый ЗАГОЛОВОК:", reply_markup=main_menu_kb())
    
    elif call.data == "cancel":
        bot.answer_callback_query(call.id, "Отменено ❌")
        clear_state(uid)
        bot.send_message(call.message.chat.id, "❌ Отменено", reply_markup=main_menu_kb())

# =========================
# Message handlers
# =========================
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "👋 <b>Привет! Я бот для оформления постов</b>\n\n"
        "<b>📝 Доступные шаблоны:</b>\n"
        "• 📰 МН — классический, текст по левому краю, РЕГУЛИРОВКА ШРИФТА (60-140%)\n"
        "• 🚨 ЧП ВМ — яркий, контрастный\n"
        "• ✨ АМ — с размытой верхней полосой\n"
        "• 📱 Сторис ФДР — формат историй\n\n"
        "Нажми «Оформить пост» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda message: message.text == "📝 Оформить пост")
def handle_post_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_template"}
    bot.send_message(message.chat.id, "📝 <b>Выбери шаблон:</b>", parse_mode="HTML", reply_markup=template_kb())

@bot.message_handler(content_types=["photo"])
def on_photo(message):
    uid = message.from_user.id
    st = user_state.get(uid) or {}
    step = st.get("step")
    
    if step in ["waiting_photo", "waiting_photo_fdr_story"]:
        try:
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            photo_bytes = bot.download_file(file_info.file_path)
            
            st["photo_bytes"] = photo_bytes
            
            if step == "waiting_photo_fdr_story":
                st["step"] = "waiting_title_fdr"
                user_state[uid] = st
                bot.reply_to(message, "📸 Фото сохранено!\n\n✏️ Теперь отправь <b>ЗАГОЛОВОК</b> для сторис:", parse_mode="HTML")
            else:
                st["step"] = "waiting_title"
                user_state[uid] = st
                bot.reply_to(message, "📸 Фото сохранено!\n\n✏️ Теперь отправь <b>ЗАГОЛОВОК</b> для поста:", parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Сначала выбери шаблон через «Оформить пост»")

@bot.message_handler(content_types=["text"])
def on_text(message):
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state.get(uid) or {"step": "idle"}
    
    if st.get("step") == "waiting_title":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        
        try:
            template = st.get("template", "MN")
            font_mult = st.get("font_size_multiplier", 1.0)
            
            logger.info(f"Creating card with multiplier: {font_mult}")
            
            card = make_card(
                st["photo_bytes"], text, template,
                text_position=st.get("text_position", TEXT_POSITION_TOP),
                font_size_multiplier=font_mult
            )
            st["card_bytes"] = card.getvalue()
            st["title"] = text
            st["step"] = "waiting_body"
            user_state[uid] = st
            bot.send_document(
                message.chat.id, document=BytesIO(st["card_bytes"]),
                visible_file_name="post.jpg",
                caption=f"✅ Пост готов! (размер шрифта: {int(font_mult*100)}%)\n\n✏️ Теперь отправь <b>ОСНОВНОЙ ТЕКСТ</b>:",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error: {e}")
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    if st.get("step") == "waiting_body":
        if not text:
            bot.reply_to(message, "❌ Текст не может быть пустым")
            return
        st["body_raw"] = text
        st["step"] = "waiting_action"
        user_state[uid] = st
        caption = build_caption_html(st["title"], text)
        bot.send_photo(
            message.chat.id, photo=BytesIO(st["card_bytes"]),
            caption=caption, parse_mode="HTML", reply_markup=preview_kb()
        )
        bot.reply_to(message, "🎉 <b>Превью готово!</b>\n\nНажми кнопку под фото.", parse_mode="HTML")
        return
    
    if st.get("step") == "waiting_title_fdr":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        st["title"] = text
        st["step"] = "waiting_body_fdr"
        user_state[uid] = st
        bot.reply_to(message, "✅ Заголовок сохранён!\n\n✏️ Теперь отправь <b>ОСНОВНОЙ ТЕКСТ</b>:", parse_mode="HTML")
        return
    
    if st.get("step") == "waiting_body_fdr":
        if not text:
            bot.reply_to(message, "❌ Текст не может быть пустым")
            return
        try:
            card = make_card_fdr_story(st["photo_bytes"], st["title"], text)
            st["card_bytes"] = card.getvalue()
            st["body_raw"] = text
            st["step"] = "waiting_action"
            user_state[uid] = st
            caption = f"<b>📱 {html.escape(st['title'])}</b>\n\n{html.escape(text)}"
            bot.send_photo(
                message.chat.id, photo=BytesIO(st["card_bytes"]),
                caption=caption, parse_mode="HTML", reply_markup=preview_kb()
            )
            bot.reply_to(message, "🎉 <b>Превью сторис готово!</b>\n\nНажми кнопку.", parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    if st.get("step") == "waiting_template":
        bot.send_message(message.chat.id, "📝 Выбери шаблон кнопками выше ☝️")
        return
    
    if st.get("step") == "waiting_text_position":
        bot.send_message(message.chat.id, "📐 Выбери расположение текста кнопками выше ☝️")
        return
    
    bot.send_message(message.chat.id, "📝 Нажми «Оформить пост»", reply_markup=main_menu_kb())

# =========================
# Main
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting bot on Background Worker...")
    ensure_fonts()
    
    time.sleep(2)
    
    try:
        bot.remove_webhook()
        logger.info("Webhook removed")
        time.sleep(0.5)
    except Exception as e:
        logger.warning(f"Failed to remove webhook: {e}")
    
    logger.info("✅ Bot started polling!")
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            logger.info("Restarting polling in 5 seconds...")
            time.sleep(5)
