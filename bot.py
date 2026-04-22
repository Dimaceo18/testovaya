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
FONT_PATH = "Montserrat-Black.ttf"
FONT_FALLBACK = "CaviarDreams.ttf"

# Размеры шрифта
FONT_SIZE_TITLE = 90
FONT_SIZE_MIN = 30

# Затемнение фото
BRIGHTNESS_FACTOR = 0.85

# Градиент
GRADIENT_HEIGHT_PCT = 0.48
GRADIENT_MAX_ALPHA = 220

# Отступы
MARGIN_X_PCT = 0.06
MARGIN_TOP_PCT = 0.08
MARGIN_BOTTOM_PCT = 0.08
LINE_SPACING_RATIO = 0.22

# Цвета для выделения
HIGHLIGHT_COLORS = {
    "red": (255, 80, 80),     # красный
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
def download_font():
    """Скачивает шрифт если нет"""
    if os.path.exists(FONT_PATH):
        return True
    
    url = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Black.ttf"
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

def apply_top_gradient(img: Image.Image, height_pct: float, max_alpha: int = 220) -> Image.Image:
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

def text_width(draw, s: str, font) -> int:
    bbox = draw.textbbox((0, 0), s, font=font)
    return bbox[2] - bbox[0]

def wrap_no_truncate(draw, text: str, font, max_width: int, max_lines: int = 6) -> Tuple[List[str], bool]:
    words = [w for w in (text or "").split() if w.strip()]
    if not words:
        return [""], True

    lines: List[str] = []
    cur = ""
    i = 0

    while i < len(words):
        w = words[i]
        test = (cur + " " + w).strip()
        if text_width(draw, test, font) <= max_width:
            cur = test
            i += 1
        else:
            if not cur:
                return [words[i]], False
            lines.append(cur)
            cur = ""
            if len(lines) >= max_lines:
                return lines, False

    if cur:
        lines.append(cur)

    if len(lines) > max_lines:
        return lines[:max_lines], False

    return lines, True

def fit_text_block(draw, text: str, font_path: str, safe_w: int, max_block_h: int,
                   max_lines: int = 6, start_size: int = 90, min_size: int = 16,
                   line_spacing_ratio: float = 0.22):
    text = (text or "").strip()
    if not text:
        text = " "

    size = start_size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        lines, ok = wrap_no_truncate(draw, text, font, safe_w, max_lines=max_lines)
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

    font = ImageFont.truetype(font_path, min_size)
    lines, _ = wrap_no_truncate(draw, text, font, safe_w, max_lines=max_lines)
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

def draw_text_with_highlight(draw, line: str, highlight_phrase: str, highlight_color, font, x, y):
    """Рисует строку текста с выделением фразы цветом"""
    line_upper = line.upper()
    highlight_upper = highlight_phrase.upper() if highlight_phrase else ""
    
    if not highlight_upper or highlight_upper not in line_upper:
        draw.text((x, y), line_upper, font=font, fill="white")
        return y
    
    # Разбиваем строку на части
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

def create_poster_chp(image_bytes: bytes, title_text: str, text_position: str,
                      highlight_phrase: str = "", highlight_color: tuple = None) -> BytesIO:
    """Шаблон ЧП ВМ с выделением фразы"""
    
    # Открываем фото
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img = crop_to_4x5(img)
    img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
    
    # Яркость
    img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
    
    # Градиент
    if text_position == "top":
        img = apply_top_gradient(img, height_pct=GRADIENT_HEIGHT_PCT, max_alpha=GRADIENT_MAX_ALPHA)
    else:
        img = apply_bottom_gradient(img, height_pct=GRADIENT_HEIGHT_PCT, max_alpha=GRADIENT_MAX_ALPHA)
    
    draw = ImageDraw.Draw(img)
    
    # Отступы
    margin_x = int(TARGET_W * MARGIN_X_PCT)
    margin_top = int(TARGET_H * MARGIN_TOP_PCT)
    margin_bottom = int(TARGET_H * MARGIN_BOTTOM_PCT)
    safe_w = TARGET_W - 2 * margin_x
    
    # Текст в верхний регистр
    text = (title_text or "").strip().upper()
    title_max_h = int(TARGET_H * 0.23)
    
    # Подбор шрифта
    font, lines, heights, spacing, total_h = fit_text_block(
        draw=draw,
        text=text,
        font_path=FONT_PATH,
        safe_w=safe_w,
        max_block_h=title_max_h,
        max_lines=6,
        start_size=int(TARGET_H * 0.11),
        min_size=FONT_SIZE_MIN,
        line_spacing_ratio=LINE_SPACING_RATIO
    )
    
    # Позиция текста
    if text_position == "top":
        y = margin_top
    else:
        y = TARGET_H - margin_bottom - total_h
    
    # Рисуем строки с выделением
    for i, ln in enumerate(lines):
        # Центрируем строку
        line_w = text_width(draw, ln, font)
        x = (TARGET_W - line_w) // 2
        
        # Рисуем с выделением
        draw_text_with_highlight(draw, ln, highlight_phrase, highlight_color, font, x, y)
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
        f"✅ Текст будет расположен <b>{pos_text}</b> фотографии.\n\n"
        f"✏️ Теперь отправь <b>ЗАГОЛОВОК</b>:",
        c.message.chat.id, c.message.message_id,
        parse_mode="HTML"
    )

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
        card = create_poster_chp(
            st["photo_bytes"],
            st.get("title", ""),
            st.get("text_position", "top"),
            st.get("highlight_phrase", ""),
            st.get("highlight_color")
        )
        
        st["card_bytes"] = card.getvalue()
        st["step"] = "waiting_action"
        user_state[uid] = st
        
        bot.send_photo(
            c.message.chat.id,
            photo=BytesIO(st["card_bytes"]),
            caption="🎉 <b>Карточка готова!</b>\n\nНажми кнопку для публикации:",
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
        "👋 <b>Привет! Я бот для оформления постов в стиле ЧП ВМ</b>\n\n"
        "<b>📝 Как работает:</b>\n"
        "1️⃣ Отправь фото\n"
        "2️⃣ Выбери расположение текста (сверху/снизу)\n"
        "3️⃣ Отправь ЗАГОЛОВОК\n"
        "4️⃣ Отправь ФРАЗУ для выделения цветом\n"
        "5️⃣ Выбери цвет: 🔴 красный или 🟡 желтый\n\n"
        "Нажми «Шаблон ЧП ВМ» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda message: message.text == "🚨 Шаблон ЧП ВМ")
def handle_template_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_photo"}
    bot.send_message(
        message.chat.id,
        "🚨 <b>Шаблон ЧП ВМ</b>\n\n"
        "📸 Пришли фото для поста:",
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
            st["step"] = "waiting_text_position"
            user_state[uid] = st
            
            bot.reply_to(
                message,
                "📸 Фото сохранено!\n\n"
                "📐 <b>Выбери расположение текста:</b>",
                parse_mode="HTML",
                reply_markup=text_position_kb()
            )
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Сначала нажми «🚨 Шаблон ЧП ВМ»")

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
        st["step"] = "waiting_highlight_phrase"
        user_state[uid] = st
        
        # Показываем превью без выделения
        try:
            card = create_poster_chp(
                st["photo_bytes"],
                text,
                st.get("text_position", "top"),
                "",
                None
            )
            st["preview_bytes"] = card.getvalue()
            user_state[uid] = st
            
            bot.send_photo(
                message.chat.id,
                photo=BytesIO(st["preview_bytes"]),
                caption=f"✅ Заголовок: <b>{html.escape(text)}</b>\n\n"
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
            # Без выделения - сразу финальный результат
            try:
                card = create_poster_chp(
                    st["photo_bytes"],
                    st.get("title", ""),
                    st.get("text_position", "top"),
                    "",
                    None
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
    bot.send_message(
        message.chat.id,
        "📝 Нажми «🚨 Шаблон ЧП ВМ» чтобы начать",
        reply_markup=main_menu_kb()
    )

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
