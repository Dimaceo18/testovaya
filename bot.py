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
BLACK = (20, 22, 32)
WHITE = (255, 255, 255)
LIGHT_BG = (255, 255, 255)
DARK_BG = (7, 7, 10)
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


def create_story(photo_bytes, title, body, dark_mode=False):
    if len(body) > 900:
        raise ValueError("Текст слишком длинный, сделайте его короче (максимум 900 символов)")

    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

    # Выбираем цвета в зависимости от темы
    bg_color = DARK_BG if dark_mode else LIGHT_BG
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

    # ========== ЭЛЛИПС В ЛЕВОМ НИЖНЕМ УГЛУ (из вашего файла) ==========
    # Рисуем фиолетовый эллипс
    ellipse_size = 50
    ellipse_offset = 50
    draw.ellipse(
        (ellipse_offset, H - ellipse_offset - ellipse_size,
         ellipse_offset + ellipse_size, H - ellipse_offset),
        fill=PURPLE
    )
    
    # Текст внутри эллипса
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
    
    # Текст fider.by рядом с эллипсом
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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state == "waiting_title":
        title = update.message.text.strip()

        if len(title) < 5:
            await update.message.reply_text("Заголовок слишком короткий.")
            return

        context.user_data["title"] = title
        context.user_data["state"] = "waiting_body"

        await update.message.reply_text("✅ Заголовок получил. Теперь отправь основной текст (макс. 900 символов).")
        return

    if state == "waiting_body":
        body = update.message.text.strip()
        photo = context.user_data.get("photo")
        title = context.user_data.get("title")
        dark_mode = context.user_data.get("dark_mode", False)

        if not photo or not title:
            context.user_data.clear()
            context.user_data["state"] = "waiting_photo"
            await update.message.reply_text("Что-то потерялось. Нажми /start.")
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
            await update.message.reply_text(f"❌ {str(e)}\n\nОтправьте новый текст.")
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

    await update.message.reply_text("Нажми /start.")


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
    print("🌓 Светлая и тёмная тема")

    app.run_polling(
        drop_pending_updates=True,
        poll_interval=2.0,
        timeout=30,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
