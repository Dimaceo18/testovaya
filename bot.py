# -*- coding: utf-8 -*-

import os
import io
import threading
import logging
import math

from flask import Flask
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

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
PURPLE_DARK = (88, 40, 200)
PURPLE_LIGHT = (147, 95, 255)
PURPLE_GLOW = (180, 130, 255)
BLACK = (20, 22, 32)
WHITE = (255, 255, 255)

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


def draw_gradient_rectangle(draw, bbox, color1, color2):
    """Рисует прямоугольник с градиентом"""
    x1, y1, x2, y2 = bbox
    width = x2 - x1
    height = y2 - y1
    
    for i in range(height):
        ratio = i / height
        r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
        g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
        b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
        draw.rectangle((x1, y1 + i, x2, y1 + i + 1), fill=(r, g, b))


def draw_stylish_divider(draw, y, width):
    """Рисует стильную плашку-разделитель с дизайнерскими элементами"""
    divider_height = 65
    
    # Основная плашка с градиентом (скруглённая)
    margin = 20
    x1, x2 = margin, width - margin
    y1, y2 = y, y + divider_height
    
    # Скруглённый прямоугольник (рисуем через многоугольник для скруглений)
    radius = 20
    draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=PURPLE)
    
    # Добавляем градиент поверх (более светлый к центру)
    for i in range(divider_height):
        ratio = 1 - abs(i - divider_height/2) / (divider_height/2) * 0.5
        r = int(PURPLE[0] + (PURPLE_LIGHT[0] - PURPLE[0]) * ratio)
        g = int(PURPLE[1] + (PURPLE_LIGHT[1] - PURPLE[1]) * ratio)
        b = int(PURPLE[2] + (PURPLE_LIGHT[2] - PURPLE[2]) * ratio)
        draw.rectangle((x1 + 2, y1 + i, x2 - 2, y1 + i + 1), fill=(r, g, b))
    
    # Тонкая светлая линия сверху
    draw.rounded_rectangle((x1 + 10, y1 + 5, x2 - 10, y1 + 7), radius=3, fill=PURPLE_GLOW)
    
    # Тонкая светлая линия снизу
    draw.rounded_rectangle((x1 + 10, y2 - 7, x2 - 10, y2 - 5), radius=3, fill=PURPLE_GLOW)
    
    # Декоративный элемент - маленький ромб в центре
    center_x = width // 2
    center_y = y + divider_height // 2
    diamond_size = 8
    
    diamond_points = [
        (center_x, center_y - diamond_size),
        (center_x + diamond_size, center_y),
        (center_x, center_y + diamond_size),
        (center_x - diamond_size, center_y)
    ]
    draw.polygon(diamond_points, fill=PURPLE_GLOW)
    
    # Маленькие кружочки по бокам от ромба
    circle_radius = 3
    draw.ellipse((center_x - 30 - circle_radius, center_y - circle_radius,
                  center_x - 30 + circle_radius, center_y + circle_radius), fill=PURPLE_GLOW)
    draw.ellipse((center_x + 30 - circle_radius, center_y - circle_radius,
                  center_x + 30 + circle_radius, center_y + circle_radius), fill=PURPLE_GLOW)
    
    # Штриховка по краям (декоративные вертикальные линии)
    for x_offset in [40, 60]:
        for y_offset in range(15, divider_height - 15, 8):
            draw.line((x1 + x_offset, y1 + y_offset, x1 + x_offset + 4, y1 + y_offset + 4), 
                     fill=PURPLE_GLOW, width=2)
            draw.line((x2 - x_offset, y1 + y_offset, x2 - x_offset - 4, y1 + y_offset + 4), 
                     fill=PURPLE_GLOW, width=2)
    
    return divider_height


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

    # Логотип
    try:
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
        
        # Маленький акцент на логотипе
        draw.rounded_rectangle(
            (logo_x + logo_w - 30, logo_y + 10, logo_x + logo_w - 10, logo_y + logo_h - 10),
            radius=10,
            fill=PURPLE_LIGHT
        )
    except:
        pass

    # Стильная плашка-разделитель
    divider_y = photo_h
    divider_height = draw_stylish_divider(draw, divider_y, W)

    # Белая зона под текстом
    white_bg_start = divider_y + divider_height
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

    # Декоративная полоска под заголовком
    y += 10
    draw.rounded_rectangle((80, y, 180, y + 6), radius=3, fill=PURPLE)
    y += 45

    # Основной текст
    body = body.strip()
    available_height = H - y - 80

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

    # Элегантная тонкая линия внизу
    footer_height = 3
    footer_y = H - footer_height
    
    # Градиентная линия внизу
    for i in range(footer_height):
        ratio = i / footer_height
        r = int(PURPLE_DARK[0] * (1 - ratio) + PURPLE[0] * ratio)
        g = int(PURPLE_DARK[1] * (1 - ratio) + PURPLE[1] * ratio)
        b = int(PURPLE_DARK[2] * (1 - ratio) + PURPLE[2] * ratio)
        draw.rectangle((0, footer_y + i, W, footer_y + i + 1), fill=(r, g, b))

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
        "Я соберу готовую сторис 9:16 со стильным дизайном."
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

    print("✅ FIDER STORY BOT STARTED (with stylish design)")

    app.run_polling(
        drop_pending_updates=True,
        poll_interval=2.0,
        timeout=60,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
