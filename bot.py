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

FONT_BOLD = "Montserrat-Black.ttf"
FONT_REGULAR = "Montserrat-Bold.ttf"
DIVIDER_PATH = "3322.png"

# Высота плашки-разделителя в пикселях
DIVIDER_HEIGHT = 50


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


def create_story(photo_bytes, title, body):
    # Проверка длины текста
    if len(body) > 900:
        raise ValueError("Текст слишком длинный, сделайте его короче (максимум 900 символов)")

    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

    canvas = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(canvas)

    # Высота фото - 40% от высоты сторис
    photo_h = int(H * 0.4)
    photo = crop_cover(img, (W, photo_h))
    photo = ImageEnhance.Brightness(photo).enhance(0.92)
    canvas.paste(photo, (0, 0))

    # СНАЧАЛА создаём белый фон с текстом
    white_bg_start = photo_h
    draw.rectangle((0, white_bg_start, W, H), fill=WHITE)

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

    y = white_bg_start + 80

    for line in title_lines[:5]:
        draw.text((80, y), line, font=title_font, fill=BLACK)
        y += title_font.size + 10

    y += 18
    draw.rounded_rectangle((80, y, 190, y + 10), radius=5, fill=PURPLE)
    y += 70

    # Основной текст
    body = body.strip()
    available_height = H - y - 150

    body_font, body_lines = fit_text(
        draw,
        body,
        FONT_REGULAR,
        max_width=900,
        max_height=available_height,
        start_size=33,
        min_size=16,
        gap=8,
    )

    for line in body_lines:
        draw.text((80, y), line, font=body_font, fill=BLACK)
        y += body_font.size + 8

    # ПОСЛЕ ТОГО КАК ВЕСЬ ТЕКСТ НАРИСОВАН, накладываем плашку ПОВЕРХ ВСЕГО
    divider_y = photo_h
    
    if not os.path.exists(DIVIDER_PATH):
        draw.rectangle((0, divider_y, W, divider_y + DIVIDER_HEIGHT), fill=PURPLE)
    else:
        divider = Image.open(DIVIDER_PATH).convert("RGBA")
        divider = divider.resize((W, DIVIDER_HEIGHT), Image.LANCZOS)
        
        # Создаём временный слой для плашки, чтобы сохранить прозрачность
        temp_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        temp_layer.paste(divider, (0, divider_y), divider)
        
        # Накладываем плашку поверх всего canvas
        canvas = canvas.convert("RGBA")
        canvas = Image.alpha_composite(canvas, temp_layer)
        canvas = canvas.convert("RGB")
        draw = ImageDraw.Draw(canvas)

    # Логотип внизу по центру
    logo_font = font(FONT_BOLD, 38)
    logo_text = "fider.by"
    
    # Получаем размер текста логотипа
    try:
        bbox = draw.textbbox((0, 0), logo_text, font=logo_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
    except:
        text_width = len(logo_text) * 20
        text_height = 40
    
    # Позиция по центру внизу
    logo_x = (W - text_width) // 2
    logo_y = H - 80
    
    # Рисуем фон под логотипом
    padding = 20
    logo_bg_x1 = logo_x - padding
    logo_bg_y1 = logo_y - 12
    logo_bg_x2 = logo_x + text_width + padding
    logo_bg_y2 = logo_y + text_height + 12
    
    draw.rounded_rectangle(
        (logo_bg_x1, logo_bg_y1, logo_bg_x2, logo_bg_y2),
        radius=18,
        fill=PURPLE
    )
    
    # Рисуем текст логотипа
    draw.text(
        (logo_x, logo_y),
        logo_text,
        font=logo_font,
        fill=WHITE
    )

    # Тонкая фиолетовая полоса внизу (10 пикселей)
    footer_height = 10
    draw.rectangle((0, H - footer_height, W, H), fill=PURPLE)

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
        "3. Потом отправь основной текст (максимум 900 символов)\n\n"
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

        await update.message.reply_text("✅ Заголовок получил. Теперь отправь основной текст (максимум 900 символов).")
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
            context.user_data.clear()
            context.user_data["state"] = "waiting_photo"

        except ValueError as e:
            await update.message.reply_text(f"❌ {str(e)}\n\nОтправьте новый, более короткий текст.")
            return

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
        .connect_timeout(60)
        .read_timeout(120)
        .write_timeout(120)
        .pool_timeout(120)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ FIDER STORY BOT STARTED")

    app.run_polling(
        drop_pending_updates=True,
        poll_interval=2.0,
        timeout=60,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
