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

# Размеры (4:5)
TARGET_W = 1080
TARGET_H = 1350

# Шрифт
FONT_PATH = "Montserrat-Regular.ttf"
FONT_BOLD = "Montserrat-Bold.ttf"

# Размеры шрифта (как на фото - средний, компактный)
FONT_SIZE_NORMAL = 48
FONT_SIZE_BOLD = 52
LINE_SPACING = 12

# Затемнение фото
BRIGHTNESS_FACTOR = 0.55

# Вертикальный градиент (сверху вниз)
GRADIENT_HEIGHT_PCT = 0.35      # градиент занимает 35% высоты сверху
GRADIENT_MAX_ALPHA = 200

# Отступы (как на фото - слева хороший отступ)
MARGIN_LEFT = 80
MARGIN_TOP = 300
MAX_TEXT_WIDTH = TARGET_W - MARGIN_LEFT - 80   # максимальная ширина текста

# Цвета для выделения
HIGHLIGHT_COLORS = {
    "red": (255, 80, 80),
    "yellow": (255, 220, 80)
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
        "Montserrat-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Regular.ttf",
        "Montserrat-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Bold.ttf"
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
    cur_ratio = w / h
    if cur_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))

def apply_top_gradient(img: Image.Image, height_pct: float, max_alpha: int = 200) -> Image.Image:
    """Вертикальный градиент сверху вниз"""
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

def text_width(draw, s: str, font) -> int:
    bbox = draw.textbbox((0, 0), s, font=font)
    return bbox[2] - bbox[0]

def wrap_text(draw, text: str, font, max_width: int) -> List[str]:
    """Перенос текста по словам"""
    words = text.split()
    if not words:
        return []
    
    lines = []
    current = words[0]
    
    for word in words[1:]:
        candidate = current + " " + word
        if text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines

def draw_text_with_highlight(draw, line: str, highlight_phrase: str, highlight_color, font, x, y):
    """Рисует строку с выделением фразы цветом"""
    line_upper = line.upper()
    highlight_upper = highlight_phrase.upper() if highlight_phrase else ""
    
    if not highlight_upper or highlight_upper not in line_upper:
        draw.text((x, y), line, font=font, fill="white")
        return y
    
    parts = line_upper.split(highlight_upper)
    current_x = x
    
    for i, part in enumerate(parts):
        if part:
            draw.text((current_x, y), part, font=font, fill="white")
            current_x += text_width(draw, part, font)
        
        if i < len(parts) - 1:
            draw.text((current_x, y), highlight_upper, font=font, fill=highlight_color)
            current_x += text_width(draw, highlight_upper, font)
    
    return y

def create_poster(image_bytes: bytes, title_text: str, highlight_phrase: str = "", highlight_color: tuple = None) -> BytesIO:
    """Создает постер как на фото"""
    
    # Открываем фото
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    
    # Затемнение
    img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
    
    # Вертикальный градиент сверху
    img = apply_top_gradient(img, height_pct=GRADIENT_HEIGHT_PCT, max_alpha=GRADIENT_MAX_ALPHA)
    
    draw = ImageDraw.Draw(img)
    
    # Шрифты
    font_bold = load_font(FONT_BOLD, FONT_SIZE_BOLD)
    font_normal = load_font(FONT_PATH, FONT_SIZE_NORMAL)
    
    # Текст в верхний регистр
    text = (title_text or "").strip().upper()
    
    # Разбиваем на строки
    lines = wrap_text(draw, text, font_normal, MAX_TEXT_WIDTH)
    
    # Позиция текста (как на фото - сверху с отступом)
    y = MARGIN_TOP
    
    # Рисуем первую строку (может быть жирным как заголовок)
    if lines:
        first_line = lines[0]
        # Первая строка жирным шрифтом
        draw_text_with_highlight(draw, first_line, highlight_phrase, highlight_color, font_bold, MARGIN_LEFT, y)
        y += FONT_SIZE_BOLD + LINE_SPACING
    
    # Остальные строки обычным шрифтом
    for line in lines[1:]:
        y = draw_text_with_highlight(draw, line, highlight_phrase, highlight_color, font_normal, MARGIN_LEFT, y)
        y += FONT_SIZE_NORMAL + LINE_SPACING
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0)
    out.seek(0)
    return out

# =========================
# Keyboard
# =========================
def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📝 Создать пост"))
    return kb

def color_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🔴 Красный", callback_data="color:red"),
        InlineKeyboardButton("🟡 Желтый", callback_data="color:yellow")
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
        st["highlight_phrase"] = ""
        st["highlight_color"] = None
        bot.answer_callback_query(c.id, "Без выделения ✅")
    else:
        st["highlight_phrase"] = st.get("temp_highlight_phrase", "")
        st["highlight_color"] = HIGHLIGHT_COLORS.get(color_key)
        color_names = {"red": "красный", "yellow": "желтый"}
        bot.answer_callback_query(c.id, f"Выбран {color_names.get(color_key)} цвет ✅")
    
    st["step"] = "creating"
    user_state[uid] = st
    
    try:
        card = create_poster(
            st["photo_bytes"],
            st.get("title", ""),
            st.get("highlight_phrase", ""),
            st.get("highlight_color")
        )
        
        st["card_bytes"] = card.getvalue()
        st["step"] = "waiting_action"
        user_state[uid] = st
        
        bot.send_photo(
            c.message.chat.id,
            photo=BytesIO(st["card_bytes"]),
            caption="🎉 <b>Пост готов!</b>\n\nНажми кнопку для публикации:",
            parse_mode="HTML",
            reply_markup=preview_kb()
        )
        bot.delete_message(c.message.chat.id, c.message.message_id)
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
        "👋 <b>Привет! Я делаю посты как на фото</b>\n\n"
        "<b>📝 Как работает:</b>\n"
        "1️⃣ Отправь фото\n"
        "2️⃣ Отправь ТЕКСТ\n"
        "3️⃣ Отправь ФРАЗУ для выделения цветом\n"
        "4️⃣ Выбери цвет: 🔴 красный или 🟡 желтый\n\n"
        "<b>📐 Настройки:</b>\n"
        "• Размер: 1080×1350 (4:5)\n"
        "• Шрифт: Montserrat\n"
        "• Выравнивание: по левому краю\n"
        "• Градиент: сверху вниз\n"
        "• Без плашки под текстом\n\n"
        "Нажми «Создать пост» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda message: message.text == "📝 Создать пост")
def handle_create_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_photo"}
    bot.send_message(
        message.chat.id,
        "📝 <b>Создание поста</b>\n\n"
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
                "✏️ <b>Введи ТЕКСТ</b> (будет наложен на фото):",
                parse_mode="HTML"
            )
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Сначала нажми «📝 Создать пост»")

@bot.message_handler(content_types=["text"])
def on_text(message):
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state.get(uid) or {"step": "idle"}
    step = st.get("step")
    
    # Текст поста
    if step == "waiting_title":
        if not text:
            bot.reply_to(message, "❌ Текст не может быть пустым")
            return
        
        st["title"] = text
        st["step"] = "waiting_highlight_phrase"
        user_state[uid] = st
        
        # Показываем превью без выделения
        try:
            card = create_poster(
                st["photo_bytes"],
                text,
                "",
                None
            )
            st["preview_bytes"] = card.getvalue()
            user_state[uid] = st
            
            bot.send_photo(
                message.chat.id,
                photo=BytesIO(st["preview_bytes"]),
                caption=f"✅ Текст сохранён!\n\n"
                       f"✏️ <b>Напиши ФРАЗУ, которую нужно выделить цветом</b>\n"
                       f"(или отправь «-» чтобы пропустить):",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error: {e}")
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    # Фраза для выделения
    if step == "waiting_highlight_phrase":
        if text == "-":
            card = create_poster(
                st["photo_bytes"],
                st.get("title", ""),
                "",
                None
            )
            st["card_bytes"] = card.getvalue()
            st["step"] = "waiting_action"
            user_state[uid] = st
            
            bot.send_photo(
                message.chat.id,
                photo=BytesIO(st["card_bytes"]),
                caption="🎉 <b>Пост готов!</b>\n\nНажми кнопку для публикации:",
                parse_mode="HTML",
                reply_markup=preview_kb()
            )
        else:
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
    
    bot.send_message(
        message.chat.id,
        "📝 Нажми «📝 Создать пост» чтобы начать",
        reply_markup=main_menu_kb()
    )

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
