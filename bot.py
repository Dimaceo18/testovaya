# -*- coding: utf-8 -*-
import os
import html
import time
import logging
from io import BytesIO
from typing import List, Dict, Optional, Tuple

import requests
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
STANDARD_W, STANDARD_H = 750, 938      # 4:5
SQUARE_SIZE = 1080                      # 1:1
STORY_W, STORY_H = 720, 1280           # Сторис

# Константы для MN и MN2
MN_TITLE_ZONE_PCT = 0.23
MN_FONT_START_PCT = 0.11
MN_MARGIN_X_PCT = 0.06
MN_MARGIN_TOP_PCT = 0.06
MN_MARGIN_BOTTOM_PCT = 0.07
MN_LINE_SPACING_RATIO = 0.22
MN_FOOTER_SIZE_PCT = 0.034

# Константы для MN2 (дополнительно)
MN2_LINE_SPACING_RATIO = 0.25

# Константы для CHP
CHP_GRADIENT_PCT = 0.48
CHP_FONT_START_PCT = 0.11
CHP_MARGIN_X_PCT = 0.06
CHP_MARGIN_TOP_PCT = 0.08
CHP_MARGIN_BOTTOM_PCT = 0.08
CHP_LINE_SPACING_RATIO = 0.22

# Константы для AM
AM_TOP_BLUR_PCT = 0.20
AM_BLUR_RADIUS = 18
AM_BLUR_BLEND = 0.50
AM_FONT_START_PCT = 0.060
AM_MARGIN_X_PCT = 0.055
AM_TEXT_ZONE_MARGIN_PCT = 0.12
AM_MAX_LINES = 3
AM_LINE_SPACING_RATIO = 0.16

# Константы для FDR_POST
FDR_POST_GRADIENT_PCT = 0.48
FDR_POST_PURPLE_COLOR = (122, 58, 240)
FDR_POST_HIGHLIGHT_PADDING = 10

# Константы для FDR_STORY
STORY_PHOTO_H = 410
STORY_HEADER_H = 220
STORY_PURPLE_COLOR = (122, 58, 240)
STORY_PADDING = 34
STORY_TITLE_FONT_MIN = 28
STORY_TITLE_FONT_MAX = 54
STORY_BODY_FONT_MIN = 14
STORY_BODY_FONT_MAX = 30

# Константы для MN_TG
MN_TG_TEXT_OPACITY = 38
MN_TG_TOP_POSITION_PCT = 0.20
MN_TG_BOTTOM_POSITION_PCT = 0.80
MN_TG_FONT_SIZE_PCT = 0.08

# Шрифты
FONT_MN = "CaviarDreams.ttf"
FONT_MN_BOLD = "CaviarDreams_Bold.ttf"
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
    fonts = [FONT_MN, FONT_MN_BOLD, FONT_CHP, FONT_AM, FONT_MONTSERRAT]
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
              max_width: int, max_lines: int = 6) -> Tuple[List[str], bool]:
    """Перенос текста с учётом максимальной ширины"""
    if not text:
        return [""], True
    
    words = text.split()
    if not words:
        return [""], True
    
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
                # Добавляем многоточие
                dots = "..."
                if text_width(draw, lines[-1] + dots, font) <= max_width:
                    lines[-1] = lines[-1] + dots
                return lines, False
    
    if current_line:
        if len(lines) < max_lines:
            lines.append(current_line)
        else:
            dots = "..."
            if text_width(draw, lines[-1] + dots, font) <= max_width:
                lines[-1] = lines[-1] + dots
    
    return lines, len(lines) <= max_lines

def fit_text_block(draw: ImageDraw.ImageDraw, text: str, font_path: str,
                   safe_w: int, max_block_h: int, max_lines: int = 6,
                   start_size: int = 90, min_size: int = 16,
                   line_spacing_ratio: float = 0.22) -> Tuple[ImageFont.FreeTypeFont, List[str], int, int]:
    """Подбор размера шрифта и возврат строк"""
    text = (text or "").strip().upper()
    if not text:
        text = " "
    
    size = start_size
    best_font = None
    best_lines = []
    best_line_height = 0
    best_spacing = 0
    
    while size >= min_size:
        try:
            font = ImageFont.truetype(font_path, size)
            lines, _ = wrap_text(draw, text, font, safe_w, max_lines)
            
            if not lines:
                size -= 2
                continue
            
            bbox = draw.textbbox((0, 0), "A", font=font)
            line_height = bbox[3] - bbox[1]
            spacing = int(line_height * line_spacing_ratio)
            total_h = len(lines) * line_height + (len(lines) - 1) * spacing
            
            if total_h <= max_block_h:
                best_font = font
                best_lines = lines
                best_line_height = line_height
                best_spacing = spacing
                break
            size -= 2
        except:
            size -= 2
    
    if best_font is None:
        best_font = ImageFont.truetype(font_path, min_size)
        best_lines, _ = wrap_text(draw, text, best_font, safe_w, max_lines)
        bbox = draw.textbbox((0, 0), "A", font=best_font)
        best_line_height = bbox[3] - bbox[1]
        best_spacing = int(best_line_height * line_spacing_ratio)
    
    return best_font, best_lines, best_line_height, best_spacing

def crop_to_4x5(img: Image.Image) -> Image.Image:
    """Обрезка в пропорцию 4:5"""
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

def crop_to_square(img: Image.Image) -> Image.Image:
    """Обрезка в квадрат"""
    w, h = img.size
    size = min(w, h)
    left = (w - size) // 2
    top = (h - size) // 2
    return img.crop((left, top, left + size, top + size))

def apply_top_gradient(img: Image.Image, height_pct: float, max_alpha: int = 165) -> Image.Image:
    """Градиент сверху вниз"""
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
    """Градиент снизу вверх"""
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
    """Мягкий градиент снизу"""
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
    """Размытая верхняя полоса для шаблона АМ"""
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
# Шаблон "МН" (MN)
# =========================
def make_card_mn(photo_bytes: bytes, title_text: str,
                 text_position: str = TEXT_POSITION_TOP,
                 is_square: bool = False) -> BytesIO:
    ensure_fonts()
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), Image.Resampling.LANCZOS)
        target_w, target_h = SQUARE_SIZE, SQUARE_SIZE
    else:
        img = crop_to_4x5(img)
        img = img.resize((STANDARD_W, STANDARD_H), Image.Resampling.LANCZOS)
        target_w, target_h = STANDARD_W, STANDARD_H
    
    # Яркость 0.55 (затемнение на 45%)
    img = ImageEnhance.Brightness(img).enhance(0.55)
    
    # Градиент
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    else:
        img = apply_bottom_gradient_soft(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    
    draw = ImageDraw.Draw(img)
    
    # Отступы
    margin_x = int(target_w * MN_MARGIN_X_PCT)
    margin_top = int(target_h * MN_MARGIN_TOP_PCT)
    margin_bottom = int(target_h * MN_MARGIN_BOTTOM_PCT)
    safe_w = target_w - 2 * margin_x
    
    # Футер
    footer_size = max(24, int(target_h * MN_FOOTER_SIZE_PCT))
    footer_font = ImageFont.truetype(FONT_MN, footer_size)
    fb = draw.textbbox((0, 0), FOOTER_TEXT, font=footer_font)
    footer_w = fb[2] - fb[0]
    footer_h = fb[3] - fb[1]
    
    # Зона заголовка (23% высоты)
    title_max_h = int(target_h * MN_TITLE_ZONE_PCT)
    
    # Подбор шрифта
    start_size = int(target_h * MN_FONT_START_PCT)
    font, lines, line_height, spacing = fit_text_block(
        draw=draw, text=title_text, font_path=FONT_MN,
        safe_w=safe_w, max_block_h=title_max_h, max_lines=6,
        start_size=start_size, min_size=16,
        line_spacing_ratio=MN_LINE_SPACING_RATIO
    )
    
    total_text_height = len(lines) * line_height + (len(lines) - 1) * spacing
    
    # Позиционирование
    if text_position == TEXT_POSITION_TOP:
        title_y = margin_top
        footer_y = target_h - margin_bottom + (margin_bottom - footer_h) // 2
    else:
        title_y = target_h - margin_bottom - total_text_height
        footer_y = margin_top
    
    # Рисуем заголовок (центрируем каждую строку)
    y = title_y
    for line in lines:
        line_w = text_width(draw, line, font)
        x = (target_w - line_w) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height + spacing
    
    # Рисуем футер
    footer_x = (target_w - footer_w) // 2
    draw.text((footer_x, footer_y), FOOTER_TEXT, font=footer_font, fill="white")
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

# =========================
# Шаблон "ЧП ВМ" (CHP)
# =========================
def make_card_chp(photo_bytes: bytes, title_text: str,
                  text_position: str = TEXT_POSITION_TOP,
                  is_square: bool = False) -> BytesIO:
    ensure_fonts()
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), Image.Resampling.LANCZOS)
        target_w, target_h = SQUARE_SIZE, SQUARE_SIZE
    else:
        img = crop_to_4x5(img)
        img = img.resize((STANDARD_W, STANDARD_H), Image.Resampling.LANCZOS)
        target_w, target_h = STANDARD_W, STANDARD_H
    
    # Яркость 0.85 (затемнение на 15%)
    img = ImageEnhance.Brightness(img).enhance(0.85)
    
    # Градиент 48%
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
    else:
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
    
    draw = ImageDraw.Draw(img)
    
    # Отступы
    margin_x = int(target_w * CHP_MARGIN_X_PCT)
    margin_top = int(target_h * CHP_MARGIN_TOP_PCT)
    margin_bottom = int(target_h * CHP_MARGIN_BOTTOM_PCT)
    safe_w = target_w - 2 * margin_x
    
    # Зона заголовка (23% высоты)
    title_max_h = int(target_h * MN_TITLE_ZONE_PCT)
    
    # Подбор шрифта
    start_size = int(target_h * CHP_FONT_START_PCT)
    font, lines, line_height, spacing = fit_text_block(
        draw=draw, text=title_text, font_path=FONT_CHP,
        safe_w=safe_w, max_block_h=title_max_h, max_lines=6,
        start_size=start_size, min_size=16,
        line_spacing_ratio=CHP_LINE_SPACING_RATIO
    )
    
    total_h = len(lines) * line_height + (len(lines) - 1) * spacing
    
    # Позиционирование (текст слева)
    if text_position == TEXT_POSITION_TOP:
        y = margin_top
    else:
        y = target_h - margin_bottom - total_h
    
    for line in lines:
        draw.text((margin_x, y), line, font=font, fill="white")
        y += line_height + spacing
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

# =========================
# Шаблон "АМ" (AM)
# =========================
def make_card_am(photo_bytes: bytes, title_text: str,
                 is_square: bool = False) -> BytesIO:
    ensure_fonts()
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), Image.Resampling.LANCZOS)
        target_w, target_h = SQUARE_SIZE, SQUARE_SIZE
    else:
        img = crop_to_4x5(img)
        img = img.resize((STANDARD_W, STANDARD_H), Image.Resampling.LANCZOS)
        target_w, target_h = STANDARD_W, STANDARD_H
    
    # Размытая верхняя полоса
    img = apply_top_blur_band(img)
    
    draw = ImageDraw.Draw(img)
    
    # Отступы по краям
    margin_x = int(target_w * AM_MARGIN_X_PCT)
    band_h = int(target_h * AM_TOP_BLUR_PCT)
    safe_w = target_w - 2 * margin_x
    
    text = (title_text or "").strip().upper()
    
    # Зона текста внутри размытой полосы (минус 12% отступы)
    text_zone_top = int(band_h * AM_TEXT_ZONE_MARGIN_PCT)
    text_zone_bottom = int(band_h * AM_TEXT_ZONE_MARGIN_PCT)
    text_zone_h = max(1, band_h - text_zone_top - text_zone_bottom)
    
    # Подбор шрифта
    start_size = int(target_h * AM_FONT_START_PCT)
    font, lines, line_height, spacing = fit_text_block(
        draw=draw, text=text, font_path=FONT_AM,
        safe_w=safe_w, max_block_h=text_zone_h, max_lines=3,
        start_size=start_size, min_size=20,
        line_spacing_ratio=AM_LINE_SPACING_RATIO
    )
    
    total_h = len(lines) * line_height + (len(lines) - 1) * spacing
    
    # Центрирование по вертикали внутри размытой полосы
    y = text_zone_top + max(0, (text_zone_h - total_h) // 2)
    
    # Центрирование каждой строки по горизонтали
    for line in lines:
        line_w = text_width(draw, line, font)
        x = (target_w - line_w) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height + spacing
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

# =========================
# Шаблон "Сторис ФДР" (FDR_STORY)
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
    
    # Фиолетовая шапка
    canvas.paste(Image.new("RGB", (STORY_W, STORY_HEADER_H), STORY_PURPLE_COLOR), (0, STORY_PHOTO_H))
    draw.rectangle([0, STORY_PHOTO_H + STORY_HEADER_H, STORY_W, STORY_H], fill=(0, 0, 0))
    
    # Заголовок
    title_box = (STORY_PADDING, STORY_PHOTO_H + STORY_PADDING,
                 STORY_W - STORY_PADDING, STORY_PHOTO_H + STORY_HEADER_H - STORY_PADDING)
    
    # Подбор размера шрифта для заголовка
    title_font_size = STORY_TITLE_FONT_MAX
    title_font = ImageFont.truetype(FONT_MONTSERRAT, title_font_size)
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]
    
    # Если не помещается, уменьшаем шрифт
    while (title_w > STORY_W - 2 * STORY_PADDING or title_h > STORY_HEADER_H - 2 * STORY_PADDING) and title_font_size > STORY_TITLE_FONT_MIN:
        title_font_size -= 2
        title_font = ImageFont.truetype(FONT_MONTSERRAT, title_font_size)
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_w = title_bbox[2] - title_bbox[0]
        title_h = title_bbox[3] - title_bbox[1]
    
    title_x = (STORY_W - title_w) // 2
    title_y = STORY_PHOTO_H + (STORY_HEADER_H - title_h) // 2
    draw.text((title_x, title_y), title, font=title_font, fill="white")
    
    # Основной текст с переносом
    body_box = (STORY_PADDING, STORY_PHOTO_H + STORY_HEADER_H + STORY_PADDING,
                STORY_W - STORY_PADDING, STORY_H - STORY_PADDING)
    
    body_font_size = STORY_BODY_FONT_MAX
    body_font = ImageFont.truetype(FONT_MONTSERRAT, body_font_size)
    
    # Перенос текста
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
    line_spacing = int(line_height * 0.10)
    max_body_h = STORY_H - STORY_PHOTO_H - STORY_HEADER_H - 2 * STORY_PADDING
    
    # Если текст не помещается, уменьшаем шрифт
    while len(lines) * (line_height + line_spacing) > max_body_h and body_font_size > STORY_BODY_FONT_MIN:
        body_font_size -= 2
        body_font = ImageFont.truetype(FONT_MONTSERRAT, body_font_size)
        line_height = body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1]
        line_spacing = int(line_height * 0.10)
        
        # Пересобираем строки с новым шрифтом
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
    
    y = body_box[1]
    for line in lines[:15]:
        draw.text((STORY_PADDING, y), line, font=body_font, fill="white")
        y += line_height + line_spacing
    
    out = BytesIO()
    canvas.save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    return out

# =========================
# Шаблон "Пост ФДР" (FDR_POST)
# =========================
def make_card_fdr_post(photo_bytes: bytes, title_text: str,
                       highlight_phrase: str, is_square: bool = False) -> BytesIO:
    ensure_fonts()
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), Image.Resampling.LANCZOS)
        target_w, target_h = SQUARE_SIZE, SQUARE_SIZE
    else:
        img = crop_to_4x5(img)
        img = img.resize((STANDARD_W, STANDARD_H), Image.Resampling.LANCZOS)
        target_w, target_h = STANDARD_W, STANDARD_H
    
    # Яркость 0.85
    img = ImageEnhance.Brightness(img).enhance(0.85)
    
    # Градиент снизу 48%
    img = apply_bottom_gradient(img, height_pct=FDR_POST_GRADIENT_PCT, max_alpha=220)
    
    draw = ImageDraw.Draw(img)
    
    # Отступы
    margin_x = int(target_w * CHP_MARGIN_X_PCT)
    margin_bottom = int(target_h * CHP_MARGIN_BOTTOM_PCT)
    safe_w = target_w - 2 * margin_x
    
    # Зона заголовка
    title_max_h = int(target_h * MN_TITLE_ZONE_PCT)
    
    # Подбор шрифта
    start_size = int(target_h * CHP_FONT_START_PCT)
    font, lines, line_height, spacing = fit_text_block(
        draw=draw, text=title_text, font_path=FONT_CHP,
        safe_w=safe_w, max_block_h=title_max_h, max_lines=6,
        start_size=start_size, min_size=16,
        line_spacing_ratio=CHP_LINE_SPACING_RATIO
    )
    
    total_h = len(lines) * line_height + (len(lines) - 1) * spacing
    base_y = target_h - margin_bottom - total_h
    
    # Подготовка выделенных слов
    highlight_phrase_upper = highlight_phrase.strip().upper()
    highlight_words = set(highlight_phrase_upper.split())
    
    # Рисуем фон (фиолетовые плашки) под выделенными словами
    y = base_y
    for line_idx, line in enumerate(lines):
        line_words = line.split()
        current_x = margin_x
        
        for word in line_words:
            word_bbox = draw.textbbox((current_x, y), word, font=font)
            word_x1, word_y1, word_x2, word_y2 = word_bbox
            
            if word in highlight_words:
                draw.rectangle(
                    [word_x1 - FDR_POST_HIGHLIGHT_PADDING,
                     word_y1 - FDR_POST_HIGHLIGHT_PADDING,
                     word_x2 + FDR_POST_HIGHLIGHT_PADDING,
                     word_y2 + FDR_POST_HIGHLIGHT_PADDING],
                    fill=FDR_POST_PURPLE_COLOR
                )
            
            space_width = text_width(draw, " ", font) if word != line_words[-1] else 0
            current_x += text_width(draw, word, font) + space_width
        
        y += line_height + spacing
    
    # Рисуем текст поверх
    y = base_y
    for line_idx, line in enumerate(lines):
        line_words = line.split()
        current_x = margin_x
        
        for word in line_words:
            draw.text((current_x, y), word, font=font, fill="white")
            space_width = text_width(draw, " ", font) if word != line_words[-1] else 0
            current_x += text_width(draw, word, font) + space_width
        
        y += line_height + spacing
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

# =========================
# Шаблон "МН ТГ" (MN_TG)
# =========================
def make_card_mn_tg(photo_bytes: bytes, title_text: str,
                    text_position: str = TEXT_POSITION_TOP,
                    is_square: bool = False) -> BytesIO:
    ensure_fonts()
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), Image.Resampling.LANCZOS)
        target_w, target_h = SQUARE_SIZE, SQUARE_SIZE
    else:
        img = crop_to_4x5(img)
        img = img.resize((STANDARD_W, STANDARD_H), Image.Resampling.LANCZOS)
        target_w, target_h = STANDARD_W, STANDARD_H
    
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    # Размер шрифта 8% от ширины
    font_size = int(target_w * MN_TG_FONT_SIZE_PCT)
    font = ImageFont.truetype(FONT_MN, font_size)
    
    text = (title_text or "").strip().upper()
    text_bbox = draw.textbbox((0, 0), FOOTER_TEXT, font=font)
    text_width_val = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    
    # Позиционирование
    x = (target_w - text_width_val) // 2
    
    if text_position == TEXT_POSITION_TOP:
        y = int(target_h * MN_TG_TOP_POSITION_PCT) - (text_height // 2)
    else:
        y = int(target_h * MN_TG_BOTTOM_POSITION_PCT) - (text_height // 2)
    
    # Рисуем с прозрачностью 38/255 (~15%)
    draw.text((x, y), FOOTER_TEXT, font=font, fill=(255, 255, 255, MN_TG_TEXT_OPACITY))
    
    result = Image.alpha_composite(img.convert("RGBA"), overlay)
    result = result.convert("RGB")
    
    out = BytesIO()
    result.save(out, format="JPEG", quality=95, optimize=True)
    out.seek(0)
    return out

# =========================
# Шаблон "МН 2" (MN2)
# =========================
def make_card_mn2(photo_bytes: bytes, title_text: str,
                  text_position: str = TEXT_POSITION_TOP,
                  font_size_multiplier: float = 1.0,
                  is_square: bool = False,
                  bold_phrase: str = "") -> BytesIO:
    ensure_fonts()
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), Image.Resampling.LANCZOS)
        target_w, target_h = SQUARE_SIZE, SQUARE_SIZE
    else:
        img = crop_to_4x5(img)
        img = img.resize((STANDARD_W, STANDARD_H), Image.Resampling.LANCZOS)
        target_w, target_h = STANDARD_W, STANDARD_H
    
    # Яркость 0.55
    img = ImageEnhance.Brightness(img).enhance(0.55)
    
    # Градиент
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    else:
        img = apply_bottom_gradient_soft(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    
    draw = ImageDraw.Draw(img)
    
    # Отступы
    margin_x = int(target_w * MN_MARGIN_X_PCT)
    margin_top = int(target_h * MN_MARGIN_TOP_PCT)
    margin_bottom = int(target_h * MN_MARGIN_BOTTOM_PCT)
    safe_w = target_w - 2 * margin_x
    
    # Футер
    footer_size = max(24, int(target_h * MN_FOOTER_SIZE_PCT))
    footer_font = ImageFont.truetype(FONT_MN, footer_size)
    fb = draw.textbbox((0, 0), FOOTER_TEXT, font=footer_font)
    footer_w = fb[2] - fb[0]
    footer_h = fb[3] - fb[1]
    
    # Зона заголовка
    title_max_h = int(target_h * MN_TITLE_ZONE_PCT)
    text = (title_text or "").strip().upper()
    
    # Подготовка выделенных слов
    bold_phrase_upper = bold_phrase.strip().upper() if bold_phrase else ""
    bold_words = set(bold_phrase_upper.split())
    
    # Подбор шрифта с множителем
    base_start_size = int(target_h * MN_FONT_START_PCT)
    adjusted_start_size = int(base_start_size * font_size_multiplier)
    
    font, lines, line_height, spacing = fit_text_block(
        draw=draw, text=text, font_path=FONT_MN,
        safe_w=safe_w, max_block_h=title_max_h, max_lines=6,
        start_size=adjusted_start_size, min_size=16,
        line_spacing_ratio=MN2_LINE_SPACING_RATIO
    )
    
    total_text_height = len(lines) * line_height + (len(lines) - 1) * spacing
    
    # Позиционирование
    if text_position == TEXT_POSITION_TOP:
        title_y = margin_top
        footer_y = target_h - margin_bottom + (margin_bottom - footer_h) // 2
    else:
        title_y = target_h - margin_bottom - total_text_height
        footer_y = margin_top
    
    # Рисуем текст с выделением жирным
    def draw_line_with_bold(line_text: str, x_start: int, y_pos: int):
        words = line_text.split()
        current_x = x_start
        for word in words:
            if word in bold_words:
                bold_font = ImageFont.truetype(FONT_MN_BOLD, font.size)
                draw.text((current_x, y_pos), word, font=bold_font, fill="white")
            else:
                draw.text((current_x, y_pos), word, font=font, fill="white")
            
            if word != words[-1]:
                space_width = text_width(draw, " ", font)
                current_x += text_width(draw, word, font) + space_width
    
    # Центрируем каждую строку
    y = title_y
    for line in lines:
        line_w = sum(text_width(draw, word, font) for word in line.split())
        space_width = text_width(draw, " ", font) * (len(line.split()) - 1)
        total_w = line_w + space_width
        x = (target_w - total_w) // 2
        draw_line_with_bold(line, x, y)
        y += line_height + spacing
    
    # Рисуем футер
    footer_x = (target_w - footer_w) // 2
    draw.text((footer_x, footer_y), FOOTER_TEXT, font=footer_font, fill="white")
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

# =========================
# Основная функция make_card
# =========================
def make_card(photo_bytes: bytes, title_text: str, template: str,
              body_text: str = "", text_position: str = TEXT_POSITION_TOP,
              font_size_multiplier: float = 1.0, is_square: bool = False,
              bold_phrase: str = "", highlight_phrase: str = "") -> BytesIO:
    if template == "CHP":
        return make_card_chp(photo_bytes, title_text, text_position, is_square)
    elif template == "AM":
        return make_card_am(photo_bytes, title_text, is_square)
    elif template == "FDR_STORY":
        return make_card_fdr_story(photo_bytes, title_text, body_text)
    elif template == "FDR_POST":
        return make_card_fdr_post(photo_bytes, title_text, highlight_phrase, is_square)
    elif template == "MN_TG":
        return make_card_mn_tg(photo_bytes, title_text, text_position, is_square)
    elif template == "MN2":
        return make_card_mn2(photo_bytes, title_text, text_position,
                             font_size_multiplier, is_square, bold_phrase)
    else:  # MN
        return make_card_mn(photo_bytes, title_text, text_position, is_square)

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
        InlineKeyboardButton("📱 Сторис ФДР", callback_data="tpl:FDR_STORY"),
        InlineKeyboardButton("💜 Пост ФДР", callback_data="tpl:FDR_POST"),
        InlineKeyboardButton("📱 МН ТГ", callback_data="tpl:MN_TG"),
        InlineKeyboardButton("🆕 МН 2", callback_data="tpl:MN2")
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
@bot.callback_query_handler(func=lambda c: c.data.startswith("tpl:"))
def on_template_select(c):
    uid = c.from_user.id
    template = c.data.split(":", 1)[1]
    st = user_state.get(uid) or {}
    st["template"] = template
    st["is_square"] = False
    user_state[uid] = st
    
    names = {"MN": "МН", "CHP": "ЧП ВМ", "AM": "АМ", "FDR_STORY": "Сторис ФДР",
             "FDR_POST": "Пост ФДР", "MN_TG": "МН ТГ", "MN2": "МН 2"}
    name = names.get(template, template)
    
    if template in ["MN", "CHP", "MN_TG", "MN2"]:
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
    elif template == "FDR_POST":
        st["step"] = "waiting_photo_fdr_post"
        user_state[uid] = st
        bot.answer_callback_query(c.id, f"Шаблон {name} выбран ✅")
        bot.edit_message_text(
            f"💜 Выбран шаблон <b>{name}</b>\n\n📸 Пришли фото.\n\n<i>Дальше:</i>\n1️⃣ Полный заголовок\n2️⃣ Фраза для плашки",
            c.message.chat.id, c.message.message_id, parse_mode="HTML"
        )
    elif template == "MN2":
        st["step"] = "waiting_font_size"
        user_state[uid] = st
        bot.answer_callback_query(c.id, f"Шаблон {name} выбран ✅")
        bot.edit_message_text(
            f"🔤 Настрой размер шрифта (0.5 - 2.0)\n\nТекущий: 100%",
            c.message.chat.id, c.message.message_id
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
        "• 📰 МН — классический\n"
        "• 🚨 ЧП ВМ — яркий\n"
        "• ✨ АМ — с размытием\n"
        "• 📱 Сторис ФДР — формат историй\n"
        "• 💜 Пост ФДР — с фиолетовой плашкой\n"
        "• 📱 МН ТГ — прозрачный текст\n"
        "• 🆕 МН 2 — с жирным выделением\n\n"
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
    
    if step in ["waiting_photo", "waiting_photo_fdr_story", "waiting_photo_fdr_post"]:
        try:
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            photo_bytes = bot.download_file(file_info.file_path)
            
            st["photo_bytes"] = photo_bytes
            
            if step == "waiting_photo_fdr_story":
                st["step"] = "waiting_title_fdr"
                user_state[uid] = st
                bot.reply_to(message, "📸 Фото сохранено!\n\n✏️ Теперь отправь <b>ЗАГОЛОВОК</b> для сторис:", parse_mode="HTML")
            elif step == "waiting_photo_fdr_post":
                st["step"] = "waiting_title_fdr_post"
                user_state[uid] = st
                bot.reply_to(message, "📸 Фото сохранено!\n\n✏️ Теперь отправь <b>ПОЛНЫЙ ЗАГОЛОВОК</b>:", parse_mode="HTML")
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
    
    # FDR_POST: ожидание заголовка
    if st.get("step") == "waiting_title_fdr_post":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        st["title"] = text
        st["step"] = "waiting_highlight_fdr_post"
        user_state[uid] = st
        bot.reply_to(message, "✅ Заголовок сохранён!\n\n✏️ Теперь отправь <b>ФРАЗУ ДЛЯ ФИОЛЕТОВОЙ ПЛАШКИ</b>:", parse_mode="HTML")
        return
    
    # FDR_POST: ожидание фразы для выделения
    if st.get("step") == "waiting_highlight_fdr_post":
        try:
            card = make_card(
                st["photo_bytes"], st["title"], "FDR_POST",
                highlight_phrase=text, is_square=False
            )
            st["card_bytes"] = card.getvalue()
            st["body_raw"] = text
            st["step"] = "waiting_action"
            user_state[uid] = st
            
            caption = build_caption_html(st["title"], text)
            bot.send_photo(
                message.chat.id, photo=BytesIO(st["card_bytes"]),
                caption=caption, parse_mode="HTML", reply_markup=preview_kb()
            )
            bot.reply_to(message, "🎉 <b>Превью готово!</b>\n\nНажми кнопку.", parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    # Обычный шаблон: ожидание заголовка
    if st.get("step") == "waiting_title":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        
        try:
            card = make_card(
                st["photo_bytes"], text, st.get("template", "MN"),
                text_position=st.get("text_position", TEXT_POSITION_TOP),
                is_square=st.get("is_square", False)
            )
            st["card_bytes"] = card.getvalue()
            st["title"] = text
            st["step"] = "waiting_body"
            user_state[uid] = st
            bot.send_document(
                message.chat.id, document=BytesIO(st["card_bytes"]),
                visible_file_name="post.jpg",
                caption="✅ Пост готов!\n\n✏️ Теперь отправь <b>ОСНОВНОЙ ТЕКСТ</b>:",
                parse_mode="HTML"
            )
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    # Обычный шаблон: ожидание текста
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
        bot.reply_to(message, "🎉 <b>Превью готово!</b>\n\nНажми кнопку.", parse_mode="HTML")
        return
    
    # FDR_STORY: ожидание заголовка
    if st.get("step") == "waiting_title_fdr":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        st["title"] = text
        st["step"] = "waiting_body_fdr"
        user_state[uid] = st
        bot.reply_to(message, "✅ Заголовок сохранён!\n\n✏️ Теперь отправь <b>ОСНОВНОЙ ТЕКСТ</b>:", parse_mode="HTML")
        return
    
    # FDR_STORY: ожидание текста
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
    
    # Если бот в состоянии ожидания шаблона
    if st.get("step") == "waiting_template":
        bot.send_message(message.chat.id, "📝 Выбери шаблон кнопками выше ☝️")
        return
    
    # Если бот ожидает позицию текста
    if st.get("step") == "waiting_text_position":
        bot.send_message(message.chat.id, "📐 Выбери расположение текста кнопками выше ☝️")
        return
    
    bot.send_message(message.chat.id, "📝 Нажми «Оформить пост»", reply_markup=main_menu_kb())

# =========================
# Main
# =========================
if __name__ == "__main__":
    import time
    
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
