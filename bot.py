import asyncio
import sqlite3
import os
import re
import io
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from fastapi import FastAPI
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from openai import AsyncOpenAI

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/minsk_news")
SUGGEST_LINK = os.getenv("SUGGEST_LINK", "https://t.me/minsk_news_bot?start=suggest")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DB_PATH = "news.db"

# Водяной знак
WATERMARK_TEXT = "MINSK NEWS"
WATERMARK_OPACITY = 38  # 15% от 255 = 38

# Инициализация DeepSeek клиента
deepseek_client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
) if DEEPSEEK_API_KEY else None

# Промпт для DeepSeek с форматированием абзацев
DEEPSEEK_PROMPT = """Ты редактор новостного сайта, у тебя строгий новостной городской формат. Без обращений на вы, ты и т.д. Только новостной формат.

Тебе нужно переделывать новость с большого объема в новость на 650 символов.
Убирая всю лишнюю воду, текст, делать интересным заголовок, никаких смайликов. Сохраняй главные факты, проверяй всю информацию несколько раз, чтобы не было никаких ошибок.

Важно: текст должен быть разбит на логические абзацы (по 2-4 предложения в абзаце). Между абзацами должна быть пустая строка.

Верни только готовую новость в формате:
Заголовок: (заголовок новости)
Текст: (текст новости на 650 символов с абзацами)"""

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                photo_bytes BLOB,
                schedule_time TIMESTAMP,
                created_at TIMESTAMP,
                has_buttons BOOLEAN DEFAULT 1,
                has_watermark BOOLEAN DEFAULT 0,
                is_designed BOOLEAN DEFAULT 0,
                is_video BOOLEAN DEFAULT 0,
                video_file_id TEXT
            )
        """)
    print("✅ База данных готова")

def save_scheduled_post(text: str, photo_bytes: bytes, schedule_time: datetime, has_buttons: bool = True, has_watermark: bool = False, is_designed: bool = False, is_video: bool = False, video_file_id: str = None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO scheduled_posts (text, photo_bytes, schedule_time, created_at, has_buttons, has_watermark, is_designed, is_video, video_file_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (text, photo_bytes, schedule_time, datetime.now(), has_buttons, has_watermark, is_designed, is_video, video_file_id)
        )

def get_pending_scheduled_posts() -> List[Dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        result = conn.execute(
            "SELECT id, text, photo_bytes, schedule_time, has_buttons, has_watermark, is_designed, is_video, video_file_id FROM scheduled_posts WHERE schedule_time <= ?",
            (datetime.now(),)
        ).fetchall()
        return [dict(row) for row in result]

def delete_scheduled_post(post_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM scheduled_posts WHERE id = ?", (post_id,))

# ==================== ОЧИСТКА ТЕКСТА ====================
def remove_emojis(text: str) -> str:
    if not text:
        return ""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"
        "\U0001FA70-\U0001FAFF"
        "]+",
        flags=re.UNICODE
    )
    return emoji_pattern.sub(r'', text)

def format_caption(title: str, body: str) -> str:
    if body and body.strip():
        return f"<b>{title}</b>\n\n{body}"
    else:
        return f"<b>{title}</b>"

# ==================== КНОПКИ ====================
def get_post_publish_keyboard():
    keyboard = [
        [InlineKeyboardButton("📢 Подписаться на канал", url=CHANNEL_LINK)],
        [InlineKeyboardButton("📝 Прислать нам новость", url=SUGGEST_LINK)]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ОБРАБОТКА ФОТО С ВОДЯНЫМ ЗНАКОМ ====================
def add_watermark_to_image(image: Image.Image) -> Image.Image:
    """Добавляет полупрозрачный водяной знак по центру изображения"""
    # Создаем копию изображения
    img = image.copy()
    
    # Конвертируем в RGBA для работы с прозрачностью
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    
    # Создаем слой для водяного знака
    watermark = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(watermark)
    
    # Рассчитываем размер шрифта (8% от меньшей стороны)
    font_size = min(img.width, img.height) // 12
    
    # Пытаемся загрузить шрифт
    font = None
    font_paths = [
        "Montserrat-Bold.ttf",
        "Montserrat-Black.ttf",
        "fonts/Montserrat-Bold.ttf",
        "/app/Montserrat-Bold.ttf",
        "arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]
    
    for font_path in font_paths:
        try:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
                print(f"✅ Загружен шрифт для водяного знака: {font_path}")
                break
        except Exception as e:
            continue
    
    if font is None:
        font = ImageFont.load_default()
        print("⚠️ Шрифт для водяного знака не найден, использую стандартный")
    
    # Получаем размер текста
    bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    # Позиция по центру
    x = (img.width - text_width) // 2
    y = (img.height - text_height) // 2
    
    # Рисуем текст с прозрачностью
    draw.text((x, y), WATERMARK_TEXT, font=font, fill=(255, 255, 255, WATERMARK_OPACITY))
    
    # Объединяем слои
    result = Image.alpha_composite(img, watermark)
    
    # Конвертируем обратно в RGB для сохранения
    return result.convert('RGB')

def add_watermark_only(photo_bytes: bytes) -> io.BytesIO:
    """Только добавляет водяной знак без оформления"""
    if not photo_bytes or len(photo_bytes) == 0:
        raise ValueError("Фото пустое")
    
    print(f"💧 Добавляю водяной знак, размер: {len(photo_bytes) / 1024:.1f}KB")
    
    # Открываем изображение
    img = Image.open(io.BytesIO(photo_bytes))
    
    # Добавляем водяной знак
    img_with_watermark = add_watermark_to_image(img)
    
    # Сохраняем результат
    output = io.BytesIO()
    img_with_watermark.save(output, format="JPEG", quality=90, optimize=True)
    output.seek(0)
    
    print(f"✅ Водяной знак добавлен, размер: {output.getbuffer().nbytes / (1024 * 1024):.2f}MB")
    return output

def wrap_text_auto(text: str, font, max_width: int, max_lines: int = 6) -> List[str]:
    words = text.split()
    lines = []
    current_line = []
    for word in words:
        test_line = ' '.join(current_line + [word])
        try:
            bbox = font.getbbox(test_line)
            width = bbox[2] - bbox[0]
        except:
            width = len(test_line) * 20
        if width <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
                current_line = [word]
            else:
                lines.append(word)
        if len(lines) >= max_lines:
            break
    if current_line and len(lines) < max_lines:
        lines.append(' '.join(current_line))
    return lines

def process_photo(photo_bytes: bytes, title_text: str, add_watermark_flag: bool = False) -> io.BytesIO:
    if not photo_bytes or len(photo_bytes) == 0:
        raise ValueError("Фото пустое")
    print(f"🖼️ Обработка фото, размер: {len(photo_bytes) / 1024:.1f}KB, водяной знак: {add_watermark_flag}")
    
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    w, h = img.size
    target_ratio = 4 / 5
    cur_ratio = w / h
    if cur_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    img = img.resize((1080, 1350), Image.Resampling.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(0.85)
    w, h = img.size
    gh = int(h * 0.48)
    if gh > 0:
        overlay_alpha = Image.new("L", (w, h), 0)
        grad = Image.new("L", (1, gh), 0)
        for y in range(gh):
            a = int(220 * (y / max(1, gh - 1)))
            grad.putpixel((0, y), a)
        grad = grad.resize((w, gh))
        overlay_alpha.paste(grad, (0, h - gh))
        black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        base = img.convert("RGBA")
        overlay = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
        img = Image.alpha_composite(base, overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    
    font = None
    font_size = 68
    
    font_paths = [
        "Montserrat-Black.ttf",
        "fonts/Montserrat-Black.ttf",
        "/app/Montserrat-Black.ttf",
        "Montserrat-Bold.ttf",
    ]
    
    for font_path in font_paths:
        try:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
                print(f"✅ Загружен шрифт: {font_path}")
                break
        except:
            continue
    
    if font is None:
        font = ImageFont.load_default()
        print("⚠️ Шрифт не найден, использую стандартный")
    
    margin_x = int(img.width * 0.05)
    margin_bottom = int(img.height * 0.08)
    max_text_width = img.width - 2 * margin_x
    title = title_text.upper()
    lines = wrap_text_auto(title, font, max_text_width, max_lines=6)
    
    if font == ImageFont.load_default():
        line_height = 35
        spacing = 10
    else:
        line_height = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
        spacing = int(line_height * 0.25)
    
    total_text_height = len(lines) * line_height + (len(lines) - 1) * spacing
    y = img.height - margin_bottom - total_text_height
    
    for line in lines:
        if font == ImageFont.load_default():
            line_width = len(line) * 20
        else:
            bbox = font.getbbox(line)
            line_width = bbox[2] - bbox[0]
        x = (img.width - line_width) // 2
        
        offsets = [(-2, -2), (-2, 2), (2, -2), (2, 2), (0, -2), (0, 2), (-2, 0), (2, 0)]
        for dx, dy in offsets:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height + spacing
    
    if add_watermark_flag:
        img = add_watermark_to_image(img)
    
    output = io.BytesIO()
    quality = 85
    while quality >= 60:
        output.seek(0)
        output.truncate()
        img.save(output, format="JPEG", quality=quality, subsampling=0, optimize=True)
        size = output.tell() / (1024 * 1024)
        if size <= 15:
            break
        quality -= 10
    output.seek(0)
    if output.getbuffer().nbytes == 0:
        raise ValueError("Результирующий файл пустой")
    print(f"✅ Фото готово: {output.getbuffer().nbytes / (1024 * 1024):.2f}MB, строк: {len(lines)}")
    return output

# ==================== КНОПКИ ДЛЯ ПОСТОВ ====================
def get_main_keyboard():
    keyboard = [[InlineKeyboardButton("📸 Отправить фото или видео", callback_data="send_media_info")]]
    return InlineKeyboardMarkup(keyboard)

def get_media_preview_keyboard(media_type: str):
    if media_type == "video":
        keyboard = [
            [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_video_with_buttons")],
            [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_video_no_buttons")],
            [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_video_text")],
            [InlineKeyboardButton("🤖 Обработать текст (ИИ)", callback_data="ai_process_video")],
            [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_video_menu")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_raw_with_buttons")],
            [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_raw_no_buttons")],
            [InlineKeyboardButton("🎨 Оформить пост", callback_data="design_post")],
            [InlineKeyboardButton("💧 Добавить водяной знак", callback_data="add_watermark_only")],
            [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_text")],
            [InlineKeyboardButton("🤖 Обработать текст (ИИ)", callback_data="ai_process")],
            [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_menu")]
        ]
    return InlineKeyboardMarkup(keyboard)

def get_watermark_preview_keyboard():
    keyboard = [
        [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_watermarked_with_buttons")],
        [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_watermarked_no_buttons")],
        [InlineKeyboardButton("🎨 Оформить пост", callback_data="design_post_from_watermark")],
        [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_text")],
        [InlineKeyboardButton("🤖 Обработать текст (ИИ)", callback_data="ai_process")],
        [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_menu_watermark")],
        [InlineKeyboardButton("◀️ Назад к исходному", callback_data="back_to_original")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_designed_post_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Опубликовать (с кнопками)", callback_data="publish_designed_with_buttons")],
        [InlineKeyboardButton("✅ Опубликовать (без кнопок)", callback_data="publish_designed_no_buttons")],
        [InlineKeyboardButton("💧 Добавить водяной знак", callback_data="add_watermark_to_designed")],
        [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_designed_text")],
        [InlineKeyboardButton("🤖 Обработать текст (ИИ)", callback_data="ai_process_designed")],
        [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_designed")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_ai_result_keyboard(media_type: str = "photo"):
    if media_type == "video":
        keyboard = [
            [InlineKeyboardButton("📤 Опубликовать видео (с кнопками)", callback_data="publish_video_with_buttons")],
            [InlineKeyboardButton("📤 Опубликовать видео (без кнопок)", callback_data="publish_video_no_buttons")],
            [InlineKeyboardButton("📝 Отправить новый запрос ИИ", callback_data="ai_new_request_video")],
            [InlineKeyboardButton("✏️ Редактировать вручную", callback_data="edit_video_text")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_video_preview")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("📤 Опубликовать (с кнопками)", callback_data="publish_raw_with_buttons")],
            [InlineKeyboardButton("📤 Опубликовать (без кнопок)", callback_data="publish_raw_no_buttons")],
            [InlineKeyboardButton("🎨 Оформить пост", callback_data="design_post")],
            [InlineKeyboardButton("💧 Добавить водяной знак", callback_data="add_watermark_only")],
            [InlineKeyboardButton("📝 Отправить новый запрос ИИ", callback_data="ai_new_request")],
            [InlineKeyboardButton("✏️ Редактировать вручную", callback_data="edit_text")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_preview")]
        ]
    return InlineKeyboardMarkup(keyboard)

def get_ai_request_keyboard(media_type: str = "photo"):
    keyboard = [
        [InlineKeyboardButton("📝 Написать свой запрос", callback_data=f"ai_custom_request_{media_type}")],
        [InlineKeyboardButton("🔄 Использовать стандартный", callback_data=f"ai_process_{media_type}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_preview" if media_type == "photo" else "back_to_video_preview")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_schedule_keyboard():
    schedule_times = [
        ("Через 30 мин", "30min"),
        ("9:05", "9:05"), ("10:05", "10:05"), ("11:07", "11:07"),
        ("12:08", "12:08"), ("13:09", "13:09"), ("14:10", "14:10"),
        ("15:11", "15:11"), ("16:12", "16:12"), ("17:13", "17:13"),
        ("18:14", "18:14"), ("19:07", "19:07"), ("20:08", "20:08"),
        ("21:09", "21:09"), ("22:11", "22:11"), ("22:45", "22:45")
    ]
    keyboard = []
    row = []
    for i, (label, value) in enumerate(schedule_times):
        row.append(InlineKeyboardButton(label, callback_data=f"schedule:{value}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_preview")])
    return InlineKeyboardMarkup(keyboard)

def get_video_schedule_keyboard():
    schedule_times = [
        ("Через 30 мин", "30min"),
        ("9:05", "9:05"), ("10:05", "10:05"), ("11:07", "11:07"),
        ("12:08", "12:08"), ("13:09", "13:09"), ("14:10", "14:10"),
        ("15:11", "15:11"), ("16:12", "16:12"), ("17:13", "17:13"),
        ("18:14", "18:14"), ("19:07", "19:07"), ("20:08", "20:08"),
        ("21:09", "21:09"), ("22:11", "22:11"), ("22:45", "22:45")
    ]
    keyboard = []
    row = []
    for i, (label, value) in enumerate(schedule_times):
        row.append(InlineKeyboardButton(label, callback_data=f"schedule_video:{value}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_video_preview")])
    return InlineKeyboardMarkup(keyboard)

def get_designed_schedule_keyboard():
    schedule_times = [
        ("Через 30 мин", "30min"),
        ("9:05", "9:05"), ("10:05", "10:05"), ("11:07", "11:07"),
        ("12:08", "12:08"), ("13:09", "13:09"), ("14:10", "14:10"),
        ("15:11", "15:11"), ("16:12", "16:12"), ("17:13", "17:13"),
        ("18:14", "18:14"), ("19:07", "19:07"), ("20:08", "20:08"),
        ("21:09", "21:09"), ("22:11", "22:11"), ("22:45", "22:45")
    ]
    keyboard = []
    row = []
    for i, (label, value) in enumerate(schedule_times):
        row.append(InlineKeyboardButton(label, callback_data=f"schedule_designed:{value}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_designed")])
    return InlineKeyboardMarkup(keyboard)

def get_watermark_schedule_keyboard():
    schedule_times = [
        ("Через 30 мин", "30min"),
        ("9:05", "9:05"), ("10:05", "10:05"), ("11:07", "11:07"),
        ("12:08", "12:08"), ("13:09", "13:09"), ("14:10", "14:10"),
        ("15:11", "15:11"), ("16:12", "16:12"), ("17:13", "17:13"),
        ("18:14", "18:14"), ("19:07", "19:07"), ("20:08", "20:08"),
        ("21:09", "21:09"), ("22:11", "22:11"), ("22:45", "22:45")
    ]
    keyboard = []
    row = []
    for i, (label, value) in enumerate(schedule_times):
        row.append(InlineKeyboardButton(label, callback_data=f"schedule_watermark:{value}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_watermark_preview")])
    return InlineKeyboardMarkup(keyboard)

# ==================== ПУБЛИКАЦИЯ ====================
async def send_to_channel(context, photo_bytes: bytes = None, file_id: str = None, text: str = "", has_buttons: bool = True, is_video: bool = False, video_file_id: str = None):
    if len(text) > 1000:
        text = text[:1000] + "..."
    
    lines = text.split('\n')
    title = lines[0] if lines else ""
    body = '\n'.join(lines[1:]) if len(lines) > 1 else ""
    caption = format_caption(title, body) if text else " "
    
    reply_markup = get_post_publish_keyboard() if has_buttons else None
    
    if is_video and video_file_id:
        await context.bot.send_video(
            chat_id=CHANNEL_ID,
            video=video_file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    elif photo_bytes:
        await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=photo_bytes,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    elif file_id:
        await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup
        )

# ==================== ОБРАБОТЧИКИ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Бот для публикации новостей MINSK NEWS*\n\n"
        "📸 *Как работать с ботом:*\n"
        "1. Отправьте фото или видео с подписью\n"
        "2. Выберите действие из меню\n\n"
        "*Доступные действия:*\n"
        "• 📤 Опубликовать с кнопками\n"
        "• 📤 Опубликовать без кнопок\n"
        "• 🎨 Оформить пост (только для фото)\n"
        "• 💧 Добавить водяной знак (только для фото)\n"
        "• ✏️ Редактировать текст\n"
        "• 🤖 Обработать текст (ИИ)\n"
        "• ⏰ Отложить публикацию\n\n"
        "👇 Отправьте фото или видео с подписью",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message.photo:
        await message.reply_text("❌ Пожалуйста, отправьте фото")
        return
    
    caption = message.caption or ""
    photo = message.photo[-1]
    
    cleaned_caption = remove_emojis(caption)
    
    print(f"📸 Получено фото")
    
    context.chat_data["pending_post"] = {
        "type": "photo",
        "text": cleaned_caption,
        "file_id": photo.file_id,
        "photo_bytes": None,
        "original_photo_bytes": None
    }
    
    try:
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        context.chat_data["pending_post"]["photo_bytes"] = photo_bytes
        context.chat_data["pending_post"]["original_photo_bytes"] = photo_bytes
        
        await message.reply_photo(
            photo=photo.file_id,
            caption=cleaned_caption if cleaned_caption else " ",
            parse_mode="HTML",
            reply_markup=get_media_preview_keyboard("photo")
        )
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await message.reply_text(f"❌ Не удалось загрузить фото")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message.video:
        return
    
    caption = message.caption or ""
    video = message.video
    
    cleaned_caption = remove_emojis(caption)
    
    print(f"📹 Получено видео")
    
    context.chat_data["pending_video"] = {
        "type": "video",
        "text": cleaned_caption,
        "file_id": video.file_id
    }
    
    await message.reply_video(
        video=video.file_id,
        caption=cleaned_caption if cleaned_caption else " ",
        parse_mode="HTML",
        reply_markup=get_media_preview_keyboard("video")
    )

# ==================== ПУБЛИКАЦИЯ ВИДЕО ====================
async def publish_video_with_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending_video", {})
    if not pending or pending.get("type") != "video":
        await query.message.reply_text("❌ Нет видео для публикации")
        return
    
    text = pending.get("text", "")
    file_id = pending.get("file_id")
    
    if not file_id:
        await query.message.reply_text("❌ Нет file_id видео")
        return
    
    try:
        await send_to_channel(context, video_file_id=file_id, text=text, has_buttons=True, is_video=True)
        await query.message.reply_text("✅ Видео опубликовано в канал (с кнопками)!")
        context.chat_data.pop("pending_video", None)
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def publish_video_no_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending_video", {})
    if not pending or pending.get("type") != "video":
        await query.message.reply_text("❌ Нет видео для публикации")
        return
    
    text = pending.get("text", "")
    file_id = pending.get("file_id")
    
    if not file_id:
        await query.message.reply_text("❌ Нет file_id видео")
        return
    
    try:
        await send_to_channel(context, video_file_id=file_id, text=text, has_buttons=False, is_video=True)
        await query.message.reply_text("✅ Видео опубликовано в канал (без кнопок)!")
        context.chat_data.pop("pending_video", None)
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def edit_video_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_for_edit_video"] = True
    await query.message.reply_text("✏️ Отправьте новый текст для видео. Или /cancel для отмены.")

async def back_to_video_preview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending_video", {})
    text = pending.get("text", "")
    file_id = pending.get("file_id")
    
    if file_id:
        await query.message.reply_video(
            video=file_id,
            caption=text if text else " ",
            parse_mode="HTML",
            reply_markup=get_media_preview_keyboard("video")
        )
        try:
            await query.message.delete()
        except:
            pass

# ==================== ВОДЯНОЙ ЗНАК (ФОТО) ====================
async def add_watermark_only_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending_post", {})
    if not pending or pending.get("type") != "photo":
        await query.message.reply_text("❌ Нет поста для обработки")
        return
    
    full_text = pending.get("text", "")
    photo_bytes = pending.get("original_photo_bytes") or pending.get("photo_bytes")
    
    if not photo_bytes:
        await query.message.reply_text("❌ Нет фото")
        return
    
    try:
        await query.message.reply_text("💧 Добавляю водяной знак...")
        
        photo_io = add_watermark_only(photo_bytes)
        
        context.chat_data["watermarked_post"] = {
            "text": full_text,
            "photo_bytes": photo_io.getvalue(),
            "original_photo_bytes": photo_bytes
        }
        
        await query.message.reply_photo(
            photo=photo_io,
            caption=f"{full_text}\n\n💧 Пост с водяным знаком!",
            parse_mode="HTML",
            reply_markup=get_watermark_preview_keyboard()
        )
        
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"⚠️ Ошибка при добавлении водяного знака: {e}")

async def publish_watermarked_with_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    watermarked = context.chat_data.get("watermarked_post", {})
    if not watermarked:
        await query.message.reply_text("❌ Нет поста с водяным знаком")
        return
    
    full_text = watermarked.get("text", "")
    photo_bytes = watermarked.get("photo_bytes")
    
    if not photo_bytes:
        await query.message.reply_text("❌ Нет фото")
        return
    
    try:
        await send_to_channel(context, photo_bytes=photo_bytes, text=full_text, has_buttons=True)
        await query.message.reply_text("✅ Пост с водяным знаком опубликован (с кнопками)!")
        
        context.chat_data.pop("pending_post", None)
        context.chat_data.pop("watermarked_post", None)
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def publish_watermarked_no_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    watermarked = context.chat_data.get("watermarked_post", {})
    if not watermarked:
        await query.message.reply_text("❌ Нет поста с водяным знаком")
        return
    
    full_text = watermarked.get("text", "")
    photo_bytes = watermarked.get("photo_bytes")
    
    if not photo_bytes:
        await query.message.reply_text("❌ Нет фото")
        return
    
    try:
        await send_to_channel(context, photo_bytes=photo_bytes, text=full_text, has_buttons=False)
        await query.message.reply_text("✅ Пост с водяным знаком опубликован (без кнопок)!")
        
        context.chat_data.pop("pending_post", None)
        context.chat_data.pop("watermarked_post", None)
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def back_to_original_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending_post", {})
    text = pending.get("text", "")
    photo_bytes = pending.get("original_photo_bytes") or pending.get("photo_bytes")
    
    if photo_bytes:
        await query.message.reply_photo(
            photo=photo_bytes,
            caption=text if text else " ",
            parse_mode="HTML",
            reply_markup=get_media_preview_keyboard("photo")
        )
        try:
            await query.message.delete()
        except:
            pass

async def back_to_watermark_preview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    watermarked = context.chat_data.get("watermarked_post", {})
    text = watermarked.get("text", "")
    photo_bytes = watermarked.get("photo_bytes")
    
    if photo_bytes:
        await query.message.edit_caption(
            caption=f"{text}\n\n💧 Пост с водяным знаком!",
            parse_mode="HTML",
            reply_markup=get_watermark_preview_keyboard()
        )

async def design_post_from_watermark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    watermarked = context.chat_data.get("watermarked_post", {})
    if not watermarked:
        await query.message.reply_text("❌ Нет поста")
        return
    
    full_text = watermarked.get("text", "")
    photo_bytes = watermarked.get("original_photo_bytes")
    
    if not photo_bytes:
        await query.message.reply_text("❌ Нет фото")
        return
    
    try:
        await query.message.reply_text("🎨 Оформляю пост...")
        
        lines = full_text.split('\n')
        title_for_photo = lines[0][:150] if lines else "Пост"
        
        photo_io = process_photo(photo_bytes, title_for_photo, add_watermark_flag=False)
        
        context.chat_data["designed_post"] = {
            "text": full_text,
            "photo_bytes": photo_io.getvalue(),
            "original_photo_bytes": photo_bytes
        }
        
        await query.message.reply_photo(
            photo=photo_io,
            caption=f"{full_text}\n\n✅ Пост оформлен!",
            parse_mode="HTML",
            reply_markup=get_designed_post_keyboard()
        )
        
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"⚠️ Ошибка: {e}")

async def add_watermark_to_designed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    designed = context.chat_data.get("designed_post", {})
    if not designed:
        await query.message.reply_text("❌ Нет оформленного поста")
        return
    
    full_text = designed.get("text", "")
    original_photo_bytes = designed.get("original_photo_bytes")
    
    if not original_photo_bytes:
        await query.message.reply_text("❌ Нет фото")
        return
    
    try:
        await query.message.reply_text("💧 Добавляю водяной знак...")
        
        photo_io = add_watermark_only(original_photo_bytes)
        
        context.chat_data["watermarked_post"] = {
            "text": full_text,
            "photo_bytes": photo_io.getvalue(),
            "original_photo_bytes": original_photo_bytes
        }
        
        await query.message.reply_photo(
            photo=photo_io,
            caption=f"{full_text}\n\n💧 Пост с водяным знаком!",
            parse_mode="HTML",
            reply_markup=get_watermark_preview_keyboard()
        )
        
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"⚠️ Ошибка: {e}")

# ==================== ПУБЛИКАЦИЯ ФОТО ====================
async def publish_raw_with_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending_post", {})
    if not pending or pending.get("type") != "photo":
        await query.message.reply_text("❌ Нет поста для публикации")
        return
    
    full_text = pending.get("text", "")
    file_id = pending.get("file_id")
    
    if not file_id:
        await query.message.reply_text("❌ Нет file_id фото")
        return
    
    try:
        await send_to_channel(context, file_id=file_id, text=full_text, has_buttons=True)
        await query.message.reply_text("✅ Пост опубликован в канал (с кнопками)!")
        context.chat_data.pop("pending_post", None)
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def publish_raw_no_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending_post", {})
    if not pending or pending.get("type") != "photo":
        await query.message.reply_text("❌ Нет поста для публикации")
        return
    
    full_text = pending.get("text", "")
    file_id = pending.get("file_id")
    
    if not file_id:
        await query.message.reply_text("❌ Нет file_id фото")
        return
    
    try:
        await send_to_channel(context, file_id=file_id, text=full_text, has_buttons=False)
        await query.message.reply_text("✅ Пост опубликован в канал (без кнопок)!")
        context.chat_data.pop("pending_post", None)
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

# ==================== ОФОРМЛЕНИЕ ПОСТА ====================
async def design_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending_post", {})
    
    if not pending or pending.get("type") != "photo":
        await query.message.reply_text("❌ Оформить можно только фото")
        return
    
    full_text = pending.get("text", "")
    if not full_text:
        await query.message.reply_text("❌ Нет текста")
        return
    
    lines = full_text.split('\n')
    title_for_photo = lines[0][:150] if lines else "Пост"
    
    if not pending.get("photo_bytes"):
        await query.message.reply_text("❌ Нет фото")
        return
    
    try:
        await query.message.reply_text("🎨 Оформляю пост...")
        
        photo_io = process_photo(pending["photo_bytes"], title_for_photo, add_watermark_flag=False)
        
        context.chat_data["designed_post"] = {
            "text": full_text,
            "photo_bytes": photo_io.getvalue(),
            "original_photo_bytes": pending["photo_bytes"]
        }
        
        await query.message.reply_photo(
            photo=photo_io,
            caption=f"{full_text}\n\n✅ Пост оформлен!",
            parse_mode="HTML",
            reply_markup=get_designed_post_keyboard()
        )
        
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"⚠️ Ошибка: {e}")

async def publish_designed_with_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    designed = context.chat_data.get("designed_post", {})
    if not designed:
        await query.message.reply_text("❌ Нет оформленного поста")
        return
    
    full_text = designed.get("text", "")
    photo_bytes = designed.get("photo_bytes")
    
    if not photo_bytes:
        await query.message.reply_text("❌ Нет фото")
        return
    
    try:
        await send_to_channel(context, photo_bytes=photo_bytes, text=full_text, has_buttons=True)
        await query.message.reply_text("✅ Оформленный пост опубликован (с кнопками)!")
        
        context.chat_data.pop("pending_post", None)
        context.chat_data.pop("designed_post", None)
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def publish_designed_no_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    designed = context.chat_data.get("designed_post", {})
    if not designed:
        await query.message.reply_text("❌ Нет оформленного поста")
        return
    
    full_text = designed.get("text", "")
    photo_bytes = designed.get("photo_bytes")
    
    if not photo_bytes:
        await query.message.reply_text("❌ Нет фото")
        return
    
    try:
        await send_to_channel(context, photo_bytes=photo_bytes, text=full_text, has_buttons=False)
        await query.message.reply_text("✅ Оформленный пост опубликован (без кнопок)!")
        
        context.chat_data.pop("pending_post", None)
        context.chat_data.pop("designed_post", None)
        try:
            await query.message.delete()
        except:
            pass
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

# ==================== РЕДАКТИРОВАНИЕ ====================
async def edit_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_for_edit"] = "photo"
    await query.message.reply_text("✏️ Отправьте новый текст для поста. Или /cancel для отмены.")

async def edit_designed_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_for_edit"] = "designed"
    await query.message.reply_text("✏️ Отправьте новый текст для поста. Или /cancel для отмены.")

async def handle_edited_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting_for_edit_video"):
        pending = context.chat_data.get("pending_video", {})
        if pending:
            pending["text"] = update.message.text
            context.chat_data["pending_video"] = pending
            await update.message.reply_text("✅ Текст видео обновлён!", reply_markup=get_media_preview_keyboard("video"))
        context.user_data["waiting_for_edit_video"] = None
        return
    
    edit_type = context.user_data.get("waiting_for_edit")
    if not edit_type:
        return
    
    new_text = update.message.text
    
    if edit_type == "photo":
        pending = context.chat_data.get("pending_post", {})
        if pending:
            pending["text"] = new_text
            context.chat_data["pending_post"] = pending
            await update.message.reply_text("✅ Текст обновлён!", reply_markup=get_media_preview_keyboard("photo"))
    
    elif edit_type == "designed":
        designed = context.chat_data.get("designed_post", {})
        if designed:
            designed["text"] = new_text
            context.chat_data["designed_post"] = designed
            photo_bytes = designed.get("photo_bytes")
            if photo_bytes:
                await update.message.reply_photo(
                    photo=photo_bytes,
                    caption=f"{new_text}\n\n✅ Текст обновлён!",
                    parse_mode="HTML",
                    reply_markup=get_designed_post_keyboard()
                )
    
    context.user_data["waiting_for_edit"] = None

async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting_for_edit"] = None
    context.user_data["waiting_for_edit_video"] = None
    context.user_data["waiting_for_custom_request"] = None
    context.user_data["waiting_for_custom_request_video"] = None
    await update.message.reply_text("✅ Редактирование отменено.")

# ==================== ОБРАБОТКА ИИ ====================
async def ai_process_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not deepseek_client:
        await query.message.reply_text("❌ API DeepSeek не настроен.")
        return
    
    custom_request = context.user_data.get("custom_ai_request", "")
    if custom_request:
        prompt = f"""{DEEPSEEK_PROMPT}
        
        Дополнительные требования пользователя: {custom_request}
        
        Переделай новость согласно этим требованиям."""
        context.user_data["custom_ai_request"] = None
    else:
        prompt = DEEPSEEK_PROMPT
    
    source = context.user_data.get("ai_source", "photo")
    
    if source == "video":
        pending = context.chat_data.get("pending_video", {})
        media_type = "video"
    elif source == "designed":
        pending = context.chat_data.get("designed_post", {})
        media_type = "photo"
    else:
        pending = context.chat_data.get("pending_post", {})
        media_type = "photo"
    
    text = pending.get("text", "")
    
    if not text:
        await query.message.reply_text("❌ Нет текста для обработки")
        return
    
    await query.message.reply_text("🤖 Обрабатываю текст через DeepSeek...")
    
    try:
        response = await deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text}
            ],
            temperature=0.7,
            max_tokens=1000
        )
        
        processed_text = response.choices[0].message.content
        
        title = ""
        body = ""
        for line in processed_text.split('\n'):
            if line.startswith("Заголовок:"):
                title = line.replace("Заголовок:", "").strip()
            elif line.startswith("Текст:"):
                body = line.replace("Текст:", "").strip()
        
        if not title and not body:
            body = processed_text
        
        if title and body:
            new_text = f"{title}\n\n{body}"
        else:
            new_text = body if body else processed_text
        
        pending["text"] = new_text
        
        if source == "video":
            context.chat_data["pending_video"] = pending
        elif source == "designed":
            context.chat_data["designed_post"] = pending
        else:
            context.chat_data["pending_post"] = pending
        
        # Показываем полный текст результата
        await query.message.reply_text(
            f"✅ *Текст обработан!*\n\n"
            f"📰 *Заголовок:* {title}\n\n"
            f"📝 *Текст:*\n{body}\n\n"
            f"Выберите действие:",
            parse_mode="Markdown",
            reply_markup=get_ai_result_keyboard(media_type)
        )
        
    except Exception as e:
        print(f"❌ Ошибка DeepSeek: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def ai_process_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["ai_source"] = "video"
    await ai_new_request_video_callback(update, context)

async def ai_process_designed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["ai_source"] = "designed"
    await ai_new_request_callback(update, context)

async def ai_new_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "📝 *Выберите вариант обработки текста для фото:*\n\n"
        "• *Стандартный* - сокращение до 650 символов с абзацами\n"
        "• *Свой запрос* - напишите, как именно обработать текст\n\n"
        "После обработки вы сможете отправить новый запрос снова.",
        parse_mode="Markdown",
        reply_markup=get_ai_request_keyboard("photo")
    )

async def ai_new_request_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "📝 *Выберите вариант обработки текста для видео:*\n\n"
        "• *Стандартный* - сокращение до 650 символов с абзацами\n"
        "• *Свой запрос* - напишите, как именно обработать текст\n\n"
        "После обработки вы сможете отправить новый запрос снова.",
        parse_mode="Markdown",
        reply_markup=get_ai_request_keyboard("video")
    )

async def ai_custom_request_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_for_custom_request"] = True
    context.user_data["ai_source"] = "photo"
    await query.message.reply_text(
        "📝 *Напишите ваш запрос для обработки текста*\n\n"
        "Примеры:\n"
        "• Сделай заголовок броским и коротким\n"
        "• Сократи до 300 символов\n"
        "• Сделай более официальным стиль\n"
        "• Добавь больше фактов и цифр\n\n"
        "После обработки вы сможете отправить новый запрос снова.\n\n"
        "Или /cancel для отмены.",
        parse_mode="Markdown"
    )

async def ai_custom_request_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_for_custom_request_video"] = True
    context.user_data["ai_source"] = "video"
    await query.message.reply_text(
        "📝 *Напишите ваш запрос для обработки текста видео*\n\n"
        "Примеры:\n"
        "• Сделай заголовок броским и коротким\n"
        "• Сократи до 300 символов\n"
        "• Сделай более официальным стиль\n"
        "• Добавь больше фактов и цифр\n\n"
        "После обработки вы сможете отправить новый запрос снова.\n\n"
        "Или /cancel для отмены.",
        parse_mode="Markdown"
    )

async def ai_process_standard_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["ai_source"] = "photo"
    context.user_data["custom_ai_request"] = None
    await ai_process_callback(update, context)

async def ai_process_standard_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["ai_source"] = "video"
    context.user_data["custom_ai_request"] = None
    await ai_process_callback(update, context)

async def handle_custom_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting_for_custom_request"):
        request = update.message.text
        context.user_data["custom_ai_request"] = request
        context.user_data["waiting_for_custom_request"] = False
        await update.message.reply_text(f"✅ Запрос принят: *{request}*\n🤖 Обрабатываю...", parse_mode="Markdown")
        
        class FakeQuery:
            def __init__(self, message):
                self.message = message
            async def answer(self):
                pass
        
        fake_query = FakeQuery(update.message)
        await ai_process_callback(update, context)
        return
    
    if context.user_data.get("waiting_for_custom_request_video"):
        request = update.message.text
        context.user_data["custom_ai_request"] = request
        context.user_data["waiting_for_custom_request_video"] = False
        await update.message.reply_text(f"✅ Запрос принят: *{request}*\n🤖 Обрабатываю...", parse_mode="Markdown")
        
        class FakeQuery:
            def __init__(self, message):
                self.message = message
            async def answer(self):
                pass
        
        fake_query = FakeQuery(update.message)
        await ai_process_callback(update, context)
        return

# ==================== ОТЛОЖЕННАЯ ПУБЛИКАЦИЯ ====================
async def schedule_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_reply_markup(reply_markup=get_schedule_keyboard())

async def schedule_video_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_reply_markup(reply_markup=get_video_schedule_keyboard())

async def schedule_menu_watermark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_reply_markup(reply_markup=get_watermark_schedule_keyboard())

async def back_to_preview_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending_post", {})
    text = pending.get("text", "")
    photo_bytes = pending.get("photo_bytes")
    
    if photo_bytes:
        await query.message.reply_photo(
            photo=photo_bytes,
            caption=text if text else " ",
            parse_mode="HTML",
            reply_markup=get_media_preview_keyboard("photo")
        )
        try:
            await query.message.delete()
        except:
            pass

async def back_to_designed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    designed = context.chat_data.get("designed_post", {})
    text = designed.get("text", "")
    photo_bytes = designed.get("photo_bytes")
    
    if photo_bytes:
        await query.message.edit_caption(
            caption=text if text else " ",
            parse_mode="HTML",
            reply_markup=get_designed_post_keyboard()
        )

async def schedule_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    time_value = query.data.split(":")[1]
    
    now = datetime.now()
    if time_value == "30min":
        publish_time = now + timedelta(minutes=30)
        time_str = "через 30 минут"
    else:
        hour, minute = map(int, time_value.split(":"))
        publish_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if publish_time <= now:
            publish_time += timedelta(days=1)
        time_str = f"{publish_time.strftime('%H:%M')} ({publish_time.strftime('%d.%m')})"
    
    pending = context.chat_data.get("pending_post", {})
    full_text = pending.get("text", "")
    photo_bytes = pending.get("photo_bytes")
    
    if not photo_bytes:
        await query.message.reply_text("❌ Нет данных для отложенной публикации")
        return
    
    save_scheduled_post(full_text, photo_bytes, publish_time, has_buttons=True, has_watermark=False, is_designed=False)
    
    await query.message.reply_text(
        f"✅ Пост запланирован на {time_str}\n\n"
        f"Он будет автоматически опубликован в канал в указанное время (с кнопками)."
    )
    
    context.chat_data.pop("pending_post", None)
    
    try:
        await query.message.delete()
    except:
        pass

async def schedule_video_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    time_value = query.data.split(":")[1]
    
    now = datetime.now()
    if time_value == "30min":
        publish_time = now + timedelta(minutes=30)
        time_str = "через 30 минут"
    else:
        hour, minute = map(int, time_value.split(":"))
        publish_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if publish_time <= now:
            publish_time += timedelta(days=1)
        time_str = f"{publish_time.strftime('%H:%M')} ({publish_time.strftime('%d.%m')})"
    
    pending = context.chat_data.get("pending_video", {})
    text = pending.get("text", "")
    file_id = pending.get("file_id")
    
    if not file_id:
        await query.message.reply_text("❌ Нет данных для отложенной публикации")
        return
    
    save_scheduled_post(text, None, publish_time, has_buttons=True, has_watermark=False, is_designed=False, is_video=True, video_file_id=file_id)
    
    await query.message.reply_text(
        f"✅ Видео запланировано на {time_str}\n\n"
        f"Оно будет автоматически опубликовано в канал в указанное время (с кнопками)."
    )
    
    context.chat_data.pop("pending_video", None)
    
    try:
        await query.message.delete()
    except:
        pass

async def schedule_watermark_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    time_value = query.data.split(":")[1]
    
    now = datetime.now()
    if time_value == "30min":
        publish_time = now + timedelta(minutes=30)
        time_str = "через 30 минут"
    else:
        hour, minute = map(int, time_value.split(":"))
        publish_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if publish_time <= now:
            publish_time += timedelta(days=1)
        time_str = f"{publish_time.strftime('%H:%M')} ({publish_time.strftime('%d.%m')})"
    
    watermarked = context.chat_data.get("watermarked_post", {})
    full_text = watermarked.get("text", "")
    photo_bytes = watermarked.get("photo_bytes")
    
    if not photo_bytes:
        await query.message.reply_text("❌ Нет данных для отложенной публикации")
        return
    
    save_scheduled_post(full_text, photo_bytes, publish_time, has_buttons=True, has_watermark=True, is_designed=False)
    
    await query.message.reply_text(
        f"✅ Пост с водяным знаком запланирован на {time_str}\n\n"
        f"Он будет автоматически опубликован в канал в указанное время."
    )
    
    context.chat_data.pop("watermarked_post", None)
    context.chat_data.pop("pending_post", None)
    
    try:
        await query.message.delete()
    except:
        pass

async def schedule_designed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_reply_markup(reply_markup=get_designed_schedule_keyboard())

async def schedule_designed_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    time_value = query.data.split(":")[1]
    
    now = datetime.now()
    if time_value == "30min":
        publish_time = now + timedelta(minutes=30)
        time_str = "через 30 минут"
    else:
        hour, minute = map(int, time_value.split(":"))
        publish_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if publish_time <= now:
            publish_time += timedelta(days=1)
        time_str = f"{publish_time.strftime('%H:%M')} ({publish_time.strftime('%d.%m')})"
    
    designed = context.chat_data.get("designed_post", {})
    full_text = designed.get("text", "")
    photo_bytes = designed.get("photo_bytes")
    
    if not photo_bytes:
        await query.message.reply_text("❌ Нет данных для отложенной публикации")
        return
    
    save_scheduled_post(full_text, photo_bytes, publish_time, has_buttons=True, has_watermark=False, is_designed=True)
    
    await query.message.reply_text(
        f"✅ Оформленный пост запланирован на {time_str}\n\n"
        f"Он будет автоматически опубликован в канал в указанное время."
    )
    
    context.chat_data.pop("designed_post", None)
    context.chat_data.pop("pending_post", None)
    
    try:
        await query.message.delete()
    except:
        pass

# ==================== ПЛАНИРОВЩИК ====================
async def check_scheduled_posts(app: Application):
    while True:
        try:
            posts = get_pending_scheduled_posts()
            for post in posts:
                photo_bytes = post["photo_bytes"]
                text = post["text"]
                has_buttons = post.get("has_buttons", True)
                is_video = post.get("is_video", False)
                video_file_id = post.get("video_file_id")
                
                if is_video and video_file_id:
                    await send_to_channel(app, text=text, has_buttons=has_buttons, is_video=True, video_file_id=video_file_id)
                elif photo_bytes:
                    await send_to_channel(app, photo_bytes=photo_bytes, text=text, has_buttons=has_buttons)
                
                delete_scheduled_post(post["id"])
                print(f"✅ Опубликован отложенный пост")
                
        except Exception as e:
            print(f"❌ Ошибка в планировщике: {e}")
        
        await asyncio.sleep(60)

# ==================== ОСНОВНОЙ ОБРАБОТЧИК ====================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == "send_media_info":
        await query.answer()
        await query.message.reply_text("📸 Отправьте фото или видео с подписью для публикации")
    
    # Видео
    elif data == "publish_video_with_buttons":
        await publish_video_with_buttons_callback(update, context)
    elif data == "publish_video_no_buttons":
        await publish_video_no_buttons_callback(update, context)
    elif data == "edit_video_text":
        await edit_video_text_callback(update, context)
    elif data == "ai_process_video":
        await ai_process_video_callback(update, context)
    elif data == "schedule_video_menu":
        await schedule_video_menu_callback(update, context)
    elif data == "back_to_video_preview":
        await back_to_video_preview_callback(update, context)
    
    # Фото - публикация
    elif data == "publish_raw_with_buttons":
        await publish_raw_with_buttons_callback(update, context)
    elif data == "publish_raw_no_buttons":
        await publish_raw_no_buttons_callback(update, context)
    
    # Фото - водяной знак
    elif data == "add_watermark_only":
        await add_watermark_only_callback(update, context)
    elif data == "publish_watermarked_with_buttons":
        await publish_watermarked_with_buttons_callback(update, context)
    elif data == "publish_watermarked_no_buttons":
        await publish_watermarked_no_buttons_callback(update, context)
    elif data == "back_to_original":
        await back_to_original_callback(update, context)
    elif data == "back_to_watermark_preview":
        await back_to_watermark_preview_callback(update, context)
    elif data == "design_post_from_watermark":
        await design_post_from_watermark_callback(update, context)
    
    # Фото - оформление
    elif data == "design_post":
        await design_post_callback(update, context)
    elif data == "publish_designed_with_buttons":
        await publish_designed_with_buttons_callback(update, context)
    elif data == "publish_designed_no_buttons":
        await publish_designed_no_buttons_callback(update, context)
    elif data == "add_watermark_to_designed":
        await add_watermark_to_designed_callback(update, context)
    
    # Редактирование
    elif data == "edit_text":
        await edit_text_callback(update, context)
    elif data == "edit_designed_text":
        await edit_designed_text_callback(update, context)
    
    # AI обработка
    elif data == "ai_process":
        context.user_data["ai_source"] = "photo"
        await ai_process_callback(update, context)
    elif data == "ai_process_designed":
        await ai_process_designed_callback(update, context)
    elif data == "ai_new_request":
        await ai_new_request_callback(update, context)
    elif data == "ai_new_request_video":
        await ai_new_request_video_callback(update, context)
    elif data == "ai_custom_request_photo":
        await ai_custom_request_photo_callback(update, context)
    elif data == "ai_custom_request_video":
        await ai_custom_request_video_callback(update, context)
    elif data == "ai_process_photo":
        await ai_process_standard_photo_callback(update, context)
    elif data == "ai_process_video":
        await ai_process_standard_video_callback(update, context)
    
    # Меню отложенной публикации
    elif data == "schedule_menu":
        await schedule_menu_callback(update, context)
    elif data == "schedule_menu_watermark":
        await schedule_menu_watermark_callback(update, context)
    elif data == "schedule_designed":
        await schedule_designed_callback(update, context)
    
    # Назад
    elif data == "back_to_preview":
        await back_to_preview_callback(update, context)
    elif data == "back_to_designed":
        await back_to_designed_callback(update, context)
    
    # Отложенная публикация с временем
    elif data.startswith("schedule:"):
        if "schedule_designed:" in data:
            await schedule_designed_time_callback(update, context)
        elif "schedule_watermark:" in data:
            await schedule_watermark_post_callback(update, context)
        elif "schedule_video:" in data:
            await schedule_video_post_callback(update, context)
        else:
            await schedule_post_callback(update, context)

# ==================== ВЕБ-СЕРВЕР ====================
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "bot": "MINSK NEWS Bot"}

@app.get("/health")
async def health():
    return {"status": "alive"}

# ==================== ЗАПУСК ====================
async def run_bot():
    init_db()
    
    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook()
    print("✅ Webhook удалён")
    
    if deepseek_client:
        print("✅ DeepSeek API подключен")
    else:
        print("⚠️ DeepSeek API не настроен")
    
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel_edit))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edited_text))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_request))
    
    await application.initialize()
    await application.start()
    
    asyncio.create_task(check_scheduled_posts(application))
    
    await application.updater.start_polling()
    
    print("✅ Бот запущен с поддержкой отложенных постов, водяных знаков, ИИ и видео!")

if __name__ == "__main__":
    import threading
    import uvicorn
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    loop.create_task(run_bot())
    
    port = int(os.getenv("PORT", 10000))
    server_thread = threading.Thread(target=lambda: uvicorn.run(app, host="0.0.0.0", port=port))
    server_thread.start()
    
    loop.run_forever()
