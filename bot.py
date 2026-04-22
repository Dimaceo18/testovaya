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
FONT_PATH = "Montserrat-Bold.ttf"

# Размеры шрифта
FONT_SIZE_TITLE = 110      # крупный заголовок
FONT_SIZE_SUBTITLE = 70    # подзаголовок

# Затемнение фото
BRIGHTNESS_FACTOR = 0.50

# Отступы (левый верхний угол)
PADDING_LEFT = 60
PADDING_TOP = 200

# Межстрочный интервал
LINE_SPACING = 15

# Цвета
TEXT_COLOR = (255, 255, 255)

# Цвета для выделения
HIGHLIGHT_COLORS = {
    "red": (255, 80, 80),     # красный
    "blue": (80, 150, 255),   # голубой
    "green": (80, 255, 120)   # зеленый
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
def download_font():
    if os.path.exists(FONT_PATH):
        return True
    
    url = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Bold.ttf"
    try:
        import requests
        logger.info(f"Downloading font...")
        response = requests.get(url, timeout=30)
        with open(FONT_PATH, "wb") as f:
            f.write(response.content)
        logger.info("Font downloaded")
        return True
    except Exception as e:
        logger.error(f"Failed to download font: {e}")
        return False

def load_font(size: int):
    try:
        return ImageFont.truetype(FONT_PATH, size=size)
    except Exception:
        return ImageFont.load_default()

def clear_state(user_id: int):
    if user_id in user_state:
        user_state[user_id] = {"step": "idle"}

def crop_to_4x5(img: Image.Image) -> Image.Image:
    w, h = img.size
    target_ratio = 4 / 5
    
    if w / h > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))

def wrap_text(draw, text: str, font, max_width: int) -> List[str]:
    """Перенос текста по словам"""
    if not text:
        return []
    
    words = text.split()
    lines = []
    current = words[0]
    
    for word in words[1:]:
        candidate = current + " " + word
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines

def draw_text_with_highlight(draw, text: str, highlight_phrase: str, highlight_color, font, x, y, max_width):
    """Рисует текст с выделением фразы цветом (выравнивание по левому краю)"""
    text_upper = text.upper()
    highlight_upper = highlight_phrase.upper() if highlight_phrase else ""
    
    if not highlight_upper or highlight_upper not in text_upper:
        draw.text((x, y), text_upper, font=font, fill=TEXT_COLOR)
        bbox = draw.textbbox((0, 0), text_upper, font=font)
        return y + (bbox[3] - bbox[1]) + LINE_SPACING
    
    # Разбиваем текст на части
    parts = text_upper.split(highlight_upper)
    current_x = x
    
    for i, part in enumerate(parts):
        if part:
            draw.text((current_x, y), part, font=font, fill=TEXT_COLOR)
            bbox = draw.textbbox((0, 0), part, font=font)
            current_x += bbox[2] - bbox[0]
        
        if i < len(parts) - 1:
            draw.text((current_x, y), highlight_upper, font=font, fill=highlight_color)
            bbox = draw.textbbox((0, 0), highlight_upper, font=font)
            current_x += bbox[2] - bbox[0]
    
    full_bbox = draw.textbbox((0, 0), text_upper, font=font)
    return y + (full_bbox[3] - full_bbox[1]) + LINE_SPACING

def create_poster(image_bytes: bytes, title: str, subtitle: str, highlight_phrase: str = "", highlight_color: tuple = None) -> BytesIO:
    """Создает постер с текстом в левом верхнем углу"""
    
    # Открываем и обрабатываем фото
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
    
    draw = ImageDraw.Draw(img)
    
    # Шрифты
    font_title = load_font(FONT_SIZE_TITLE)
    font_subtitle = load_font(FONT_SIZE_SUBTITLE)
    
    max_width = TARGET_W - PADDING_LEFT - 60  # отступ справа
    y = PADDING_TOP
    
    # Заголовок (слева)
    title_lines = wrap_text(draw, title, font_title, max_width)
    for line in title_lines:
        y = draw_text_with_highlight(draw, line, highlight_phrase, highlight_color, font_title, PADDING_LEFT, y, max_width)
    
    # Отступ между заголовком и подзаголовком
    if subtitle:
        y += 20
    
    # Подзаголовок (слева)
    sub_lines = wrap_text(draw, subtitle, font_subtitle, max_width)
    for line in sub_lines:
        y = draw_text_with_highlight(draw, line, highlight_phrase, highlight_color, font_subtitle, PADDING_LEFT, y, max_width)
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0)
    out.seek(0)
    return out

# =========================
# Keyboard
# =========================
def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("✨ Создать карточку"))
    return kb

def color_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🔴 Красный", callback_data="color:red"),
        InlineKeyboardButton("🔵 Голубой", callback_data="color:blue"),
        InlineKeyboardButton("🟢 Зеленый", callback_data="color:green")
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
@bot.callback_query_handler(func=lambda c: c.data.startswith("color:"))
def on_color_select(c):
    uid = c.from_user.id
    color_key = c.data.split(":")[1]
    st = user_state.get(uid) or {}
    
    if color_key == "none":
        highlight_phrase = ""
        highlight_color = None
        bot.answer_callback_query(c.id, "Без выделения ✅")
    else:
        highlight_phrase = st.get("temp_highlight_phrase", "")
        highlight_color = HIGHLIGHT_COLORS.get(color_key)
        color_names = {"red": "красный", "blue": "голубой", "green": "зеленый"}
        bot.answer_callback_query(c.id, f"Выбран {color_names.get(color_key)} цвет ✅")
    
    st["highlight_phrase"] = highlight_phrase
    st["highlight_color"] = highlight_color
    st["step"] = "creating"
    user_state[uid] = st
    
    try:
        card = create_poster(
            st["photo_bytes"],
            st.get("title", ""),
            st.get("subtitle", ""),
            highlight_phrase,
            highlight_color
        )
        
        st["card_bytes"] = card.getvalue()
        st["step"] = "waiting_action"
        user_state[uid] = st
        
        bot.edit_message_media(
            telebot.types.InputMediaPhoto(card.getvalue()),
            c.message.chat.id, c.message.message_id
        )
        bot.edit_message_caption(
            c.message.chat.id, c.message.message_id,
            caption="🎉 <b>Карточка готова!</b>\n\nНажми кнопку для публикации:",
            parse_mode="HTML",
            reply_markup=preview_kb()
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        bot.send_message(c.message.chat.id, f"❌ Ошибка: {e}")

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
        "👋 <b>Привет! Я делаю карточки с текстом в левом верхнем углу</b>\n\n"
        "<b>📝 Как работает:</b>\n"
        "1️⃣ Отправь фото\n"
        "2️⃣ Отправь ЗАГОЛОВОК\n"
        "3️⃣ Отправь ПОДЗАГОЛОВОК (или отправь «-» чтобы пропустить)\n"
        "4️⃣ Отправь ФРАЗУ для выделения цветом (или «-» чтобы пропустить)\n"
        "5️⃣ Выбери цвет: 🔴 красный, 🔵 голубой, 🟢 зеленый\n\n"
        "📐 Размер: 1080×1350 (4:5)\n"
        "🔤 Шрифт: Montserrat Bold\n"
        "📍 Выравнивание: левый верхний угол\n\n"
        "Нажми «Создать карточку» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda message: message.text == "✨ Создать карточку")
def handle_create_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_photo"}
    bot.send_message(message.chat.id, "✨ <b>Создание карточки</b>\n\n📸 Пришли фото:", parse_mode="HTML")

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
            st["step"] = "waiting_title"
            user_state[uid] = st
            
            bot.reply_to(message, "📸 Фото сохранено!\n\n✏️ <b>Введи ЗАГОЛОВОК</b> (например: DOUBLE TREE BY HILTON):", parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Сначала нажми «✨ Создать карточку»")

@bot.message_handler(content_types=["text"])
def on_text(message):
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state.get(uid) or {"step": "idle"}
    step = st.get("step")
    
    # Заголовок
    if step == "waiting_title":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        
        st["title"] = text
        st["step"] = "waiting_subtitle"
        user_state[uid] = st
        
        bot.reply_to(
            message,
            f"✅ Заголовок: <b>{html.escape(text)}</b>\n\n"
            f"✏️ <b>Введи ПОДЗАГОЛОВОК</b>\n(или отправь «-» чтобы пропустить):",
            parse_mode="HTML"
        )
        return
    
    # Подзаголовок
    if step == "waiting_subtitle":
        subtitle = "" if text == "-" else text
        st["subtitle"] = subtitle
        st["step"] = "waiting_highlight_phrase"
        user_state[uid] = st
        
        # Показываем превью без выделения
        try:
            card = create_poster(st["photo_bytes"], st["title"], subtitle, "", None)
            st["card_bytes"] = card.getvalue()
            user_state[uid] = st
            
            bot.send_photo(
                message.chat.id,
                photo=BytesIO(st["card_bytes"]),
                caption="🎉 <b>Предварительный просмотр</b>\n\n"
                       "✏️ <b>Напиши ФРАЗУ, которую нужно выделить цветом</b>\n"
                       "(или отправь «-» чтобы пропустить):\n\n"
                       "💡 Фраза будет найдена в тексте (заголовке или подзаголовке) и выделена цветом",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error: {e}")
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    # Фраза для выделения
    if step == "waiting_highlight_phrase":
        if text == "-":
            # Без выделения - сразу финальный результат
            try:
                card = create_poster(st["photo_bytes"], st["title"], st.get("subtitle", ""), "", None)
                st["card_bytes"] = card.getvalue()
                st["step"] = "waiting_action"
                user_state[uid] = st
                
                bot.send_photo(
                    message.chat.id,
                    photo=BytesIO(st["card_bytes"]),
                    caption="🎉 <b>Карточка готова!</b>\n\nНажми кнопку для публикации:",
                    parse_mode="HTML",
                    reply_markup=preview_kb()
                )
            except Exception as e:
                bot.reply_to(message, f"❌ Ошибка: {e}")
        else:
            # Сохраняем фразу и предлагаем выбрать цвет
            st["temp_highlight_phrase"] = text
            st["step"] = "waiting_color"
            user_state[uid] = st
            
            bot.reply_to(
                message,
                f"✏️ Фраза для выделения: <b>{html.escape(text)}</b>\n\n"
                f"🎨 <b>Выбери цвет:</b>",
                parse_mode="HTML",
                reply_markup=color_kb()
            )
        return
    
    # Если не в процессе
    bot.send_message(message.chat.id, "📝 Нажми «✨ Создать карточку» чтобы начать", reply_markup=main_menu_kb())

# =========================
# Main
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")
    download_font()
    
    time.sleep(2)
    
    try:
        bot.delete_webhook()
        time.sleep(1)
    except Exception as e:
        logger.warning(f"Webhook error: {e}")
    
    logger.info("✅ Bot started!")
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(10)
