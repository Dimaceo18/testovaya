# -*- coding: utf-8 -*-
import os
import html
import time
import logging
from io import BytesIO
from typing import Dict, List, Tuple

import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
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
TARGET_W = 1080
TARGET_H = 1350

# Шрифт
FONT_PATH = "Inter-ExtraBold.ttf"
FONT_FALLBACK = "Montserrat-Black.ttf"
FONT_REGULAR = "Inter-Regular.ttf"

# Размеры шрифта
FONT_SIZE_TITLE = 90
FONT_SIZE_MIN = 30
FONT_SIZE_DATE_PLACE = 38
FONT_SIZE_RUBRIC = 48

# Затемнение фото
BRIGHTNESS_FACTOR = 0.85

# Градиент
GRADIENT_HEIGHT_PCT = 0.48
GRADIENT_MAX_ALPHA = 220

# Отступы
MARGIN_TOP_PCT = 0.10
TEXT_MAX_WIDTH_PCT = 0.80
LINE_SPACING_RATIO = 0.22

# Отступ для даты и места
DATE_PLACE_BOTTOM_MARGIN = 160
DATE_PLACE_TOP_MARGIN = 280
DATE_PLACE_LINE_SPACING = 15
DATE_PLACE_LEFT_MARGIN = 70

# Прямоугольник для рубрики
RUBRIC_RECT_MARGIN_RIGHT = 40
RUBRIC_RECT_MARGIN_BOTTOM = 40
RUBRIC_PADDING = 30
RUBRIC_TEXT_COLOR = (0, 0, 0)

# Цвета
TEXT_COLOR = (255, 255, 255)

# Цвета для выделения
HIGHLIGHT_COLORS = {
    "red": (255, 80, 80),
    "yellow": (255, 220, 80),
    "blue": (80, 150, 255)
}

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
def download_fonts():
    fonts = {
        "Inter-ExtraBold.ttf": "https://github.com/rsms/inter/raw/master/docs/fonts/Inter-ExtraBold.otf",
        "Inter-Regular.ttf": "https://github.com/rsms/inter/raw/master/docs/fonts/Inter-Regular.otf",
        "Montserrat-Black.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Black.ttf"
    }
    
    for font_name, url in fonts.items():
        if not os.path.exists(font_name):
            try:
                import requests
                logger.info(f"Downloading {font_name}...")
                response = requests.get(url, timeout=30)
                with open(font_name, "wb") as f:
                    f.write(response.content)
                logger.info(f"Downloaded {font_name}")
            except Exception as e:
                logger.error(f"Failed to download {font_name}: {e}")

def load_font(font_name: str, size: int):
    try:
        return ImageFont.truetype(font_name, size=size)
    except Exception:
        try:
            return ImageFont.truetype(FONT_FALLBACK, size=size)
        except:
            return ImageFont.load_default()

def clear_state(user_id: int):
    if user_id in user_state:
        user_state[user_id] = {"step": "idle"}

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

def apply_gradient(img: Image.Image, direction: str, height_pct: float, max_alpha: int = 220) -> Image.Image:
    w, h = img.size
    gh = int(h * height_pct)
    if gh <= 0:
        return img
    
    overlay_alpha = Image.new("L", (w, h), 0)
    grad = Image.new("L", (1, gh), 0)
    
    for y in range(gh):
        if direction == "top":
            a = int(max_alpha * (1 - y / max(1, gh - 1)))
        else:
            a = int(max_alpha * (y / max(1, gh - 1)))
        grad.putpixel((0, y), a)
    
    grad = grad.resize((w, gh))
    
    if direction == "top":
        overlay_alpha.paste(grad, (0, 0))
    else:
        overlay_alpha.paste(grad, (0, h - gh))
    
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base = img.convert("RGBA")
    overlay = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
    out = Image.alpha_composite(base, overlay)
    return out.convert("RGB")

def text_width(draw, s: str, font) -> int:
    bbox = draw.textbbox((0, 0), s, font=font)
    return bbox[2] - bbox[0]

def wrap_text_center(draw, text: str, font, max_width: int, max_lines: int = 6) -> Tuple[List[str], bool]:
    words = text.split()
    if not words:
        return [""], True

    lines = []
    current = words[0]
    
    for word in words[1:]:
        test = current + " " + word
        if text_width(draw, test, font) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
    lines.append(current)
    
    return lines, True

def fit_text_block_center(draw, text: str, font_path: str, safe_w: int, max_block_h: int,
                          max_lines: int = 6, start_size: int = 90, min_size: int = 16,
                          line_spacing_ratio: float = 0.22):
    text = (text or "").strip()
    if not text:
        text = " "

    size = start_size
    while size >= min_size:
        font = load_font(font_path, size)
        lines, ok = wrap_text_center(draw, text, font, safe_w, max_lines=max_lines)
        spacing = int(size * line_spacing_ratio)

        heights = []
        total_h = 0
        max_w = 0
        for ln in lines:
            bb = draw.textbbox((0, 0), ln, font=font)
            lw = bb[2] - bb[0]
            lh = bb[3] - bb[1]
            heights.append(lh)
            total_h += lh
            max_w = max(max_w, lw)
        total_h += spacing * (len(lines) - 1)

        if ok and max_w <= safe_w and total_h <= max_block_h:
            return font, lines, heights, spacing, total_h

        size -= 2

    font = load_font(font_path, min_size)
    lines, _ = wrap_text_center(draw, text, font, safe_w, max_lines=max_lines)
    spacing = int(min_size * line_spacing_ratio)
    heights = []
    total_h = 0
    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=font)
        lh = bb[3] - bb[1]
        heights.append(lh)
        total_h += lh
    total_h += spacing * (len(lines) - 1)
    return font, lines, heights, spacing, total_h

def draw_text_with_highlight_simple(draw, line: str, highlight_phrase: str, highlight_color, font, x, y):
    """
    ПРОСТЕЙШАЯ функция выделения - гарантированно работает
    """
    # Если нет фразы для выделения
    if not highlight_phrase:
        draw.text((x, y), line, font=font, fill=TEXT_COLOR)
        return y
    
    # Приводим к одному регистру для поиска
    line_lower = line.lower()
    phrase_lower = highlight_phrase.lower()
    
    # Если фразы нет в строке
    if phrase_lower not in line_lower:
        draw.text((x, y), line, font=font, fill=TEXT_COLOR)
        return y
    
    # Находим позицию фразы
    pos = line_lower.find(phrase_lower)
    
    # Разбиваем на три части: ДО, ФРАЗА, ПОСЛЕ
    before = line[:pos]
    highlighted = line[pos:pos + len(highlight_phrase)]
    after = line[pos + len(highlight_phrase):]
    
    # Рисуем
    current_x = x
    if before:
        draw.text((current_x, y), before, font=font, fill=TEXT_COLOR)
        current_x += text_width(draw, before, font)
    
    if highlighted:
        draw.text((current_x, y), highlighted, font=font, fill=highlight_color)
        current_x += text_width(draw, highlighted, font)
    
    if after:
        draw.text((current_x, y), after, font=font, fill=TEXT_COLOR)
    
    return y

def draw_date_place(draw, date: str, place: str, highlight_color, x: int, y: int, max_width: int):
    font = load_font(FONT_REGULAR, FONT_SIZE_DATE_PLACE)
    
    current_y = y
    
    if date:
        date_text = f"ДАТА: {date.upper()}"
        draw.text((x, current_y), date_text, font=font, fill=highlight_color)
        line_bbox = draw.textbbox((0, 0), date_text, font=font)
        current_y += line_bbox[3] - line_bbox[1] + DATE_PLACE_LINE_SPACING
    
    if place:
        place_text = f"МЕСТО: {place.upper()}"
        if text_width(draw, place_text, font) > max_width:
            label = "МЕСТО: "
            value = place.upper()
            value_lines = wrap_text_center(draw, value, font, max_width - text_width(draw, label, font), 3)[0]
            draw.text((x, current_y), label, font=font, fill=highlight_color)
            label_width = text_width(draw, label, font)
            for i, line in enumerate(value_lines):
                if i == 0:
                    draw.text((x + label_width, current_y), line, font=font, fill=TEXT_COLOR)
                else:
                    draw.text((x + label_width, current_y), line, font=font, fill=TEXT_COLOR)
                line_bbox = draw.textbbox((0, 0), line, font=font)
                current_y += line_bbox[3] - line_bbox[1] + 5
        else:
            draw.text((x, current_y), place_text, font=font, fill=highlight_color)

def draw_rubric(draw, rubric: str, highlight_color, y_level: int):
    if not rubric:
        return
    
    font_rubric = load_font(FONT_PATH, FONT_SIZE_RUBRIC)
    rubric_upper = rubric.upper()
    
    text_bbox = draw.textbbox((0, 0), rubric_upper, font=font_rubric)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    
    rect_w = text_w + RUBRIC_PADDING * 2
    rect_h = text_h + RUBRIC_PADDING * 2
    
    rect_x = TARGET_W - rect_w - RUBRIC_RECT_MARGIN_RIGHT
    rect_y = y_level
    
    draw.rectangle([rect_x, rect_y, rect_x + rect_w, rect_y + rect_h], fill=highlight_color)
    
    text_x = rect_x + (rect_w - text_w) // 2
    text_y = rect_y + (rect_h - text_h) // 2
    draw.text((text_x, text_y), rubric_upper, font=font_rubric, fill=RUBRIC_TEXT_COLOR)

def create_poster_chp(image_bytes: bytes, title_text: str, text_position: str,
                      date: str = "", place: str = "", rubric: str = "",
                      highlight_phrase: str = "", highlight_color: tuple = None) -> BytesIO:
    
    if highlight_color is None:
        highlight_color = HIGHLIGHT_COLORS["yellow"]
    
    # Логируем для отладки
    logger.info(f"=== CREATE POSTER ===")
    logger.info(f"Title: {title_text[:50]}...")
    logger.info(f"Highlight phrase: '{highlight_phrase}'")
    logger.info(f"Highlight color: {highlight_color}")
    
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
    
    if text_position == "top":
        img = apply_gradient(img, "top", GRADIENT_HEIGHT_PCT, GRADIENT_MAX_ALPHA)
    else:
        img = apply_gradient(img, "bottom", GRADIENT_HEIGHT_PCT, GRADIENT_MAX_ALPHA)
    
    draw = ImageDraw.Draw(img)
    
    margin_top = int(TARGET_H * MARGIN_TOP_PCT)
    max_text_width = int(TARGET_W * TEXT_MAX_WIDTH_PCT)
    
    text = (title_text or "").strip().upper()
    title_max_h = int(TARGET_H * 0.23)
    
    font, lines, heights, spacing, total_h = fit_text_block_center(
        draw=draw,
        text=text,
        font_path=FONT_PATH,
        safe_w=max_text_width,
        max_block_h=title_max_h,
        max_lines=6,
        start_size=int(TARGET_H * 0.11),
        min_size=FONT_SIZE_MIN,
        line_spacing_ratio=LINE_SPACING_RATIO
    )
    
    logger.info(f"Lines to draw: {lines}")
    
    if text_position == "top":
        y = margin_top
        for i, ln in enumerate(lines):
            line_w = text_width(draw, ln, font)
            x = (TARGET_W - line_w) // 2
            draw_text_with_highlight_simple(draw, ln, highlight_phrase, highlight_color, font, x, y)
            y += heights[i] + spacing
        
        date_place_y = TARGET_H - DATE_PLACE_BOTTOM_MARGIN
        if date or place:
            draw_date_place(draw, date, place, highlight_color, DATE_PLACE_LEFT_MARGIN, date_place_y, max_text_width)
            draw_rubric(draw, rubric, highlight_color, date_place_y)
    
    else:
        date_place_y = DATE_PLACE_TOP_MARGIN
        if date or place:
            draw_date_place(draw, date, place, highlight_color, DATE_PLACE_LEFT_MARGIN, date_place_y, max_text_width)
            draw_rubric(draw, rubric, highlight_color, date_place_y)
            y = date_place_y + 250
        else:
            y = margin_top
        
        for i, ln in enumerate(lines):
            line_w = text_width(draw, ln, font)
            x = (TARGET_W - line_w) // 2
            draw_text_with_highlight_simple(draw, ln, highlight_phrase, highlight_color, font, x, y)
            y += heights[i] + spacing
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0)
    out.seek(0)
    return out

# =========================
# Keyboard
# =========================
def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("🚨 Шаблон ЧП ВМ"))
    return kb

def text_position_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⬆️ Текст сверху", callback_data="text_pos:top"),
        InlineKeyboardButton("⬇️ Текст снизу", callback_data="text_pos:bottom")
    )
    return kb

def add_date_place_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да, добавить", callback_data="date_place:yes"),
        InlineKeyboardButton("➖ Нет, пропустить", callback_data="date_place:no")
    )
    return kb

def color_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🔴 Красный", callback_data="color:red"),
        InlineKeyboardButton("🟡 Желтый", callback_data="color:yellow"),
        InlineKeyboardButton("🔵 Голубой", callback_data="color:blue")
    )
    kb.add(InlineKeyboardButton("➖ Без выделения", callback_data="color:none"))
    return kb

def preview_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    )
    return kb

# =========================
# Callback handlers
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("text_pos:"))
def on_text_position(c):
    uid = c.from_user.id
    position = c.data.split(":")[1]
    st = user_state.get(uid) or {}
    st["text_position"] = position
    st["step"] = "waiting_title"
    user_state[uid] = st
    
    pos_text = "сверху" if position == "top" else "снизу"
    bot.answer_callback_query(c.id, f"Текст будет {pos_text} ✅")
    bot.edit_message_text(
        f"✅ Текст будет расположен <b>{pos_text}</b>\n\n✏️ Теперь отправь <b>ЗАГОЛОВОК</b>:",
        c.message.chat.id, c.message.message_id,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("date_place:"))
def on_date_place_choice(c):
    uid = c.from_user.id
    choice = c.data.split(":")[1]
    st = user_state.get(uid) or {}
    
    if choice == "yes":
        st["step"] = "waiting_date"
        user_state[uid] = st
        bot.answer_callback_query(c.id, "Добавляем дату и место ✅")
        bot.edit_message_text(
            f"✏️ <b>Введи ДАТУ</b>:",
            c.message.chat.id, c.message.message_id,
            parse_mode="HTML"
        )
    else:
        st["date"] = ""
        st["place"] = ""
        st["step"] = "waiting_highlight_phrase"
        user_state[uid] = st
        
        try:
            card = create_poster_chp(
                st["photo_bytes"], st.get("title", ""), st.get("text_position", "top"),
                "", "", "", "", None
            )
            st["preview_bytes"] = card.getvalue()
            user_state[uid] = st
            
            bot.send_photo(c.message.chat.id, photo=BytesIO(st["preview_bytes"]),
                caption=f"✅ <b>Предпросмотр</b>\n\n✏️ <b>Напиши ФРАЗУ для выделения</b>\n(или «-» чтобы пропустить):\n\n💡 Фраза ищется без учета регистра",
                parse_mode="HTML")
            bot.delete_message(c.message.chat.id, c.message.message_id)
        except Exception as e:
            bot.send_message(c.message.chat.id, f"❌ Ошибка: {e}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("color:"))
def on_color_select(c):
    uid = c.from_user.id
    color_key = c.data.split(":")[1]
    st = user_state.get(uid) or {}
    
    if color_key == "none":
        st["highlight_phrase"] = ""
        st["highlight_color"] = None
        bot.answer_callback_query(c.id, "Без выделения ✅")
    else:
        st["highlight_phrase"] = st.get("temp_highlight_phrase", "")
        st["highlight_color"] = HIGHLIGHT_COLORS.get(color_key)
        color_names = {"red": "красный", "yellow": "желтый", "blue": "голубой"}
        bot.answer_callback_query(c.id, f"Выбран {color_names.get(color_key)} цвет ✅")
    
    st["step"] = "waiting_rubric"
    user_state[uid] = st
    
    bot.send_message(c.message.chat.id, f"✏️ <b>Введи РУБРИКУ</b> (слово на цветном прямоугольнике):", parse_mode="HTML")
    bot.delete_message(c.message.chat.id, c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data in ["publish", "cancel"])
def on_action(call):
    uid = call.from_user.id
    st = user_state.get(uid)
    
    if not st or st.get("step") != "waiting_action":
        bot.answer_callback_query(call.id, "Нет активного превью")
        return
    
    if call.data == "publish":
        try:
            if CHANNEL:
                bot.send_photo(CHANNEL, BytesIO(st["card_bytes"]))
                bot.answer_callback_query(call.id, "Опубликовано ✅")
                bot.send_message(call.message.chat.id, "✅ Готово!", reply_markup=main_menu_kb())
            else:
                bot.answer_callback_query(call.id, "❌ CHANNEL_USERNAME не задан")
            clear_state(uid)
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ Ошибка: {e}")
    
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
        "👋 <b>Бот для оформления постов</b>\n\n"
        "📝 Отправь фото и следуй инструкциям.\n\n"
        "Нажми «🚨 Шаблон ЧП ВМ» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda message: message.text == "🚨 Шаблон ЧП ВМ")
def handle_template_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_photo"}
    bot.send_message(message.chat.id, "🚨 <b>Шаблон ЧП ВМ</b>\n\n📸 Пришли фото:", parse_mode="HTML")

@bot.message_handler(content_types=["photo"])
def on_photo(message):
    uid = message.from_user.id
    st = user_state.get(uid) or {}
    
    if st.get("step") == "waiting_photo":
        try:
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            photo_bytes = bot.download_file(file_info.file_path)
            
            st["photo_bytes"] = photo_bytes
            st["step"] = "waiting_text_position"
            user_state[uid] = st
            
            bot.reply_to(message, "📸 Фото сохранено!\n\n📐 <b>Выбери расположение текста:</b>",
                parse_mode="HTML", reply_markup=text_position_kb())
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Нажми «🚨 Шаблон ЧП ВМ»")

@bot.message_handler(content_types=["text"])
def on_text(message):
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state.get(uid) or {"step": "idle"}
    step = st.get("step")
    
    if step == "waiting_title":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        
        st["title"] = text
        st["step"] = "waiting_date_place_choice"
        user_state[uid] = st
        
        bot.reply_to(message, f"✅ Заголовок: <b>{html.escape(text)}</b>\n\n📅 <b>Добавить дату и место?</b>",
            parse_mode="HTML", reply_markup=add_date_place_kb())
        return
    
    if step == "waiting_date":
        st["date"] = text
        st["step"] = "waiting_place"
        user_state[uid] = st
        bot.reply_to(message, f"✅ Дата: {text}\n\n✏️ <b>Введи МЕСТО</b>:", parse_mode="HTML")
        return
    
    if step == "waiting_place":
        st["place"] = text
        st["step"] = "waiting_highlight_phrase"
        user_state[uid] = st
        
        try:
            card = create_poster_chp(
                st["photo_bytes"], st.get("title", ""), st.get("text_position", "top"),
                st.get("date", ""), st.get("place", ""), "", "", None
            )
            st["preview_bytes"] = card.getvalue()
            user_state[uid] = st
            
            bot.send_photo(message.chat.id, photo=BytesIO(st["preview_bytes"]),
                caption=f"✅ <b>Предпросмотр</b>\n\n✏️ <b>Напиши ФРАЗУ для выделения цветом</b>\n(или «-» чтобы пропустить):\n\n💡 Фраза ищется без учета регистра\nПример: если в тексте есть слово «НОВОСТИ», отправь «новости»",
                parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    if step == "waiting_highlight_phrase":
        if text == "-":
            st["highlight_phrase"] = ""
            st["highlight_color"] = None
            st["step"] = "waiting_rubric"
            user_state[uid] = st
            bot.reply_to(message, f"✏️ <b>Введи РУБРИКУ</b>:", parse_mode="HTML")
        else:
            st["temp_highlight_phrase"] = text
            st["step"] = "waiting_color"
            user_state[uid] = st
            
            # Показываем пример выделения
            example_text = st.get("title", "").upper()
            if text.upper() in example_text:
                preview_text = f"✅ Фраза «{text}» НАЙДЕНА в заголовке!"
            else:
                preview_text = f"⚠️ Фраза «{text}» НЕ НАЙДЕНА в заголовке. Проверь написание."
            
            bot.reply_to(message, 
                f"✏️ Фраза: <b>{html.escape(text)}</b>\n\n{preview_text}\n\n🎨 <b>Выбери цвет выделения:</b>",
                parse_mode="HTML", reply_markup=color_kb())
        return
    
    if step == "waiting_rubric":
        st["rubric"] = text
        st["step"] = "creating"
        user_state[uid] = st
        
        try:
            card = create_poster_chp(
                st["photo_bytes"], st.get("title", ""), st.get("text_position", "top"),
                st.get("date", ""), st.get("place", ""), st.get("rubric", ""),
                st.get("highlight_phrase", ""), st.get("highlight_color")
            )
            
            st["card_bytes"] = card.getvalue()
            st["step"] = "waiting_action"
            user_state[uid] = st
            
            bot.send_photo(message.chat.id, photo=BytesIO(st["card_bytes"]),
                caption="🎉 <b>Карточка готова!</b>\n\nНажми кнопку для публикации:",
                parse_mode="HTML", reply_markup=preview_kb())
        except Exception as e:
            logger.error(f"Error: {e}")
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    bot.send_message(message.chat.id, "📝 Нажми «🚨 Шаблон ЧП ВМ»", reply_markup=main_menu_kb())

# =========================
# Main
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")
    download_fonts()
    
    time.sleep(2)
    
    try:
        bot.remove_webhook()
        logger.info("Webhook removed")
    except Exception as e:
        logger.warning(f"Webhook error: {e}")
    
    time.sleep(1)
    
    logger.info("✅ Bot started!")
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            if "409" in str(e):
                logger.info("Conflict, waiting 20s...")
                time.sleep(20)
            else:
                time.sleep(5)
