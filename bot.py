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

WATERMARK_TEXT = "MINSK NEWS"
WATERMARK_OPACITY = 38
MAX_CAPTION_LEN = 900

deepseek_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com") if DEEPSEEK_API_KEY else None

DEEPSEEK_PROMPT = """Ты редактор новостного сайта. У тебя строгий новостной формат. Без обращений на "вы", "ты". Только новостной формат.

Переделай новость в формат на 500-600 символов. Убери всю воду, сделай интересный заголовок. Без смайликов. Сохраняй главные факты.

Текст должен быть разбит на логические абзацы (2-4 предложения). Между абзацами пустая строка.

Верни строго в формате:
ЗАГОЛОВОК: (заголовок новости до 80 символов)
ТЕКСТ: (текст новости с абзацами до 550 символов)"""

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS scheduled_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, photo_bytes BLOB, schedule_time TIMESTAMP, created_at TIMESTAMP, has_buttons BOOLEAN DEFAULT 1, has_watermark BOOLEAN DEFAULT 0, is_designed BOOLEAN DEFAULT 0, is_video BOOLEAN DEFAULT 0, is_text BOOLEAN DEFAULT 0, video_file_id TEXT)")
    print("✅ База данных готова")

def save_scheduled_post(text, photo_bytes, schedule_time, has_buttons=True, has_watermark=False, is_designed=False, is_video=False, is_text=False, video_file_id=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO scheduled_posts (text, photo_bytes, schedule_time, created_at, has_buttons, has_watermark, is_designed, is_video, is_text, video_file_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (text, photo_bytes, schedule_time, datetime.now(), has_buttons, has_watermark, is_designed, is_video, is_text, video_file_id))

def get_pending_scheduled_posts():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT id, text, photo_bytes, schedule_time, has_buttons, has_watermark, is_designed, is_video, is_text, video_file_id FROM scheduled_posts WHERE schedule_time <= ?", (datetime.now(),)).fetchall()]

def delete_scheduled_post(post_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM scheduled_posts WHERE id = ?", (post_id,))

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def remove_emojis(text):
    if not text: return ""
    emoji_pattern = re.compile("["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"
        "\U0001FA70-\U0001FAFF"
        "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)

def format_caption(title, body):
    """Форматирует подпись - ОДИН перенос между заголовком и текстом"""
    if body and body.strip():
        return f"<b>{title}</b>\n{body}"
    else:
        return f"<b>{title}</b>"

def get_post_publish_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Подписаться на канал", url=CHANNEL_LINK)],
        [InlineKeyboardButton("📝 Прислать нам новость", url=SUGGEST_LINK)]
    ])

# ==================== ВОДЯНОЙ ЗНАК ====================
def add_watermark_to_image(image):
    img = image.copy()
    if img.mode != 'RGBA': img = img.convert('RGBA')
    
    watermark = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(watermark)
    font_size = min(img.width, img.height) // 12
    
    font = None
    for font_path in ["Montserrat-Bold.ttf", "arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        try:
            if os.path.exists(font_path): font = ImageFont.truetype(font_path, font_size); break
        except: continue
    if font is None: font = ImageFont.load_default()
    
    bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
    x = (img.width - (bbox[2] - bbox[0])) // 2
    y = (img.height - (bbox[3] - bbox[1])) // 2
    draw.text((x, y), WATERMARK_TEXT, font=font, fill=(255, 255, 255, WATERMARK_OPACITY))
    
    return Image.alpha_composite(img, watermark).convert('RGB')

def add_watermark_only(photo_bytes):
    img = Image.open(io.BytesIO(photo_bytes))
    output = io.BytesIO()
    add_watermark_to_image(img).save(output, format="JPEG", quality=90)
    output.seek(0)
    return output

def process_photo(photo_bytes, title_text, add_watermark_flag=False):
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    w, h = img.size
    target_ratio = 4/5
    if w/h > target_ratio:
        new_w = int(h * target_ratio)
        img = img.crop(((w - new_w)//2, 0, (w + new_w)//2, h))
    else:
        new_h = int(w / target_ratio)
        img = img.crop((0, (h - new_h)//2, w, (h + new_h)//2))
    img = img.resize((1080, 1350), Image.Resampling.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(0.85)
    
    w, h = img.size
    gh = int(h * 0.48)
    overlay_alpha = Image.new("L", (w, h), 0)
    grad = Image.new("L", (1, gh), 0)
    for y in range(gh): grad.putpixel((0, y), int(220 * (y / max(1, gh - 1))))
    grad = grad.resize((w, gh))
    overlay_alpha.paste(grad, (0, h - gh))
    overlay = Image.composite(Image.new("RGBA", (w, h), (0, 0, 0, 255)), Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    
    draw = ImageDraw.Draw(img)
    font = None
    for font_path in ["Montserrat-Black.ttf", "Montserrat-Bold.ttf"]:
        try:
            if os.path.exists(font_path): font = ImageFont.truetype(font_path, 68); break
        except: continue
    if font is None: font = ImageFont.load_default()
    
    margin_x = int(img.width * 0.05)
    margin_bottom = int(img.height * 0.08)
    max_width = img.width - 2 * margin_x
    
    title = title_text.upper()[:200]
    
    words = title.split()
    lines = []
    current = []
    for word in words:
        test = ' '.join(current + [word])
        try: width = font.getbbox(test)[2] - font.getbbox(test)[0]
        except: width = len(test) * 20
        if width <= max_width: current.append(word)
        else:
            if current: lines.append(' '.join(current)); current = [word]
            else: lines.append(word)
        if len(lines) >= 6: break
    if current and len(lines) < 6: lines.append(' '.join(current))
    
    line_height = font.getbbox("Ag")[3] - font.getbbox("Ag")[1] if font != ImageFont.load_default() else 35
    spacing = int(line_height * 0.25)
    total_h = len(lines) * line_height + (len(lines) - 1) * spacing
    y = img.height - margin_bottom - total_h
    
    for line in lines:
        try: line_width = font.getbbox(line)[2] - font.getbbox(line)[0]
        except: line_width = len(line) * 20
        x = (img.width - line_width) // 2
        for dx, dy in [(-2,-2),(-2,2),(2,-2),(2,2)]: draw.text((x+dx, y+dy), line, font=font, fill=(0,0,0))
        draw.text((x, y), line, font=font, fill=(255,255,255))
        y += line_height + spacing
    
    if add_watermark_flag: img = add_watermark_to_image(img)
    
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=85)
    output.seek(0)
    return output

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("📸 Отправить фото, видео или текст", callback_data="send_media_info")]])

def get_preview_keyboard(media_type):
    if media_type == "video":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_video_with_buttons")],
            [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_video_no_buttons")],
            [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_text")],
            [InlineKeyboardButton("🤖 Обработать текст (ИИ)", callback_data="ai_process_video")],
            [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_video_menu")]
        ])
    elif media_type == "text":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_text_with_buttons")],
            [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_text_no_buttons")],
            [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_text")],
            [InlineKeyboardButton("🤖 Обработать текст (ИИ)", callback_data="ai_process_text")],
            [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_text_menu")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_photo_with_buttons")],
            [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_photo_no_buttons")],
            [InlineKeyboardButton("🎨 Оформить пост", callback_data="design_post")],
            [InlineKeyboardButton("💧 Водяной знак", callback_data="add_watermark")],
            [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_text")],
            [InlineKeyboardButton("🤖 Обработать текст (ИИ)", callback_data="ai_process_photo")],
            [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_photo_menu")]
        ])

def get_designed_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Опубликовать (с кнопками)", callback_data="publish_designed_with_buttons")],
        [InlineKeyboardButton("✅ Опубликовать (без кнопок)", callback_data="publish_designed_no_buttons")],
        [InlineKeyboardButton("💧 Добавить водяной знак", callback_data="add_watermark_to_designed")],
        [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_text")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_photo_preview")]
    ])

def get_watermark_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_watermarked_with_buttons")],
        [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_watermarked_no_buttons")],
        [InlineKeyboardButton("🎨 Оформить", callback_data="design_from_watermark")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_original")]
    ])

def get_ai_result_keyboard(media_type):
    if media_type == "video":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать видео", callback_data="publish_video_with_buttons")],
            [InlineKeyboardButton("📝 Новый запрос ИИ", callback_data=f"ai_custom_request_video")],
            [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_text")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_video_preview")]
        ])
    elif media_type == "text":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать текст", callback_data="publish_text_with_buttons")],
            [InlineKeyboardButton("📝 Новый запрос ИИ", callback_data=f"ai_custom_request_text")],
            [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_text")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_text_preview")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать", callback_data="publish_photo_with_buttons")],
            [InlineKeyboardButton("🎨 Оформить", callback_data="design_post")],
            [InlineKeyboardButton("💧 Водяной знак", callback_data="add_watermark")],
            [InlineKeyboardButton("📝 Новый запрос ИИ", callback_data=f"ai_custom_request_photo")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_photo_preview")]
        ])

def get_custom_request_keyboard(media_type):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад", callback_data=f"back_to_ai_result_{media_type}")]
    ])

def get_schedule_keyboard(prefix):
    times = [("Через 30 мин", "30min"), ("9:05", "9:05"), ("10:05", "10:05"), ("11:07", "11:07"), ("12:08", "12:08"), ("13:09", "13:09"), ("14:10", "14:10"), ("15:11", "15:11"), ("16:12", "16:12"), ("17:13", "17:13"), ("18:14", "18:14"), ("19:07", "19:07"), ("20:08", "20:08"), ("21:09", "21:09"), ("22:11", "22:11"), ("22:45", "22:45")]
    keyboard = []
    row = []
    for label, value in times:
        row.append(InlineKeyboardButton(label, callback_data=f"{prefix}:{value}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"back_to_{prefix.replace('schedule_', '')}_preview")])
    return InlineKeyboardMarkup(keyboard)

# ==================== ОТПРАВКА В КАНАЛ ====================
async def send_to_channel(context, photo_bytes=None, file_id=None, text="", has_buttons=True, is_video=False, is_text=False, video_file_id=None):
    lines = text.split('\n')
    title = lines[0] if lines else ""
    # Убираем пустые строки после заголовка
    body_lines = []
    found_text = False
    for line in lines[1:]:
        if line.strip() or found_text:
            body_lines.append(line)
            found_text = True
    body = '\n'.join(body_lines)
    
    if len(body) > 600:
        body = body[:597] + "..."
    
    caption = format_caption(title, body) if text else " "
    reply_markup = get_post_publish_keyboard() if has_buttons else None
    
    try:
        if is_video and video_file_id:
            await context.bot.send_video(chat_id=CHANNEL_ID, video=video_file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        elif is_text:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode="HTML", reply_markup=reply_markup)
        elif photo_bytes:
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=photo_bytes, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
        elif file_id:
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        print(f"Ошибка отправки: {e}")
        if "caption is too long" in str(e):
            await context.bot.send_message(chat_id=CHANNEL_ID, text=text[:MAX_CAPTION_LEN], reply_markup=reply_markup)

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================
async def start(update, context):
    await update.message.reply_text(
        "🤖 *Бот MINSK NEWS*\n\nОтправьте текст, фото или видео с подписью.\n\n"
        "• 📤 Опубликовать с/без кнопок\n• 🎨 Оформить пост (фото)\n• 💧 Водяной знак (фото)\n• 🤖 Обработка ИИ\n• ⏰ Отложить публикацию",
        parse_mode="Markdown", reply_markup=get_main_keyboard())

async def handle_text(update, context):
    if context.user_data.get("waiting_for_custom_request"):
        return
    
    text = update.message.text
    if not text or text.startswith('/'): return
    if len(text) > MAX_CAPTION_LEN:
        text = text[:MAX_CAPTION_LEN-3] + "..."
    context.chat_data["pending"] = {"type": "text", "text": remove_emojis(text)}
    await update.message.reply_text(f"📝 Текст:\n\n{text[:500]}...\n\nВыберите действие:", parse_mode="HTML", reply_markup=get_preview_keyboard("text"))

async def handle_photo(update, context):
    msg = update.message
    photo = msg.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    text = remove_emojis(msg.caption or "")
    if len(text) > MAX_CAPTION_LEN:
        text = text[:MAX_CAPTION_LEN-3] + "..."
    context.chat_data["pending"] = {"type": "photo", "text": text, "file_id": photo.file_id, "photo_bytes": photo_bytes, "original": photo_bytes}
    await msg.reply_photo(photo=photo.file_id, caption=text or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("photo"))

async def handle_video(update, context):
    msg = update.message
    text = remove_emojis(msg.caption or "")
    if len(text) > MAX_CAPTION_LEN:
        text = text[:MAX_CAPTION_LEN-3] + "..."
    context.chat_data["pending"] = {"type": "video", "text": text, "file_id": msg.video.file_id}
    await msg.reply_video(video=msg.video.file_id, caption=text or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("video"))

# ==================== ПУБЛИКАЦИЯ ====================
async def publish_photo(update, context, has_buttons):
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("pending", {})
    if pending.get("type") != "photo":
        await query.message.reply_text("❌ Нет фото")
        return
    await send_to_channel(context, file_id=pending["file_id"], text=pending["text"], has_buttons=has_buttons)
    await query.message.reply_text(f"✅ Опубликовано" + (" (с кнопками)" if has_buttons else " (без кнопок)"))
    context.chat_data.pop("pending", None)
    try: await query.message.delete()
    except: pass

async def publish_text(update, context, has_buttons):
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("pending", {})
    if pending.get("type") != "text":
        await query.message.reply_text("❌ Нет текста")
        return
    await send_to_channel(context, text=pending["text"], has_buttons=has_buttons, is_text=True)
    await query.message.reply_text(f"✅ Текст опубликован" + (" (с кнопками)" if has_buttons else " (без кнопок)"))
    context.chat_data.pop("pending", None)
    try: await query.message.delete()
    except: pass

async def publish_video(update, context, has_buttons):
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("pending", {})
    if pending.get("type") != "video":
        await query.message.reply_text("❌ Нет видео")
        return
    await send_to_channel(context, video_file_id=pending["file_id"], text=pending["text"], has_buttons=has_buttons, is_video=True)
    await query.message.reply_text(f"✅ Видео опубликовано" + (" (с кнопками)" if has_buttons else " (без кнопок)"))
    context.chat_data.pop("pending", None)
    try: await query.message.delete()
    except: pass

async def publish_designed(update, context, has_buttons):
    query = update.callback_query
    await query.answer()
    designed = context.chat_data.get("designed", {})
    if not designed:
        await query.message.reply_text("❌ Нет оформленного поста")
        return
    await send_to_channel(context, photo_bytes=designed["photo_bytes"], text=designed["text"], has_buttons=has_buttons)
    await query.message.reply_text(f"✅ Оформленный пост опубликован" + (" (с кнопками)" if has_buttons else " (без кнопок)"))
    context.chat_data.pop("pending", None)
    context.chat_data.pop("designed", None)
    try: await query.message.delete()
    except: pass

async def publish_watermarked(update, context, has_buttons):
    query = update.callback_query
    await query.answer()
    watermarked = context.chat_data.get("watermarked", {})
    if not watermarked:
        await query.message.reply_text("❌ Нет поста")
        return
    await send_to_channel(context, photo_bytes=watermarked["photo_bytes"], text=watermarked["text"], has_buttons=has_buttons)
    await query.message.reply_text(f"✅ Пост с водяным знаком опубликован" + (" (с кнопками)" if has_buttons else " (без кнопок)"))
    context.chat_data.pop("pending", None)
    context.chat_data.pop("watermarked", None)
    try: await query.message.delete()
    except: pass

# ==================== ВОДЯНОЙ ЗНАК ====================
async def add_watermark_callback(update, context):
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("pending", {})
    if pending.get("type") != "photo":
        await query.message.reply_text("❌ Только для фото")
        return
    await query.message.reply_text("💧 Добавляю водяной знак...")
    photo_io = add_watermark_only(pending["original"])
    context.chat_data["watermarked"] = {"text": pending["text"], "photo_bytes": photo_io.getvalue(), "original": pending["original"]}
    await query.message.reply_photo(photo=photo_io, caption=f"{pending['text']}\n\n💧 Пост с водяным знаком!", parse_mode="HTML", reply_markup=get_watermark_keyboard())
    try: await query.message.delete()
    except: pass

async def add_watermark_to_designed_callback(update, context):
    query = update.callback_query
    await query.answer()
    designed = context.chat_data.get("designed", {})
    if not designed:
        await query.message.reply_text("❌ Нет оформленного поста")
        return
    await query.message.reply_text("💧 Добавляю водяной знак на оформленное фото...")
    # Берем оформленное фото и наносим водяной знак
    photo_io = add_watermark_only(designed["photo_bytes"])
    context.chat_data["watermarked"] = {"text": designed["text"], "photo_bytes": photo_io.getvalue(), "original": designed["original"]}
    await query.message.reply_photo(photo=photo_io, caption=f"{designed['text']}\n\n💧 Пост с водяным знаком на оформленном фото!", parse_mode="HTML", reply_markup=get_watermark_keyboard())
    try: await query.message.delete()
    except: pass

# ==================== ОФОРМЛЕНИЕ ====================
async def design_post_callback(update, context):
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("pending", {})
    if pending.get("type") != "photo":
        await query.message.reply_text("❌ Оформить можно только фото")
        return
    lines = pending["text"].split('\n')
    title = lines[0][:150] if lines else "Пост"
    await query.message.reply_text("🎨 Оформляю...")
    photo_io = process_photo(pending["photo_bytes"], title, add_watermark_flag=False)
    context.chat_data["designed"] = {"text": pending["text"], "photo_bytes": photo_io.getvalue(), "original": pending["photo_bytes"]}
    await query.message.reply_photo(photo=photo_io, caption=f"{pending['text']}\n\n✅ Пост оформлен!", parse_mode="HTML", reply_markup=get_designed_keyboard())
    try: await query.message.delete()
    except: pass

async def design_from_watermark_callback(update, context):
    query = update.callback_query
    await query.answer()
    watermarked = context.chat_data.get("watermarked", {})
    if not watermarked:
        await query.message.reply_text("❌ Нет поста")
        return
    lines = watermarked["text"].split('\n')
    title = lines[0][:150] if lines else "Пост"
    await query.message.reply_text("🎨 Оформляю...")
    photo_io = process_photo(watermarked["original"], title, add_watermark_flag=False)
    context.chat_data["designed"] = {"text": watermarked["text"], "photo_bytes": photo_io.getvalue(), "original": watermarked["original"]}
    await query.message.reply_photo(photo=photo_io, caption=f"{watermarked['text']}\n\n✅ Пост оформлен!", parse_mode="HTML", reply_markup=get_designed_keyboard())
    try: await query.message.delete()
    except: pass

# ==================== ОБРАБОТКА ИИ ====================
async def ai_process_with_custom_request(update, context, media_type, custom_request=None):
    query = update.callback_query
    await query.answer()
    
    if not deepseek_client:
        await query.message.reply_text("❌ API DeepSeek не настроен")
        return
    
    prompt = DEEPSEEK_PROMPT
    if custom_request:
        prompt = f"{DEEPSEEK_PROMPT}\n\nДополнительные требования пользователя: {custom_request}\n\nПеределай новость согласно этим требованиям."
    
    pending = context.chat_data.get("pending", {})
    text = pending.get("text", "")
    
    if not text:
        await query.message.reply_text("❌ Нет текста")
        return
    
    await query.message.reply_text("🤖 Обрабатываю через DeepSeek...")
    
    try:
        response = await deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": text}],
            temperature=0.7,
            max_tokens=800
        )
        
        result = response.choices[0].message.content
        
        title = ""
        body = ""
        
        if "ЗАГОЛОВОК:" in result.upper() and "ТЕКСТ:" in result.upper():
            title_match = re.search(r'(?:ЗАГОЛОВОК:|Заголовок:)\s*(.+?)(?=(?:ТЕКСТ:|$))', result, re.IGNORECASE | re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()[:100]
            
            body_match = re.search(r'(?:ТЕКСТ:|Текст:)\s*(.+?)$', result, re.IGNORECASE | re.DOTALL)
            if body_match:
                body = body_match.group(1).strip()
        else:
            lines = result.strip().split('\n')
            if len(lines) > 0 and len(lines[0]) < 100:
                title = lines[0].replace('Заголовок:', '').replace('ЗАГОЛОВОК:', '').strip()[:100]
                body = '\n'.join(lines[1:]).strip()
            else:
                body = result.strip()
        
        if not body:
            body = result.strip()
        
        if not title and body:
            title = body[:50] + "..."
        
        if len(body) > 600:
            body = body[:597] + "..."
        
        # ОДИН перенос между заголовком и текстом
        new_text = f"{title}\n{body}"
        
        pending["text"] = new_text
        context.chat_data["pending"] = pending
        
        await query.message.reply_text(
            f"✅ *Готово!*\n\n"
            f"📰 *{title}*\n\n"
            f"📝 {body}\n\n"
            f"Выберите действие:",
            parse_mode="Markdown",
            reply_markup=get_ai_result_keyboard(media_type)
        )
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def ai_custom_request_callback(update, context, media_type):
    query = update.callback_query
    await query.answer()
    
    context.user_data["waiting_for_custom_request"] = media_type
    context.user_data["original_message_id"] = query.message.message_id
    context.user_data["chat_id"] = query.message.chat_id
    
    await query.message.reply_text(
        "📝 *Напишите ваш запрос для ИИ*\n\n"
        "Примеры:\n"
        "• Сделай заголовок броским и коротким\n"
        "• Сократи до 300 символов\n"
        "• Сделай более официальным стиль\n"
        "• Добавь больше фактов и цифр\n\n"
        "Отправьте ваш запрос одним сообщением.\n\n"
        "Или нажмите /cancel для отмены.",
        parse_mode="Markdown",
        reply_markup=get_custom_request_keyboard(media_type)
    )

async def handle_custom_request_text(update, context):
    media_type = context.user_data.get("waiting_for_custom_request")
    if not media_type:
        return
    
    custom_request = update.message.text
    
    context.user_data["waiting_for_custom_request"] = None
    
    await update.message.reply_text(f"✅ Запрос принят: *{custom_request[:100]}*...\n🤖 Обрабатываю...", parse_mode="Markdown")
    
    try:
        await update.message.delete()
    except:
        pass
    
    class FakeQuery:
        def __init__(self, message, chat_id):
            self.message = message
            self.message.chat_id = chat_id
        async def answer(self):
            pass
    
    original_chat_id = context.user_data.get("chat_id")
    original_message_id = context.user_data.get("original_message_id")
    
    if original_chat_id and original_message_id:
        try:
            original_message = await context.bot.get_message(chat_id=original_chat_id, message_id=original_message_id)
            fake_query = FakeQuery(original_message, original_chat_id)
            await ai_process_with_custom_request(fake_query, context, media_type, custom_request)
        except Exception as e:
            print(f"Ошибка при получении исходного сообщения: {e}")
            await update.message.reply_text("❌ Не удалось обработать запрос. Попробуйте снова.")
    else:
        await update.message.reply_text("❌ Не удалось обработать запрос. Попробуйте снова.")

async def back_to_ai_result_callback(update, context, media_type):
    query = update.callback_query
    await query.answer()
    
    context.user_data["waiting_for_custom_request"] = None
    
    pending = context.chat_data.get("pending", {})
    text = pending.get("text", "")
    
    lines = text.split('\n', 1)
    title = lines[0].strip() if lines else ""
    body = lines[1].strip() if len(lines) > 1 else ""
    
    await query.message.edit_text(
        f"✅ *Готово!*\n\n"
        f"📰 *{title}*\n\n"
        f"📝 {body}\n\n"
        f"Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_ai_result_keyboard(media_type)
    )

# ==================== РЕДАКТИРОВАНИЕ ====================
async def edit_text_callback(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_edit"] = True
    await query.message.reply_text("✏️ Отправьте новый текст. /cancel для отмены.")

async def handle_edit_text(update, context):
    if context.user_data.get("waiting_edit"):
        pending = context.chat_data.get("pending", {})
        if pending:
            new_text = update.message.text
            if len(new_text) > MAX_CAPTION_LEN:
                new_text = new_text[:MAX_CAPTION_LEN-3] + "..."
            pending["text"] = new_text
            context.chat_data["pending"] = pending
            media_type = pending.get("type", "photo")
            await update.message.reply_text("✅ Текст обновлён!", reply_markup=get_preview_keyboard(media_type))
        context.user_data["waiting_edit"] = None

# ==================== ОТЛОЖЕННАЯ ПУБЛИКАЦИЯ ====================
async def schedule_post(update, context, media_type):
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
    
    pending = context.chat_data.get("pending", {})
    
    if media_type == "photo":
        save_scheduled_post(pending["text"], pending["photo_bytes"], publish_time, has_buttons=True)
    elif media_type == "video":
        save_scheduled_post(pending["text"], None, publish_time, has_buttons=True, is_video=True, video_file_id=pending["file_id"])
    else:
        save_scheduled_post(pending["text"], None, publish_time, has_buttons=True, is_text=True)
    
    await query.message.reply_text(f"✅ Пост запланирован на {time_str}")
    context.chat_data.pop("pending", None)
    try: await query.message.delete()
    except: pass

# ==================== НАЗАД ====================
async def back_to_preview(update, context, media_type):
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("pending", {})
    
    if media_type == "photo":
        await query.message.reply_photo(photo=pending["photo_bytes"], caption=pending["text"] or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("photo"))
    elif media_type == "video":
        await query.message.reply_video(video=pending["file_id"], caption=pending["text"] or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("video"))
    elif media_type == "text":
        await query.message.edit_text(text=f"📝 Текст:\n\n{pending['text']}\n\nВыберите действие:", parse_mode="HTML", reply_markup=get_preview_keyboard("text"))
    
    try: await query.message.delete()
    except: pass

async def back_to_original_callback(update, context):
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("pending", {})
    if pending.get("type") == "photo":
        await query.message.reply_photo(photo=pending["photo_bytes"], caption=pending["text"] or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("photo"))
        try: await query.message.delete()
        except: pass

# ==================== ПЛАНИРОВЩИК ====================
async def check_scheduled_posts(app):
    while True:
        try:
            for post in get_pending_scheduled_posts():
                if post.get("is_video") and post.get("video_file_id"):
                    await send_to_channel(app, text=post["text"], has_buttons=post["has_buttons"], is_video=True, video_file_id=post["video_file_id"])
                elif post.get("is_text"):
                    await send_to_channel(app, text=post["text"], has_buttons=post["has_buttons"], is_text=True)
                elif post.get("photo_bytes"):
                    await send_to_channel(app, photo_bytes=post["photo_bytes"], text=post["text"], has_buttons=post["has_buttons"])
                delete_scheduled_post(post["id"])
                print("✅ Опубликован отложенный пост")
        except Exception as e: print(f"❌ Ошибка планировщика: {e}")
        await asyncio.sleep(60)

# ==================== ОСНОВНОЙ ОБРАБОТЧИК ====================
async def button_callback(update, context):
    query = update.callback_query
    data = query.data
    
    # Публикация
    if data == "publish_photo_with_buttons": await publish_photo(update, context, True)
    elif data == "publish_photo_no_buttons": await publish_photo(update, context, False)
    elif data == "publish_text_with_buttons": await publish_text(update, context, True)
    elif data == "publish_text_no_buttons": await publish_text(update, context, False)
    elif data == "publish_video_with_buttons": await publish_video(update, context, True)
    elif data == "publish_video_no_buttons": await publish_video(update, context, False)
    elif data == "publish_designed_with_buttons": await publish_designed(update, context, True)
    elif data == "publish_designed_no_buttons": await publish_designed(update, context, False)
    elif data == "publish_watermarked_with_buttons": await publish_watermarked(update, context, True)
    elif data == "publish_watermarked_no_buttons": await publish_watermarked(update, context, False)
    
    # Оформление
    elif data == "design_post": await design_post_callback(update, context)
    elif data == "design_from_watermark": await design_from_watermark_callback(update, context)
    elif data == "add_watermark": await add_watermark_callback(update, context)
    elif data == "add_watermark_to_designed": await add_watermark_to_designed_callback(update, context)
    
    # AI обработка
    elif data == "ai_process_photo": 
        await ai_process_with_custom_request(update, context, "photo", None)
    elif data == "ai_process_video": 
        await ai_process_with_custom_request(update, context, "video", None)
    elif data == "ai_process_text": 
        await ai_process_with_custom_request(update, context, "text", None)
    
    # AI кастомные запросы
    elif data == "ai_custom_request_photo":
        await ai_custom_request_callback(update, context, "photo")
    elif data == "ai_custom_request_video":
        await ai_custom_request_callback(update, context, "video")
    elif data == "ai_custom_request_text":
        await ai_custom_request_callback(update, context, "text")
    
    # Возврат к результатам AI
    elif data == "back_to_ai_result_photo":
        await back_to_ai_result_callback(update, context, "photo")
    elif data == "back_to_ai_result_video":
        await back_to_ai_result_callback(update, context, "video")
    elif data == "back_to_ai_result_text":
        await back_to_ai_result_callback(update, context, "text")
    
    # Редактирование
    elif data == "edit_text": await edit_text_callback(update, context)
    
    # Отложенная публикация
    elif data == "schedule_photo_menu":
        await query.answer()
        await query.message.edit_reply_markup(reply_markup=get_schedule_keyboard("schedule_photo"))
    elif data == "schedule_text_menu":
        await query.answer()
        await query.message.edit_reply_markup(reply_markup=get_schedule_keyboard("schedule_text"))
    elif data == "schedule_video_menu":
        await query.answer()
        await query.message.edit_reply_markup(reply_markup=get_schedule_keyboard("schedule_video"))
    
    elif data.startswith("schedule_photo:"): await schedule_post(update, context, "photo")
    elif data.startswith("schedule_text:"): await schedule_post(update, context, "text")
    elif data.startswith("schedule_video:"): await schedule_post(update, context, "video")
    
    # Назад
    elif data == "back_to_photo_preview": await back_to_preview(update, context, "photo")
    elif data == "back_to_video_preview": await back_to_preview(update, context, "video")
    elif data == "back_to_text_preview": await back_to_preview(update, context, "text")
    elif data == "back_to_original": await back_to_original_callback(update, context)
    
    elif data == "send_media_info":
        await query.answer()
        await query.message.reply_text("📸 Отправьте текст, фото или видео с подписью")

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("✅ Отменено")

# ==================== ЗАПУСК ====================
app = FastAPI()
@app.get("/")
async def root(): return {"status": "ok"}
@app.get("/health")
async def health(): return {"status": "alive"}

async def run_bot():
    init_db()
    await Bot(token=BOT_TOKEN).delete_webhook()
    print("✅ Webhook удалён")
    
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_text))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_request_text))
    
    await application.initialize()
    await application.start()
    asyncio.create_task(check_scheduled_posts(application))
    await application.updater.start_polling()
    print("✅ Бот запущен!")

if __name__ == "__main__":
    import threading, uvicorn
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_bot())
    port = int(os.getenv("PORT", 10000))
    threading.Thread(target=lambda: uvicorn.run(app, host="0.0.0.0", port=port)).start()
    loop.run_forever()
