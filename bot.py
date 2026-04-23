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
WIDTH = 1080
HEIGHT = 1350

# Шрифты
FONT_BOLD = "Inter-ExtraBold.ttf"
FONT_REGULAR = "Inter-Regular.ttf"
FONT_FALLBACK = "Montserrat-Black.ttf"

# Размеры шрифтов (как было ранее)
FONT_SIZE_TITLE = 90      # вернули 90
FONT_SIZE_RUBRIC = 52
FONT_SIZE_DATE = 38
FONT_SIZE_PLACE = 38

# Отступы
MARGIN_LEFT = 70
RUBRIC_TOP_MARGIN = 60
RUBRIC_PADDING = 30        # ОДИНАКОВЫЙ отступ со всех сторон
TITLE_TOP_MARGIN = 220
DATE_BOTTOM_MARGIN = 160
LINE_SPACING = 15
TEXT_MAX_WIDTH = int(WIDTH * 0.80)

# Цвета
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# Цвета для выделения
COLORS = {
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
# Download fonts
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

def get_font(name: str, size: int):
    try:
        return ImageFont.truetype(name, size=size)
    except:
        try:
            return ImageFont.truetype(FONT_FALLBACK, size=size)
        except:
            return ImageFont.load_default()

# =========================
# Image helpers
# =========================
def crop_4x5(img: Image.Image) -> Image.Image:
    w, h = img.size
    target = 4 / 5
    if w / h > target:
        new_w = int(h * target)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))

def apply_gradient(img: Image.Image, direction: str) -> Image.Image:
    w, h = img.size
    gh = int(h * 0.48)
    if gh <= 0:
        return img
    
    overlay = Image.new("L", (w, h), 0)
    grad = Image.new("L", (1, gh), 0)
    
    for y in range(gh):
        if direction == "top":
            alpha = int(220 * (1 - y / gh))
        else:
            alpha = int(220 * (y / gh))
        grad.putpixel((0, y), alpha)
    
    grad = grad.resize((w, gh))
    
    if direction == "top":
        overlay.paste(grad, (0, 0))
    else:
        overlay.paste(grad, (0, h - gh))
    
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base = img.convert("RGBA")
    result = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay)
    result = Image.alpha_composite(base, result)
    return result.convert("RGB")

def text_width(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]

def wrap_text(draw, text: str, font, max_width: int) -> List[str]:
    words = text.split()
    if not words:
        return []
    
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
    return lines

def fit_font_size(draw, text: str, max_width: int, max_height: int, start_size: int = 90, min_size: int = 30):
    for size in range(start_size, min_size - 1, -2):
        font = get_font(FONT_BOLD, size)
        lines = wrap_text(draw, text, font, max_width)
        
        total_h = 0
        for ln in lines:
            bbox = draw.textbbox((0, 0), ln, font=font)
            total_h += bbox[3] - bbox[1]
        total_h += (len(lines) - 1) * LINE_SPACING
        
        if total_h <= max_height:
            return font, lines, total_h
    
    font = get_font(FONT_BOLD, min_size)
    lines = wrap_text(draw, text, font, max_width)
    total_h = 0
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        total_h += bbox[3] - bbox[1]
    total_h += (len(lines) - 1) * LINE_SPACING
    return font, lines, total_h

def draw_text_with_highlight(draw, text: str, highlight: str, color, font, x, y):
    if not highlight or highlight.lower() not in text.lower():
        draw.text((x, y), text, font=font, fill=WHITE)
        return
    
    text_lower = text.lower()
    word_lower = highlight.lower()
    pos = text_lower.find(word_lower)
    
    before = text[:pos]
    word = text[pos:pos + len(highlight)]
    after = text[pos + len(highlight):]
    
    cx = x
    if before:
        draw.text((cx, y), before, font=font, fill=WHITE)
        cx += text_width(draw, before, font)
    if word:
        draw.text((cx, y), word, font=font, fill=color)
        cx += text_width(draw, word, font)
    if after:
        draw.text((cx, y), after, font=font, fill=WHITE)

def draw_rubric(draw, rubric: str, color, y_offset: int = 0):
    """Рисует рубрику вверху по центру - ОДИНАКОВЫЕ ОТСТУПЫ 30px"""
    if not rubric:
        return 0
    
    font = get_font(FONT_BOLD, FONT_SIZE_RUBRIC)
    text = rubric.upper()
    
    # Получаем размеры текста
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    
    # Одинаковые отступы со всех сторон
    padding = RUBRIC_PADDING  # 30px
    rect_w = text_w + padding * 2
    rect_h = text_h + padding * 2
    
    # Прямоугольник по центру
    rect_x = (WIDTH - rect_w) // 2
    rect_y = RUBRIC_TOP_MARGIN + y_offset
    
    # Рисуем прямоугольник
    draw.rectangle([rect_x, rect_y, rect_x + rect_w, rect_y + rect_h], fill=color)
    
    # Текст со смещением на padding
    text_x = rect_x + padding
    text_y = rect_y + padding
    
    draw.text((text_x, text_y), text, font=font, fill=BLACK)
    
    return rect_y + rect_h

def draw_date_place(draw, date: str, place: str, color, y: int):
    font = get_font(FONT_REGULAR, FONT_SIZE_DATE)
    
    current_y = y
    
    if date:
        draw.text((MARGIN_LEFT, current_y), "ДАТА:", font=font, fill=color)
        label_w = text_width(draw, "ДАТА:", font)
        draw.text((MARGIN_LEFT + label_w, current_y), f" {date.upper()}", font=font, fill=WHITE)
        
        bbox = draw.textbbox((0, 0), f"ДАТА: {date.upper()}", font=font)
        current_y += bbox[3] - bbox[1] + 10
    
    if place:
        draw.text((MARGIN_LEFT, current_y), "МЕСТО:", font=font, fill=color)
        label_w = text_width(draw, "МЕСТО:", font)
        
        full_text = f" {place.upper()}"
        if text_width(draw, full_text, font) <= TEXT_MAX_WIDTH - label_w - 20:
            draw.text((MARGIN_LEFT + label_w, current_y), full_text, font=font, fill=WHITE)
        else:
            value = place.upper()
            value_lines = wrap_text(draw, value, font, TEXT_MAX_WIDTH - label_w - 30)
            for i, line in enumerate(value_lines):
                if i == 0:
                    draw.text((MARGIN_LEFT + label_w, current_y), f" {line}", font=font, fill=WHITE)
                else:
                    draw.text((MARGIN_LEFT + label_w, current_y), f" {line}", font=font, fill=WHITE)
                line_bbox = draw.textbbox((0, 0), line, font=font)
                current_y += line_bbox[3] - line_bbox[1] + 5
            return
    
    return current_y

def create_poster(photo_bytes: bytes, title: str, rubric: str, date: str, place: str,
                  highlight_word: str, highlight_color: tuple, text_position: str = "top") -> BytesIO:
    
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    img = crop_4x5(img)
    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(0.85)
    
    if text_position == "top":
        img = apply_gradient(img, "top")
    else:
        img = apply_gradient(img, "bottom")
    
    draw = ImageDraw.Draw(img)
    
    # Рисуем рубрику
    rubric_bottom = draw_rubric(draw, rubric, highlight_color)
    
    # Отступ для заголовка
    if rubric_bottom > 0:
        title_y = rubric_bottom + 80
    else:
        title_y = TITLE_TOP_MARGIN
    
    # Заголовок
    title_upper = title.upper()
    max_height = HEIGHT - title_y - 300
    font, lines, total_h = fit_font_size(draw, title_upper, TEXT_MAX_WIDTH, max_height, start_size=90)
    
    y = title_y
    for line in lines:
        line_w = text_width(draw, line, font)
        x = (WIDTH - line_w) // 2
        draw_text_with_highlight(draw, line, highlight_word, highlight_color, font, x, y)
        y += (draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1]) + LINE_SPACING
    
    # Дата и место
    if text_position == "top":
        date_y = HEIGHT - DATE_BOTTOM_MARGIN
        if date or place:
            draw_date_place(draw, date, place, highlight_color, date_y)
    else:
        date_y = rubric_bottom + 80 if rubric_bottom > 0 else 200
        if date or place:
            draw_date_place(draw, date, place, highlight_color, date_y)
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0)
    out.seek(0)
    return out

# =========================
# Keyboards
# =========================
def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🎨 Создать карточку"))
    return kb

def position_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⬆️ Текст сверху", callback_data="pos:top"),
        InlineKeyboardButton("⬇️ Текст снизу", callback_data="pos:bottom")
    )
    return kb

def color_kb():
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🔴 Красный", callback_data="color:red"),
        InlineKeyboardButton("🟡 Желтый", callback_data="color:yellow"),
        InlineKeyboardButton("🔵 Голубой", callback_data="color:blue")
    )
    kb.add(InlineKeyboardButton("➖ Пропустить", callback_data="color:none"))
    return kb

def yes_no_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Да", callback_data="yesno:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="yesno:no")
    )
    return kb

def publish_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    )
    return kb

# =========================
# Callbacks
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("pos:"))
def on_position(c):
    uid = c.from_user.id
    pos = c.data.split(":")[1]
    st = user_state.get(uid, {})
    st["text_position"] = pos
    st["step"] = "waiting_title"
    user_state[uid] = st
    
    pos_text = "сверху" if pos == "top" else "снизу"
    bot.answer_callback_query(c.id, f"Текст {pos_text} ✅")
    bot.edit_message_text(
        f"✅ Текст будет расположен <b>{pos_text}</b>\n\n✏️ Теперь отправь <b>ЗАГОЛОВОК</b>:",
        c.message.chat.id, c.message.message_id,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("yesno:"))
def on_yesno(c):
    uid = c.from_user.id
    choice = c.data.split(":")[1]
    st = user_state.get(uid, {})
    
    if choice == "yes":
        st["step"] = "waiting_date"
        user_state[uid] = st
        bot.answer_callback_query(c.id, "Добавляем ✅")
        bot.edit_message_text(
            f"✏️ <b>Введи ДАТУ</b> (например: 25 МАЯ, 19:00):",
            c.message.chat.id, c.message.message_id,
            parse_mode="HTML"
        )
    else:
        st["date"] = ""
        st["place"] = ""
        st["step"] = "waiting_highlight"
        user_state[uid] = st
        bot.answer_callback_query(c.id, "Пропускаем ✅")
        
        try:
            card = create_poster(
                st["photo_bytes"], st.get("title", ""), "", "", "",
                "", COLORS["yellow"], st.get("text_position", "top")
            )
            st["preview_bytes"] = card.getvalue()
            user_state[uid] = st
            
            bot.send_photo(
                c.message.chat.id, photo=BytesIO(st["preview_bytes"]),
                caption=f"✅ <b>Предпросмотр</b>\n\n✏️ <b>Напиши СЛОВО для выделения цветом</b>\n(или «-» чтобы пропустить):",
                parse_mode="HTML"
            )
            bot.delete_message(c.message.chat.id, c.message.message_id)
        except Exception as e:
            bot.send_message(c.message.chat.id, f"❌ Ошибка: {e}")
            logger.error(f"Error: {e}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("color:"))
def on_color(c):
    uid = c.from_user.id
    color_key = c.data.split(":")[1]
    st = user_state.get(uid, {})
    
    if color_key == "none":
        st["highlight_word"] = ""
        st["highlight_color"] = COLORS["yellow"]
    else:
        st["highlight_word"] = st.get("temp_highlight", "")
        st["highlight_color"] = COLORS.get(color_key, COLORS["yellow"])
    
    st["step"] = "waiting_rubric"
    user_state[uid] = st
    
    colors_name = {"red": "красный", "yellow": "желтый", "blue": "голубой", "none": "пропущен"}
    name = colors_name.get(color_key, "выбран")
    bot.answer_callback_query(c.id, f"Цвет {name} ✅")
    
    bot.send_message(
        c.message.chat.id,
        f"✏️ <b>Введи РУБРИКУ</b> (слово на цветном прямоугольнике вверху):",
        parse_mode="HTML"
    )
    bot.delete_message(c.message.chat.id, c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data in ["publish", "cancel"])
def on_publish(c):
    uid = c.from_user.id
    st = user_state.get(uid, {})
    
    if st.get("step") != "waiting_publish":
        bot.answer_callback_query(c.id, "Нет активного превью")
        return
    
    if c.data == "publish":
        try:
            if CHANNEL:
                bot.send_photo(CHANNEL, BytesIO(st["card_bytes"]))
                bot.answer_callback_query(c.id, "Опубликовано ✅")
                bot.send_message(c.message.chat.id, "✅ Готово!", reply_markup=main_menu_kb())
            else:
                bot.answer_callback_query(c.id, "❌ Канал не настроен")
        except Exception as e:
            bot.answer_callback_query(c.id, f"❌ {e}")
    else:
        bot.answer_callback_query(c.id, "Отменено ❌")
        bot.send_message(c.message.chat.id, "❌ Отменено", reply_markup=main_menu_kb())
    
    clear_state(uid)

# =========================
# Message handlers
# =========================
def clear_state(uid: int):
    if uid in user_state:
        user_state[uid] = {"step": "idle"}

@bot.message_handler(commands=["start"])
def cmd_start(m):
    clear_state(m.from_user.id)
    bot.send_message(
        m.chat.id,
        "👋 <b>Бот для создания карточек</b>\n\n"
        "📝 Просто отправь фото и следуй инструкциям.\n\n"
        "✅ Выбранный цвет применяется к:\n"
        "• Выделенному слову\n"
        "• Словам ДАТА: и МЕСТО:\n"
        "• Прямоугольнику с рубрикой\n\n"
        "Нажми «Создать карточку» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

@bot.message_handler(func=lambda m: m.text == "🎨 Создать карточку")
def new_card(m):
    uid = m.from_user.id
    user_state[uid] = {"step": "waiting_photo"}
    bot.send_message(m.chat.id, "🎨 <b>Создание карточки</b>\n\n📸 Пришли фото:", parse_mode="HTML")

@bot.message_handler(content_types=["photo"])
def handle_photo(m):
    uid = m.from_user.id
    st = user_state.get(uid, {})
    
    if st.get("step") != "waiting_photo":
        bot.reply_to(m, "❌ Сначала нажми «Создать карточку»")
        return
    
    try:
        file_id = m.photo[-1].file_id
        file_info = bot.get_file(file_id)
        photo_bytes = bot.download_file(file_info.file_path)
        
        st["photo_bytes"] = photo_bytes
        st["step"] = "waiting_position"
        user_state[uid] = st
        
        bot.reply_to(
            m,
            "📸 Фото сохранено!\n\n📐 <b>Выбери расположение текста:</b>",
            parse_mode="HTML",
            reply_markup=position_kb()
        )
    except Exception as e:
        bot.reply_to(m, f"❌ Ошибка: {e}")

@bot.message_handler(content_types=["text"])
def handle_text(m):
    uid = m.from_user.id
    text = m.text.strip()
    st = user_state.get(uid, {"step": "idle"})
    step = st.get("step")
    
    if step == "waiting_title":
        if not text:
            bot.reply_to(m, "❌ Заголовок не может быть пустым")
            return
        
        st["title"] = text
        st["step"] = "waiting_date_place"
        user_state[uid] = st
        
        bot.reply_to(
            m,
            f"✅ Заголовок: <b>{html.escape(text)}</b>\n\n📅 <b>Добавить дату и место?</b>",
            parse_mode="HTML",
            reply_markup=yes_no_kb()
        )
        return
    
    if step == "waiting_date":
        st["date"] = text
        st["step"] = "waiting_place"
        user_state[uid] = st
        bot.reply_to(m, f"✅ Дата: {text}\n\n✏️ <b>Введи МЕСТО</b>:", parse_mode="HTML")
        return
    
    if step == "waiting_place":
        st["place"] = text
        st["step"] = "waiting_highlight"
        user_state[uid] = st
        
        try:
            card = create_poster(
                st["photo_bytes"], st.get("title", ""), "", 
                st.get("date", ""), st.get("place", ""),
                "", COLORS["yellow"], st.get("text_position", "top")
            )
            st["preview_bytes"] = card.getvalue()
            user_state[uid] = st
            
            bot.send_photo(
                m.chat.id, photo=BytesIO(st["preview_bytes"]),
                caption=f"✅ <b>Предпросмотр</b>\n\n✏️ <b>Напиши СЛОВО для выделения цветом</b>\n(или «-» чтобы пропустить):",
                parse_mode="HTML"
            )
        except Exception as e:
            bot.reply_to(m, f"❌ Ошибка: {e}")
            logger.error(f"Error: {e}")
        return
    
    if step == "waiting_highlight":
        if text == "-":
            st["highlight_word"] = ""
            st["highlight_color"] = COLORS["yellow"]
            st["step"] = "waiting_rubric"
            user_state[uid] = st
            bot.reply_to(m, f"✏️ <b>Введи РУБРИКУ</b>:", parse_mode="HTML")
        else:
            title = st.get("title", "").lower()
            if text.lower() in title:
                st["temp_highlight"] = text
                st["step"] = "waiting_color"
                user_state[uid] = st
                bot.reply_to(
                    m,
                    f"✅ Слово «{text}» <b>НАЙДЕНО</b> в заголовке!\n\n🎨 <b>Выбери цвет:</b>",
                    parse_mode="HTML",
                    reply_markup=color_kb()
                )
            else:
                bot.reply_to(
                    m,
                    f"⚠️ Слово «{text}» <b>НЕ НАЙДЕНО</b> в заголовке!\n\nЗаголовок: «{st.get('title', '')}»\n\nПопробуй другое слово или нажми «-»",
                    parse_mode="HTML"
                )
        return
    
    if step == "waiting_rubric":
        st["rubric"] = text
        st["step"] = "creating"
        user_state[uid] = st
        
        try:
            card = create_poster(
                st["photo_bytes"], st.get("title", ""), st.get("rubric", ""),
                st.get("date", ""), st.get("place", ""),
                st.get("highlight_word", ""), st.get("highlight_color", COLORS["yellow"]),
                st.get("text_position", "top")
            )
            
            st["card_bytes"] = card.getvalue()
            st["step"] = "waiting_publish"
            user_state[uid] = st
            
            bot.send_photo(
                m.chat.id, photo=BytesIO(st["card_bytes"]),
                caption="🎉 <b>Карточка готова!</b>\n\nНажми кнопку для публикации:",
                parse_mode="HTML",
                reply_markup=publish_kb()
            )
        except Exception as e:
            logger.error(f"Error: {e}")
            bot.reply_to(m, f"❌ Ошибка: {e}")
        return
    
    bot.send_message(m.chat.id, "📝 Нажми «Создать карточку»", reply_markup=main_menu_kb())

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
            bot.infinity_polling(timeout=30, skip_pending=True)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            if "409" in str(e):
                logger.info("Conflict, waiting 20s...")
                time.sleep(20)
            else:
                time.sleep(5)
