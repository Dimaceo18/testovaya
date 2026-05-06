import asyncio
import sqlite3
import os
import re
import io
import logging
import base64
from datetime import datetime, timedelta

from fastapi import FastAPI
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import Conflict
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from openai import AsyncOpenAI
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNELS = {
    "news": {
        "name": "📰 Новости Минска",
        "chat_id": os.getenv("CHANNEL_NEWS_ID", ""),
        "link": os.getenv("CHANNEL_NEWS_LINK", "https://t.me/minsk_news")
    },
    "afisha": {
        "name": "🎭 Афиша Минска",
        "chat_id": os.getenv("CHANNEL_AFISHA_ID", ""),
        "link": os.getenv("CHANNEL_AFISHA_LINK", "https://t.me/minsk_afisha")
    }
}

SUGGEST_LINK = os.getenv("SUGGEST_LINK", "https://t.me/minsk_news_bot?start=suggest")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_PATH = "news.db"

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ==================== ПРОМПТ ДЛЯ GPT-IMAGE-1 ====================
AFISHA_IMAGE_PROMPT = """Создай современный Instagram-пост 4:5 в стиле premium city media.

Используй присланную фотографию как основу.

Стиль:
- cinematic
- minimalistic
- urban editorial
- Apple-style
- realistic lighting
- premium magazine aesthetic
- dark emerald overlay
- warm yellow accents
- soft cream typography
- realistic shadows
- modern Belarus media style 2026

Композиция:
- слева темный зелёный градиент
- справа атмосферная фотография города
- clean typography
- много воздуха
- rounded corners
- тонкие декоративные линии сверху

Важно:
- не делать дешёвый баннер
- не делать overly AI style
- сохранить realism
- стиль дорогого городского медиа

НЕ ДОБАВЛЯЙ ТЕКСТ на изображение. Только фон, стиль, атмосферу.

Тема: {user_text}"""

# Промпты для текстовых функций
DEEPSEEK_PROMPT = """Ты редактор новостного сайта. Переделай новость в формат на 500-600 символов. Убери воду, сделай интересный заголовок. Без смайликов.

Верни строго в формате:
ЗАГОЛОВОК: (заголовок)
ТЕКСТ: (текст с абзацами)"""

CHATGPT_POST_PROMPT = """Создай современный Instagram-пост в стиле Minsk City Magazine.

Формат:
ЗАГОЛОВОК: (цепляющий заголовок до 60 символов)
ТЕКСТ: (2-3 предложения)
ПРИЗЫВ: (призыв к действию)
ХЭШТЕГИ: (2-3 хэштега)"""

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS scheduled_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            text TEXT, photo_bytes BLOB, schedule_time TIMESTAMP, 
            created_at TIMESTAMP, has_buttons BOOLEAN DEFAULT 1, 
            channel_id TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, role TEXT, content TEXT, created_at TIMESTAMP)""")
    print("✅ База данных готова")

def save_scheduled_post(text, photo_bytes, schedule_time, has_buttons=True, channel_id=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""INSERT INTO scheduled_posts 
            (text, photo_bytes, schedule_time, created_at, has_buttons, channel_id) 
            VALUES (?, ?, ?, ?, ?, ?)""",
            (text, photo_bytes, schedule_time, datetime.now(), has_buttons, channel_id))

def get_pending_scheduled_posts():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "SELECT * FROM scheduled_posts WHERE schedule_time <= ?", (datetime.now(),)).fetchall()]

def delete_scheduled_post(post_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM scheduled_posts WHERE id = ?", (post_id,))

def save_chat_message(user_id, role, content):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO chat_history (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, role, content, datetime.now()))

def get_chat_history(user_id, limit=10):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", 
            (user_id, limit)).fetchall()][::-1]

def clear_chat_history(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))

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
        "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)

def format_caption(title, body):
    if body and body.strip():
        return f"<b>{title}</b>\n\n{body}"
    return f"<b>{title}</b>"

def add_links_to_text(text, channel_link, has_buttons=True):
    if not has_buttons: return text
    return text + f"\n\n<a href=\"{channel_link}\">📢 Подписаться на канал</a>\n<a href=\"{SUGGEST_LINK}\">📝 Прислать новость</a>"

# ==================== НАЛОЖЕНИЕ ТЕКСТА PILLOW ====================
def add_text_with_pillow(photo_bytes, title, description, call_to_action, hashtags):
    """Накладывает текст на изображение через Pillow (стабильно, красиво, с русским текстом)"""
    if not photo_bytes:
        raise ValueError("Фото пустое")
    
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    w, h = img.size
    
    # Обрезка до 4:5
    target_ratio = 4/5
    if w/h > target_ratio:
        new_w = int(h * target_ratio)
        img = img.crop(((w - new_w)//2, 0, (w + new_w)//2, h))
    else:
        new_h = int(w / target_ratio)
        img = img.crop((0, (h - new_h)//2, w, (h + new_h)//2))
    img = img.resize((1080, 1350), Image.Resampling.LANCZOS)
    w, h = img.size
    
    draw = ImageDraw.Draw(img)
    
    # Загрузка шрифтов
    try:
        font_title = ImageFont.truetype("Montserrat-Bold.ttf", 72)
        font_text = ImageFont.truetype("Montserrat-Regular.ttf", 36)
        font_small = ImageFont.truetype("Montserrat-Light.ttf", 28)
    except:
        # Если шрифтов нет, используем стандартные
        font_title = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Декоративные линии сверху
    for i in range(3):
        draw.line([(60 + i*80, 80), (60 + i*80 + 50, 80)], fill=(218, 165, 32), width=3)
    
    # Заголовок
    title_y = 140
    title_lines = []
    current_line = ""
    for word in title.split():
        test_line = current_line + " " + word if current_line else word
        try:
            bbox = font_title.getbbox(test_line)
            width = bbox[2] - bbox[0]
        except:
            width = len(test_line) * 40
        if width <= w - 120:
            current_line = test_line
        else:
            if current_line:
                title_lines.append(current_line)
            current_line = word
    if current_line:
        title_lines.append(current_line)
    
    for i, line in enumerate(title_lines[:3]):
        # Тень
        for offset in [(2,2)]:
            draw.text((60 + offset[0], title_y + i*85 + offset[1]), line, font=font_title, fill=(0,0,0))
        # Основной текст - первая строка золотом
        if i == 0:
            draw.text((60, title_y + i*85), line, font=font_title, fill=(255, 215, 0))
        else:
            draw.text((60, title_y + i*85), line, font=font_title, fill=(255, 255, 255))
    
    # Описание
    if description:
        desc_y = title_y + len(title_lines)*85 + 60
        # Разбиваем на строки
        lines = []
        current = ""
        for word in description.split():
            test = current + " " + word if current else word
            if len(test) * 25 < w - 120:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        
        for i, line in enumerate(lines[:6]):
            # Тень
            draw.text((61, desc_y + i*50 + 1), line, font=font_text, fill=(0,0,0))
            draw.text((60, desc_y + i*50), line, font=font_text, fill=(255, 255, 255))
        
        call_y = desc_y + len(lines)*50 + 50
    else:
        call_y = 350
    
    # Призыв
    if call_to_action:
        draw.text((61, call_y + 1), f"✨ {call_to_action}", font=font_small, fill=(0,0,0))
        draw.text((60, call_y), f"✨ {call_to_action}", font=font_small, fill=(255, 215, 0))
        hash_y = call_y + 60
    else:
        hash_y = call_y
    
    # Хэштеги
    if hashtags:
        draw.text((61, hash_y + 1), hashtags, font=font_small, fill=(0,0,0))
        draw.text((60, hash_y), hashtags, font=font_small, fill=(180, 180, 180))
    
    # Нижние линии
    for i in range(3):
        draw.line([(60 + i*80, h - 60), (60 + i*80 + 50, h - 60)], fill=(218, 165, 32), width=2)
    
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=90)
    output.seek(0)
    return output

# ==================== ГЕНЕРАЦИЯ ФОНА ЧЕРЕЗ GPT-IMAGE-1 ====================
async def generate_background_with_gpt(photo_bytes, user_text):
    """Генерирует фон/стиль через DALL-E 2"""
    if not openai_client:
        return None, "❌ OpenAI API не настроен"
    
    prompt = AFISHA_IMAGE_PROMPT.format(user_text=user_text)
    
    # Преобразуем bytearray в bytes
    if isinstance(photo_bytes, bytearray):
        photo_bytes = bytes(photo_bytes)
    
    try:
        # МЕНЯЕМ model="dall-e-2" ВМЕСТО "gpt-image-1"
        response = await openai_client.images.generate(  # ЗАМЕНИЛИ edit НА generate
            model="dall-e-2",  # <--- ИЗМЕНЕНО
            prompt=prompt,
            size="512x512",
            n=1,
            quality="standard"
        )
        
        # Получаем результат
        image_url = response.data[0].url
        async with httpx.AsyncClient() as client:
            img_response = await client.get(image_url)
            return io.BytesIO(img_response.content), None
            
    except Exception as e:
        logger.error(f"DALL-E 2 ошибка: {e}")
        return None, f"Ошибка: {e}"

# ==================== ПОЛНЫЙ ПРОЦЕСС СОЗДАНИЯ АФИШИ ====================
async def create_afisha_full(photo_bytes, user_text):
    """Полный процесс: DALL-E 2 генерирует фон, Pillow накладывает текст"""
    
    # Шаг 1: Генерируем фон через DALL-E 2
    background, error = await generate_background_with_gpt(photo_bytes, user_text)
    if error:
        return None, error
    
    # Шаг 2: Парсим текст для наложения
    lines = user_text.split('\n')
    title = lines[0][:60] if lines else user_text[:60]
    description = '\n'.join(lines[1:]) if len(lines) > 1 else ""
    
    # Шаг 3: Накладываем текст через Pillow
    call_to_action = "Подпишитесь на канал"
    hashtags = "#Minsk #Афиша"
    
    final_image = add_text_with_pillow(background.getvalue(), title, description, call_to_action, hashtags)
    
    # Формируем финальный текст
    final_text = f"{title}\n\n{description}\n\n✨ {call_to_action}\n\n{hashtags}"
    
    return final_image, final_text

# ==================== AI ФУНКЦИИ ====================
async def chat_with_gpt(user_id, message):
    if not openai_client:
        return "❌ OpenAI API не настроен"
    save_chat_message(user_id, "user", message)
    history = get_chat_history(user_id, 10)
    messages = [{"role": "system", "content": "Ты дружелюбный помощник. Отвечай кратко."}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": message})
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.7,
            max_tokens=1000
        )
        reply = response.choices[0].message.content
        save_chat_message(user_id, "assistant", reply)
        return reply
    except Exception as e:
        return f"❌ Ошибка: {e}"

async def generate_gpt_post(text):
    if not openai_client:
        return None, "❌ OpenAI не настроен"
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": CHATGPT_POST_PROMPT}, {"role": "user", "content": text}],
            temperature=0.8,
            max_tokens=500
        )
        result = response.choices[0].message.content
        title = ""; body = ""; call = ""; hashtags = ""
        for line in result.split('\n'):
            if line.startswith("ЗАГОЛОВОК:"): title = line.replace("ЗАГОЛОВОК:", "").strip()
            elif line.startswith("ТЕКСТ:"): body = line.replace("ТЕКСТ:", "").strip()
            elif line.startswith("ПРИЗЫВ:"): call = line.replace("ПРИЗЫВ:", "").strip()
            elif line.startswith("ХЭШТЕГИ:"): hashtags = line.replace("ХЭШТЕГИ:", "").strip()
        if not title: title = text[:50]
        if not call: call = "Подпишитесь"
        if not hashtags: hashtags = "#Minsk"
        return f"{title}\n\n{body}\n\n✨ {call}\n\n{hashtags}", None
    except Exception as e:
        return None, str(e)

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Создать афишу", callback_data="create_afisha")],
        [InlineKeyboardButton("✨ Стильный пост (GPT)", callback_data="create_style_post")],
        [InlineKeyboardButton("🤖 Чат с GPT", callback_data="start_gpt_chat")],
        [InlineKeyboardButton("📤 Опубликовать", callback_data="publish_menu")]
    ])

def get_afisha_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_with_buttons")],
        [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_no_buttons")],
        [InlineKeyboardButton("🔄 Новая афиша", callback_data="create_afisha")],
        [InlineKeyboardButton("⏰ Отложить", callback_data="schedule_menu")],
        [InlineKeyboardButton("🏠 Меню", callback_data="back_to_menu")]
    ])

def get_preview_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_with_buttons")],
        [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_no_buttons")],
        [InlineKeyboardButton("⏰ Отложить", callback_data="schedule_menu")],
        [InlineKeyboardButton("🏠 Меню", callback_data="back_to_menu")]
    ])

def get_chat_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Выйти", callback_data="exit_chat")]])

def get_channel_keyboard(action):
    keyboard = []
    for key, channel in CHANNELS.items():
        if channel["chat_id"]:
            keyboard.append([InlineKeyboardButton(channel["name"], callback_data=f"{action}:{key}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_schedule_keyboard():
    times = [("30 мин", "30"), ("9:05", "9:05"), ("10:05", "10:05"), ("11:07", "11:07"),
             ("12:08", "12:08"), ("13:09", "13:09"), ("14:10", "14:10"), ("15:11", "15:11"),
             ("16:12", "16:12"), ("17:13", "17:13"), ("18:14", "18:14"), ("19:07", "19:07"),
             ("20:08", "20:08"), ("21:09", "21:09"), ("22:11", "22:11"), ("22:45", "22:45")]
    keyboard = []
    row = []
    for label, val in times:
        row.append(InlineKeyboardButton(label, callback_data=f"schedule:{val}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_preview")])
    return InlineKeyboardMarkup(keyboard)

# ==================== ОТПРАВКА В КАНАЛ ====================
async def send_to_channel(context, channel_id, channel_link, photo_bytes, text, has_buttons=True):
    lines = text.split('\n')
    title = lines[0] if lines else ""
    body = '\n'.join(lines[1:]) if len(lines) > 1 else ""
    caption = add_links_to_text(format_caption(title, body), channel_link, has_buttons)
    try:
        await context.bot.send_photo(chat_id=channel_id, photo=photo_bytes, caption=caption, parse_mode="HTML", disable_web_page_preview=True)
        return True
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return False

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================
async def start(update, context):
    await update.message.reply_text(
        "🤖 *MINSK NEWS BOT*\n\n"
        "🎨 *Афиша* - фото → стильный фон от GPT + ваш текст от Pillow\n"
        "✨ *Стильный пост* - текст → готовый пост\n"
        "🤖 *Чат с GPT* - общение с ИИ\n"
        "📤 *Опубликовать* - выбрать канал\n\n"
        "Все афиши создаются в едином стиле premium city media!",
        parse_mode="Markdown", reply_markup=get_main_keyboard())

# === АФИША ===
async def create_afisha(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📸 Отправьте фото для афиши:")
    context.user_data["state"] = "afisha_photo"

async def handle_afisha_photo(update, context):
    if context.user_data.get("state") != "afisha_photo":
        return
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    context.user_data["afisha_photo_bytes"] = photo_bytes
    context.user_data["state"] = "afisha_text"
    await update.message.reply_text("✅ Фото получено! Теперь отправьте текст для афиши:")

async def handle_afisha_text(update, context):
    if context.user_data.get("state") != "afisha_text":
        return
    text = update.message.text
    photo_bytes = context.user_data.get("afisha_photo_bytes")
    if not photo_bytes:
        await update.message.reply_text("❌ Ошибка, начните заново", reply_markup=get_main_keyboard())
        return
    
    await update.message.reply_text("🎨 Создаю афишу...\n\nШаг 1/2: GPT генерирует фон и стиль (10-20 сек)")
    
    # Создаём афишу (GPT фон + Pillow текст)
    result_image, final_text = await create_afisha_full(photo_bytes, text)
    
    if result_image:
        context.user_data["pending"] = {"type": "photo", "text": final_text, "photo_bytes": result_image.getvalue()}
        context.user_data["state"] = None
        await update.message.reply_photo(
            photo=result_image, 
            caption=f"✨ *Афиша готова!*\n\n{final_text}\n\n👇 Выберите действие:", 
            parse_mode="Markdown",
            reply_markup=get_afisha_keyboard())
    else:
        await update.message.reply_text(f"❌ {final_text}", reply_markup=get_main_keyboard())

# === СТИЛЬНЫЙ ПОСТ ===
async def create_style_post(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📝 Отправьте текст для стильного поста:")
    context.user_data["state"] = "style_text"

async def handle_style_text(update, context):
    if context.user_data.get("state") != "style_text":
        return
    text = update.message.text
    await update.message.reply_text("✨ Генерирую...")
    result, error = await generate_gpt_post(text)
    if result:
        context.user_data["pending"] = {"type": "text", "text": result, "photo_bytes": None}
        await update.message.reply_text(f"✨ *Сгенерированный пост:*\n\n{result}\n\nВыберите действие:", parse_mode="Markdown", reply_markup=get_preview_keyboard())
    else:
        await update.message.reply_text(f"❌ {error}")

# === ЧАТ GPT ===
async def start_gpt_chat(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["chat_mode"] = True
    await query.message.reply_text("🤖 Чат с GPT активирован!\nОтправляйте сообщения.\n/exit для выхода.", reply_markup=get_chat_keyboard())

async def handle_chat_message(update, context):
    if not context.user_data.get("chat_mode"):
        return
    text = update.message.text
    if text == "/exit":
        context.user_data["chat_mode"] = False
        await update.message.reply_text("👋 Выход из чата.", reply_markup=get_main_keyboard())
        return
    await update.message.reply_text("🤔 Думаю...")
    response = await chat_with_gpt(update.effective_user.id, text)
    await update.message.reply_text(response, reply_markup=get_chat_keyboard())

# === ОБЩИЕ ФУНКЦИИ ===
async def publish_with_selection(update, context, has_buttons):
    query = update.callback_query
    await query.answer()
    pending = context.user_data.get("pending") or context.chat_data.get("pending")
    if not pending:
        await query.message.reply_text("❌ Нет готового поста. Сначала создайте афишу или стильный пост.")
        return
    
    # Для текстового поста нет фото
    if pending.get("type") == "text":
        await query.message.reply_text("ℹ️ Текстовый пост нельзя опубликовать в канал как сообщение с фото.\nСначала добавьте фото через 'Создать афишу'.")
        return
    
    if not pending.get("photo_bytes"):
        await query.message.reply_text("❌ Нет фото для публикации")
        return
    
    context.user_data["publish_data"] = {"photo": pending["photo_bytes"], "text": pending["text"], "has_buttons": has_buttons}
    await query.message.reply_text("📢 Выберите канал:", reply_markup=get_channel_keyboard("publish"))

async def execute_publish(update, context):
    query = update.callback_query
    data = query.data
    if data.startswith("publish:"):
        channel_key = data.split(":")[1]
        channel = CHANNELS.get(channel_key)
        if not channel or not channel["chat_id"]:
            await query.message.reply_text("❌ Канал не настроен")
            return
        pub_data = context.user_data.get("publish_data", {})
        success = await send_to_channel(context, channel["chat_id"], channel["link"], pub_data["photo"], pub_data["text"], pub_data.get("has_buttons", True))
        await query.message.reply_text(f"✅ Опубликовано в {channel['name']}" if success else "❌ Ошибка")
        context.user_data.pop("publish_data", None)
    elif data == "back_to_menu":
        await back_to_menu(update, context)

async def schedule_menu(update, context):
    query = update.callback_query
    await query.answer()
    await query.message.edit_reply_markup(reply_markup=get_schedule_keyboard())

async def schedule_post_callback(update, context):
    query = update.callback_query
    await query.answer()
    time_val = query.data.split(":")[1]
    now = datetime.now()
    if time_val == "30":
        publish_time = now + timedelta(minutes=30)
        time_str = "через 30 минут"
    else:
        hour, minute = map(int, time_val.split(":"))
        publish_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if publish_time <= now:
            publish_time += timedelta(days=1)
        time_str = publish_time.strftime("%H:%M")
    
    pending = context.user_data.get("pending") or context.chat_data.get("pending")
    if not pending or not pending.get("photo_bytes"):
        await query.message.reply_text("❌ Нет поста для отложенной публикации")
        return
    
    default_channel = CHANNELS.get("news", {}).get("chat_id", "")
    save_scheduled_post(pending["text"], pending["photo_bytes"], publish_time, has_buttons=True, channel_id=default_channel)
    await query.message.reply_text(f"✅ Пост запланирован на {time_str}")

async def back_to_menu(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text("🏠 Главное меню:", reply_markup=get_main_keyboard())

async def back_to_preview(update, context):
    query = update.callback_query
    await query.answer()
    pending = context.user_data.get("pending") or context.chat_data.get("pending")
    if pending and pending.get("photo_bytes"):
        await query.message.reply_photo(photo=pending["photo_bytes"], caption=pending["text"], reply_markup=get_preview_keyboard())
    elif pending:
        await query.message.reply_text(pending["text"], reply_markup=get_preview_keyboard())

async def exit_chat(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["chat_mode"] = False
    await back_to_menu(update, context)

# === ОБРАБОТЧИК ВХОДНЫХ СООБЩЕНИЙ ===
async def handle_message(update, context):
    if update.message.photo:
        await handle_afisha_photo(update, context)
    elif context.user_data.get("chat_mode"):
        await handle_chat_message(update, context)
    elif context.user_data.get("state") == "afisha_text":
        await handle_afisha_text(update, context)
    elif context.user_data.get("state") == "style_text":
        await handle_style_text(update, context)

# === КОЛБЭК ===
async def button_callback(update, context):
    query = update.callback_query
    data = query.data
    
    if data == "create_afisha":
        await create_afisha(update, context)
    elif data == "create_style_post":
        await create_style_post(update, context)
    elif data == "start_gpt_chat":
        await start_gpt_chat(update, context)
    elif data == "publish_menu":
        await publish_with_selection(update, context, True)
    elif data == "publish_with_buttons":
        await publish_with_selection(update, context, True)
    elif data == "publish_no_buttons":
        await publish_with_selection(update, context, False)
    elif data == "schedule_menu":
        await schedule_menu(update, context)
    elif data == "back_to_menu":
        await back_to_menu(update, context)
    elif data == "back_to_preview":
        await back_to_preview(update, context)
    elif data == "exit_chat":
        await exit_chat(update, context)
    elif data.startswith("publish:"):
        await execute_publish(update, context)
    elif data.startswith("schedule:"):
        await schedule_post_callback(update, context)
    
    await query.answer()

# === ПЛАНИРОВЩИК ===
async def check_scheduled_posts(app):
    while True:
        try:
            for post in get_pending_scheduled_posts():
                channel_id = post.get("channel_id") or CHANNELS.get("news", {}).get("chat_id", "")
                channel_link = CHANNELS.get("news", {}).get("link", "")
                await send_to_channel(app, channel_id, channel_link, post["photo_bytes"], post["text"], post["has_buttons"])
                delete_scheduled_post(post["id"])
        except Exception as e:
            logger.error(f"Ошибка планировщика: {e}")
        await asyncio.sleep(60)

# === ЗАПУСК ===
app = FastAPI()

@app.get("/")
async def root(): 
    return {"status": "ok"}

@app.get("/health")
async def health(): 
    return {"status": "alive"}

async def run_bot():
    init_db()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.PHOTO | filters.TEXT, handle_message))
    
    await application.initialize()
    await application.start()
    asyncio.create_task(check_scheduled_posts(application))
    
    while True:
        try:
            await application.updater.start_polling(drop_pending_updates=True)
            logger.info("✅ Бот запущен!")
            break
        except Conflict:
            logger.info("Конфликт, перезапуск через 5 сек...")
            await asyncio.sleep(5)
    
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    import threading, uvicorn
    port = int(os.getenv("PORT", 10000))
    threading.Thread(target=lambda: uvicorn.run(app, host="0.0.0.0", port=port), daemon=True).start()
    asyncio.run(run_bot())
