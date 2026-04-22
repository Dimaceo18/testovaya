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

# Отступы
PADDING_X = 50
PADDING_Y_START = 300

# Цвета
TEXT_COLOR = (255, 255, 255)

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
    """Скачивает шрифт Montserrat-Bold если нет"""
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

def load_font(size: int) -> ImageFont.FreeTypeFont:
    """Загружает шрифт"""
    try:
        return ImageFont.truetype(FONT_PATH, size=size)
    except Exception:
        return ImageFont.load_default()

def clear_state(user_id: int):
    if user_id in user_state:
        user_state[user_id] = {"step": "idle"}

def crop_to_4x5(img: Image.Image) -> Image.Image:
    """Обрезка под 4:5"""
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

def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    """Перенос текста по словам"""
    words = text.split()
    if not words:
        return []
    
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

def create_poster(image_bytes: bytes, title: str, subtitle: str = "") -> BytesIO:
    """Создает простой постер с крупным текстом на затемнённом фоне"""
    
    # Открываем фото
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    
    # Затемнение
    img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
    
    draw = ImageDraw.Draw(img)
    
    # Шрифты
    font_title = load_font(FONT_SIZE_TITLE)
    font_subtitle = load_font(FONT_SIZE_SUBTITLE)
    
    # Текст в верхний регистр
    title_upper = title.upper()
    subtitle_upper = subtitle.upper() if subtitle else ""
    
    # Заголовок
    title_lines = wrap_text(draw, title_upper, font_title, TARGET_W - PADDING_X * 2)
    
    y = PADDING_Y_START
    
    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=font_title)
        x = (TARGET_W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font_title, fill=TEXT_COLOR)
        y += (bbox[3] - bbox[1]) + 15
    
    # Подзаголовок
    if subtitle_upper:
        y += 40
        sub_lines = wrap_text(draw, subtitle_upper, font_subtitle, TARGET_W - PADDING_X * 2)
        
        for line in sub_lines:
            bbox = draw.textbbox((0, 0), line, font=font_subtitle)
            x = (TARGET_W - (bbox[2] - bbox[0])) // 2
            draw.text((x, y), line, font=font_subtitle, fill=TEXT_COLOR)
            y += (bbox[3] - bbox[1]) + 12
    
    # Сохраняем
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
        "👋 <b>Привет! Я делаю карточки с крупным текстом на фото</b>\n\n"
        "<b>📝 Как работает:</b>\n"
        "1️⃣ Отправь фото\n"
        "2️⃣ Отправь ЗАГОЛОВОК\n"
        "3️⃣ Отправь ПОДЗАГОЛОВОК (или отправь «-» чтобы пропустить)\n\n"
        "📐 Размер: 1080×1350 (4:5)\n"
        "🔤 Шрифт: Montserrat Bold\n"
        "📏 Размер: 110px / 70px\n\n"
        "Нажми «Создать карточку» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda message: message.text == "✨ Создать карточку")
def handle_create_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_photo"}
    bot.send_message(
        message.chat.id,
        "✨ <b>Создание карточки</b>\n\n"
        "📸 Пришли фото:",
        parse_mode="HTML"
    )

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
            
            bot.reply_to(
                message,
                "📸 Фото сохранено!\n\n"
                "✏️ <b>Введи ЗАГОЛОВОК</b> (например: DOUBLE TREE BY HILTON):",
                parse_mode="HTML"
            )
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Сначала нажми «✨ Создать карточку»")

@bot.message_handler(content_types=["text"])
def on_text(message):
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state.get(uid) or {"step": "idle"}
    
    # Заголовок
    if st.get("step") == "waiting_title":
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
    if st.get("step") == "waiting_subtitle":
        subtitle = "" if text == "-" else text
        
        st["subtitle"] = subtitle
        st["step"] = "creating"
        user_state[uid] = st
        
        try:
            card = create_poster(
                st["photo_bytes"],
                st.get("title", ""),
                subtitle
            )
            
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
            logger.error(f"Error: {e}")
            bot.reply_to(message, f"❌ Ошибка: {e}")
            clear_state(uid)
        return
    
    # Если не в процессе
    bot.send_message(
        message.chat.id,
        "📝 Нажми «✨ Создать карточку» чтобы начать",
        reply_markup=main_menu_kb()
    )

# =========================
# Main
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")
    download_font()
    
    time.sleep(3)
    
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
