# -*- coding: utf-8 -*-

import os
import io
import threading
import logging
import re
import asyncio

from flask import Flask
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import TimedOut
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN")

# DeepSeek клиент
deepseek_client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
) if DEEPSEEK_API_KEY else None

web_app = Flask(__name__)

@web_app.get("/")
def health():
    return "OK", 200

def run_web():
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port)

W, H = 1080, 1920

PURPLE = (111, 55, 245)
BLACK = (20, 22, 32)
WHITE = (255, 255, 255)
LIGHT_BG = (255, 255, 255)
DARK_BG = (7, 7, 10)
LIGHT_TEXT = (20, 22, 32)
DARK_TEXT = (255, 255, 255)

FONT_BOLD = "Montserrat-Black.ttf"
FONT_REGULAR = "Montserrat-Bold.ttf"

# Промпт для DeepSeek
DEEPSEEK_PROMPT = """Ты редактор новостного сайта. У тебя строгий новостной формат. Без обращений на "вы", "ты". Только новостной формат.

Переделай новость в формат на 600-650 символов. Убери всю воду, сделай интересный заголовок. Без смайликов. Сохраняй главные факты.

Текст должен быть разбит на логические абзацы (2-4 предложения). Между абзацами пустая строка.

Верни строго в формате:
ЗАГОЛОВОК: (заголовок новости)
ТЕКСТ: (текст новости с абзацами)"""


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()


def crop_cover(img, size):
    target_w, target_h = size
    img_w, img_h = img.size
    scale = max(target_w / img_w, target_h / img_h)

    img = img.resize((int(img_w * scale), int(img_h * scale)), Image.LANCZOS)

    left = (img.width - target_w) // 2
    top = (img.height - target_h) // 2

    return img.crop((left, top, left + target_w, top + target_h))


def wrap_text(draw, text, fnt, max_width):
    words = text.split()
    lines = []
    line = ""

    for word in words:
        test = line + " " + word if line else word
        box = draw.textbbox((0, 0), test, font=fnt)

        if box[2] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word

    if line:
        lines.append(line)

    return lines


def fit_text(draw, text, font_path, max_width, max_height, start_size, min_size, gap):
    size = start_size

    while size >= min_size:
        fnt = font(font_path, size)
        lines = wrap_text(draw, text, fnt, max_width)
        total_h = len(lines) * (size + gap)

        if total_h <= max_height:
            return fnt, lines

        size -= 2

    fnt = font(font_path, min_size)
    lines = wrap_text(draw, text, fnt, max_width)
    return fnt, lines


def draw_l_shape_corner(draw, x, y, width, height, thickness, color):
    """Рисует Г-образную плашку в левом верхнем углу"""
    draw.rectangle((x, y, x + thickness, y + height), fill=color)
    draw.rectangle((x, y, x + width, y + thickness), fill=color)


def create_story(photo_bytes, title, body, dark_mode=False):
    if len(body) > 900:
        raise ValueError("Текст слишком длинный, сделайте его короче (максимум 900 символов)")

    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

    bg_color = DARK_BG if dark_mode else LIGHT_BG
    text_color = DARK_TEXT if dark_mode else LIGHT_TEXT
    
    canvas = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(canvas)

    top_line_height = 15
    draw.rectangle((0, 0, W, top_line_height), fill=PURPLE)

    photo_h = int(H * 0.4)
    photo = crop_cover(img, (W, photo_h))
    
    if dark_mode:
        photo = ImageEnhance.Brightness(photo).enhance(0.85)
    else:
        photo = ImageEnhance.Brightness(photo).enhance(0.92)
    
    canvas.paste(photo, (0, top_line_height))

    corner_x = 30
    corner_y = top_line_height + 30
    corner_width = 120
    corner_height = 80
    corner_thickness = 12
    draw_l_shape_corner(draw, corner_x, corner_y, corner_width, corner_height, corner_thickness, PURPLE)

    bottom_line_height = 15
    divider_y = photo_h + top_line_height
    draw.rectangle((0, divider_y, W, divider_y + bottom_line_height), fill=PURPLE)

    text_bg_start = divider_y + bottom_line_height
    draw.rectangle((0, text_bg_start, W, H), fill=bg_color)

    title = title.strip()
    title_font, title_lines = fit_text(
        draw, title, FONT_BOLD, max_width=900, max_height=300,
        start_size=58, min_size=38, gap=8,
    )

    y = text_bg_start + 80
    for line in title_lines[:5]:
        draw.text((80, y), line, font=title_font, fill=text_color)
        y += title_font.size + 10

    y += 18
    dot_radius = 12
    dot_spacing = 18
    start_x = 80
    
    for i in range(3):
        x = start_x + i * (dot_radius * 2 + dot_spacing)
        y_dot = y + 8
        draw.ellipse((x - dot_radius, y_dot - dot_radius, x + dot_radius, y_dot + dot_radius), fill=PURPLE)
    
    y += 60

    body = body.strip()
    available_height = H - y - 150
    body_font, body_lines = fit_text(
        draw, body, FONT_REGULAR, max_width=900, max_height=available_height,
        start_size=33, min_size=16, gap=8,
    )

    for line in body_lines:
        draw.text((80, y), line, font=body_font, fill=text_color)
        y += body_font.size + 8

    ellipse_size = 50
    ellipse_offset = 50
    draw.ellipse(
        (ellipse_offset, H - ellipse_offset - ellipse_size,
         ellipse_offset + ellipse_size, H - ellipse_offset),
        fill=PURPLE
    )
    
    ellipse_font = font(FONT_BOLD, 18)
    text_ellipse = "f"
    bbox = draw.textbbox((0, 0), text_ellipse, font=ellipse_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    draw.text(
        (ellipse_offset + (ellipse_size - text_w) // 2,
         H - ellipse_offset - ellipse_size + (ellipse_size - text_h) // 2 - 2),
        text_ellipse,
        font=ellipse_font,
        fill=WHITE
    )
    
    text_font = font(FONT_BOLD, 28)
    draw.text(
        (ellipse_offset + ellipse_size + 15, H - ellipse_offset - 35),
        "fider.by",
        font=text_font,
        fill=PURPLE
    )

    output = io.BytesIO()
    canvas.save(output, format="PNG", quality=95)
    output.seek(0)
    return output


def remove_emojis(text: str) -> str:
    """Удаляет эмодзи из текста"""
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


# ==================== ОБРАБОТЧИКИ ИИ ====================
async def ai_process_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текста через DeepSeek AI"""
    query = update.callback_query
    await query.answer()
    
    if not deepseek_client:
        await query.message.reply_text("❌ API DeepSeek не настроен. Добавьте DEEPSEEK_API_KEY в переменные окружения.")
        return
    
    body = context.user_data.get("temp_body", "")
    if not body:
        await query.message.reply_text("❌ Нет текста для обработки")
        return
    
    await query.message.reply_text("🤖 Обрабатываю текст через DeepSeek AI...")
    
    try:
        response = await deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": DEEPSEEK_PROMPT},
                {"role": "user", "content": body}
            ],
            temperature=0.7,
            max_tokens=1000
        )
        
        result = response.choices[0].message.content
        
        # Парсим ответ
        title = ""
        new_body = ""
        
        if "ЗАГОЛОВОК:" in result.upper() and "ТЕКСТ:" in result.upper():
            title_match = re.search(r'(?:ЗАГОЛОВОК:|Заголовок:)\s*(.+?)(?=(?:ТЕКСТ:|$))', result, re.IGNORECASE | re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()
            
            body_match = re.search(r'(?:ТЕКСТ:|Текст:)\s*(.+?)$', result, re.IGNORECASE | re.DOTALL)
            if body_match:
                new_body = body_match.group(1).strip()
        else:
            lines = result.strip().split('\n')
            if len(lines) > 0 and len(lines[0]) < 100:
                title = lines[0].replace('Заголовок:', '').replace('ЗАГОЛОВОК:', '').strip()
                new_body = '\n'.join(lines[1:]).strip()
            else:
                new_body = result.strip()
        
        if not new_body:
            new_body = result.strip()
        
        if not title and new_body:
            title = new_body[:50] + "..."
        
        context.user_data["temp_title"] = title
        context.user_data["temp_body"] = new_body
        
        char_count = len(new_body)
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Использовать", callback_data="use_ai_result")],
            [InlineKeyboardButton("🔄 Переделать", callback_data="ai_reprocess")],
            [InlineKeyboardButton("✏️ Редактировать вручную", callback_data="edit_manually")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_input")]
        ])
        
        await query.message.reply_text(
            f"✅ *Текст обработан через ИИ!*\n\n"
            f"📰 *Заголовок:* {title}\n\n"
            f"📝 *Текст:*\n{new_body}\n\n"
            f"📊 *Длина текста:* {char_count} символов\n\n"
            f"Что делаем дальше?",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        
        try:
            await query.message.delete()
        except:
            pass
        
    except Exception as e:
        logger.error(f"Ошибка DeepSeek: {e}")
        await query.message.reply_text(f"❌ Ошибка при обработке: {e}")


async def ai_reprocess_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повторная обработка с новым запросом"""
    query = update.callback_query
    await query.answer()
    
    context.user_data["waiting_custom_request"] = True
    
    await query.message.reply_text(
        "📝 *Напишите ваш запрос для переделки текста*\n\n"
        "Примеры:\n"
        "• Сделай заголовок броским\n"
        "• Сократи до 400 символов\n"
        "• Сделай более официальным\n"
        "• Добавь больше фактов\n\n"
        "Или отправьте /cancel для отмены.",
        parse_mode="Markdown"
    )
    
    try:
        await query.message.delete()
    except:
        pass


async def handle_custom_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кастомного запроса к ИИ"""
    if not context.user_data.get("waiting_custom_request"):
        return
    
    custom_request = update.message.text
    context.user_data["waiting_custom_request"] = False
    
    body = context.user_data.get("temp_body", "")
    if not body:
        await update.message.reply_text("❌ Нет текста для обработки")
        return
    
    prompt = f"{DEEPSEEK_PROMPT}\n\nДополнительные требования пользователя: {custom_request}\n\nПеределай новость согласно этим требованиям."
    
    await update.message.reply_text("🤖 Обрабатываю с новым запросом...")
    
    try:
        response = await deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": body}
            ],
            temperature=0.7,
            max_tokens=1000
        )
        
        result = response.choices[0].message.content
        
        title = ""
        new_body = ""
        
        if "ЗАГОЛОВОК:" in result.upper() and "ТЕКСТ:" in result.upper():
            title_match = re.search(r'(?:ЗАГОЛОВОК:|Заголовок:)\s*(.+?)(?=(?:ТЕКСТ:|$))', result, re.IGNORECASE | re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()
            
            body_match = re.search(r'(?:ТЕКСТ:|Текст:)\s*(.+?)$', result, re.IGNORECASE | re.DOTALL)
            if body_match:
                new_body = body_match.group(1).strip()
        else:
            lines = result.strip().split('\n')
            if len(lines) > 0 and len(lines[0]) < 100:
                title = lines[0].strip()
                new_body = '\n'.join(lines[1:]).strip()
            else:
                new_body = result.strip()
        
        if not new_body:
            new_body = result.strip()
        
        if not title and new_body:
            title = new_body[:50] + "..."
        
        context.user_data["temp_title"] = title
        context.user_data["temp_body"] = new_body
        
        char_count = len(new_body)
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Использовать", callback_data="use_ai_result")],
            [InlineKeyboardButton("🔄 Переделать", callback_data="ai_reprocess")],
            [InlineKeyboardButton("✏️ Редактировать вручную", callback_data="edit_manually")],
            [InlineKeyboardButton("◀️ Назад", callback_data="back_to_input")]
        ])
        
        await update.message.reply_text(
            f"✅ *Текст обработан!*\n\n"
            f"📰 *Заголовок:* {title}\n\n"
            f"📝 *Текст:*\n{new_body}\n\n"
            f"📊 *Длина текста:* {char_count} символов\n\n"
            f"Что делаем дальше?",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def use_ai_result_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Использовать результат обработки ИИ"""
    query = update.callback_query
    await query.answer()
    
    title = context.user_data.get("temp_title", "")
    body = context.user_data.get("temp_body", "")
    
    if not title or not body:
        await query.message.reply_text("❌ Нет данных. Начните заново.")
        return
    
    context.user_data["title"] = title
    context.user_data["body"] = body
    context.user_data["state"] = "ready_to_create"
    
    dark_mode = context.user_data.get("dark_mode", False)
    theme_name = "тёмной" if dark_mode else "светлой"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Создать сторис", callback_data="create_story_final")],
        [InlineKeyboardButton("✏️ Редактировать заголовок", callback_data="edit_title_manual")],
        [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_body_manual")],
        [InlineKeyboardButton("🤖 Обработать снова", callback_data="ai_process")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_input")]
    ])
    
    await query.message.reply_text(
        f"✅ *Готово!*\n\n"
        f"📰 *Заголовок:* {title}\n\n"
        f"📝 *Текст:*\n{body}\n\n"
        f"🎨 *Тема:* {theme_name}\n\n"
        f"Нажмите 'Создать сторис' для генерации изображения.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    
    try:
        await query.message.delete()
    except:
        pass


async def edit_manually_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактирование вручную"""
    query = update.callback_query
    await query.answer()
    
    context.user_data["waiting_edit_body"] = True
    await query.message.reply_text(
        "✏️ Отправьте новый текст (макс. 900 символов)\n\n"
        "Или /cancel для отмены."
    )
    
    try:
        await query.message.delete()
    except:
        pass


async def edit_title_manual_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактирование заголовка вручную"""
    query = update.callback_query
    await query.answer()
    
    context.user_data["waiting_edit_title"] = True
    await query.message.reply_text(
        "✏️ Отправьте новый заголовок\n\n"
        "Или /cancel для отмены."
    )
    
    try:
        await query.message.delete()
    except:
        pass


async def edit_body_manual_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Редактирование текста вручную"""
    query = update.callback_query
    await query.answer()
    
    context.user_data["waiting_edit_body"] = True
    await query.message.reply_text(
        "✏️ Отправьте новый текст (макс. 900 символов)\n\n"
        "Или /cancel для отмены."
    )
    
    try:
        await query.message.delete()
    except:
        pass


async def handle_manual_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ручного редактирования"""
    if context.user_data.get("waiting_edit_title"):
        new_title = update.message.text.strip()
        if new_title and len(new_title) >= 3:
            context.user_data["title"] = new_title
            context.user_data["waiting_edit_title"] = False
            await update.message.reply_text(f"✅ Заголовок обновлён:\n\n{new_title}")
            
            # Показываем текущее состояние
            title = context.user_data.get("title", "")
            body = context.user_data.get("body", "")
            dark_mode = context.user_data.get("dark_mode", False)
            theme_name = "тёмной" if dark_mode else "светлой"
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎨 Создать сторис", callback_data="create_story_final")],
                [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_body_manual")],
                [InlineKeyboardButton("🤖 Обработать ИИ", callback_data="ai_process")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_input")]
            ])
            
            await update.message.reply_text(
                f"📰 *Заголовок:* {title}\n\n"
                f"📝 *Текст:*\n{body}\n\n"
                f"🎨 *Тема:* {theme_name}\n\n"
                f"Что дальше?",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text("❌ Заголовок слишком короткий (минимум 3 символа)")
        return
    
    if context.user_data.get("waiting_edit_body"):
        new_body = update.message.text.strip()
        if new_body:
            if len(new_body) > 900:
                await update.message.reply_text("❌ Текст слишком длинный (макс. 900 символов)")
                return
            context.user_data["body"] = new_body
            context.user_data["waiting_edit_body"] = False
            await update.message.reply_text(f"✅ Текст обновлён!\n\n{new_body[:200]}...")
            
            title = context.user_data.get("title", "")
            dark_mode = context.user_data.get("dark_mode", False)
            theme_name = "тёмной" if dark_mode else "светлой"
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎨 Создать сторис", callback_data="create_story_final")],
                [InlineKeyboardButton("✏️ Редактировать заголовок", callback_data="edit_title_manual")],
                [InlineKeyboardButton("🤖 Обработать ИИ", callback_data="ai_process")],
                [InlineKeyboardButton("◀️ Назад", callback_data="back_to_input")]
            ])
            
            await update.message.reply_text(
                f"📰 *Заголовок:* {title}\n\n"
                f"📝 *Текст:*\n{new_body}\n\n"
                f"🎨 *Тема:* {theme_name}\n\n"
                f"Что дальше?",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text("❌ Текст не может быть пустым")
        return


async def create_story_final_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание сторис из подготовленных данных"""
    query = update.callback_query
    await query.answer()
    
    photo = context.user_data.get("photo")
    title = context.user_data.get("title")
    body = context.user_data.get("body")
    dark_mode = context.user_data.get("dark_mode", False)
    
    if not photo or not title or not body:
        await query.message.reply_text("❌ Не хватает данных. Нажмите /start.")
        return
    
    theme_name = "тёмной" if dark_mode else "светлой"
    msg = await query.message.reply_text(f"🎨 Создаю сторис в {theme_name} теме...")
    
    try:
        result = create_story(photo, title, body, dark_mode)
        result.name = "fider_story.png"
        
        await query.message.reply_photo(
            photo=result,
            caption=f"✨ Готово в {theme_name} теме\n\nfider.by"
        )
        
        context.user_data.clear()
        context.user_data["state"] = "waiting_photo"
        context.user_data["dark_mode"] = dark_mode
        
    except ValueError as e:
        await query.message.reply_text(f"❌ {str(e)}\n\nОтправьте новый текст.")
        return
    except Exception as e:
        logger.exception(e)
        await query.message.reply_text(f"❌ Ошибка: {str(e)}")
        context.user_data.clear()
        context.user_data["state"] = "waiting_photo"
    
    try:
        await msg.delete()
    except:
        pass


async def back_to_input_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат к вводу текста"""
    query = update.callback_query
    await query.answer()
    
    context.user_data["state"] = "waiting_body"
    await query.message.reply_text(
        "✏️ Отправьте основной текст (макс. 900 символов)\n\n"
        "Или нажмите /cancel для отмены."
    )
    
    try:
        await query.message.delete()
    except:
        pass


async def continue_without_ai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Продолжить без обработки ИИ"""
    query = update.callback_query
    await query.answer()
    
    body = context.user_data.get("original_body", "")
    
    context.user_data["body"] = body
    context.user_data["state"] = "ready_to_create"
    
    dark_mode = context.user_data.get("dark_mode", False)
    theme_name = "тёмной" if dark_mode else "светлой"
    title = context.user_data.get("title", "")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Создать сторис", callback_data="create_story_final")],
        [InlineKeyboardButton("✏️ Редактировать заголовок", callback_data="edit_title_manual")],
        [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_body_manual")],
        [InlineKeyboardButton("🤖 Обработать ИИ", callback_data="ai_process")]
    ])
    
    await query.message.reply_text(
        f"📰 *Заголовок:* {title}\n\n"
        f"📝 *Текст:*\n{body}\n\n"
        f"🎨 *Тема:* {theme_name}\n\n"
        f"Текст {'можно сократить' if len(body) > 650 else 'хорошей длины'}.\n\n"
        f"Что делаем?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    
    try:
        await query.message.delete()
    except:
        pass


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена текущего действия"""
    context.user_data.clear()
    await update.message.reply_text("✅ Отменено. Нажмите /start для начала.")


# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "waiting_photo"
    context.user_data["dark_mode"] = False

    await update.message.reply_text(
        "🟣 Бот сторис Fider.by\n\n"
        "✨ ДОСТУПНЫЕ ТЕМЫ:\n"
        "☀️ /light — светлая тема (по умолчанию)\n"
        "🌙 /dark — тёмная тема\n\n"
        "КАК СОЗДАТЬ СТОРИС:\n"
        "1. Отправь фото\n"
        "2. Отправь заголовок\n"
        "3. Отправь основной текст (максимум 900 символов)\n\n"
        "🤖 ИЛИ:\n"
        "После отправки текста нажми кнопку 'Обработать через ИИ'\n"
        "ИИ сократит текст до 600-650 символов и расставит абзацы\n\n"
        "Я соберу готовую сторис 9:16."
    )


async def dark_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dark_mode"] = True
    await update.message.reply_text("🌙 Включена тёмная тема\n\nТеперь отправляй фото.")


async def light_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dark_mode"] = False
    await update.message.reply_text("☀️ Включена светлая тема\n\nТеперь отправляй фото.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_photo":
        await update.message.reply_text("Нажми /start и отправь фото заново.")
        return

    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()

        context.user_data["photo"] = bytes(photo_bytes)
        context.user_data["state"] = "waiting_title"

        await update.message.reply_text("✅ Фото получил. Теперь отправь заголовок.")
    except TimedOut:
        await update.message.reply_text("⏱️ Превышено время ожидания. Попробуй ещё раз.")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке фото.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_photo":
        await update.message.reply_text("Нажми /start и отправь фото заново.")
        return

    doc = update.message.document

    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Нужен файл изображения.")
        return

    try:
        file = await context.bot.get_file(doc.file_id)
        photo_bytes = await file.download_as_bytearray()

        context.user_data["photo"] = bytes(photo_bytes)
        context.user_data["state"] = "waiting_title"

        await update.message.reply_text("✅ Фото получил. Теперь отправь заголовок.")
    except TimedOut:
        await update.message.reply_text("⏱️ Превышено время ожидания. Попробуй ещё раз.")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке файла.")


async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка заголовка"""
    if context.user_data.get("state") != "waiting_title":
        return
    
    title = update.message.text.strip()

    if len(title) < 3:
        await update.message.reply_text("❌ Заголовок слишком короткий (минимум 3 символа).")
        return

    context.user_data["title"] = title
    context.user_data["state"] = "waiting_body"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Продолжить без обработки", callback_data="continue_without_ai")],
        [InlineKeyboardButton("🤖 Обработать через ИИ", callback_data="ai_process")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_title")]
    ])
    
    await update.message.reply_text(
        f"✅ Заголовок сохранён:\n\n{title}\n\n"
        f"📝 Теперь отправь основной текст (макс. 900 символов)\n\n"
        f"Или выбери действие:",
        reply_markup=keyboard
    )


async def back_to_title_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться к вводу заголовка"""
    query = update.callback_query
    await query.answer()
    
    context.user_data["state"] = "waiting_title"
    await query.message.reply_text("✏️ Отправьте новый заголовок.")
    
    try:
        await query.message.delete()
    except:
        pass


async def handle_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка основного текста"""
    if context.user_data.get("state") != "waiting_body":
        return
    
    body = update.message.text.strip()
    
    if len(body) < 20:
        await update.message.reply_text("❌ Текст слишком короткий (минимум 20 символов).")
        return
    
    if len(body) > 900:
        await update.message.reply_text("❌ Текст слишком длинный (макс. 900 символов).")
        return
    
    context.user_data["original_body"] = body
    context.user_data["temp_body"] = body
    context.user_data["state"] = "ready_to_create"
    
    dark_mode = context.user_data.get("dark_mode", False)
    theme_name = "тёмной" if dark_mode else "светлой"
    title = context.user_data.get("title", "")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Создать сторис", callback_data="create_story_final")],
        [InlineKeyboardButton("🤖 Обработать через ИИ", callback_data="ai_process")],
        [InlineKeyboardButton("✏️ Редактировать заголовок", callback_data="edit_title_manual")],
        [InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_body_manual")]
    ])
    
    await update.message.reply_text(
        f"✅ *Всё готово!*\n\n"
        f"📰 *Заголовок:* {title}\n\n"
        f"📝 *Текст:*\n{body}\n\n"
        f"🎨 *Тема:* {theme_name}\n\n"
        f"Текст {'можно сократить через ИИ' if len(body) > 650 else 'хорошей длины'}.\n\n"
        f"Что делаем?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


async def post_init(app):
    await app.bot.delete_webhook(drop_pending_updates=True)


def main():
    threading.Thread(target=run_web, daemon=True).start()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dark", dark_mode))
    app.add_handler(CommandHandler("light", light_mode))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # Сообщения
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_body))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manual_edit))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_request))
    
    # Callback кнопки
    app.add_handler(CallbackQueryHandler(continue_without_ai_callback, pattern="continue_without_ai"))
    app.add_handler(CallbackQueryHandler(back_to_title_callback, pattern="back_to_title"))
    app.add_handler(CallbackQueryHandler(back_to_input_callback, pattern="back_to_input"))
    app.add_handler(CallbackQueryHandler(ai_process_callback, pattern="ai_process"))
    app.add_handler(CallbackQueryHandler(ai_reprocess_callback, pattern="ai_reprocess"))
    app.add_handler(CallbackQueryHandler(use_ai_result_callback, pattern="use_ai_result"))
    app.add_handler(CallbackQueryHandler(edit_manually_callback, pattern="edit_manually"))
    app.add_handler(CallbackQueryHandler(edit_title_manual_callback, pattern="edit_title_manual"))
    app.add_handler(CallbackQueryHandler(edit_body_manual_callback, pattern="edit_body_manual"))
    app.add_handler(CallbackQueryHandler(create_story_final_callback, pattern="create_story_final"))

    print("✅ FIDER STORY BOT STARTED")
    print("🌓 Светлая и тёмная тема")
    if deepseek_client:
        print("🤖 DeepSeek AI подключен и готов к работе")
    else:
        print("⚠️ DeepSeek AI не настроен (добавьте DEEPSEEK_API_KEY)")

    app.run_polling(
        drop_pending_updates=True,
        poll_interval=2.0,
        timeout=30,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
