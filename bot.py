import asyncio
import sqlite3
import os
import re
import io
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from fastapi import FastAPI
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import Conflict
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from openai import AsyncOpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Каналы для публикации
CHANNELS = {
    "news": {
        "name": "📰 Новости Минска",
        "chat_id": os.getenv("CHANNEL_NEWS_ID", ""),
        "link": os.getenv("CHANNEL_NEWS_LINK", "https://t.me/minsk_news")
    },
    "incident": {
        "name": "🚨 ЧП И ДТП Минска",
        "chat_id": os.getenv("CHANNEL_INCIDENT_ID", ""),
        "link": os.getenv("CHANNEL_INCIDENT_LINK", "https://t.me/minsk_chp")
    },
    "afisha": {
        "name": "🎭 Афиша Минска",
        "chat_id": os.getenv("CHANNEL_AFISHA_ID", ""),
        "link": os.getenv("CHANNEL_AFISHA_LINK", "https://t.me/minsk_afisha")
    }
}

SUGGEST_LINK = os.getenv("SUGGEST_LINK", "https://t.me/minsk_news_bot?start=suggest")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
CHATGPT_API_KEY = os.getenv("CHATGPT_API_KEY")
DB_PATH = "news.db"

WATERMARK_TEXT = "MINSK NEWS"
WATERMARK_OPACITY = 38

# Инициализация клиентов AI
deepseek_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com") if DEEPSEEK_API_KEY else None
chatgpt_client = AsyncOpenAI(api_key=CHATGPT_API_KEY, base_url="https://api.openai.com/v1") if CHATGPT_API_KEY else None

# Промпт для DeepSeek
DEEPSEEK_PROMPT = """Ты редактор новостного сайта. У тебя строгий новостной формат. Без обращений на "вы", "ты". Только новостной формат.

Переделай новость в формат на 500-600 символов. Убери всю воду, сделай интересный заголовок. Без смайликов. Сохраняй главные факты.

Текст должен быть разбит на логические абзацы (2-4 предложения). Между абзацами пустая строка.

Верни строго в формате:
ЗАГОЛОВОК: (заголовок новости до 80 символов)
ТЕКСТ: (текст новости с абзацами)"""

# Промпт для ChatGPT для создания стильного поста
CHATGPT_POST_PROMPT = """Ты креативный копирайтер premium city media. Создай современный Instagram-пост в стиле Minsk City Magazine.

Формат: короткий, ёмкий, вовлекающий текст для социальных сетей.

Требования к посту:
- Заголовок (крупный, цепляющий)
- Основной текст (3-4 предложения, атмосферно)
- Вовлекающий вопрос или призыв к действию в конце
- 2-3 хэштега (#Minsk #MinskCity #MinskNews)

Стиль: минималистичный, кинематографичный, эстетичный, премиальный.

Верни строго в формате:
ЗАГОЛОВОК: (заголовок)
ТЕКСТ: (основной текст)
ПРИЗЫВ: (призыв к действию)
ХЭШТЕГИ: (хэштеги через пробел)"""

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS scheduled_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, photo_bytes BLOB, schedule_time TIMESTAMP, created_at TIMESTAMP, has_buttons BOOLEAN DEFAULT 1, has_watermark BOOLEAN DEFAULT 0, is_designed BOOLEAN DEFAULT 0, is_video BOOLEAN DEFAULT 0, is_text BOOLEAN DEFAULT 0, is_album BOOLEAN DEFAULT 0, video_file_id TEXT, channel_id TEXT, is_ai_generated BOOLEAN DEFAULT 0, ai_model TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, created_at TIMESTAMP)")
    print("✅ База данных готова")

def save_scheduled_post(text, photo_bytes, schedule_time, has_buttons=True, has_watermark=False, is_designed=False, is_video=False, is_text=False, is_album=False, video_file_id=None, channel_id=None, is_ai_generated=False, ai_model=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO scheduled_posts (text, photo_bytes, schedule_time, created_at, has_buttons, has_watermark, is_designed, is_video, is_text, is_album, video_file_id, channel_id, is_ai_generated, ai_model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (text, photo_bytes, schedule_time, datetime.now(), has_buttons, has_watermark, is_designed, is_video, is_text, is_album, video_file_id, channel_id, is_ai_generated, ai_model))

def get_pending_scheduled_posts():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT id, text, photo_bytes, schedule_time, has_buttons, has_watermark, is_designed, is_video, is_text, is_album, video_file_id, channel_id, is_ai_generated, ai_model FROM scheduled_posts WHERE schedule_time <= ?", (datetime.now(),)).fetchall()]

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
        return [dict(row) for row in conn.execute("SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()][::-1]

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
        "\U0001F900-\U0001F9FF"
        "\U0001FA70-\U0001FAFF"
        "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)

def format_caption(title, body):
    if body and body.strip():
        return f"<b>{title}</b>\n\n{body}"
    else:
        return f"<b>{title}</b>"

def add_links_to_text(text, channel_link, has_buttons=True):
    if not has_buttons:
        return text
    
    links = f"\n\n<a href=\"{channel_link}\">📢 Подписаться на канал</a>\n<a href=\"{SUGGEST_LINK}\">📝 Прислать нам новость</a>"
    return text + links

# ==================== СОЗДАНИЕ СТИЛЬНОГО ПОСТА (С НАЛОЖЕНИЕМ ТЕКСТА) ====================
def create_city_post(photo_bytes, title, text, call_to_action, hashtags):
    """Создаёт стильный пост с наложением текста на фото"""
    if not photo_bytes or len(photo_bytes) == 0:
        raise ValueError("Фото пустое")
    
    print(f"🎨 Создаю стильный пост, размер: {len(photo_bytes) / 1024:.1f}KB")
    
    # Открываем изображение
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    w, h = img.size
    
    # Обрезаем до соотношения 4:5
    target_ratio = 4/5
    cur_ratio = w / h
    if cur_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    
    # Ресайз
    img = img.resize((1080, 1350), Image.Resampling.LANCZOS)
    w, h = img.size
    
    # Затемняем изображение
    img = ImageEnhance.Brightness(img).enhance(0.7)
    
    # Добавляем градиент слева
    gradient_width = int(w * 0.45)  # 45% ширины
    overlay_alpha = Image.new("L", (w, h), 0)
    grad = Image.new("L", (gradient_width, 1), 0)
    for x in range(gradient_width):
        # Плавный переход от чёрного к прозрачному
        a = int(200 * (1 - x / gradient_width))
        grad.putpixel((x, 0), a)
    grad = grad.resize((gradient_width, h))
    overlay_alpha.paste(grad, (0, 0))
    
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base = img.convert("RGBA")
    overlay = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
    img = Image.alpha_composite(base, overlay).convert("RGB")
    
    draw = ImageDraw.Draw(img)
    
    # Загрузка шрифтов
    try:
        font_title = ImageFont.truetype("Montserrat-Bold.ttf", 72)
        font_text = ImageFont.truetype("Montserrat-Regular.ttf", 32)
        font_small = ImageFont.truetype("Montserrat-Light.ttf", 28)
    except:
        font_title = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Декоративная линия сверху
    line_x = 60
    line_y = 80
    for i in range(3):
        draw.line([(line_x + i*80, line_y), (line_x + i*80 + 50, line_y)], fill=(218, 165, 32), width=3)  # золотой цвет
    
    # Заголовок (слева, крупный)
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
    
    for i, line in enumerate(title_lines):
        # Тень
        for offset in [(2,2), (2,-2), (-2,2), (-2,-2)]:
            draw.text((60 + offset[0], title_y + i*85 + offset[1]), line, font=font_title, fill=(0,0,0))
        # Основной текст
        if i == 0:
            draw.text((60, title_y + i*85), line, font=font_title, fill=(255, 215, 0))  # золотой для первой строки
        else:
            draw.text((60, title_y + i*85), line, font=font_title, fill=(255,255,255))
    
    # Основной текст
    text_y = title_y + len(title_lines)*85 + 60
    text_lines = []
    for paragraph in text.split('\n'):
        words = paragraph.split()
        current_line = ""
        for word in words:
            test_line = current_line + " " + word if current_line else word
            try:
                bbox = font_text.getbbox(test_line)
                width = bbox[2] - bbox[0]
            except:
                width = len(test_line) * 20
            if width <= w - 120:
                current_line = test_line
            else:
                if current_line:
                    text_lines.append(current_line)
                current_line = word
        if current_line:
            text_lines.append(current_line)
        text_lines.append("")  # пустая строка между абзацами
    
    for i, line in enumerate(text_lines):
        if line:
            # Тень
            for offset in [(1,1)]:
                draw.text((60 + offset[0], text_y + i*40 + offset[1]), line, font=font_text, fill=(0,0,0))
            draw.text((60, text_y + i*40), line, font=font_text, fill=(240,240,240))
    
    # Призыв к действию
    call_y = text_y + len(text_lines)*40 + 40
    for offset in [(1,1)]:
        draw.text((60 + offset[0], call_y + offset[1]), f"✨ {call_to_action}", font=font_small, fill=(0,0,0))
    draw.text((60, call_y), f"✨ {call_to_action}", font=font_small, fill=(255, 215, 0))
    
    # Хэштеги
    hash_y = call_y + 50
    for offset in [(1,1)]:
        draw.text((60 + offset[0], hash_y + offset[1]), hashtags, font=font_small, fill=(0,0,0))
    draw.text((60, hash_y), hashtags, font=font_small, fill=(180,180,180))
    
    # Нижняя декоративная линия
    line_y_bottom = h - 60
    for i in range(3):
        draw.line([(60 + i*80, line_y_bottom), (60 + i*80 + 50, line_y_bottom)], fill=(218, 165, 32), width=2)
    
    # Сохраняем
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=90)
    output.seek(0)
    
    print(f"✅ Стильный пост создан! Размер: {output.getbuffer().nbytes / (1024 * 1024):.2f}MB")
    return output

# ==================== AI ЧАТ ====================
async def chat_with_gpt(user_id, message):
    """Общение с ChatGPT с сохранением истории"""
    if not chatgpt_client:
        return "❌ ChatGPT API не настроен. Добавьте переменную окружения CHATGPT_API_KEY"
    
    # Сохраняем сообщение пользователя
    save_chat_message(user_id, "user", message)
    
    # Получаем историю
    history = get_chat_history(user_id, 10)
    
    # Формируем сообщения
    messages = [{"role": "system", "content": "Ты дружелюбный и полезный ассистент. Отвечай кратко, по делу, но дружелюбно."}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": message})
    
    try:
        response = await chatgpt_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.7,
            max_tokens=1000
        )
        
        reply = response.choices[0].message.content
        save_chat_message(user_id, "assistant", reply)
        return reply
    except Exception as e:
        logger.error(f"Ошибка ChatGPT: {e}")
        return f"❌ Ошибка: {e}"

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

# ==================== ПРОЦЕССИНГ ФОТО ====================
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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Отправить фото для поста", callback_data="send_media_info")],
        [InlineKeyboardButton("🤖 Чат с GPT", callback_data="start_gpt_chat")],
        [InlineKeyboardButton("✨ Сделать стильный пост (GPT)", callback_data="create_style_post")]
    ])

def get_gpt_chat_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Очистить историю", callback_data="clear_chat_history")],
        [InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")]
    ])

def get_channel_selection_keyboard(original_callback, has_buttons):
    keyboard = []
    for key, channel in CHANNELS.items():
        if channel["chat_id"]:
            keyboard.append([InlineKeyboardButton(
                channel["name"], 
                callback_data=f"select_channel:{key}:{original_callback}:{has_buttons}"
            )])
    return InlineKeyboardMarkup(keyboard)

def get_album_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Опубликовать альбом с кнопками", callback_data="publish_album_with_buttons")],
        [InlineKeyboardButton("📤 Опубликовать альбом без кнопок", callback_data="publish_album_no_buttons")],
        [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_album_text")],
        [InlineKeyboardButton("🤖 Обработать текст (ИИ)", callback_data="ai_process_album")],
        [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_album_menu")]
    ])

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
    elif media_type == "album":
        return get_album_keyboard()
    elif media_type == "designed":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Опубликовать (с кнопками)", callback_data="publish_designed_with_buttons")],
            [InlineKeyboardButton("✅ Опубликовать (без кнопок)", callback_data="publish_designed_no_buttons")],
            [InlineKeyboardButton("💧 Добавить водяной знак", callback_data="add_watermark_to_designed")],
            [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_designed_text")],
            [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_designed_menu")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_photo_preview")]
        ])
    elif media_type == "watermarked":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать (с кнопками)", callback_data="publish_watermarked_with_buttons")],
            [InlineKeyboardButton("📤 Опубликовать (без кнопок)", callback_data="publish_watermarked_no_buttons")],
            [InlineKeyboardButton("🎨 Оформить", callback_data="design_from_watermark")],
            [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_watermarked_menu")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_original")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать с кнопками", callback_data="publish_photo_with_buttons")],
            [InlineKeyboardButton("📤 Опубликовать без кнопок", callback_data="publish_photo_no_buttons")],
            [InlineKeyboardButton("🎨 Оформить пост", callback_data="design_post")],
            [InlineKeyboardButton("💧 Водяной знак", callback_data="add_watermark")],
            [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_text")],
            [InlineKeyboardButton("🤖 Обработать текст (DeepSeek)", callback_data="ai_process_photo")],
            [InlineKeyboardButton("✨ Сделать стильный пост (GPT)", callback_data="create_style_post_from_photo")],
            [InlineKeyboardButton("⏰ Отложить публикацию", callback_data="schedule_photo_menu")]
        ])

def get_ai_result_keyboard(media_type):
    if media_type == "video":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать видео", callback_data="publish_video_with_buttons")],
            [InlineKeyboardButton("📝 Новый запрос ИИ", callback_data=f"ai_custom_request_video")],
            [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_text")],
            [InlineKeyboardButton("⏰ Отложить", callback_data=f"schedule_video_menu")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_video_preview")]
        ])
    elif media_type == "text":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать текст", callback_data="publish_text_with_buttons")],
            [InlineKeyboardButton("📝 Новый запрос ИИ", callback_data=f"ai_custom_request_text")],
            [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_text")],
            [InlineKeyboardButton("⏰ Отложить", callback_data=f"schedule_text_menu")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_text_preview")]
        ])
    elif media_type == "album":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать альбом", callback_data="publish_album_with_buttons")],
            [InlineKeyboardButton("📝 Новый запрос ИИ", callback_data=f"ai_custom_request_album")],
            [InlineKeyboardButton("✏️ Редактировать", callback_data="edit_album_text")],
            [InlineKeyboardButton("⏰ Отложить", callback_data=f"schedule_album_menu")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_album_preview")]
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Опубликовать", callback_data="publish_photo_with_buttons")],
            [InlineKeyboardButton("🎨 Оформить", callback_data="design_post")],
            [InlineKeyboardButton("💧 Водяной знак", callback_data="add_watermark")],
            [InlineKeyboardButton("📝 Новый запрос DeepSeek", callback_data=f"ai_custom_request_photo")],
            [InlineKeyboardButton("✨ Сделать стильный пост (GPT)", callback_data="create_style_post_from_photo")],
            [InlineKeyboardButton("⏰ Отложить", callback_data="schedule_photo_menu")],
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
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"back_to_{prefix.replace('schedule_', '').replace('_menu', '')}_preview")])
    return InlineKeyboardMarkup(keyboard)

# ==================== ОТПРАВКА В КАНАЛ ====================
async def send_to_channel(context, channel_id, channel_link, photo_bytes=None, file_id=None, text="", has_buttons=True, is_video=False, is_text=False, is_album=False, album_photos=None, video_file_id=None):
    lines = text.split('\n')
    title = lines[0] if lines else ""
    
    body_lines = []
    found_empty = False
    for line in lines[1:]:
        if not found_empty and line.strip() == "":
            found_empty = True
            continue
        if found_empty:
            body_lines.append(line)
        elif line.strip():
            body_lines.append(line)
    
    body = '\n'.join(body_lines).strip()
    
    if body:
        caption = f"<b>{title}</b>\n\n{body}"
    else:
        caption = f"<b>{title}</b>"
    
    caption = add_links_to_text(caption, channel_link, has_buttons)
    
    try:
        if is_video and video_file_id:
            await context.bot.send_video(
                chat_id=channel_id, 
                video=video_file_id, 
                caption=caption, 
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        elif is_album and album_photos:
            media_group = []
            for i, photo in enumerate(album_photos[:10]):
                if i == 0:
                    media_group.append({
                        "type": "photo", 
                        "media": photo, 
                        "caption": caption, 
                        "parse_mode": "HTML"
                    })
                else:
                    media_group.append({"type": "photo", "media": photo})
            await context.bot.send_media_group(chat_id=channel_id, media=media_group)
        elif is_text:
            await context.bot.send_message(
                chat_id=channel_id, 
                text=caption, 
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        elif photo_bytes:
            await context.bot.send_photo(
                chat_id=channel_id, 
                photo=photo_bytes, 
                caption=caption, 
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        elif file_id:
            await context.bot.send_photo(
                chat_id=channel_id, 
                photo=file_id, 
                caption=caption, 
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки в канал {channel_id}: {e}")
        return False

# ==================== ОБРАБОТЧИКИ ====================
async def start(update, context):
    await update.message.reply_text(
        "🤖 *MINSK NEWS BOT*\n\n"
        "📸 *Отправьте фото* для создания поста\n"
        "🤖 *Чат с GPT* - общайтесь с ИИ\n"
        "✨ *Стильный пост* - создайте премиальный пост через GPT\n\n"
        "👇 Выберите действие:",
        parse_mode="Markdown", reply_markup=get_main_keyboard())

async def handle_text(update, context):
    # Если в режиме чата с GPT
    if context.user_data.get("gpt_chat_mode"):
        user_id = update.effective_user.id
        message = update.message.text
        
        if message.lower() == "/exit":
            context.user_data["gpt_chat_mode"] = False
            await update.message.reply_text("👋 Выход из чата с GPT. Используйте /start для возврата в меню.")
            return
        
        await update.message.reply_text("🤔 Думаю...")
        response = await chat_with_gpt(user_id, message)
        await update.message.reply_text(response, parse_mode="Markdown", reply_markup=get_gpt_chat_keyboard())
        return
    
    # Обычная обработка текста для поста
    if context.user_data.get("waiting_for_custom_request"):
        return
    
    text = update.message.text
    if not text or text.startswith('/'): return
    context.chat_data["pending"] = {"type": "text", "text": remove_emojis(text)}
    await update.message.reply_text(f"📝 Текст:\n\n{text[:500]}...\n\nВыберите действие:", parse_mode="HTML", reply_markup=get_preview_keyboard("text"))

async def handle_photo(update, context):
    msg = update.message
    
    # Проверка на альбом
    if msg.media_group_id:
        album_key = f"album_{msg.media_group_id}"
        if album_key not in context.chat_data:
            context.chat_data[album_key] = []
        
        photo = msg.photo[-1]
        context.chat_data[album_key].append(photo.file_id)
        
        if not context.chat_data.get("processing_album"):
            asyncio.create_task(handle_album(update, context))
        return
    
    # Одиночное фото
    photo = msg.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    text = remove_emojis(msg.caption or "")
    context.chat_data["pending"] = {
        "type": "photo", 
        "text": text, 
        "file_id": photo.file_id, 
        "photo_bytes": photo_bytes, 
        "original": photo_bytes
    }
    await msg.reply_photo(photo=photo.file_id, caption=text or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("photo"))

async def handle_album(update, context):
    message = update.message
    if not message.media_group_id:
        return
    
    if context.chat_data.get("processing_album") == message.media_group_id:
        return
    
    context.chat_data["processing_album"] = message.media_group_id
    
    await asyncio.sleep(0.5)
    
    album_photos = context.chat_data.get(f"album_{message.media_group_id}", [])
    
    if not album_photos and message.photo:
        album_photos.append(message.photo[-1].file_id)
    
    if not album_photos:
        context.chat_data["processing_album"] = None
        return
    
    caption = remove_emojis(message.caption or "")
    
    photo_bytes_list = []
    for file_id in album_photos[:10]:
        try:
            file = await context.bot.get_file(file_id)
            photo_bytes = await file.download_as_bytearray()
            photo_bytes_list.append(photo_bytes)
        except Exception as e:
            logger.error(f"Ошибка загрузки фото: {e}")
    
    context.chat_data["pending"] = {
        "type": "album",
        "text": caption,
        "album_photos": album_photos[:10],
        "album_photos_bytes": photo_bytes_list
    }
    
    first_photo = album_photos[0]
    await message.reply_photo(
        photo=first_photo,
        caption=f"📸 *Альбом из {len(album_photos)} фото*\n\nТекст: {caption[:200]}...\n\nВыберите действие:",
        parse_mode="Markdown",
        reply_markup=get_album_keyboard()
    )
    
    context.chat_data["processing_album"] = None

async def handle_video(update, context):
    msg = update.message
    text = remove_emojis(msg.caption or "")
    context.chat_data["pending"] = {"type": "video", "text": text, "file_id": msg.video.file_id}
    await msg.reply_video(video=msg.video.file_id, caption=text or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("video"))

# ==================== GPT ЧАТ ====================
async def start_gpt_chat(update, context):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    context.user_data["gpt_chat_mode"] = True
    clear_chat_history(user_id)
    
    await query.message.reply_text(
        "🤖 *Чат с GPT активирован!*\n\n"
        "Просто отправляйте мне сообщения, и я буду отвечать.\n"
        "История диалога сохраняется.\n\n"
        "Команды:\n"
        "• /exit - выйти из чата\n"
        "• /clear - очистить историю\n\n"
        "👇 Начните общение!",
        parse_mode="Markdown",
        reply_markup=get_gpt_chat_keyboard()
    )

async def clear_chat_history_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    clear_chat_history(user_id)
    
    await query.message.reply_text("🗑 История диалога очищена!")

# ==================== СОЗДАНИЕ СТИЛЬНОГО ПОСТА ЧЕРЕЗ GPT ====================
async def create_style_post(update, context, photo_bytes=None):
    """Создаёт стильный пост через GPT и накладывает текст на фото"""
    query = update.callback_query
    await query.answer()
    
    if not chatgpt_client:
        await query.message.reply_text("❌ ChatGPT API не настроен. Добавьте переменную CHATGPT_API_KEY")
        return
    
    # Получаем текст для обработки
    pending = context.chat_data.get("pending", {})
    text = pending.get("text", "")
    
    if not text:
        await query.message.reply_text("❌ Нет текста для обработки. Сначала отправьте текст или фото с подписью.")
        return
    
    # Если нет фото, просим отправить
    if not photo_bytes and pending.get("type") == "photo":
        photo_bytes = pending.get("original")
    
    await query.message.reply_text("✨ Генерирую стильный пост через ChatGPT...")
    
    try:
        # Генерируем текст через GPT
        response = await chatgpt_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": CHATGPT_POST_PROMPT},
                {"role": "user", "content": f"Создай пост на тему: {text}"}
            ],
            temperature=0.8,
            max_tokens=500
        )
        
        result = response.choices[0].message.content
        
        # Парсим результат
        title = ""
        body = ""
        call_to_action = ""
        hashtags = ""
        
        for line in result.split('\n'):
            if line.startswith("ЗАГОЛОВОК:"):
                title = line.replace("ЗАГОЛОВОК:", "").strip()
            elif line.startswith("ТЕКСТ:"):
                body = line.replace("ТЕКСТ:", "").strip()
            elif line.startswith("ПРИЗЫВ:"):
                call_to_action = line.replace("ПРИЗЫВ:", "").strip()
            elif line.startswith("ХЭШТЕГИ:"):
                hashtags = line.replace("ХЭШТЕГИ:", "").strip()
        
        if not title:
            title = text[:50]
        if not call_to_action:
            call_to_action = "Подпишитесь на наш канал!"
        if not hashtags:
            hashtags = "#Minsk #MinskNews #MinskCity"
        
        # Создаём стильный пост с наложением текста
        if photo_bytes:
            await query.message.reply_text("🎨 Создаю визуальное оформление...")
            styled_photo = create_city_post(photo_bytes, title, body, call_to_action, hashtags)
            
            # Формируем финальный текст
            final_text = f"{title}\n\n{body}\n\n✨ {call_to_action}\n\n{hashtags}"
            
            # Сохраняем в pending
            context.chat_data["pending"]["text"] = final_text
            context.chat_data["pending"]["photo_bytes"] = styled_photo.getvalue()
            
            await query.message.reply_photo(
                photo=styled_photo,
                caption=f"{final_text}\n\n✅ Стильный пост создан!",
                parse_mode="HTML",
                reply_markup=get_preview_keyboard("photo")
            )
        else:
            await query.message.reply_text(
                f"✨ *Сгенерированный пост:*\n\n"
                f"📰 *{title}*\n\n"
                f"📝 {body}\n\n"
                f"✨ {call_to_action}\n\n"
                f"{hashtags}\n\n"
                f"ℹ️ Для создания визуального оформления отправьте фото.",
                parse_mode="Markdown",
                reply_markup=get_ai_result_keyboard("photo")
            )
            context.chat_data["pending"]["text"] = f"{title}\n\n{body}\n\n✨ {call_to_action}\n\n{hashtags}"
        
    except Exception as e:
        logger.error(f"Ошибка GPT: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def create_style_post_from_photo(update, context):
    query = update.callback_query
    await query.answer()
    
    pending = context.chat_data.get("pending", {})
    if pending.get("type") != "photo":
        await query.message.reply_text("❌ Сначала отправьте фото с подписью")
        return
    
    await create_style_post(update, context, pending.get("original"))

# ==================== ПУБЛИКАЦИЯ С ВЫБОРОМ КАНАЛА ====================
async def publish_with_channel_selection(update, context, media_type, has_buttons):
    query = update.callback_query
    await query.answer()
    
    context.user_data["pending_publish"] = {
        "media_type": media_type,
        "has_buttons": has_buttons
    }
    
    await query.message.reply_text(
        "📢 *Куда публикуем?*\n\nВыберите канал для публикации:",
        parse_mode="Markdown",
        reply_markup=get_channel_selection_keyboard(media_type, has_buttons)
    )

async def execute_publish(update, context, channel_key, media_type, has_buttons):
    query = update.callback_query
    await query.answer()
    
    channel = CHANNELS.get(channel_key)
    if not channel or not channel["chat_id"]:
        await query.message.reply_text("❌ Канал не настроен. Обратитесь к администратору.")
        return
    
    pending = context.chat_data.get("pending", {})
    
    if media_type == "photo":
        if pending.get("type") != "photo":
            await query.message.reply_text("❌ Нет фото для публикации")
            return
        success = await send_to_channel(
            context, 
            channel_id=channel["chat_id"],
            channel_link=channel["link"],
            file_id=pending["file_id"], 
            text=pending["text"], 
            has_buttons=has_buttons
        )
    elif media_type == "album":
        if pending.get("type") != "album":
            await query.message.reply_text("❌ Нет альбома для публикации")
            return
        album_photos = pending.get("album_photos_bytes", [])
        if not album_photos:
            await query.message.reply_text("❌ Не удалось загрузить фото")
            return
        success = await send_to_channel(
            context,
            channel_id=channel["chat_id"],
            channel_link=channel["link"],
            text=pending["text"],
            has_buttons=has_buttons,
            is_album=True,
            album_photos=album_photos
        )
    elif media_type == "text":
        if pending.get("type") != "text":
            await query.message.reply_text("❌ Нет текста для публикации")
            return
        success = await send_to_channel(
            context,
            channel_id=channel["chat_id"],
            channel_link=channel["link"],
            text=pending["text"],
            has_buttons=has_buttons,
            is_text=True
        )
    elif media_type == "video":
        if pending.get("type") != "video":
            await query.message.reply_text("❌ Нет видео для публикации")
            return
        success = await send_to_channel(
            context,
            channel_id=channel["chat_id"],
            channel_link=channel["link"],
            video_file_id=pending["file_id"],
            text=pending["text"],
            has_buttons=has_buttons,
            is_video=True
        )
    elif media_type == "designed":
        designed = context.chat_data.get("designed", {})
        if not designed:
            await query.message.reply_text("❌ Нет оформленного поста")
            return
        success = await send_to_channel(
            context,
            channel_id=channel["chat_id"],
            channel_link=channel["link"],
            photo_bytes=designed["photo_bytes"],
            text=designed["text"],
            has_buttons=has_buttons
        )
    elif media_type == "watermarked":
        watermarked = context.chat_data.get("watermarked", {})
        if not watermarked:
            await query.message.reply_text("❌ Нет поста с водяным знаком")
            return
        success = await send_to_channel(
            context,
            channel_id=channel["chat_id"],
            channel_link=channel["link"],
            photo_bytes=watermarked["photo_bytes"],
            text=watermarked["text"],
            has_buttons=has_buttons
        )
    else:
        await query.message.reply_text("❌ Неизвестный тип публикации")
        return
    
    if success:
        await query.message.reply_text(f"✅ Опубликовано в {channel['name']}" + (" (с кнопками)" if has_buttons else " (без кнопок)"))
        if media_type in ["photo", "album", "text", "video"]:
            context.chat_data.pop("pending", None)
        elif media_type == "designed":
            context.chat_data.pop("designed", None)
            context.chat_data.pop("pending", None)
        elif media_type == "watermarked":
            context.chat_data.pop("watermarked", None)
            context.chat_data.pop("pending", None)
    else:
        await query.message.reply_text(f"❌ Ошибка при публикации в {channel['name']}")
    
    try:
        await query.message.delete()
    except:
        pass

# ==================== ПУБЛИКАЦИЯ ДЛЯ РАЗНЫХ ТИПОВ ====================
async def publish_photo_with_buttons(update, context):
    await publish_with_channel_selection(update, context, "photo", True)

async def publish_photo_no_buttons(update, context):
    await publish_with_channel_selection(update, context, "photo", False)

async def publish_album_with_buttons(update, context):
    await publish_with_channel_selection(update, context, "album", True)

async def publish_album_no_buttons(update, context):
    await publish_with_channel_selection(update, context, "album", False)

async def publish_text_with_buttons(update, context):
    await publish_with_channel_selection(update, context, "text", True)

async def publish_text_no_buttons(update, context):
    await publish_with_channel_selection(update, context, "text", False)

async def publish_video_with_buttons(update, context):
    await publish_with_channel_selection(update, context, "video", True)

async def publish_video_no_buttons(update, context):
    await publish_with_channel_selection(update, context, "video", False)

async def publish_designed_with_buttons(update, context):
    await publish_with_channel_selection(update, context, "designed", True)

async def publish_designed_no_buttons(update, context):
    await publish_with_channel_selection(update, context, "designed", False)

async def publish_watermarked_with_buttons(update, context):
    await publish_with_channel_selection(update, context, "watermarked", True)

async def publish_watermarked_no_buttons(update, context):
    await publish_with_channel_selection(update, context, "watermarked", False)

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
    await query.message.reply_photo(photo=photo_io, caption=f"{pending['text']}\n\n💧 Пост с водяным знаком!", parse_mode="HTML", reply_markup=get_preview_keyboard("watermarked"))
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
    photo_io = add_watermark_only(designed["photo_bytes"])
    context.chat_data["watermarked"] = {"text": designed["text"], "photo_bytes": photo_io.getvalue(), "original": designed["original"]}
    await query.message.reply_photo(photo=photo_io, caption=f"{designed['text']}\n\n💧 Пост с водяным знаком на оформленном фото!", parse_mode="HTML", reply_markup=get_preview_keyboard("watermarked"))
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
    
    text = pending["text"]
    lines = text.split('\n')
    title = lines[0][:150] if lines else "Пост"
    
    await query.message.reply_text("🎨 Оформляю...")
    photo_io = process_photo(pending["photo_bytes"], title, add_watermark_flag=False)
    context.chat_data["designed"] = {"text": pending["text"], "photo_bytes": photo_io.getvalue(), "original": pending["photo_bytes"]}
    await query.message.reply_photo(photo=photo_io, caption=f"{pending['text']}\n\n✅ Пост оформлен!", parse_mode="HTML", reply_markup=get_preview_keyboard("designed"))
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
    await query.message.reply_photo(photo=photo_io, caption=f"{watermarked['text']}\n\n✅ Пост оформлен!", parse_mode="HTML", reply_markup=get_preview_keyboard("designed"))
    try: await query.message.delete()
    except: pass

# ==================== ОБРАБОТКА AI ====================
async def ai_process_with_custom_request(update, context, media_type, custom_request=None, use_chatgpt=False):
    query = update.callback_query
    await query.answer()
    
    client = chatgpt_client if use_chatgpt else deepseek_client
    model = "gpt-3.5-turbo" if use_chatgpt else "deepseek-chat"
    
    if not client:
        await query.message.reply_text(f"❌ API {'ChatGPT' if use_chatgpt else 'DeepSeek'} не настроен")
        return
    
    prompt = DEEPSEEK_PROMPT
    if custom_request:
        prompt = f"{DEEPSEEK_PROMPT}\n\nДополнительные требования пользователя: {custom_request}\n\nПеределай новость согласно этим требованиям."
    
    pending = context.chat_data.get("pending", {})
    text = pending.get("text", "")
    
    if not text:
        await query.message.reply_text("❌ Нет текста")
        return
    
    await query.message.reply_text(f"🤖 Обрабатываю через {'ChatGPT' if use_chatgpt else 'DeepSeek'}...")
    
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": text}],
            temperature=0.7,
            max_tokens=1000
        )
        
        result = response.choices[0].message.content
        
        title = ""
        body = ""
        
        if "ЗАГОЛОВОК:" in result.upper() and "ТЕКСТ:" in result.upper():
            title_match = re.search(r'(?:ЗАГОЛОВОК:|Заголовок:)\s*(.+?)(?=(?:ТЕКСТ:|$))', result, re.IGNORECASE | re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()
            
            body_match = re.search(r'(?:ТЕКСТ:|Текст:)\s*(.+?)$', result, re.IGNORECASE | re.DOTALL)
            if body_match:
                body = body_match.group(1).strip()
        else:
            lines = result.strip().split('\n')
            if len(lines) > 0 and len(lines[0]) < 100:
                title = lines[0].replace('Заголовок:', '').replace('ЗАГОЛОВОК:', '').strip()
                body = '\n'.join(lines[1:]).strip()
            else:
                body = result.strip()
        
        if not body:
            body = result.strip()
        
        if not title and body:
            title = body[:50] + "..."
        
        new_text = f"{title}\n\n{body}"
        
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
        logger.error(f"Ошибка AI: {e}")
        await query.message.reply_text(f"❌ Ошибка: {e}")

async def ai_process_photo_deepseek(update, context):
    await ai_process_with_custom_request(update, context, "photo", None, False)

async def ai_process_photo_chatgpt(update, context):
    await ai_process_with_custom_request(update, context, "photo", None, True)

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
            await ai_process_with_custom_request(fake_query, context, media_type, custom_request, False)
        except Exception as e:
            logger.error(f"Ошибка при получении исходного сообщения: {e}")
            await update.message.reply_text("❌ Не удалось обработать запрос. Попробуйте снова.")
    else:
        await update.message.reply_text("❌ Не удалось обработать запрос. Попробуйте снова.")

async def back_to_ai_result_callback(update, context, media_type):
    query = update.callback_query
    await query.answer()
    
    context.user_data["waiting_for_custom_request"] = None
    
    pending = context.chat_data.get("pending", {})
    text = pending.get("text", "")
    
    lines = text.split('\n\n', 1)
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

async def edit_designed_text_callback(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_edit_designed"] = True
    await query.message.reply_text("✏️ Отправьте новый текст для оформленного поста. /cancel для отмены.")

async def edit_album_text_callback(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["waiting_edit_album"] = True
    await query.message.reply_text("✏️ Отправьте новый текст для альбома. /cancel для отмены.")

async def handle_edit_text(update, context):
    if context.user_data.get("waiting_edit_album"):
        pending = context.chat_data.get("pending", {})
        if pending and pending.get("type") == "album":
            new_text = update.message.text
            pending["text"] = new_text
            context.chat_data["pending"] = pending
            await update.message.reply_text("✅ Текст альбома обновлён!", reply_markup=get_album_keyboard())
        context.user_data["waiting_edit_album"] = None
        return
    
    if context.user_data.get("waiting_edit_designed"):
        designed = context.chat_data.get("designed", {})
        if designed:
            new_text = update.message.text
            designed["text"] = new_text
            context.chat_data["designed"] = designed
            photo_bytes = designed.get("photo_bytes")
            if photo_bytes:
                await update.message.reply_photo(
                    photo=photo_bytes,
                    caption=f"{new_text}\n\n✅ Текст обновлён!",
                    parse_mode="HTML",
                    reply_markup=get_preview_keyboard("designed")
                )
        context.user_data["waiting_edit_designed"] = None
        return
    
    if context.user_data.get("waiting_edit"):
        pending = context.chat_data.get("pending", {})
        if pending:
            new_text = update.message.text
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
    designed = context.chat_data.get("designed", {})
    watermarked = context.chat_data.get("watermarked", {})
    
    default_channel_id = CHANNELS.get("news", {}).get("chat_id", "")
    
    if media_type == "photo":
        save_scheduled_post(pending["text"], pending["photo_bytes"], publish_time, has_buttons=True, channel_id=default_channel_id)
        context.chat_data.pop("pending", None)
    elif media_type == "designed":
        save_scheduled_post(designed["text"], designed["photo_bytes"], publish_time, has_buttons=True, is_designed=True, channel_id=default_channel_id)
        context.chat_data.pop("designed", None)
        context.chat_data.pop("pending", None)
    elif media_type == "watermarked":
        save_scheduled_post(watermarked["text"], watermarked["photo_bytes"], publish_time, has_buttons=True, has_watermark=True, channel_id=default_channel_id)
        context.chat_data.pop("watermarked", None)
        context.chat_data.pop("pending", None)
    elif media_type == "album":
        album_photos_bytes = pending.get("album_photos_bytes", [])
        if album_photos_bytes:
            save_scheduled_post(pending["text"], album_photos_bytes[0] if album_photos_bytes else None, publish_time, has_buttons=True, is_album=True, channel_id=default_channel_id)
        context.chat_data.pop("pending", None)
    elif media_type == "video":
        save_scheduled_post(pending["text"], None, publish_time, has_buttons=True, is_video=True, video_file_id=pending["file_id"], channel_id=default_channel_id)
        context.chat_data.pop("pending", None)
    else:
        save_scheduled_post(pending["text"], None, publish_time, has_buttons=True, is_text=True, channel_id=default_channel_id)
        context.chat_data.pop("pending", None)
    
    await query.message.reply_text(f"✅ Пост запланирован на {time_str}")
    try: await query.message.delete()
    except: pass

# ==================== НАЗАД ====================
async def back_to_preview(update, context, media_type):
    query = update.callback_query
    await query.answer()
    pending = context.chat_data.get("pending", {})
    designed = context.chat_data.get("designed", {})
    
    if media_type == "photo":
        await query.message.reply_photo(photo=pending["photo_bytes"], caption=pending["text"] or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("photo"))
    elif media_type == "designed":
        await query.message.reply_photo(photo=designed["photo_bytes"], caption=designed["text"] or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("designed"))
    elif media_type == "video":
        await query.message.reply_video(video=pending["file_id"], caption=pending["text"] or " ", parse_mode="HTML", reply_markup=get_preview_keyboard("video"))
    elif media_type == "text":
        await query.message.edit_text(text=f"📝 Текст:\n\n{pending['text']}\n\nВыберите действие:", parse_mode="HTML", reply_markup=get_preview_keyboard("text"))
    elif media_type == "album":
        first_photo = pending.get("album_photos", [None])[0]
        if first_photo:
            await query.message.reply_photo(
                photo=first_photo,
                caption=f"📸 *Альбом*\n\nТекст: {pending['text'][:200]}...\n\nВыберите действие:",
                parse_mode="Markdown",
                reply_markup=get_album_keyboard()
            )
    
    try: await query.message.delete()
    except: pass

async def back_to_menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["gpt_chat_mode"] = False
    await query.message.reply_text("🏠 Главное меню:", reply_markup=get_main_keyboard())

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
                channel_id = post.get("channel_id")
                if not channel_id:
                    continue
                
                channel_link = None
                for key, ch in CHANNELS.items():
                    if ch["chat_id"] == channel_id:
                        channel_link = ch["link"]
                        break
                
                if not channel_link:
                    channel_link = CHANNELS.get("news", {}).get("link", "")
                
                if post.get("is_video") and post.get("video_file_id"):
                    await send_to_channel(app, channel_id, channel_link, text=post["text"], has_buttons=post["has_buttons"], is_video=True, video_file_id=post["video_file_id"])
                elif post.get("is_text"):
                    await send_to_channel(app, channel_id, channel_link, text=post["text"], has_buttons=post["has_buttons"], is_text=True)
                elif post.get("is_album"):
                    if post.get("photo_bytes"):
                        await send_to_channel(app, channel_id, channel_link, photo_bytes=post["photo_bytes"], text=post["text"], has_buttons=post["has_buttons"])
                elif post.get("photo_bytes"):
                    await send_to_channel(app, channel_id, channel_link, photo_bytes=post["photo_bytes"], text=post["text"], has_buttons=post["has_buttons"])
                delete_scheduled_post(post["id"])
                logger.info("✅ Опубликован отложенный пост")
        except Exception as e:
            logger.error(f"Ошибка планировщика: {e}")
        await asyncio.sleep(60)

# ==================== ОСНОВНОЙ КОЛБЭК ====================
async def button_callback(update, context):
    query = update.callback_query
    data = query.data
    
    # Выбор канала
    if data.startswith("select_channel:"):
        parts = data.split(":")
        if len(parts) >= 4:
            channel_key = parts[1]
            original_callback = parts[2]
            has_buttons = parts[3] == "True"
            
            await execute_publish(update, context, channel_key, original_callback, has_buttons)
        return
    
    # GPT чат
    elif data == "start_gpt_chat":
        await start_gpt_chat(update, context)
    elif data == "clear_chat_history":
        await clear_chat_history_callback(update, context)
    elif data == "back_to_menu":
        await back_to_menu_callback(update, context)
    
    # Стильный пост
    elif data == "create_style_post":
        await create_style_post(update, context, None)
    elif data == "create_style_post_from_photo":
        await create_style_post_from_photo(update, context)
    
    # Публикация (показываем выбор канала)
    elif data == "publish_photo_with_buttons": 
        await publish_photo_with_buttons(update, context)
    elif data == "publish_photo_no_buttons": 
        await publish_photo_no_buttons(update, context)
    elif data == "publish_album_with_buttons": 
        await publish_album_with_buttons(update, context)
    elif data == "publish_album_no_buttons": 
        await publish_album_no_buttons(update, context)
    elif data == "publish_text_with_buttons": 
        await publish_text_with_buttons(update, context)
    elif data == "publish_text_no_buttons": 
        await publish_text_no_buttons(update, context)
    elif data == "publish_video_with_buttons": 
        await publish_video_with_buttons(update, context)
    elif data == "publish_video_no_buttons": 
        await publish_video_no_buttons(update, context)
    elif data == "publish_designed_with_buttons": 
        await publish_designed_with_buttons(update, context)
    elif data == "publish_designed_no_buttons": 
        await publish_designed_no_buttons(update, context)
    elif data == "publish_watermarked_with_buttons": 
        await publish_watermarked_with_buttons(update, context)
    elif data == "publish_watermarked_no_buttons": 
        await publish_watermarked_no_buttons(update, context)
    
    # Оформление
    elif data == "design_post": await design_post_callback(update, context)
    elif data == "design_from_watermark": await design_from_watermark_callback(update, context)
    elif data == "add_watermark": await add_watermark_callback(update, context)
    elif data == "add_watermark_to_designed": await add_watermark_to_designed_callback(update, context)
    
    # AI обработка
    elif data == "ai_process_photo": 
        await ai_process_photo_deepseek(update, context)
    elif data == "ai_process_chatgpt":
        await ai_process_photo_chatgpt(update, context)
    elif data == "ai_process_video": 
        await ai_process_with_custom_request(update, context, "video", None, False)
    elif data == "ai_process_text": 
        await ai_process_with_custom_request(update, context, "text", None, False)
    elif data == "ai_process_album":
        await ai_process_with_custom_request(update, context, "album", None, False)
    
    # AI кастомные запросы
    elif data == "ai_custom_request_photo":
        await ai_custom_request_callback(update, context, "photo")
    elif data == "ai_custom_request_video":
        await ai_custom_request_callback(update, context, "video")
    elif data == "ai_custom_request_text":
        await ai_custom_request_callback(update, context, "text")
    elif data == "ai_custom_request_album":
        await ai_custom_request_callback(update, context, "album")
    
    # Возврат к результатам AI
    elif data == "back_to_ai_result_photo":
        await back_to_ai_result_callback(update, context, "photo")
    elif data == "back_to_ai_result_video":
        await back_to_ai_result_callback(update, context, "video")
    elif data == "back_to_ai_result_text":
        await back_to_ai_result_callback(update, context, "text")
    elif data == "back_to_ai_result_album":
        await back_to_ai_result_callback(update, context, "album")
    
    # Редактирование
    elif data == "edit_text": await edit_text_callback(update, context)
    elif data == "edit_designed_text": await edit_designed_text_callback(update, context)
    elif data == "edit_album_text": await edit_album_text_callback(update, context)
    
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
    elif data == "schedule_album_menu":
        await query.answer()
        await query.message.edit_reply_markup(reply_markup=get_schedule_keyboard("schedule_album"))
    elif data == "schedule_designed_menu":
        await query.answer()
        await query.message.edit_reply_markup(reply_markup=get_schedule_keyboard("schedule_designed"))
    elif data == "schedule_watermarked_menu":
        await query.answer()
        await query.message.edit_reply_markup(reply_markup=get_schedule_keyboard("schedule_watermarked"))
    
    elif data.startswith("schedule_photo:"): await schedule_post(update, context, "photo")
    elif data.startswith("schedule_text:"): await schedule_post(update, context, "text")
    elif data.startswith("schedule_video:"): await schedule_post(update, context, "video")
    elif data.startswith("schedule_album:"): await schedule_post(update, context, "album")
    elif data.startswith("schedule_designed:"): await schedule_post(update, context, "designed")
    elif data.startswith("schedule_watermarked:"): await schedule_post(update, context, "watermarked")
    
    # Назад
    elif data == "back_to_photo_preview": await back_to_preview(update, context, "photo")
    elif data == "back_to_designed_preview": await back_to_preview(update, context, "designed")
    elif data == "back_to_video_preview": await back_to_preview(update, context, "video")
    elif data == "back_to_text_preview": await back_to_preview(update, context, "text")
    elif data == "back_to_album_preview": await back_to_preview(update, context, "album")
    elif data == "back_to_original": await back_to_original_callback(update, context)
    
    elif data == "send_media_info":
        await query.answer()
        await query.message.reply_text("📸 Отправьте текст, фото, видео или альбом фото с подписью")

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("✅ Отменено")

# ==================== ЗАПУСК ====================
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "bot": "MINSK NEWS Bot with ChatGPT"}

@app.get("/health")
async def health():
    return {"status": "alive"}

async def run_bot():
    init_db()
    
    # Проверка настроек каналов
    logger.info("📢 Настройка каналов:")
    for key, channel in CHANNELS.items():
        if channel["chat_id"]:
            logger.info(f"  ✅ {channel['name']}: {channel['chat_id']}")
        else:
            logger.info(f"  ⚠️ {channel['name']}: не настроен")
    
    logger.info(f"🤖 DeepSeek API: {'✅' if deepseek_client else '❌'}")
    logger.info(f"🤖 ChatGPT API: {'✅' if chatgpt_client else '❌'}")
    
    # Создаём приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_text))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_request_text))
    
    await application.initialize()
    await application.start()
    
    # Запускаем планировщик
    asyncio.create_task(check_scheduled_posts(application))
    
    # Запускаем polling с повторными попытками при конфликте
    while True:
        try:
            logger.info("🚀 Запуск polling...")
            await application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                timeout=30,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30
            )
            logger.info("✅ Бот успешно запущен!")
            break
        except Conflict as e:
            logger.error(f"❌ Конфликт: {e}")
            logger.info("🔄 Повторная попытка через 5 секунд...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            logger.info("🔄 Повторная попытка через 10 секунд...")
            await asyncio.sleep(10)
    
    # Держим бота запущенным
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    import threading
    import uvicorn
    
    # Запускаем FastAPI в отдельном потоке
    port = int(os.getenv("PORT", 10000))
    server_thread = threading.Thread(target=lambda: uvicorn.run(app, host="0.0.0.0", port=port))
    server_thread.start()
    
    # Запускаем бота
    asyncio.run(run_bot())
