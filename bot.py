# -*- coding: utf-8 -*-

import os
import io
import logging
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

# =========================
# НАСТРОЙКИ
# =========================

W, H = 1080, 1920

PURPLE = (111, 55, 245)
PURPLE_DARK = (76, 36, 190)
BLACK = (20, 22, 32)
WHITE = (255, 255, 255)
LIGHT_BG = (250, 249, 255)

FONT_BOLD = "Montserrat-Black.ttf"
FONT_REGULAR = "Montserrat-Bold.ttf"


# =========================
# PILLOW-ФУНКЦИИ
# =========================

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


def draw_gradient(draw, box, color1, color2):
    x1, y1, x2, y2 = box
    height = y2 - y1

    for i in range(height):
        ratio = i / height
        r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
        g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
        b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
        draw.line((x1, y1 + i, x2, y1 + i), fill=(r, g, b))


def create_story(photo_bytes, title, body):
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

    canvas = Image.new("RGB", (W, H), LIGHT_BG)
    draw = ImageDraw.Draw(canvas)

    # Фото сверху
    photo_h = 800
    photo = crop_cover(img, (W, photo_h))

    photo = ImageEnhance.Brightness(photo).enhance(0.82)
    canvas.paste(photo, (0, 0))

    # Логотип fider.by
    logo_font = font(FONT_BOLD, 42)
    draw.rounded_rectangle((60, 70, 245, 135), radius=18, fill=PURPLE)
    draw.text((84, 84), "fider.by", font=logo_font, fill=WHITE)

    # Фиолетовая волна-разделитель
    wave_y = 735
    draw.polygon(
        [
            (0, wave_y),
            (W, wave_y - 35),
            (W, wave_y + 80),
            (0, wave_y + 45),
        ],
        fill=PURPLE,
    )

    # Белая нижняя зона
    draw.rectangle((0, wave_y + 70, W, H), fill=WHITE)

    # Заголовок
    title_font, title_lines = fit_text(
        draw,
        title,
        FONT_BOLD,
        max_width=920,
        max_height=330,
        start_size=66,
        min_size=42,
        gap=8,
    )

    y = 860
    for line in title_lines[:5]:
        if "BYN" in line or "руб" in line:
            draw.text((80, y), line, font=title_font, fill=PURPLE_DARK)
        else:
            draw.text((80, y), line, font=title_font, fill=BLACK)
        y += title_font.size + 8

    # Маленькая фиолетовая линия
    draw.rounded_rectangle((80, y + 20, 190, y + 30), radius=5, fill=PURPLE)

    # Основной текст
    body = body.strip()
    if len(body) > 950:
        body = body[:950].rsplit(" ", 1)[0] + "..."

    body_font, body_lines = fit_text(
        draw,
        body,
        FONT_REGULAR,
        max_width=900,
        max_height=560,
        start_size=38,
        min_size=26,
        gap=10,
    )

    y += 75

    for line in body_lines:
        draw.text((80, y), line, font=body_font, fill=BLACK)
        y += body_font.size + 10

    # Нижняя линия
    draw.rounded_rectangle((80, 1780, 1000, 1784), radius=2, fill=PURPLE)

    # Подвал
    footer_font = font(FONT_REGULAR, 34)
    draw.text((140, 1815), "Читайте больше на", font=footer_font, fill=BLACK)

    site_font = font(FONT_BOLD, 34)
    draw.text((475, 1815), "fider.by", font=site_font, fill=PURPLE)

    # Иконка слева
    draw.ellipse((80, 1808, 120, 1848), fill=PURPLE)
    small_font = font(FONT_BOLD, 24)
    draw.text((91, 1814), "f", font=small_font, fill=WHITE)

    output = io.BytesIO()
    canvas.save(output, format="PNG", quality=95)
    output.seek(0)
    return output


# =========================
# TELEGRAM-БОТ
# =========================

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


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
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
