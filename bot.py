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
FONT_SIZE_EMOJI = 48      # размер для смайлика

# Затемнение фото
BRIGHTNESS_FACTOR = 0.85

# Градиент
GRADIENT_HEIGHT_PCT = 0.48
GRADIENT_MAX_ALPHA = 220

# Отступы
MARGIN_TOP_PCT = 0.15
TEXT_MAX_WIDTH_PCT = 0.80
LINE_SPACING_RATIO = 0.22

# Отступ для даты и места
DATE_PLACE_BOTTOM_MARGIN = 160
DATE_PLACE_TOP_MARGIN = 280
DATE_PLACE_LINE_SPACING = 15
DATE_PLACE_LEFT_MARGIN = 70

# Прямоугольник для рубрики (вверху по центру)
RUBRIC_TOP_MARGIN = 60
RUBRIC_PADDING = 30
RUBRIC_TEXT_COLOR = (0, 0, 0)

# Цвета
TEXT_COLOR = (255, 255, 255)
DATE_VALUE_COLOR = (255, 255, 255)

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

def draw_highlighted_text(draw, text: str, highlight_word: str, color, font, x, y):
    if not highlight_word:
        draw.text((x, y), text, font=font, fill=TEXT_COLOR)
        return
    
    text_lower = text.lower()
    word_lower = highlight_word.lower()
    
    if word_lower not in text_lower:
        draw.text((x, y), text, font=font, fill=TEXT_COLOR)
        return
    
    pos = text_lower.find(word_lower)
    
    before = text[:pos]
    word_part = text[pos:pos + len(highlight_word)]
    after = text[pos + len(highlight_word):]
    
    current_x = x
    if before:
        draw.text((current_x, y), before, font=font, fill=TEXT_COLOR)
        current_x += text_width(draw, before, font)
    
    if word_part:
        draw.text((current_x, y), word_part, font=font, fill=color)
        current_x += text_width(draw, word_part, font)
    
    if after:
        draw.text((current_x, y), after, font=font, fill=TEXT_COLOR)

def draw_date_place(draw, date: str, place: str, highlight_color, x: int, y: int, max_width: int):
    font = load_font(FONT_REGULAR, FONT_SIZE_DATE_PLACE)
    
    current_y = y
    
    if date:
        draw.text((x, current_y), "ДАТА:", font=font, fill=highlight_color)
        label_width = text_width(draw, "ДАТА:", font)
        date_value = f" {date.upper()}"
        draw.text((x + label_width, current_y), date_value, font=font, fill=DATE_VALUE_COLOR)
        line_bbox = draw.textbbox((0, 0), f"ДАТА: {date.upper()}", font=font)
        current_y += line_bbox[3] - line_bbox[1] + DATE_PLACE_LINE_SPACING
    
    if place:
        draw.text((x, current_y), "МЕСТО:", font=font, fill=highlight_color)
        label_width = text_width(draw, "МЕСТО:", font)
        place_value = f" {place.upper()}"
        
        full_text = f"МЕСТО: {place.upper()}"
        if text_width(draw, full_text, font) <= max_width:
            draw.text((x + label_width, current_y), place_value, font=font, fill=DATE_VALUE_COLOR)
        else:
            draw.text((x, current_y), "МЕСТО:", font=font, fill=highlight_color)
            value_lines = wrap_text_center(draw, place.upper(), font, max_width - label_width - 20, 3)[0]
            val_y = current_y
            for i, line in enumerate(value_lines):
                if i == 0:
                    draw.text((x + label_width + 15, val_y), line, font=font, fill=DATE_VALUE_COLOR)
                else:
                    draw.text((x + label_width + 15, val_y), line, font=font, fill=DATE_VALUE_COLOR)
                line_bbox = draw.textbbox((0, 0), line, font=font)
                val_y += line_bbox[3] - line_bbox[1] + 5

def draw_rubric_top_center(draw, rubric: str, emoji: str, highlight_color):
    """
    Рисует прямоугольник с рубрикой вверху по центру.
    Смайлик в левом верхнем углу прямоугольника.
    Текст идеально отцентрирован с одинаковыми отступами.
    """
    if not rubric:
        return 0
    
    font_rubric = load_font(FONT_PATH, FONT_SIZE_RUBRIC)
    font_emoji = load_font(FONT_PATH, FONT_SIZE_EMOJI)
    
    rubric_text = rubric.upper()
    emoji_text = emoji if emoji else ""
    
    # Получаем размеры текста рубрики
    rubric_bbox = draw.textbbox((0, 0), rubric_text, font=font_rubric)
    rubric_w = rubric_bbox[2] - rubric_bbox[0]
    rubric_h = rubric_bbox[3] - rubric_bbox[1]
    
    # Получаем размеры смайлика
    emoji_w = 0
    emoji_h = 0
    if emoji_text:
        emoji_bbox = draw.textbbox((0, 0), emoji_text, font=font_emoji)
        emoji_w = emoji_bbox[2] - emoji_bbox[0]
        emoji_h = emoji_bbox[3] - emoji_bbox[1]
    
    # Общая ширина: отступ слева + смайлик + отступ между + рубрика + отступ справа
    # Делаем отступы одинаковыми со всех сторон
    padding = RUBRIC_PADDING
    
    # Ширина смайлика с отступом слева
    emoji_total_w = emoji_w + padding if emoji_text else 0
    
    # Ширина рубрики
    rubric_total_w = rubric_w + padding * 2 if not emoji_text else rubric_w + padding
    
    # Общая ширина прямоугольника
    if emoji_text:
        rect_w = emoji_w + padding + rubric_w + padding
    else:
        rect_w = rubric_w + padding * 2
    
    rect_h = max(rubric_h, emoji_h) + padding * 2
    
    # Позиция прямоугольника по центру
    rect_x = (TARGET_W - rect_w) // 2
    rect_y = RUBRIC_TOP_MARGIN
    
    # Рисуем прямоугольник
    draw.rectangle([rect_x, rect_y, rect_x + rect_w, rect_y + rect_h], fill=highlight_color)
    
    # Рисуем смайлик (в левом верхнем углу, с отступом padding)
    if emoji_text:
        emoji_x = rect_x + padding
        emoji_y = rect_y + (rect_h - emoji_h) // 2
        draw.text((emoji_x, emoji_y), emoji_text, font=font_emoji, fill=RUBRIC_TEXT_COLOR)
    
    # Рисуем текст рубрики (после смайлика или по центру)
    if emoji_text:
        text_x = rect_x + padding + emoji_w + padding
    else:
        text_x = rect_x + (rect_w - rubric_w) // 2
    
    text_y = rect_y + (rect_h - rubric_h) // 2
    draw.text((text_x, text_y), rubric_text, font=font_rubric, fill=RUBRIC_TEXT_COLOR)
    
    return rect_y + rect_h

def create_poster_chp(image_bytes: bytes, title_text: str, text_position: str,
                      date: str = "", place: str = "", rubric: str = "", emoji: str = "",
                      highlight_word: str = "", highlight_color: tuple = None) -> BytesIO:
    
    if highlight_color is None:
        highlight_color = HIGHLIGHT_COLORS["yellow"]
    
    logger.info(f"=== CREATE POSTER ===")
    logger.info(f"Highlight word: '{highlight_word}'")
    logger.info(f"Rubric: '{rubric}', Emoji: '{emoji}'")
    
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
    
    if text_position == "top":
        img = apply_gradient(img, "top", GRADIENT_HEIGHT_PCT, GRADIENT_MAX_ALPHA)
    else:
        img = apply_gradient(img, "bottom", GRADIENT_HEIGHT_PCT, GRADIENT_MAX_ALPHA)
    
    draw = ImageDraw.Draw(img)
    
    # Рисуем рубрику с эмодзи
    rubric_bottom = 0
    if rubric:
        rubric_bottom = draw_rubric_top_center(draw, rubric, emoji, highlight_color)
    
    # Отступ для заголовка
    margin_top = int(TARGET_H * MARGIN_TOP_PCT)
    if rubric_bottom > 0:
        margin_top = rubric_bottom + 60
    else:
        margin_top = 200
    
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
    
    if text_position == "top":
        y = margin_top
        for i, ln in enumerate(lines):
            line_w = text_width(draw, ln, font)
            x = (TARGET_W - line_w) // 2
            draw_highlighted_text(draw, ln, highlight_word, highlight_color, font, x, y)
            y += heights[i] + spacing
        
        date_place_y = TARGET_H - DATE_PLACE_BOTTOM_MARGIN
        if date or place:
            draw_date_place(draw, date, place, highlight_color, DATE_PLACE_LEFT_MARGIN, date_place_y, max_text_width)
    
    else:
        date_place_y = DATE_PLACE_TOP_MARGIN
        if date or place:
            draw_date_place(draw, date, place, highlight_color, DATE_PLACE_LEFT_MARGIN, date_place_y, max_text_width)
            y = date_place_y + 200
        else:
            y = margin_top
        
        for i, ln in enumerate(lines):
            line_w = text_width(draw, ln, font)
            x = (TARGET_W - line_w) // 2
            draw_highlighted_text(draw, ln, highlight_word, highlight_color, font, x, y)
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
        st["step"] = "waiting_highlight_word"
        user_state[uid] = st
        
        try:
            card = create_poster_chp(
                st["photo_bytes"], st.get("title", ""), st.get("text_position", "top"),
                "", "", "", "", "", None
            )
            st["preview_bytes"] = card.getvalue()
            user_state[uid] = st
            
            bot.send_photo(c.message.chat.id, photo=BytesIO(st["preview_bytes"]),
                caption=f"✅ <b>Предпросмотр</b>\n\n✏️ <b>Напиши СЛОВО для выделения цветом</b>\n(или «-» чтобы пропустить):",
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
        st["highlight_word"] = ""
        st["highlight_color"] = None
        bot.answer_callback_query(c.id, "Без выделения ✅")
    else:
        st["highlight_word"] = st.get("temp_highlight_word", "")
        st["highlight_color"] = HIGHLIGHT_COLORS.get(color_key)
        color_names = {"red": "красный", "yellow": "желтый", "blue": "голубой"}
        bot.answer_callback_query(c.id, f"Выбран {color_names.get(color_key)} цвет ✅")
    
    st["step"] = "waiting_rubric"
    user_state[uid] = st
    
    bot.send_message(c.message.chat.id, f"✏️ <b>Введи РУБРИКУ</b> (слово на цветном прямоугольнике вверху):", parse_mode="HTML")
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
        st["step"] = "waiting_highlight_word"
        user_state[uid] = st
        
        try:
            card = create_poster_chp(
                st["photo_bytes"], st.get("title", ""), st.get("text_position", "top"),
                st.get("date", ""), st.get("place", ""), "", "", "", None
            )
            st["preview_bytes"] = card.getvalue()
            user_state[uid] = st
            
            bot.send_photo(message.chat.id, photo=BytesIO(st["preview_bytes"]),
                caption=f"✅ <b>Предпросмотр</b>\n\n✏️ <b>Напиши СЛОВО для выделения цветом</b>\n(или «-» чтобы пропустить):",
                parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    if step == "waiting_highlight_word":
        if text == "-":
            st["highlight_word"] = ""
            st["highlight_color"] = None
            st["step"] = "waiting_emoji"
            user_state[uid] = st
            bot.reply_to(message, f"✏️ <b>Отправь СМАЙЛИК</b> для прямоугольника (например: 🎶, 🔥, ✨):", parse_mode="HTML")
        else:
            title = st.get("title", "").lower()
            if text.lower() in title:
                st["temp_highlight_word"] = text
                st["step"] = "waiting_color"
                user_state[uid] = st
                bot.reply_to(message, f"✅ Слово «{text}» <b>НАЙДЕНО</b> в заголовке!\n\n🎨 <b>Выбери цвет выделения:</b>",
                    parse_mode="HTML", reply_markup=color_kb())
            else:
                bot.reply_to(message, f"⚠️ Слово «{text}» <b>НЕ НАЙДЕНО</b> в заголовке!\n\nЗаголовок: «{st.get('title', '')}»\n\nПопробуй другое слово или нажми «-» чтобы пропустить.",
                    parse_mode="HTML")
        return
    
    if step == "waiting_rubric":
        st["rubric"] = text
        st["step"] = "waiting_emoji"
        user_state[uid] = st
        bot.reply_to(message, f"✏️ <b>Отправь СМАЙЛИК</b> для прямоугольника (например: 🎶, 🔥, ✨):", parse_mode="HTML")
        return
    
    if step == "waiting_emoji":
        st["emoji"] = text
        st["step"] = "creating"
        user_state[uid] = st
        
        try:
            card = create_poster_chp(
                st["photo_bytes"], st.get("title", ""), st.get("text_position", "top"),
                st.get("date", ""), st.get("place", ""), st.get("rubric", ""), st.get("emoji", ""),
                st.get("highlight_word", ""), st.get("highlight_color")
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
            bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            if "409" in str(e):
                logger.info("Conflict, waiting 20s...")
                time.sleep(20)
            else:
                time.sleep(5)
