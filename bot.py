# -*- coding: utf-8 -*-

import os
import io
import threading
import logging
from enum import Enum

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

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN")

web_app = Flask(__name__)

@web_app.get("/")
def health():
    return "OK", 200


def run_web():
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port)


W, H = 1080, 1920

# Цвета
PURPLE = (111, 55, 245)  # #6F37F5
WHITE = (255, 255, 255)
BLACK = (7, 7, 10)  # #07070A

# Шрифты
FONT_BLACK = "Montserrat-Black.ttf"
FONT_BOLD = "Montserrat-Bold.ttf"

# Отступы и размеры
PHOTO_HEIGHT_PERCENT = 0.40  # 40% высоты
PHOTO_PADDING = 60  # отступы слева/справа
LINE_WIDTH_PERCENT = 0.88  # 88% ширины
LINE_HEIGHT = 22  # высота линии
LINE_TOP_MARGIN = 35  # отступ сверху после фото
TITLE_TOP_MARGIN = 55  # отступ сверху после линии
TITLE_WIDTH_PERCENT = 0.82  # 82% ширины
TITLE_MAX_LINES = 5
DOT_SIZE = 26  # диаметр точек
DOT_SPACING = 18  # расстояние между точками
DOT_TOP_MARGIN = 40  # отступ сверху до точек
TEXT_WIDTH_PERCENT = 0.86  # 86% ширины
TEXT_MAX_CHARS = 900
BUTTON_HEIGHT = 68
BUTTON_PADDING = 30
BUTTON_BOTTOM_MARGIN = 80


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()


def crop_cover_with_padding(img, target_w, target_h, padding):
    """Обрезает фото с сохранением пропорций и добавляет отступы по бокам"""
    # Сначала обрезаем с отступами
    img_w, img_h = img.size
    
    # Целевая ширина с учётом отступов
    target_content_w = target_w - (padding * 2)
    
    scale = max(target_content_w / img_w, target_h / img_h)
    
    new_w = int(img_w * scale)
    new_h = int(img_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    
    # Обрезаем по центру до нужной высоты
    left = (new_w - target_content_w) // 2
    top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_content_w, top + target_h))
    
    # Создаём полотно с отступами
    result = Image.new("RGB", (target_w, target_h), WHITE)
    result.paste(img, (padding, 0))
    
    return result


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


def fit_text(draw, text, font_path, max_width, max_height, start_size, min_size, line_spacing):
    """Подбирает размер шрифта, чтобы текст влез в заданную высоту"""
    size = start_size
    
    while size >= min_size:
        fnt = font(font_path, size)
        lines = wrap_text(draw, text, fnt, max_width)
        line_height = int(size * line_spacing)
        total_h = len(lines) * line_height
        
        if total_h <= max_height and len(lines) <= 15:
            return fnt, lines
        
        size -= 2
    
    fnt = font(font_path, min_size)
    lines = wrap_text(draw, text, fnt, max_width)
    return fnt, lines


def create_story(photo_bytes, title, body, dark_mode=False):
    """Создаёт сторис в светлой или тёмной теме"""
    
    # Проверка длины текста
    if len(body) > TEXT_MAX_CHARS:
        raise ValueError(f"Текст слишком длинный, сделайте его короче (максимум {TEXT_MAX_CHARS} символов)")
    
    # Выбираем тему
    bg_color = BLACK if dark_mode else WHITE
    text_color = WHITE if dark_mode else BLACK
    
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    
    # Создаём холст
    canvas = Image.new("RGB", (W, H), bg_color)
    draw = ImageDraw.Draw(canvas)
    
    # ========== ФОТО ==========
    photo_h = int(H * PHOTO_HEIGHT_PERCENT)
    photo = crop_cover_with_padding(img, W, photo_h, PHOTO_PADDING)
    
    # Лёгкое затемнение (5%)
    if dark_mode:
        enhancer = ImageEnhance.Brightness(photo)
        photo = enhancer.enhance(0.85)  # для тёмной темы чуть темнее
    else:
        enhancer = ImageEnhance.Brightness(photo)
        photo = enhancer.enhance(0.95)  # 5% затемнения
    
    canvas.paste(photo, (0, 0))
    
    # ========== ЛИНИЯ ПОД ФОТО ==========
    line_width = int(W * LINE_WIDTH_PERCENT)
    line_x = (W - line_width) // 2
    line_y = photo_h + LINE_TOP_MARGIN
    draw.rectangle((line_x, line_y, line_x + line_width, line_y + LINE_HEIGHT), fill=PURPLE)
    
    # ========== ЗАГОЛОВОК ==========
    title_y = line_y + LINE_HEIGHT + TITLE_TOP_MARGIN
    title_width = int(W * TITLE_WIDTH_PERCENT)
    
    title_font, title_lines = fit_text(
        draw,
        title,
        FONT_BLACK,
        max_width=title_width,
        max_height=500,
        start_size=92,
        min_size=78,
        line_spacing=1.0,  # плотный межстрочный интервал
    )
    
    title_lines = title_lines[:TITLE_MAX_LINES]
    
    current_y = title_y
    for line in title_lines:
        draw.text((PHOTO_PADDING, current_y), line, font=title_font, fill=text_color)
        current_y += title_font.size + 5
    
    # ========== ТОЧКИ ==========
    dots_y = current_y + DOT_TOP_MARGIN
    dot_radius = DOT_SIZE // 2
    
    # Центрируем точки по левому краю (как текст)
    start_x = PHOTO_PADDING
    
    for i in range(3):
        x = start_x + i * (DOT_SIZE + DOT_SPACING) + dot_radius
        y_center = dots_y + dot_radius
        draw.ellipse(
            (x - dot_radius, y_center - dot_radius, x + dot_radius, y_center + dot_radius),
            fill=PURPLE
        )
    
    # ========== ОСНОВНОЙ ТЕКСТ ==========
    text_y = dots_y + DOT_SIZE + 45
    text_width = int(W * TEXT_WIDTH_PERCENT)
    max_text_height = H - text_y - BUTTON_BOTTOM_MARGIN - BUTTON_HEIGHT - 50
    
    body_font, body_lines = fit_text(
        draw,
        body,
        FONT_BOLD,
        max_width=text_width,
        max_height=max_text_height,
        start_size=38,
        min_size=32,
        line_spacing=1.15,
    )
    
    current_y = text_y
    for line in body_lines:
        draw.text((PHOTO_PADDING, current_y), line, font=body_font, fill=text_color)
        current_y += int(body_font.size * 1.15)
    
    # ========== КНОПКА ВНИЗУ ==========
    button_text = "fider.by"
    button_font = font(FONT_BLACK, 36)
    
    # Размеры кнопки
    bbox = draw.textbbox((0, 0), button_text, font=button_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    
    button_w = text_w + BUTTON_PADDING * 2
    button_h = BUTTON_HEIGHT
    button_x = (W - button_w) // 2
    button_y = H - BUTTON_BOTTOM_MARGIN
    
    # Рисуем кнопку
    draw.rounded_rectangle(
        (button_x, button_y, button_x + button_w, button_y + button_h),
        radius=button_h // 2,
        fill=PURPLE
    )
    
    # Рисуем текст кнопки
    text_x = button_x + BUTTON_PADDING
    text_y = button_y + (button_h - text_h) // 2 - 2
    draw.text((text_x, text_y), button_text, font=button_font, fill=WHITE)
    
    # ========== ТОНКАЯ ЛИНИЯ ВНИЗУ (опционально) ==========
    # Маленький акцент - тонкая линия над кнопкой
    accent_line_y = button_y - 20
    accent_line_width = 60
    accent_line_x = (W - accent_line_width) // 2
    draw.rectangle(
        (accent_line_x, accent_line_y, accent_line_x + accent_line_width, accent_line_y + 2),
        fill=PURPLE
    )
    
    output = io.BytesIO()
    canvas.save(output, format="PNG", quality=95)
    output.seek(0)
    return output


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "waiting_photo"
    context.user_data["dark_mode"] = False  # по умолчанию светлая тема

    await update.message.reply_text(
        "🟣 Fider.by Story Bot\n\n"
        "Создавай минималистичные сторис в стиле editorial.\n\n"
        "1. Отправь фото\n"
        "2. Отправь заголовок\n"
        "3. Отправь основной текст (макс. 900 символов)\n\n"
        "✨ Премиум-дизайн\n"
        "🎨 Чистая типографика\n"
        "📱 Адаптация под все экраны\n\n"
        "По умолчанию — светлая тема.\n"
        "Для тёмной темы отправь /dark\n"
        "Для светлой темы отправь /light"
    )


async def dark_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dark_mode"] = True
    await update.message.reply_text("🌙 Включена тёмная тема. Теперь отправляй фото.")


async def light_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dark_mode"] = False
    await update.message.reply_text("☀️ Включена светлая тема. Теперь отправляй фото.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_photo":
        await update.message.reply_text("Нажми /start и отправь фото заново.")
        return
    
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    
    context.user_data["photo"] = bytes(photo_bytes)
    context.user_data["state"] = "waiting_title"
    
    await update.message.reply_text("✅ Фото готово. Теперь отправь заголовок.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_photo":
        await update.message.reply_text("Нажми /start и отправь фото заново.")
        return
    
    doc = update.message.document
    
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Нужен файл изображения.")
        return
    
    file = await context.bot.get_file(doc.file_id)
    photo_bytes = await file.download_as_bytearray()
    
    context.user_data["photo"] = bytes(photo_bytes)
    context.user_data["state"] = "waiting_title"
    
    await update.message.reply_text("✅ Фото получено в высоком качестве. Теперь отправь заголовок.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    
    if state == "waiting_title":
        title = update.message.text.strip()
        
        if len(title) < 5:
            await update.message.reply_text("Заголовок слишком короткий. Отправь более информативный заголовок.")
            return
        
        context.user_data["title"] = title
        context.user_data["state"] = "waiting_body"
        
        await update.message.reply_text("✅ Заголовок принят. Теперь отправь основной текст (макс. 900 символов).")
        return
    
    if state == "waiting_body":
        body = update.message.text.strip()
        photo = context.user_data.get("photo")
        title = context.user_data.get("title")
        dark_mode = context.user_data.get("dark_mode", False)
        
        if not photo or not title:
            context.user_data.clear()
            context.user_data["state"] = "waiting_photo"
            await update.message.reply_text("Что-то пошло не так. Нажми /start и начни заново.")
            return
        
        msg = await update.message.reply_text("🎨 Создаю сторис в стиле Fider.by...")
        
        try:
            result = create_story(photo, title, body, dark_mode)
            result.name = "fider_story.png"
            
            theme_name = "тёмной" if dark_mode else "светлой"
            await update.message.reply_photo(
                photo=result,
                caption=f"✨ Готово в {theme_name} теме\n\nfider.by — главный технологический"
            )
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
    
    await update.message.reply_text("Нажми /start, чтобы начать создание сторис.")


async def post_init(app):
    await app.bot.delete_webhook(drop_pending_updates=True)


def main():
    threading.Thread(target=run_web, daemon=True).start()
    
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .connect_timeout(60)
        .read_timeout(120)
        .write_timeout(120)
        .pool_timeout(120)
        .build()
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dark", dark_mode))
    app.add_handler(CommandHandler("light", light_mode))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("✅ FIDER.STORY BOT STARTED")
    print("🎨 Дизайн: минимализм / editorial / premium")
    print("📐 Формат: 1080x1920")
    print("🌓 Поддержка светлой и тёмной темы")
    
    app.run_polling(
        drop_pending_updates=True,
        poll_interval=2.0,
        timeout=60,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
