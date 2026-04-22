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
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
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

# Размеры (4:5)
TARGET_W = 1080
TARGET_H = 1350

# Плашка под заголовок
OVERLAY_HEIGHT = 260
OVERLAY_ALPHA = 150
BLUR_RADIUS = 8
SIDE_PADDING = 70
TOP_PADDING = 28
BOTTOM_PADDING = 24

# Шрифты
FONT_MAIN = "Montserrat-ExtraBold.ttf"
FONT_FALLBACK = "CaviarDreams.ttf"

TITLE_FONT_SIZE = 92
MIN_FONT_SIZE = 46
LINE_SPACING = 10

# Цвета для выделения
COLORS = {
    "red": (255, 80, 80),
    "blue": (80, 150, 255),
    "yellow": (255, 220, 80)
}

# Качество фото
SHARPEN_FACTOR = 1.15
CONTRAST_FACTOR = 1.05
BRIGHTNESS_FACTOR = 0.98

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
    if not os.path.exists(FONT_MAIN):
        logger.warning(f"Font not found: {FONT_MAIN}, using {FONT_FALLBACK}")
        return FONT_FALLBACK
    return FONT_MAIN

def load_font(size: int) -> ImageFont.FreeTypeFont:
    font_path = ensure_fonts()
    try:
        return ImageFont.truetype(font_path, size=size)
    except Exception:
        return ImageFont.load_default()

def clear_state(user_id: int):
    if user_id in user_state:
        user_state[user_id] = {"step": "idle"}

def fit_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Обрезка фото под формат cover 4:5"""
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        new_h = target_h
        new_w = int(new_h * src_ratio)
    else:
        new_w = target_w
        new_h = int(new_w / src_ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))

def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    words = text.split()
    if not words:
        return []

    lines = []
    current = words[0]

    for word in words[1:]:
        candidate = current + " " + word
        bbox = draw.textbbox((0, 0), candidate, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines

def text_block_height(draw: ImageDraw.ImageDraw, lines: List[str], font: ImageFont.FreeTypeFont, line_spacing: int) -> int:
    total = 0
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        h = bbox[3] - bbox[1]
        total += h
        if i < len(lines) - 1:
            total += line_spacing
    return total

def choose_font_and_lines(text: str, max_width: int, max_height: int) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    temp_img = Image.new("RGB", (TARGET_W, TARGET_H), "black")
    draw = ImageDraw.Draw(temp_img)

    for size in range(TITLE_FONT_SIZE, MIN_FONT_SIZE - 1, -2):
        font = load_font(size)
        lines = wrap_text(draw, text, font, max_width)
        height = text_block_height(draw, lines, font, LINE_SPACING)
        if height <= max_height:
            return font, lines

    font = load_font(MIN_FONT_SIZE)
    lines = wrap_text(draw, text, font, max_width)
    return font, lines

def make_top_overlay(base: Image.Image) -> Image.Image:
    """Создает затемненную верхнюю плашку с легким blur"""
    top_part = base.crop((0, 0, TARGET_W, OVERLAY_HEIGHT)).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))

    shade = Image.new("RGBA", (TARGET_W, OVERLAY_HEIGHT), (0, 0, 0, OVERLAY_ALPHA))
    top_rgba = top_part.convert("RGBA")
    top_rgba.alpha_composite(shade)

    # Мягкий градиент вниз
    gradient = Image.new("L", (1, OVERLAY_HEIGHT))
    for y in range(OVERLAY_HEIGHT):
        if y < OVERLAY_HEIGHT * 0.65:
            alpha = 255
        else:
            remain = OVERLAY_HEIGHT - y
            total = OVERLAY_HEIGHT * 0.35
            alpha = int(max(0, min(255, 255 * (remain / total))))
        gradient.putpixel((0, y), alpha)

    alpha_mask = gradient.resize((TARGET_W, OVERLAY_HEIGHT))
    top_rgba.putalpha(alpha_mask)
    return top_rgba

def draw_text_with_highlight(
    draw: ImageDraw.ImageDraw,
    text: str,
    highlight_phrase: str,
    highlight_color: Tuple[int, int, int],
    font: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    line_h: int
) -> int:
    """Рисует текст с выделением определенной фразы цветом"""
    
    if not highlight_phrase or highlight_phrase not in text:
        # Если нет фразы для выделения, рисуем обычный текст
        draw.text((x, y), text, font=font, fill=(255, 255, 255))
        return y + line_h + LINE_SPACING
    
    # Разбиваем текст на части
    parts = text.split(highlight_phrase)
    
    current_x = x
    for i, part in enumerate(parts):
        # Рисуем обычную часть
        if part:
            draw.text((current_x, y), part, font=font, fill=(255, 255, 255))
            bbox = draw.textbbox((0, 0), part, font=font)
            current_x += bbox[2] - bbox[0]
        
        # Рисуем выделенную фразу (если не последняя)
        if i < len(parts) - 1:
            draw.text((current_x, y), highlight_phrase, font=font, fill=highlight_color)
            bbox = draw.textbbox((0, 0), highlight_phrase, font=font)
            current_x += bbox[2] - bbox[0]
    
    return y + line_h + LINE_SPACING

def create_poster(image_bytes: bytes, title: str, highlight_phrase: str = "", highlight_color: Tuple[int, int, int] = (255, 255, 255)) -> BytesIO:
    """Создает постер с заголовком и выделенной фразой"""
    
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = fit_cover(img, TARGET_W, TARGET_H)

    # Улучшаем фото
    img = ImageEnhance.Sharpness(img).enhance(SHARPEN_FACTOR)
    img = ImageEnhance.Contrast(img).enhance(CONTRAST_FACTOR)
    img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)

    result = img.convert("RGBA")
    top_overlay = make_top_overlay(img)
    result.alpha_composite(top_overlay, (0, 0))

    draw = ImageDraw.Draw(result)

    max_text_width = TARGET_W - SIDE_PADDING * 2
    max_text_height = OVERLAY_HEIGHT - TOP_PADDING - BOTTOM_PADDING
    
    # Заголовок в верхнем регистре
    title_upper = title.upper()
    font, lines = choose_font_and_lines(title_upper, max_text_width, max_text_height)

    block_h = text_block_height(draw, lines, font, LINE_SPACING)
    y = TOP_PADDING + (max_text_height - block_h) // 2

    # Если есть фраза для выделения, обрабатываем каждую строку
    if highlight_phrase:
        highlight_upper = highlight_phrase.upper()
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_w = bbox[2] - bbox[0]
            line_h = bbox[3] - bbox[1]
            x = (TARGET_W - line_w) // 2
            
            # Тень
            shadow_offset = 2
            # Рисуем тень (упрощенно - просто темный текст)
            draw.text((x + shadow_offset, y + shadow_offset), line, font=font, fill=(0, 0, 0, 120))
            
            # Рисуем текст с выделением
            y = draw_text_with_highlight(draw, line, highlight_upper, highlight_color, font, x, y, line_h)
            y -= LINE_SPACING  # корректировка, так как draw_text_with_highlight уже добавил отступ
    else:
        # Обычный текст без выделения
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_w = bbox[2] - bbox[0]
            line_h = bbox[3] - bbox[1]
            x = (TARGET_W - line_w) // 2
            
            shadow_offset = 2
            draw.text((x + shadow_offset, y + shadow_offset), line, font=font, fill=(0, 0, 0, 120))
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_h + LINE_SPACING

    output = BytesIO()
    result.convert("RGB").save(output, format="JPEG", quality=95, subsampling=0)
    output.seek(0)
    return output

# =========================
# Keyboard для выбора цвета
# =========================
def color_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🔴 Красный", callback_data="color:red"),
        InlineKeyboardButton("🔵 Голубой", callback_data="color:blue"),
        InlineKeyboardButton("🟡 Желтый", callback_data="color:yellow")
    )
    kb.add(InlineKeyboardButton("❌ Пропустить (без выделения)", callback_data="color:skip"))
    return kb

def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("✨ Шаблон АМ"))
    return kb

def preview_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
        InlineKeyboardButton("✏️ Редактировать заголовок", callback_data="edit_title"),
        InlineKeyboardButton("✏️ Редактировать фразу", callback_data="edit_phrase"),
        InlineKeyboardButton("🎨 Сменить цвет", callback_data="change_color"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    )
    return kb

def channel_kb():
    return InlineKeyboardMarkup()

def build_caption_html(title: str, phrase: str = "") -> str:
    if phrase:
        return f"<b>✨ {html.escape(title)}</b>\n\n<blockquote>{html.escape(phrase)}</blockquote>"
    return f"<b>✨ {html.escape(title)}</b>"

# =========================
# Callback handlers
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("color:"))
def on_color_select(c):
    uid = c.from_user.id
    color_key = c.data.split(":")[1]
    st = user_state.get(uid) or {}
    
    if color_key == "skip":
        st["highlight_color"] = None
        st["highlight_phrase"] = ""
        bot.answer_callback_query(c.id, "Без выделения ✅")
    else:
        st["highlight_color"] = COLORS.get(color_key, COLORS["red"])
        st["highlight_color_key"] = color_key
        color_names = {"red": "красный", "blue": "голубой", "yellow": "желтый"}
        bot.answer_callback_query(c.id, f"Выбран {color_names.get(color_key, color_key)} цвет ✅")
    
    st["step"] = "waiting_highlight_phrase"
    user_state[uid] = st
    
    bot.edit_message_text(
        f"🎨 Цвет выбран!\n\n"
        f"✏️ Теперь отправь <b>ФРАЗУ ДЛЯ ВЫДЕЛЕНИЯ</b> (или отправь «-» чтобы пропустить):",
        c.message.chat.id, c.message.message_id,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda c: c.data in ["publish", "edit_title", "edit_phrase", "change_color", "cancel"])
def on_action(call):
    uid = call.from_user.id
    st = user_state.get(uid)
    
    if not st or st.get("step") != "waiting_action":
        bot.answer_callback_query(call.id, "Нет активного превью")
        return
    
    if call.data == "publish":
        try:
            caption = build_caption_html(st.get("title", ""), st.get("highlight_phrase", ""))
            
            if CHANNEL:
                bot.send_photo(CHANNEL, BytesIO(st["card_bytes"]), caption=caption, parse_mode="HTML", reply_markup=channel_kb())
                bot.answer_callback_query(call.id, "Опубликовано ✅")
                bot.send_message(call.message.chat.id, "✅ Готово!", reply_markup=main_menu_kb())
            else:
                bot.answer_callback_query(call.id, "❌ CHANNEL_USERNAME не задан")
            clear_state(uid)
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ Ошибка: {e}")
    
    elif call.data == "edit_title":
        st["step"] = "waiting_title"
        user_state[uid] = st
        bot.answer_callback_query(call.id, "✏️ Введи новый заголовок")
        bot.send_message(call.message.chat.id, "📝 Пришли новый ЗАГОЛОВОК:", reply_markup=main_menu_kb())
    
    elif call.data == "edit_phrase":
        st["step"] = "waiting_highlight_phrase"
        user_state[uid] = st
        bot.answer_callback_query(call.id, "✏️ Введи новую фразу")
        bot.send_message(call.message.chat.id, "📝 Пришли новую ФРАЗУ ДЛЯ ВЫДЕЛЕНИЯ:", reply_markup=main_menu_kb())
    
    elif call.data == "change_color":
        st["step"] = "waiting_color"
        user_state[uid] = st
        bot.answer_callback_query(call.id, "🎨 Выбери цвет")
        bot.send_message(call.message.chat.id, "🎨 Выбери цвет для выделения:", reply_markup=color_kb())
    
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
        "👋 <b>Привет! Я бот для оформления постов в стиле Афиша Минска</b>\n\n"
        "<b>✨ Как работает шаблон АМ:</b>\n"
        "1️⃣ Отправь фото\n"
        "2️⃣ Отправь заголовок\n"
        "3️⃣ Выбери цвет для выделения (красный/голубой/желтый)\n"
        "4️⃣ Отправь фразу, которую нужно выделить цветом\n\n"
        "Нажми «Шаблон АМ» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda message: message.text == "✨ Шаблон АМ")
def handle_template_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_photo", "template": "AM"}
    bot.send_message(
        message.chat.id,
        "✨ Выбран шаблон <b>АМ</b>\n\n"
        "📸 Пришли фото для поста:",
        parse_mode="HTML"
    )

@bot.message_handler(content_types=["photo"])
def on_photo(message):
    uid = message.from_user.id
    st = user_state.get(uid) or {}
    step = st.get("step")
    
    if step == "waiting_photo":
        try:
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            photo_bytes = bot.download_file(file_info.file_path)
            
            st["photo_bytes"] = photo_bytes
            st["step"] = "waiting_title"
            user_state[uid] = st
            
            bot.reply_to(
                message,
                "📸 Фото сохранено!\n\n✏️ Теперь отправь <b>ЗАГОЛОВОК</b> для поста:",
                parse_mode="HTML"
            )
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Сначала выбери «✨ Шаблон АМ»")

@bot.message_handler(content_types=["text"])
def on_text(message):
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state.get(uid) or {"step": "idle"}
    
    # Обработка заголовка
    if st.get("step") == "waiting_title":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        
        st["title"] = text
        st["step"] = "waiting_color"
        user_state[uid] = st
        
        bot.reply_to(
            message,
            f"✅ Заголовок сохранён: <b>{html.escape(text)}</b>\n\n"
            f"🎨 Теперь выбери цвет для выделения фразы:",
            parse_mode="HTML",
            reply_markup=color_kb()
        )
        return
    
    # Обработка фразы для выделения
    if st.get("step") == "waiting_highlight_phrase":
        highlight_phrase = "" if text == "-" else text
        
        st["highlight_phrase"] = highlight_phrase
        st["step"] = "creating"
        user_state[uid] = st
        
        try:
            highlight_color = st.get("highlight_color", COLORS["red"])
            if not highlight_color:
                highlight_color = COLORS["red"]
            
            card = create_poster(
                st["photo_bytes"],
                st["title"],
                highlight_phrase,
                highlight_color
            )
            
            st["card_bytes"] = card.getvalue()
            st["step"] = "waiting_action"
            user_state[uid] = st
            
            caption = build_caption_html(st["title"], highlight_phrase)
            
            bot.send_photo(
                message.chat.id,
                photo=BytesIO(st["card_bytes"]),
                caption=caption,
                parse_mode="HTML",
                reply_markup=preview_kb()
            )
            bot.reply_to(
                message,
                "🎉 <b>Превью готово!</b>\n\n"
                "Нажми кнопку под фото для публикации или редактирования.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error: {e}")
            bot.reply_to(message, f"❌ Ошибка при создании постера: {e}")
            clear_state(uid)
        return
    
    # Если пользователь в неожиданном состоянии
    if st.get("step") not in ["idle", None]:
        bot.send_message(
            message.chat.id,
            "📝 Следуй инструкциям! Нажми «✨ Шаблон АМ» чтобы начать заново.",
            reply_markup=main_menu_kb()
        )
    else:
        bot.send_message(
            message.chat.id,
            "📝 Нажми «✨ Шаблон АМ» чтобы начать",
            reply_markup=main_menu_kb()
        )

# =========================
# Main
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")
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
