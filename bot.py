# -*- coding: utf-8 -*-
import os
import html
import re
import time
import logging
from io import BytesIO
from typing import Dict, List, Tuple, Optional

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
TARGET_W = 1080
TARGET_H = 1350

# Плашка для текста (белая полупрозрачная)
TEXT_BOX_MARGIN = 40
TEXT_BOX_PADDING = 35
TEXT_BOX_BG_ALPHA = 180  # прозрачность белого фона
TEXT_BOX_BORDER_RADIUS = 20

# Шрифты
FONT_BOLD = "Montserrat-ExtraBold.ttf"
FONT_SEMIBOLD = "Montserrat-SemiBold.ttf"
FONT_REGULAR = "Montserrat-Regular.ttf"

# Размеры шрифтов
FONT_SIZE_TITLE = 62      # заголовок (ВЕЧЕР ЖИВОЙ МУЗЫКИ)
FONT_SIZE_SUBTITLE = 38   # подзаголовок (ОРГАНИЗУЮТ В...)
FONT_SIZE_BODY = 32       # основной текст
FONT_SIZE_LABEL = 36      # жирные метки (ДАТА:, МЕСТО:)

# Цвета
TEXT_COLOR = (255, 255, 255)
LABEL_COLOR = (255, 200, 80)  # золотистый для меток

# Затемнение фото
BRIGHTNESS_FACTOR = 0.45

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
def download_font_if_needed(font_name: str, url: str) -> bool:
    """Скачивает шрифт если его нет"""
    if os.path.exists(font_name):
        return True
    
    try:
        import requests
        logger.info(f"Downloading {font_name}...")
        response = requests.get(url, timeout=30)
        with open(font_name, "wb") as f:
            f.write(response.content)
        logger.info(f"Downloaded {font_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to download {font_name}: {e}")
        return False

def ensure_fonts():
    """Проверяет наличие шрифтов, скачивает при необходимости"""
    fonts = {
        "Montserrat-ExtraBold.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-ExtraBold.ttf",
        "Montserrat-SemiBold.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-SemiBold.ttf",
        "Montserrat-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Regular.ttf",
    }
    
    for font_name, url in fonts.items():
        download_font_if_needed(font_name, url)
    
    return True

def load_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """Загружает шрифт"""
    try:
        return ImageFont.truetype(font_name, size=size)
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

def draw_rounded_rect(draw: ImageDraw.ImageDraw, xy, radius, fill):
    """Рисует прямоугольник со скругленными углами"""
    x1, y1, x2, y2 = xy
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
    draw.pieslice([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=fill)
    draw.pieslice([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=fill)
    draw.pieslice([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=fill)
    draw.pieslice([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=fill)

def create_poster(image_bytes: bytes, title: str, subtitle: str, body: str, date: str, place: str) -> BytesIO:
    """Создает постер в стиле афиши"""
    
    # Открываем и обрабатываем фото
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    
    # Затемнение
    img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
    
    # Создаем белый полупрозрачный блок для текста
    overlay = Image.new("RGBA", (TARGET_W, TARGET_H), (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    
    # Параметры текстового блока
    box_width = TARGET_W - (TEXT_BOX_MARGIN * 2)
    box_x = TEXT_BOX_MARGIN
    
    # Начинаем с середины картинки
    current_y = TARGET_H // 2 - 100
    
    # Рисуем белый полупрозрачный фон
    box_height = 600  # примерная высота, можно динамически
    draw_rounded_rect(
        draw_overlay,
        (box_x, current_y - TEXT_BOX_PADDING, box_x + box_width, current_y + box_height),
        TEXT_BOX_BORDER_RADIUS,
        (255, 255, 255, TEXT_BOX_BG_ALPHA)
    )
    
    # Комбинируем с изображением
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, overlay)
    
    draw = ImageDraw.Draw(img)
    
    # Шрифты
    font_title = load_font(FONT_BOLD, FONT_SIZE_TITLE)
    font_subtitle = load_font(FONT_SEMIBOLD, FONT_SIZE_SUBTITLE)
    font_body = load_font(FONT_REGULAR, FONT_SIZE_BODY)
    font_label = load_font(FONT_BOLD, FONT_SIZE_LABEL)
    
    # Рисуем заголовок с эмодзи
    title_with_emoji = f"🎶 {title}"
    title_bbox = draw.textbbox((0, 0), title_with_emoji, font=font_title)
    title_x = (TARGET_W - (title_bbox[2] - title_bbox[0])) // 2
    draw.text((title_x, current_y), title_with_emoji, font=font_title, fill=TEXT_COLOR)
    current_y += (title_bbox[3] - title_bbox[1]) + 15
    
    # Подзаголовок
    sub_bbox = draw.textbbox((0, 0), subtitle, font=font_subtitle)
    sub_x = (TARGET_W - (sub_bbox[2] - sub_bbox[0])) // 2
    draw.text((sub_x, current_y), subtitle, font=font_subtitle, fill=TEXT_COLOR)
    current_y += (sub_bbox[3] - sub_bbox[1]) + 25
    
    # Основной текст (с переносом)
    body_lines = []
    words = body.split()
    current_line = ""
    for word in words:
        test_line = current_line + " " + word if current_line else word
        bbox = draw.textbbox((0, 0), test_line, font=font_body)
        if (bbox[2] - bbox[0]) <= box_width - (TEXT_BOX_PADDING * 2):
            current_line = test_line
        else:
            if current_line:
                body_lines.append(current_line)
            current_line = word
    if current_line:
        body_lines.append(current_line)
    
    body_y = current_y
    for line in body_lines[:4]:  # максимум 4 строки
        line_bbox = draw.textbbox((0, 0), line, font=font_body)
        line_x = (TARGET_W - (line_bbox[2] - line_bbox[0])) // 2
        draw.text((line_x, body_y), line, font=font_body, fill=TEXT_COLOR)
        body_y += (line_bbox[3] - line_bbox[1]) + 8
    
    current_y = body_y + 30
    
    # Дата
    if date:
        date_label_bbox = draw.textbbox((0, 0), "ДАТА:", font=font_label)
        draw.text((box_x + TEXT_BOX_PADDING, current_y), "ДАТА:", font=font_label, fill=LABEL_COLOR)
        date_value_bbox = draw.textbbox((0, 0), f"  {date}", font=font_body)
        draw.text((box_x + TEXT_BOX_PADDING + (date_label_bbox[2] - date_label_bbox[0]), current_y), f"  {date}", font=font_body, fill=TEXT_COLOR)
        current_y += max(date_label_bbox[3] - date_label_bbox[1], date_value_bbox[3] - date_value_bbox[1]) + 20
    
    # Место
    if place:
        place_label_bbox = draw.textbbox((0, 0), "МЕСТО:", font=font_label)
        draw.text((box_x + TEXT_BOX_PADDING, current_y), "МЕСТО:", font=font_label, fill=LABEL_COLOR)
        
        # Перенос длинного места
        place_words = place.split()
        place_line = ""
        place_lines = []
        for word in place_words:
            test_line = place_line + " " + word if place_line else word
            bbox = draw.textbbox((0, 0), test_line, font=font_body)
            if (bbox[2] - bbox[0]) <= box_width - TEXT_BOX_PADDING * 2 - 120:
                place_line = test_line
            else:
                if place_line:
                    place_lines.append(place_line)
                place_line = word
        if place_line:
            place_lines.append(place_line)
        
        place_y = current_y
        for i, line in enumerate(place_lines):
            if i == 0:
                place_bbox = draw.textbbox((0, 0), f"  {line}", font=font_body)
                draw.text((box_x + TEXT_BOX_PADDING + (place_label_bbox[2] - place_label_bbox[0]), place_y), f"  {line}", font=font_body, fill=TEXT_COLOR)
            else:
                draw.text((box_x + TEXT_BOX_PADDING + (place_label_bbox[2] - place_label_bbox[0]), place_y), f"  {line}", font=font_body, fill=TEXT_COLOR)
            place_y += (place_bbox[3] - place_bbox[1]) + 8
    
    # Сохраняем
    out = BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=95, subsampling=0)
    out.seek(0)
    return out

# =========================
# Keyboard
# =========================
def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("🎨 Создать афишу"))
    return kb

def preview_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
        InlineKeyboardButton("✏️ Редактировать", callback_data="edit"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    )
    return kb

# =========================
# Callback handlers
# =========================
@bot.callback_query_handler(func=lambda c: c.data in ["publish", "edit", "cancel"])
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
    
    elif call.data == "edit":
        st["step"] = "waiting_title"
        user_state[uid] = st
        bot.answer_callback_query(call.id, "✏️ Начни с заголовка")
        bot.send_message(call.message.chat.id, "📝 Пришли ЗАГОЛОВОК (например: ВЕЧЕР ЖИВОЙ МУЗЫКИ):")
    
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
        "👋 <b>Привет! Я создаю афиши в стиле культурных мероприятий</b>\n\n"
        "<b>📝 Как работает бот:</b>\n"
        "1️⃣ Отправь фото\n"
        "2️⃣ Введи ЗАГОЛОВОК\n"
        "3️⃣ Введи ПОДЗАГОЛОВОК\n"
        "4️⃣ Введи ОПИСАНИЕ\n"
        "5️⃣ Введи ДАТУ\n"
        "6️⃣ Введи МЕСТО\n\n"
        "Нажми «Создать афишу» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda message: message.text == "🎨 Создать афишу")
def handle_template_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_photo"}
    bot.send_message(
        message.chat.id,
        "🎨 <b>Создание афиши</b>\n\n"
        "📸 Пришли фото для фона:",
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
                "✏️ <b>Введи ЗАГОЛОВОК</b> (например: ВЕЧЕР ЖИВОЙ МУЗЫКИ):",
                parse_mode="HTML"
            )
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Сначала нажми «🎨 Создать афишу»")

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
        
        st["title"] = text.upper()
        st["step"] = "waiting_subtitle"
        user_state[uid] = st
        
        bot.reply_to(
            message,
            f"✅ Заголовок: <b>{html.escape(text.upper())}</b>\n\n"
            f"✏️ <b>Введи ПОДЗАГОЛОВОК</b> (например: ОРГАНИЗУЮТ В ХУДОЖЕСТВЕННОМ МУЗЕЕ):",
            parse_mode="HTML"
        )
        return
    
    # Подзаголовок
    if st.get("step") == "waiting_subtitle":
        st["subtitle"] = text.upper()
        st["step"] = "waiting_body"
        user_state[uid] = st
        
        bot.reply_to(
            message,
            f"✅ Подзаголовок: <b>{html.escape(text.upper())}</b>\n\n"
            f"✏️ <b>Введи ОПИСАНИЕ</b> (основной текст):",
            parse_mode="HTML"
        )
        return
    
    # Основной текст
    if st.get("step") == "waiting_body":
        st["body"] = text
        st["step"] = "waiting_date"
        user_state[uid] = st
        
        bot.reply_to(
            message,
            f"✅ Описание сохранено\n\n"
            f"✏️ <b>Введи ДАТУ</b> (например: 15 МАРТА, 19:00 или УТОЧНЯЕТСЯ):",
            parse_mode="HTML"
        )
        return
    
    # Дата
    if st.get("step") == "waiting_date":
        st["date"] = text.upper()
        st["step"] = "waiting_place"
        user_state[uid] = st
        
        bot.reply_to(
            message,
            f"✅ Дата: <b>{html.escape(text.upper())}</b>\n\n"
            f"✏️ <b>Введи МЕСТО</b> (например: НАЦИОНАЛЬНЫЙ ХУДОЖЕСТВЕННЫЙ МУЗЕЙ):",
            parse_mode="HTML"
        )
        return
    
    # Место - финальный шаг
    if st.get("step") == "waiting_place":
        st["place"] = text.upper()
        st["step"] = "creating"
        user_state[uid] = st
        
        try:
            card = create_poster(
                st["photo_bytes"],
                st.get("title", ""),
                st.get("subtitle", ""),
                st.get("body", ""),
                st.get("date", ""),
                st.get("place", "")
            )
            
            st["card_bytes"] = card.getvalue()
            st["step"] = "waiting_action"
            user_state[uid] = st
            
            bot.send_photo(
                message.chat.id,
                photo=BytesIO(st["card_bytes"]),
                caption="🎉 <b>Афиша готова!</b>\n\nНажми кнопку для публикации:",
                parse_mode="HTML",
                reply_markup=preview_kb()
            )
        except Exception as e:
            logger.error(f"Error: {e}")
            bot.reply_to(message, f"❌ Ошибка: {e}")
            clear_state(uid)
        return
    
    # Если не в процессе создания
    bot.send_message(
        message.chat.id,
        "📝 Нажми «🎨 Создать афишу» чтобы начать",
        reply_markup=main_menu_kb()
    )

# =========================
# Main
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")
    ensure_fonts()
    
    time.sleep(3)
    
    try:
        bot.delete_webhook()
        time.sleep(1)
    except Exception as e:
        logger.warning(f"Webhook error: {e}")
    
    logger.info("✅ Bot started polling!")
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(10)
