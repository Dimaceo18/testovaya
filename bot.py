# -*- coding: utf-8 -*-
import os
import html
import time
import logging
from io import BytesIO
from typing import Dict, List, Tuple, Optional

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

# Шрифты
FONT_BOLD = "Montserrat-Bold.ttf"
FONT_REGULAR = "Montserrat-Regular.ttf"

# Размеры шрифтов
FONT_SIZE_TITLE = 68       # Заголовок (ВЕЧЕР ЖИВОЙ МУЗЫКИ)
FONT_SIZE_SUBTITLE = 42    # Подзаголовок (ОРГАНИЗУЮТ В...)
FONT_SIZE_BODY = 36        # Основной текст
FONT_SIZE_LABEL = 44       # Метки (ДАТА:, МЕСТО:)
FONT_SIZE_FOOTER = 32      # Нижний текст

# Затемнение фото
BRIGHTNESS_FACTOR = 0.45

# Отступы
PADDING_X = 60
START_Y = 350               # начальная позиция текста сверху
LINE_SPACING = 12

# Цвета по умолчанию
TEXT_COLOR = (255, 255, 255)
LABEL_COLOR = (255, 200, 80)  # золотистый для меток

# Цвета для выделения
HIGHLIGHT_COLORS = {
    "red": (255, 80, 80),     # красный
    "blue": (80, 150, 255),   # голубой
    "yellow": (255, 220, 80)  # желтый
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
    """Скачивает шрифты если нет"""
    fonts = {
        "Montserrat-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Bold.ttf",
        "Montserrat-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Regular.ttf",
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
    """Рисует текст с выделением фразы цветом"""
    if not highlight_phrase or highlight_phrase not in text:
        draw.text((x, y), text, font=font, fill=TEXT_COLOR)
        bbox = draw.textbbox((0, 0), text, font=font)
        return y + (bbox[3] - bbox[1]) + LINE_SPACING
    
    # Разбиваем текст на части
    parts = text.split(highlight_phrase)
    current_x = x
    
    for i, part in enumerate(parts):
        if part:
            draw.text((current_x, y), part, font=font, fill=TEXT_COLOR)
            bbox = draw.textbbox((0, 0), part, font=font)
            current_x += bbox[2] - bbox[0]
        
        if i < len(parts) - 1:
            draw.text((current_x, y), highlight_phrase, font=font, fill=highlight_color)
            bbox = draw.textbbox((0, 0), highlight_phrase, font=font)
            current_x += bbox[2] - bbox[0]
    
    bbox = draw.textbbox((0, 0), text, font=font)
    return y + (bbox[3] - bbox[1]) + LINE_SPACING

def draw_section(draw, label: str, value: str, font_label, font_value, x, y, max_width):
    """Рисует секцию с меткой и значением"""
    # Метка
    draw.text((x, y), label, font=font_label, fill=LABEL_COLOR)
    label_bbox = draw.textbbox((0, 0), label, font=font_label)
    label_width = label_bbox[2] - label_bbox[0]
    
    # Значение (с переносом)
    value_lines = wrap_text(draw, value, font_value, max_width - label_width - 20)
    value_y = y
    
    for i, line in enumerate(value_lines):
        if i == 0:
            draw.text((x + label_width + 15, value_y), line, font=font_value, fill=TEXT_COLOR)
        else:
            draw.text((x + label_width + 15, value_y), line, font=font_value, fill=TEXT_COLOR)
        
        line_bbox = draw.textbbox((0, 0), line, font=font_value)
        value_y += (line_bbox[3] - line_bbox[1]) + 8
    
    total_height = value_y - y
    return y + total_height + LINE_SPACING * 2

def create_poster(image_bytes: bytes, data: dict, highlight_phrase: str = "", highlight_color: tuple = None) -> BytesIO:
    """Создает структурированный постер"""
    
    # Открываем и обрабатываем фото
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
    
    draw = ImageDraw.Draw(img)
    
    # Загружаем шрифты
    font_title = load_font(FONT_BOLD, FONT_SIZE_TITLE)
    font_subtitle = load_font(FONT_BOLD, FONT_SIZE_SUBTITLE)
    font_body = load_font(FONT_REGULAR, FONT_SIZE_BODY)
    font_label = load_font(FONT_BOLD, FONT_SIZE_LABEL)
    font_footer = load_font(FONT_BOLD, FONT_SIZE_FOOTER)
    
    max_text_width = TARGET_W - PADDING_X * 2
    y = START_Y
    
    # Заголовок
    title_lines = wrap_text(draw, data.get("title", "").upper(), font_title, max_text_width)
    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=font_title)
        x = (TARGET_W - (bbox[2] - bbox[0])) // 2
        y = draw_text_with_highlight(draw, line, highlight_phrase, highlight_color, font_title, x, y, max_text_width)
    y += 15
    
    # Подзаголовок
    if data.get("subtitle"):
        sub_lines = wrap_text(draw, data.get("subtitle", "").upper(), font_subtitle, max_text_width)
        for line in sub_lines:
            bbox = draw.textbbox((0, 0), line, font=font_subtitle)
            x = (TARGET_W - (bbox[2] - bbox[0])) // 2
            y = draw_text_with_highlight(draw, line, highlight_phrase, highlight_color, font_subtitle, x, y, max_text_width)
        y += 25
    
    # Основной текст
    if data.get("body"):
        body_lines = wrap_text(draw, data.get("body", ""), font_body, max_text_width)
        for line in body_lines:
            bbox = draw.textbbox((0, 0), line, font=font_body)
            x = (TARGET_W - (bbox[2] - bbox[0])) // 2
            y = draw_text_with_highlight(draw, line, highlight_phrase, highlight_color, font_body, x, y, max_text_width)
        y += 20
    
    # Дата
    if data.get("date"):
        y = draw_section(draw, "ДАТА:", data.get("date", "").upper(), font_label, font_body, PADDING_X, y, max_text_width)
    
    # Место
    if data.get("place"):
        y = draw_section(draw, "МЕСТО:", data.get("place", "").upper(), font_label, font_body, PADDING_X, y, max_text_width)
    
    # Нижний текст
    if data.get("footer"):
        footer_lines = wrap_text(draw, data.get("footer", "").upper(), font_footer, max_text_width)
        for line in footer_lines:
            bbox = draw.textbbox((0, 0), line, font=font_footer)
            x = (TARGET_W - (bbox[2] - bbox[0])) // 2
            draw.text((x, y), line, font=font_footer, fill=TEXT_COLOR)
            y += (bbox[3] - bbox[1]) + LINE_SPACING
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0)
    out.seek(0)
    return out

# =========================
# Keyboard
# =========================
def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("🎨 Создать афишу"))
    return kb

def color_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🔴 Красный", callback_data="color:red"),
        InlineKeyboardButton("🔵 Голубой", callback_data="color:blue"),
        InlineKeyboardButton("🟡 Желтый", callback_data="color:yellow")
    )
    kb.add(InlineKeyboardButton("➖ Без выделения", callback_data="color:none"))
    return kb

def preview_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
        InlineKeyboardButton("✏️ Изменить", callback_data="edit"),
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
        st["highlight_phrase"] = ""
        st["highlight_color"] = None
        bot.answer_callback_query(c.id, "Без выделения ✅")
    else:
        st["highlight_phrase"] = st.get("temp_highlight_phrase", "")
        st["highlight_color"] = HIGHLIGHT_COLORS.get(color_key)
        color_names = {"red": "красный", "blue": "голубой", "yellow": "желтый"}
        bot.answer_callback_query(c.id, f"Выбран {color_names.get(color_key)} цвет ✅")
    
    st["step"] = "creating"
    user_state[uid] = st
    
    try:
        card = create_poster(
            st["photo_bytes"],
            st["data"],
            st.get("highlight_phrase", ""),
            st.get("highlight_color")
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
            caption="🎉 <b>Афиша готова!</b>\n\nНажми кнопку для публикации:",
            parse_mode="HTML",
            reply_markup=preview_kb()
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        bot.send_message(c.message.chat.id, f"❌ Ошибка: {e}")

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
        bot.answer_callback_query(call.id, "✏️ Редактируем")
        bot.send_message(call.message.chat.id, "📝 Введи ЗАГОЛОВОК:")
    
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
        "👋 <b>Привет! Я создаю афиши для мероприятий</b>\n\n"
        "<b>📝 Порядок работы:</b>\n"
        "1️⃣ Отправь фото\n"
        "2️⃣ Отправь ЗАГОЛОВОК\n"
        "3️⃣ Отправь ПОДЗАГОЛОВОК\n"
        "4️⃣ Отправь ОПИСАНИЕ\n"
        "5️⃣ Отправь ДАТУ\n"
        "6️⃣ Отправь МЕСТО\n"
        "7️⃣ Отправь НИЖНИЙ ТЕКСТ\n"
        "8️⃣ Выбери фразу для выделения и цвет\n\n"
        "Нажми «Создать афишу» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda message: message.text == "🎨 Создать афишу")
def handle_create_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_photo", "data": {}}
    bot.send_message(message.chat.id, "🎨 <b>Создание афиши</b>\n\n📸 Пришли фото:", parse_mode="HTML")

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
            
            bot.reply_to(message, "📸 Фото сохранено!\n\n✏️ <b>Введи ЗАГОЛОВОК</b> (например: ВЕЧЕР ЖИВОЙ МУЗЫКИ):", parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Сначала нажми «🎨 Создать афишу»")

@bot.message_handler(content_types=["text"])
def on_text(message):
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state.get(uid) or {"step": "idle", "data": {}}
    step = st.get("step")
    
    # Заголовок
    if step == "waiting_title":
        st["data"]["title"] = text
        st["step"] = "waiting_subtitle"
        user_state[uid] = st
        bot.reply_to(message, f"✅ Заголовок: {text}\n\n✏️ <b>Введи ПОДЗАГОЛОВОК</b> (или отправь «-» чтобы пропустить):", parse_mode="HTML")
        return
    
    # Подзаголовок
    if step == "waiting_subtitle":
        st["data"]["subtitle"] = "" if text == "-" else text
        st["step"] = "waiting_body"
        user_state[uid] = st
        bot.reply_to(message, f"✅ Подзаголовок: {text if text != '-' else 'пропущен'}\n\n✏️ <b>Введи ОПИСАНИЕ</b> (основной текст):", parse_mode="HTML")
        return
    
    # Основной текст
    if step == "waiting_body":
        st["data"]["body"] = text
        st["step"] = "waiting_date"
        user_state[uid] = st
        bot.reply_to(message, f"✅ Описание сохранено\n\n✏️ <b>Введи ДАТУ</b> (или отправь «-» чтобы пропустить):", parse_mode="HTML")
        return
    
    # Дата
    if step == "waiting_date":
        st["data"]["date"] = "" if text == "-" else text
        st["step"] = "waiting_place"
        user_state[uid] = st
        bot.reply_to(message, f"✅ Дата: {text if text != '-' else 'пропущена'}\n\n✏️ <b>Введи МЕСТО</b> (или отправь «-» чтобы пропустить):", parse_mode="HTML")
        return
    
    # Место
    if step == "waiting_place":
        st["data"]["place"] = "" if text == "-" else text
        st["step"] = "waiting_footer"
        user_state[uid] = st
        bot.reply_to(message, f"✅ Место: {text if text != '-' else 'пропущено'}\n\n✏️ <b>Введи НИЖНИЙ ТЕКСТ</b> (или отправь «-» чтобы пропустить):", parse_mode="HTML")
        return
    
    # Нижний текст
    if step == "waiting_footer":
        st["data"]["footer"] = "" if text == "-" else text
        st["step"] = "waiting_highlight_phrase"
        user_state[uid] = st
        
        # Показываем превью без выделения
        try:
            card = create_poster(st["photo_bytes"], st["data"], "", None)
            
            st["card_bytes"] = card.getvalue()
            st["step"] = "waiting_highlight_phrase"
            user_state[uid] = st
            
            bot.send_photo(
                message.chat.id,
                photo=BytesIO(st["card_bytes"]),
                caption="🎉 <b>Предварительный просмотр</b>\n\n"
                       "✏️ <b>Напиши ФРАЗУ, которую нужно выделить цветом</b>\n"
                       "(или отправь «-» чтобы пропустить):",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error: {e}")
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    # Фраза для выделения
    if step == "waiting_highlight_phrase":
        if text == "-":
            # Без выделения - сразу показываем финальный результат
            try:
                card = create_poster(st["photo_bytes"], st["data"], "", None)
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
    bot.send_message(message.chat.id, "📝 Нажми «🎨 Создать афишу» чтобы начать", reply_markup=main_menu_kb())

# =========================
# Main
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")
    download_fonts()
    
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
