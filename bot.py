# -*- coding: utf-8 -*-
import os
import re
import html
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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL = os.getenv("CHANNEL_USERNAME", "").strip()
SUGGEST_URL = os.getenv("SUGGEST_URL", "")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

if CHANNEL and not CHANNEL.startswith("@"):
    CHANNEL = "@" + CHANNEL

MAX_FILE_SIZE = 20 * 1024 * 1024
TARGET_W, TARGET_H = 750, 938
STORY_W, STORY_H = 720, 1280

# Шрифты
FONT_MN = "CaviarDreams.ttf"
FONT_MN_BOLD = "CaviarDreams_Bold.ttf"
FONT_CHP = "Montserrat-Black.ttf"
FONT_AM = "IntroInline.ttf"
FONT_MONTSERRAT = "Montserrat-Regular.ttf"

FOOTER_TEXT = "MINSK NEWS"
MN_TITLE_ZONE_PCT = 0.30
CHP_GRADIENT_PCT = 0.48
AM_TOP_BLUR_PCT = 0.20
AM_BLUR_RADIUS = 18
AM_BLUR_BLEND = 0.50

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
# Session
# =========================
SESSION = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
SESSION.mount("http://", adapter)
SESSION.mount("https://", adapter)

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

def text_width(draw, s, font):
    bb = draw.textbbox((0, 0), s, font=font)
    return bb[2] - bb[0]

def wrap_text_uniform(draw, text, font, max_width, max_lines=5):
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
                dots = "..."
                last_line = lines[-1]
                if text_width(draw, last_line + dots, font) <= max_width:
                    lines[-1] = last_line + dots
                return lines, False
    
    if current_line:
        if len(lines) < max_lines:
            lines.append(current_line)
        else:
            dots = "..."
            if text_width(draw, lines[-1] + dots, font) <= max_width:
                lines[-1] = lines[-1] + dots
    
    return lines, len(lines) <= max_lines

def fit_text_block_uniform(draw, text, font_path, safe_w, max_block_h, max_lines=5, start_size=90, min_size=16, line_spacing_ratio=0.12):
    text = (text or "").strip().upper()
    if not text:
        text = " "
    
    size = start_size
    best_font = None
    best_lines = []
    best_line_height = 0
    
    while size >= min_size:
        try:
            font = ImageFont.truetype(font_path, size)
            lines, _ = wrap_text_uniform(draw, text, font, safe_w, max_lines)
            
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
                break
            size -= 2
        except:
            size -= 2
    
    if best_font is None:
        best_font = ImageFont.truetype(font_path, min_size)
        best_lines, _ = wrap_text_uniform(draw, text, best_font, safe_w, max_lines)
        bbox = draw.textbbox((0, 0), "A", font=best_font)
        best_line_height = bbox[3] - bbox[1]
    
    return best_font, best_lines, best_line_height

# =========================
# Image processing
# =========================
def crop_to_4x5(img):
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

def apply_top_gradient(img, height_pct, max_alpha=165):
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

def apply_bottom_gradient(img, height_pct, max_alpha=220):
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

def apply_top_blur_band(img, band_pct=AM_TOP_BLUR_PCT, radius=AM_BLUR_RADIUS, blend=AM_BLUR_BLEND):
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
# Card making
# =========================
def make_card_mn(photo_bytes, title_text, text_position=TEXT_POSITION_TOP):
    ensure_fonts()
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(0.55)
    
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    else:
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    
    draw = ImageDraw.Draw(img)
    margin_x = int(img.width * 0.06)
    margin_top = int(img.height * 0.06)
    margin_bottom = int(img.height * 0.07)
    safe_w = img.width - 2 * margin_x
    
    footer_size = max(24, int(img.height * 0.034))
    footer_font = ImageFont.truetype(FONT_MN, footer_size)
    fb = draw.textbbox((0, 0), FOOTER_TEXT, font=footer_font)
    footer_w = fb[2] - fb[0]
    footer_h = fb[3] - fb[1]
    
    title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
    
    font, lines, line_height = fit_text_block_uniform(
        draw=draw, text=title_text, font_path=FONT_MN,
        safe_w=safe_w, max_block_h=title_max_h, max_lines=5,
        start_size=int(img.height * 0.10), min_size=20, line_spacing_ratio=0.12
    )
    
    line_spacing = int(line_height * 0.12)
    total_text_height = len(lines) * line_height + (len(lines) - 1) * line_spacing
    
    if text_position == TEXT_POSITION_TOP:
        title_y = margin_top
        footer_y = img.height - margin_bottom + (margin_bottom - footer_h) // 2
    else:
        title_y = img.height - margin_bottom - total_text_height
        footer_y = margin_top
    
    y = title_y
    for line in lines:
        line_w = text_width(draw, line, font)
        x = (img.width - line_w) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height + line_spacing
    
    footer_x = (img.width - footer_w) // 2
    draw.text((footer_x, footer_y), FOOTER_TEXT, font=footer_font, fill="white")
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

def make_card_chp(photo_bytes, title_text, text_position=TEXT_POSITION_TOP):
    ensure_fonts()
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(0.85)
    
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
    else:
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
    
    draw = ImageDraw.Draw(img)
    margin_x = int(img.width * 0.06)
    margin_bottom = int(img.height * 0.08)
    margin_top = int(img.height * 0.08)
    safe_w = img.width - 2 * margin_x
    
    title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
    text = (title_text or "").strip().upper()
    
    font, lines, line_height = fit_text_block_uniform(
        draw=draw, text=text, font_path=FONT_CHP,
        safe_w=safe_w, max_block_h=title_max_h, max_lines=5,
        start_size=int(img.height * 0.11), min_size=20, line_spacing_ratio=0.12
    )
    
    line_spacing = int(line_height * 0.12)
    total_h = len(lines) * line_height + (len(lines) - 1) * line_spacing
    
    if text_position == TEXT_POSITION_TOP:
        y = margin_top
    else:
        y = img.height - margin_bottom - total_h
    
    for line in lines:
        draw.text((margin_x, y), line, font=font, fill="white")
        y += line_height + line_spacing
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

def make_card_am(photo_bytes, title_text):
    ensure_fonts()
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    img = apply_top_blur_band(img)
    
    draw = ImageDraw.Draw(img)
    margin_x = int(img.width * 0.055)
    band_h = int(img.height * AM_TOP_BLUR_PCT)
    safe_w = img.width - 2 * margin_x
    text = (title_text or "").strip().upper()
    
    text_zone_top = int(band_h * 0.12)
    text_zone_bottom = int(band_h * 0.12)
    text_zone_h = max(1, band_h - text_zone_top - text_zone_bottom)
    
    font, lines, line_height = fit_text_block_uniform(
        draw=draw, text=text, font_path=FONT_AM,
        safe_w=safe_w, max_block_h=text_zone_h, max_lines=3,
        start_size=int(img.height * 0.060), min_size=20, line_spacing_ratio=0.10
    )
    
    line_spacing = int(line_height * 0.10)
    total_h = len(lines) * line_height + (len(lines) - 1) * line_spacing
    y = text_zone_top + max(0, (text_zone_h - total_h) // 2)
    
    for line in lines:
        lw = text_width(draw, line, font)
        x = (img.width - lw) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height + line_spacing
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out

def make_card_fdr_story(photo_bytes, title, body_text):
    ensure_fonts()
    canvas = Image.new("RGB", (STORY_W, STORY_H), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    photo_h = 410
    header_h = 220
    photo = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    def fit_cover(im, target_w, target_h):
        src_w, src_h = im.size
        scale = max(target_w / src_w, target_h / src_h)
        nw, nh = int(src_w * scale), int(src_h * scale)
        resized = im.resize((nw, nh), Image.LANCZOS)
        left = max(0, (nw - target_w) // 2)
        top = max(0, (nh - target_h) // 2)
        return resized.crop((left, top, left + target_w, top + target_h))
    
    story_photo = fit_cover(photo, STORY_W, photo_h)
    canvas.paste(story_photo, (0, 0))
    purple_color = (122, 58, 240)
    canvas.paste(Image.new("RGB", (STORY_W, header_h), purple_color), (0, photo_h))
    draw.rectangle([0, photo_h + header_h, STORY_W, STORY_H], fill=(0, 0, 0))
    
    padding = 34
    title_font = ImageFont.truetype(FONT_MONTSERRAT, 40)
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]
    title_x = (STORY_W - title_w) // 2
    title_y = photo_h + (header_h - title_h) // 2
    draw.text((title_x, title_y), title, font=title_font, fill="white")
    
    body_font = ImageFont.truetype(FONT_MONTSERRAT, 28)
    words = body_text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = current_line + " " + word if current_line else word
        test_bbox = draw.textbbox((0, 0), test_line, font=body_font)
        if test_bbox[2] - test_bbox[0] <= STORY_W - 2 * padding:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    
    line_height = body_font.getbbox("Ag")[3] - body_font.getbbox("Ag")[1]
    line_spacing = 10
    y = photo_h + header_h + padding
    for line in lines[:10]:
        draw.text((padding, y), line, font=body_font, fill="white")
        y += line_height + line_spacing
    
    out = BytesIO()
    canvas.save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    return out

def make_card(photo_bytes, title_text, template, body_text="", text_position=TEXT_POSITION_TOP):
    if template == "CHP":
        return make_card_chp(photo_bytes, title_text, text_position)
    elif template == "AM":
        return make_card_am(photo_bytes, title_text)
    elif template == "FDR_STORY":
        return make_card_fdr_story(photo_bytes, title_text, body_text)
    else:
        return make_card_mn(photo_bytes, title_text, text_position)

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
    if SUGGEST_URL:
        kb.add(InlineKeyboardButton("📝 Предложить новость", url=SUGGEST_URL))
    return kb

def build_caption_html(title, body):
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
    user_state[uid] = st
    
    names = {"MN": "МН", "CHP": "ЧП ВМ", "AM": "АМ", "FDR_STORY": "Сторис ФДР"}
    name = names.get(template, template)
    
    if template in ["MN", "CHP"]:
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
    else:
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
            
            if len(photo_bytes) > MAX_FILE_SIZE:
                bot.reply_to(message, "❌ Файл слишком большой (макс 20MB)")
                return
            
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
    
    # Обычный шаблон: ожидание заголовка
    if st.get("step") == "waiting_title":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        
        try:
            card = make_card(
                st["photo_bytes"], text, st.get("template", "MN"),
                text_position=st.get("text_position", TEXT_POSITION_TOP)
            )
            st["card_bytes"] = card.getvalue()
            st["title"] = text
            st["step"] = "waiting_body"
            user_state[uid] = st
            bot.send_document(message.chat.id, document=BytesIO(st["card_bytes"]), visible_file_name="post.jpg",
                              caption="✅ Пост готов!\n\n✏️ Теперь отправь <b>ОСНОВНОЙ ТЕКСТ</b>:", parse_mode="HTML")
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
        bot.send_photo(message.chat.id, photo=BytesIO(st["card_bytes"]), caption=caption,
                       parse_mode="HTML", reply_markup=preview_kb())
        bot.reply_to(message, "🎉 <b>Превью готово!</b>\n\nНажми кнопку под фото.", parse_mode="HTML")
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
            bot.send_photo(message.chat.id, photo=BytesIO(st["card_bytes"]), caption=caption,
                           parse_mode="HTML", reply_markup=preview_kb())
            bot.reply_to(message, "🎉 <b>Превью сторис готово!</b>\n\nНажми кнопку.", parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    # Если бот в состоянии ожидания шаблона, напоминаем
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
    logger.info("🚀 Starting bot on Background Worker...")
    ensure_fonts()
    logger.info("✅ Bot started polling!")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
