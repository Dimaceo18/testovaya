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

logging.basicConfig(level=logging.INFO)
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

FONT_BOLD = "Montserrat-Black.ttf"
FONT_REGULAR = "Montserrat-Bold.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


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


def create_story(photo_bytes, title, body):
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

    canvas = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(canvas)

    # Фото
    photo_h = 760
    photo = crop_cover(img, (W, photo_h))
    photo = ImageEnhance.Brightness(photo).enhance(0.92)
    canvas.paste(photo, (0, 0))

    # Логотип
    logo_font = font(FONT_BOLD, 38)
    logo_x, logo_y = 55, 55
    logo_w, logo_h = 205, 68

    draw.rounded_rectangle(
        (logo_x, logo_y, logo_x + logo_w, logo_y + logo_h),
        radius=18,
        fill=PURPLE
    )

    draw.text(
        (logo_x + 26, logo_y + 12),
        "fider.by",
        font=logo_font,
        fill=WHITE
    )

    # Фиолетовая плашка
    divider_y = 755
    divider_h = 38

    draw.rectangle((0, divider_y, W, divider_y + divider_h), fill=PURPLE)
    draw.rectangle((0, divider_y + divider_h, W, H), fill=WHITE)

    # Заголовок
    title = title.strip()

    title_font, title_lines = fit_text(
        draw,
        title,
        FONT_BOLD,
        max_width=900,
        max_height=300,
        start_size=58,
        min_size=38,
        gap=8,
    )

    y = 900

    for line in title_lines[:5]:
        draw.text((80, y), line, font=title_font, fill=BLACK)
        y += title_font.size + 10

    # Акцентная линия
    y += 18
    draw.rounded_rectangle((80, y, 190, y + 10), radius=5, fill=PURPLE)
    y += 70

    # Основной текст
    body = body.strip()

    if len(body) > 720:
        body = body[:720].rsplit(" ", 1)[0] + "..."

    body_font, body_lines = fit_text(
        draw,
        body,
        FONT_REGULAR,
        max_width=900,
        max_height=500,
        start_size=33,
        min_size=24,
        gap=8,
    )

    max_body_y = 1685

    for line in body_lines:
        if y + body_font.size > max_body_y:
            draw.text((80, y), "...", font=body_font, fill=BLACK)
            break

        draw.text((80, y), line, font=body_font, fill=BLACK)
        y += body_font.size + 8

    # Подвал
    footer_y = 1768

    draw.rounded_rectangle(
        (80, footer_y, 1000, footer_y + 5),
        radius=3,
        fill=PURPLE
    )

    footer_text_y = footer_y + 42

    draw.ellipse(
        (80, footer_text_y - 4, 126, footer_text_y + 42),
        fill=PURPLE
    )

    small_font = font(FONT_BOLD, 25)
    draw.text((96, footer_text_y + 2), "f", font=small_font, fill=WHITE)

    footer_font = font(FONT_REGULAR, 30)
    draw.text(
        (150, footer_text_y + 1),
        "Читайте больше на",
        font=footer_font,
        fill=BLACK
    )

    site_font = font(FONT_BOLD, 30)
    draw.text(
        (455, footer_text_y + 1),
        "fider.by",
        font=site_font,
        fill=PURPLE
    )

    output = io.BytesIO()
    canvas.save(output, format="PNG", quality=95)
    output.seek(0)
    return output


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "waiting_photo"

    await update.message.reply_text(
        "🟣 Бот сторис Fider.by\n\n"
        "1. Отправь фото\n"
        "2. Потом отправь заголовок\n"
        "3. Потом отправь основной текст\n\n"
        "Я соберу готовую сторис 9:16."
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_photo":
        await update.message.reply_text("Нажми /start и отправь фото заново.")
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()

    context.user_data["photo"] = bytes(photo_bytes)
    context.user_data["state"] = "waiting_title"

    await update.message.reply_text("✅ Фото получил. Теперь отправь заголовок.")


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

    await update.message.reply_text("✅ Фото получил в хорошем качестве. Теперь отправь заголовок.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state == "waiting_title":
        title = update.message.text.strip()

        if len(title) < 5:
            await update.message.reply_text("Заголовок слишком короткий. Отправь нормальный заголовок.")
            return

        context.user_data["title"] = title
        context.user_data["state"] = "waiting_body"

        await update.message.reply_text("✅ Заголовок получил. Теперь отправь основной текст.")
        return

    if state == "waiting_body":
        body = update.message.text.strip()
        photo = context.user_data.get("photo")
        title = context.user_data.get("title")

        if not photo or not title:
            context.user_data.clear()
            context.user_data["state"] = "waiting_photo"
            await update.message.reply_text("Что-то потерялось. Нажми /start и начни заново.")
            return

        msg = await update.message.reply_text("🎨 Оформляю сторис...")

        try:
            result = create_story(photo, title, body)
            result.name = "fider_story.png"

            await update.message.reply_photo(
                photo=result,
                caption="Готово ✨"
            )

        except Exception as e:
            logger.exception(e)
            await update.message.reply_text(f"❌ Ошибка:\n{e}")

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
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(60)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ FIDER STORY BOT STARTED")

    app.run_polling(
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=30,
    )


if __name__ == "__main__":
    main()
