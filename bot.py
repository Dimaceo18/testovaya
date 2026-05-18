# -*- coding: utf-8 -*-

import os
import io
import re
import json
import threading
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TimedOut

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN")

web_app = Flask(__name__)

# Файл для хранения статистики
STATS_FILE = Path("user_stats.json")

def load_stats():
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_stats(stats):
    with open(STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

@web_app.get("/")
def health():
    return "OK", 200


def run_web():
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port)


W, H = 1080, 1920

PURPLE = (111, 55, 245)
WHITE = (255, 255, 255)
BLACK = (7, 7, 10)
LIGHT_TEXT = (20, 22, 32)
DARK_TEXT = (255, 255, 255)

FONT_BOLD = "Montserrat-Black.ttf"
FONT_REGULAR = "Montserrat-Bold.ttf"


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


def extract_title_and_body(text):
    """Извлекает заголовок (жирный текст/первая строка) и основной текст из поста"""
    lines = text.strip().split('\n')
    
    # Фильтруем пустые строки
    lines = [l.strip() for l in lines if l.strip()]
    
    if not lines:
        return None, None
    
    # Заголовок - первая строка
    title = lines[0]
    
    # Убираем возможные маркеры жирности (*текст* или **текст**)
    title = re.sub(r'\*{1,2}(.*?)\*{1,2}', r'\1', title)
    
    # Остальное - основной текст
    body = '\n'.join(lines[1:]) if len(lines) > 1 else ""
    
    # Очищаем от лишних символов, но сохраняем пунктуацию
    title = re.sub(r'[^\w\s\.\,\!\?\-\(\)]', '', title)
    body = re.sub(r'[^\w\s\.\,\!\?\-\(\)\'\"]', '', body)
    
    # Ограничиваем длину
    if len(title) > 200:
        title = title[:197] + "..."
    
    if len(body) > 900:
        body = body[:897] + "..."
        raise ValueError("Текст слишком длинный, сделайте его короче (максимум 900 символов)")
    
    return title, body


def create_story(photo_bytes, title, body, dark_mode=False):
    if len(body) > 900:
        raise ValueError("Текст слишком длинный, сделайте его короче (максимум 900 символов)")

    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

    # Выбираем цвета в зависимости от темы
    bg_color = BLACK if dark_mode else WHITE
    text_color = DARK_TEXT if dark_mode else LIGHT_TEXT
    
    canvas = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(canvas)

    # ========== ВЕРХНЯЯ ПОЛОСА (15px) ==========
    top_line_height = 15
    draw.rectangle((0, 0, W, top_line_height), fill=PURPLE)

    # ========== ФОТО ==========
    photo_h = int(H * 0.4)
    photo = crop_cover(img, (W, photo_h))
    
    if dark_mode:
        photo = ImageEnhance.Brightness(photo).enhance(0.85)
    else:
        photo = ImageEnhance.Brightness(photo).enhance(0.92)
    
    canvas.paste(photo, (0, top_line_height))

    # ========== Г-ОБРАЗНАЯ ПЛАШКА НА ФОТО ==========
    corner_x = 30
    corner_y = top_line_height + 30
    corner_width = 120
    corner_height = 80
    corner_thickness = 12
    draw_l_shape_corner(draw, corner_x, corner_y, corner_width, corner_height, corner_thickness, PURPLE)

    # ========== ПОЛОСА ПОСЛЕ ФОТО (15px) ==========
    bottom_line_height = 15
    divider_y = photo_h + top_line_height
    draw.rectangle((0, divider_y, W, divider_y + bottom_line_height), fill=PURPLE)

    # ========== ЗОНА С ТЕКСТОМ ==========
    text_bg_start = divider_y + bottom_line_height
    draw.rectangle((0, text_bg_start, W, H), fill=bg_color)

    # ========== ЗАГОЛОВОК ==========
    title_font, title_lines = fit_text(
        draw, title, FONT_BOLD, max_width=900, max_height=300,
        start_size=58, min_size=38, gap=8,
    )

    y = text_bg_start + 35

    for line in title_lines[:5]:
        draw.text((80, y), line, font=title_font, fill=text_color)
        y += title_font.size + 10

    # ========== ОСНОВНОЙ ТЕКСТ (без кружков) ==========
    # Отступ после заголовка перед текстом
    y += 25

    if body:
        available_height = H - y - 150
        body_font, body_lines = fit_text(
            draw, body, FONT_REGULAR, max_width=900, max_height=available_height,
            start_size=33, min_size=16, gap=8,
        )

        for line in body_lines:
            draw.text((80, y), line, font=body_font, fill=text_color)
            y += body_font.size + 8

    # ========== ПОДВАЛ ==========
    thin_line_y = H - 80
    thin_line_height = 2
    draw.rectangle((0, thin_line_y, W, thin_line_y + thin_line_height), fill=PURPLE)
    
    # Кнопка fider.by слева
    button_font = font(FONT_BOLD, 28)
    button_text = "fider.by"
    
    bbox = draw.textbbox((0, 0), button_text, font=button_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    button_padding_x = 20
    button_padding_y = 12
    button_x = 50
    button_y = thin_line_y + thin_line_height + 20
    
    button_bg_x1 = button_x
    button_bg_y1 = button_y
    button_bg_x2 = button_x + text_width + button_padding_x * 2
    button_bg_y2 = button_y + text_height + button_padding_y * 2
    
    draw.rounded_rectangle(
        (button_bg_x1, button_bg_y1, button_bg_x2, button_bg_y2),
        radius=25,
        fill=PURPLE
    )
    
    text_x = button_x + button_padding_x
    text_y = button_y + button_padding_y
    draw.text(
        (text_x, text_y),
        button_text,
        font=button_font,
        fill=WHITE
    )
    
    # Надпись справа
    right_text_font = font(FONT_BOLD, 22)
    right_text = "ПРИСЫЛАЙТЕ НОВОСТИ В ДИРЕКТ"
    
    right_text_bbox = draw.textbbox((0, 0), right_text, font=right_text_font)
    right_text_width = right_text_bbox[2] - right_text_bbox[0]
    right_text_height = right_text_bbox[3] - right_text_bbox[1]
    
    right_text_x = W - right_text_width - 50
    right_text_y = button_y + (button_padding_y * 2 + text_height - right_text_height) // 2
    
    draw.text(
        (right_text_x, right_text_y),
        right_text,
        font=right_text_font,
        fill=PURPLE
    )

    output = io.BytesIO()
    canvas.save(output, format="PNG", quality=95)
    output.seek(0)
    return output


def update_stats(user_id):
    """Обновляет статистику пользователя"""
    stats = load_stats()
    user_id_str = str(user_id)
    
    if user_id_str not in stats:
        stats[user_id_str] = {"count": 0, "last_used": None}
    
    stats[user_id_str]["count"] += 1
    stats[user_id_str]["last_used"] = datetime.now().isoformat()
    save_stats(stats)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "waiting_photo"
    context.user_data["dark_mode"] = False

    await update.message.reply_text(
        "🟣 **Fider.by Story Bot**\n\n"
        "✨ **ДОСТУПНЫЕ ТЕМЫ:**\n"
        "☀️ /light — светлая тема (по умолчанию)\n"
        "🌙 /dark — тёмная тема\n\n"
        "📝 **СПОСОБЫ СОЗДАНИЯ СТОРИС:**\n\n"
        "**1. РЕПОСТ ПОСТА**\n"
        "Просто перешли любой пост в этот чат — бот автоматически извлечёт фото, заголовок и текст!\n\n"
        "**2. РУЧНОЙ ВВОД**\n"
        "• Отправь фото\n"
        "• Отправь заголовок\n"
        "• Отправь основной текст (макс. 900 символов)\n\n"
        "📊 /stats — твоя статистика\n"
        "🆘 /help — помощь",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 **Помощь по боту Fider.by**\n\n"
        "**📱 КАК ИСПОЛЬЗОВАТЬ:**\n\n"
        "**Способ 1 — Репост поста:**\n"
        "• Найди любой пост в Telegram\n"
        "• Нажми на него и выбери «Переслать»\n"
        "• Отправь в этот чат\n"
        "• Бот сам извлечёт фото, заголовок и текст и сразу сделает сторис!\n\n"
        "**Способ 2 — Ручной ввод:**\n"
        "1. /start — начать создание\n"
        "2. Отправь фото\n"
        "3. Отправь заголовок\n"
        "4. Отправь основной текст\n\n"
        "**🎨 КОМАНДЫ:**\n"
        "/light — светлая тема\n"
        "/dark — тёмная тема\n"
        "/stats — статистика использования\n"
        "/help — эта справка\n\n"
        "💡 **Совет:** При репосте бот сам определяет заголовок (первая строка) и основной текст.",
        parse_mode="Markdown"
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = load_stats()
    user_id_str = str(update.effective_user.id)
    
    if user_id_str in stats:
        count = stats[user_id_str]["count"]
        last_used = stats[user_id_str]["last_used"]
        if last_used:
            last_used = datetime.fromisoformat(last_used).strftime("%d.%m.%Y %H:%M")
        else:
            last_used = "неизвестно"
        
        await update.message.reply_text(
            f"📊 **Ваша статистика**\n\n"
            f"• Создано сторис: **{count}**\n"
            f"• Последнее использование: {last_used}\n\n"
            f"Продолжайте создавать качественный контент с Fider.by! ✨",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "📊 **Ваша статистика**\n\n"
            "Вы ещё не создали ни одной сторис.\n\n"
            "Начните прямо сейчас — отправьте /start или перешлите пост! 🚀",
            parse_mode="Markdown"
        )


async def dark_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dark_mode"] = True
    await update.message.reply_text(
        "🌙 **Включена тёмная тема**\n\n"
        "Теперь все сторис будут создаваться в тёмном оформлении.\n\n"
        "Отправляй фото или пересылай посты!",
        parse_mode="Markdown"
    )


async def light_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dark_mode"] = False
    await update.message.reply_text(
        "☀️ **Включена светлая тема**\n\n"
        "Теперь все сторис будут создаваться в светлом оформлении.\n\n"
        "Отправляй фото или пересылай посты!",
        parse_mode="Markdown"
    )


async def handle_forwarded_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает пересланные посты — сразу делает сторис без дополнительных вопросов"""
    message = update.message
    
    # Проверяем, есть ли в пересланном сообщении текст
    original_text = message.caption or message.text
    
    if not original_text:
        await update.message.reply_text("❌ В пересланном сообщении нет текста. Попробуй другой пост.")
        return True
    
    # Ищем фото в сообщении
    photo_bytes = None
    
    if message.photo:
        photo = message.photo[-1]
        try:
            file = await context.bot.get_file(photo.file_id)
            photo_bytes = await file.download_as_bytearray()
        except Exception as e:
            logger.error(f"Ошибка загрузки фото: {e}")
            await update.message.reply_text("❌ Не удалось загрузить фото из поста.")
            return True
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        try:
            file = await context.bot.get_file(message.document.file_id)
            photo_bytes = await file.download_as_bytearray()
        except Exception as e:
            logger.error(f"Ошибка загрузки документа: {e}")
            await update.message.reply_text("❌ Не удалось загрузить файл.")
            return True
    else:
        await update.message.reply_text("❌ В пересланном сообщении нет фото. Попробуй другой пост.")
        return True
    
    # Извлекаем заголовок и текст из поста
    try:
        title, body = extract_title_and_body(original_text)
        
        if not title:
            await update.message.reply_text("❌ Не удалось извлечь заголовок из текста. Попробуй другой пост.")
            return True
        
        dark_mode = context.user_data.get("dark_mode", False)
        theme_name = "тёмной" if dark_mode else "светлой"
        
        msg = await update.message.reply_text(
            f"📱 **Обработка поста...**\n\n"
            f"📷 Фото: ✅\n"
            f"📝 Заголовок: {title[:60]}...\n"
            f"📄 Текст: {len(body)} символов\n"
            f"🎨 Тема: {theme_name}\n\n"
            f"⏳ Создаю сторис...",
            parse_mode="Markdown"
        )
        
        # Сразу создаём сторис
        result = create_story(photo_bytes, title, body, dark_mode)
        result.name = "fider_story.png"
        
        await update.message.reply_photo(
            photo=result,
            caption=f"✨ **Готово!** Сторис создан из пересланного поста\n\n"
                   f"📌 **{title[:80]}**\n\n"
                   f"#fiderby #сторис\n\n"
                   f"💡 Хочешь другую тему? Используй /light или /dark",
            parse_mode="Markdown"
        )
        
        # Обновляем статистику
        update_stats(update.effective_user.id)
        
        await msg.delete()
        return True
        
    except ValueError as e:
        await update.message.reply_text(f"❌ {str(e)}\n\nПопробуй другой пост или отредактируй текст.")
        return True
    except Exception as e:
        logger.exception(e)
        await update.message.reply_text(f"❌ Ошибка при обработке поста: {str(e)}")
        return True


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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Если это пересланный пост — обрабатываем сразу
    if update.message.forward_origin:
        await handle_forwarded_post(update, context)
        return
    
    state = context.user_data.get("state")

    if state == "waiting_title":
        title = update.message.text.strip()

        if len(title) < 5:
            await update.message.reply_text("Заголовок слишком короткий. Отправь нормальный заголовок.")
            return

        context.user_data["title"] = title
        context.user_data["state"] = "waiting_body"

        await update.message.reply_text("✅ Заголовок получил. Теперь отправь основной текст (максимум 900 символов).")
        return

    if state == "waiting_body":
        body = update.message.text.strip()
        photo = context.user_data.get("photo")
        title = context.user_data.get("title")
        dark_mode = context.user_data.get("dark_mode", False)

        if not photo or not title:
            context.user_data.clear()
            context.user_data["state"] = "waiting_photo"
            await update.message.reply_text("Что-то потерялось. Нажми /start и начни заново.")
            return

        theme_name = "тёмной" if dark_mode else "светлой"
        msg = await update.message.reply_text(f"🎨 Создаю сторис в {theme_name} теме...")

        try:
            result = create_story(photo, title, body, dark_mode)
            result.name = "fider_story.png"

            await update.message.reply_photo(
                photo=result,
                caption=f"✨ Готово в {theme_name} теме\n\nfider.by"
            )
            
            update_stats(update.effective_user.id)
            
            context.user_data.clear()
            context.user_data["state"] = "waiting_photo"
            context.user_data["dark_mode"] = dark_mode

        except ValueError as e:
            await update.message.reply_text(f"❌ {str(e)}\n\nОтправьте новый, более короткий текст.")
            return
        except Exception as e:
            logger.exception(e)
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")
            context.user_data.clear()
            context.user_data["state"] = "waiting_photo"

        try:
            await msg.delete()
        except Exception:
            pass

        return

    if update.message.text:
        await update.message.reply_text("Нажми /start и отправь фото, или просто перешли сюда любой пост!")


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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("dark", dark_mode))
    app.add_handler(CommandHandler("light", light_mode))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ FIDER STORY BOT STARTED")
    print("🌓 Поддержка светлой и тёмной темы")
    print("📱 Автоматическая обработка репостов постов")

    app.run_polling(
        drop_pending_updates=True,
        poll_interval=2.0,
        timeout=30,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
