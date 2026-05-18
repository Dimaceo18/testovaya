# -*- coding: utf-8 -*-

import os
import io
import threading
import logging

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

@web_app.get("/")
def health():
    return "OK", 200


def run_web():
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port)


W, H = 1080, 1920

PURPLE = (111, 55, 245)
WHITE = (255, 255, 255)
BLACK = (7, 7, 10)  # #07070A для тёмной темы
LIGHT_TEXT = (20, 22, 32)  # тёмный текст на светлом фоне
DARK_TEXT = (255, 255, 255)  # белый текст на тёмном фоне

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
    
    # Затемнение в зависимости от темы
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
    title = title.strip()
    title_font, title_lines = fit_text(
        draw, title, FONT_BOLD, max_width=900, max_height=300,
        start_size=58, min_size=38, gap=8,
    )

    y = text_bg_start + 80
    for line in title_lines[:5]:
        draw.text((80, y), line, font=title_font, fill=text_color)
        y += title_font.size + 10

    # ========== ТРИ ТОЧКИ ==========
    y += 18
    dot_radius = 12
    dot_spacing = 18
    start_x = 80
    
    for i in range(3):
        x = start_x + i * (dot_radius * 2 + dot_spacing)
        y_dot = y + 8
        draw.ellipse((x - dot_radius, y_dot - dot_radius, x + dot_radius, y_dot + dot_radius), fill=PURPLE)
    
    y += 60

    # ========== ОСНОВНОЙ ТЕКСТ ==========
    body = body.strip()
    available_height = H - y - 150
    body_font, body_lines = fit_text(
        draw, body, FONT_REGULAR, max_width=900, max_height=available_height,
        start_size=33, min_size=16, gap=8,
    )

    for line in body_lines:
        draw.text((80, y), line, font=body_font, fill=text_color)
        y += body_font.size + 8

    # ========== ПОДВАЛ: ТОНКАЯ ПОЛОСА + КНОПКА fider.by СЛЕВА ==========
    # Тонкая фиолетовая полоса (2px)
    thin_line_y = H - 80
    thin_line_height = 2
    draw.rectangle((0, thin_line_y, W, thin_line_y + thin_line_height), fill=PURPLE)
    
    # Кнопка fider.by слева (скруглённый прямоугольник)
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
    
    # Скруглённый прямоугольник (кнопка)
    draw.rounded_rectangle(
        (button_bg_x1, button_bg_y1, button_bg_x2, button_bg_y2),
        radius=25,
        fill=PURPLE
    )
    
    # Текст внутри кнопки
    text_x = button_x + button_padding_x
    text_y = button_y + button_padding_y
    draw.text(
        (text_x, text_y),
        button_text,
        font=button_font,
        fill=WHITE
    )

    output = io.BytesIO()
    canvas.save(output, format="PNG", quality=95)
    output.seek(0)
    return output


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
        "2. Потом отправь заголовок\n"
        "3. Потом отправь основной текст (максимум 900 символов)\n\n"
        "Я соберу готовую сторис 9:16."
    )


async def dark_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dark_mode"] = True
    await update.message.reply_text(
        "🌙 Включена тёмная тема\n\n"
        "Теперь отправляй фото для создания сторис в тёмном оформлении."
    )


async def light_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dark_mode"] = False
    await update.message.reply_text(
        "☀️ Включена светлая тема\n\n"
        "Теперь отправляй фото для создания сторис в светлом оформлении."
    )


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

        await update.message.reply_text("✅ Фото получил в хорошем качестве. Теперь отправь заголовок.")
    except TimedOut:
        await update.message.reply_text("⏱️ Превышено время ожидания. Попробуй ещё раз.")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке файла.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    await update.message.reply_text("Нажми /start и отправь фото.")


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
    app.add_handler(CommandHandler("dark", dark_mode))
    app.add_handler(CommandHandler("light", light_mode))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ FIDER STORY BOT STARTED")
    print("🌓 Поддержка светлой и тёмной темы")

    app.run_polling(
        drop_pending_updates=True,
        poll_interval=2.0,
        timeout=30,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
