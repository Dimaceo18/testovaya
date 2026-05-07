# -*- coding: utf-8 -*-

import os
import io
import base64
import logging

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from openai import AsyncOpenAI

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ======================================================
# НАСТРОЙКИ
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OUT_W = 1080
OUT_H = 1350

# ======================================================
# PROMPT
# ======================================================

STYLE_PROMPT = """
Transform the uploaded photo into a premium cinematic editorial image.

Style:
- realistic photography
- premium city media aesthetic
- cinematic lighting
- clean Apple-style composition
- rich shadows
- glossy highlights
- modern urban atmosphere
- realistic depth
- stylish Belarus media look
- magazine quality

Keep:
- same person
- same pose
- same framing
- same location feeling

Do not add:
- text
- typography
- logos
- signs
- cartoon style
- illustration style

The image should look like a premium city media photo.
"""

# ======================================================
# ШРИФТЫ
# ======================================================

def get_font(size=40, bold=True):

    paths = []

    if bold:
        paths = [
            "./fonts/Montserrat-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    else:
        paths = [
            "./fonts/Montserrat-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except:
            pass

    return ImageFont.load_default()

# ======================================================
# ОБРЕЗКА
# ======================================================

def cover_crop(img, size=(OUT_W, OUT_H)):

    target_w, target_h = size

    src_w, src_h = img.size

    scale = max(target_w / src_w, target_h / src_h)

    new_w = int(src_w * scale)
    new_h = int(src_h * scale)

    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2

    return img.crop((left, top, left + target_w, top + target_h))

# ======================================================
# AI ПЕРЕРИСОВКА
# ======================================================

async def ai_redraw(photo_bytes: bytes):

    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

    img = cover_crop(img, (1536, 1536))

    image_file = io.BytesIO()

    img.save(image_file, format="PNG")

    image_file.seek(0)

    image_file.name = "input.png"

    response = await client.images.edit(
        model="gpt-image-1",
        image=image_file,
        prompt=STYLE_PROMPT,
        size="1536x1536",
    )

    image_base64 = response.data[0].b64_json

    image_bytes = base64.b64decode(image_base64)

    return image_bytes

# ======================================================
# ГРАДИЕНТ
# ======================================================

def add_gradient(img):

    img = img.convert("RGBA")

    w, h = img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    draw = ImageDraw.Draw(overlay)

    grad_w = int(w * 0.65)

    for x in range(grad_w):

        t = x / grad_w

        alpha = int(225 * (1 - t) ** 1.7)

        draw.line(
            [(x, 0), (x, h)],
            fill=(0, 35, 28, alpha),
            width=1
        )

    return Image.alpha_composite(img, overlay)

# ======================================================
# ДЕКОР
# ======================================================

def draw_decor(draw):

    green = (130, 190, 110)

    for i in range(5):

        draw.arc(
            (-120 + i * 25, -130 + i * 18,
             280 + i * 25, 240 + i * 18),
            15,
            120,
            fill=green,
            width=3
        )

    draw.arc(
        (-350, 920, 340, 1600),
        205,
        20,
        fill=(48, 92, 54),
        width=55
    )

# ======================================================
# ПЛАШКА
# ======================================================

def draw_label(draw, x, y, text):

    font = get_font(22)

    bbox = draw.textbbox((0, 0), text, font=font)

    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    draw.rounded_rectangle(
        (x, y, x + w + 40, y + h + 22),
        radius=18,
        fill=(255, 202, 54)
    )

    draw.text(
        (x + 20, y + 9),
        text,
        font=font,
        fill=(0, 25, 20)
    )

# ======================================================
# ПЕРЕНОС ТЕКСТА
# ======================================================

def wrap_text(draw, text, font, max_width):

    words = text.split()

    lines = []

    current = ""

    for word in words:

        test = current + " " + word if current else word

        bbox = draw.textbbox((0, 0), test, font=font)

        width = bbox[2] - bbox[0]

        if width <= max_width:
            current = test
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines

# ======================================================
# ИКОНКИ
# ======================================================

def draw_pin(draw, x, y):

    yellow = (255, 196, 45)

    draw.ellipse((x - 14, y - 24, x + 14, y + 4), fill=yellow)

    draw.polygon(
        [(x - 12, y),
         (x + 12, y),
         (x, y + 28)],
        fill=yellow
    )

def draw_calendar(draw, x, y):

    yellow = (255, 196, 45)

    draw.rounded_rectangle(
        (x - 18, y - 18, x + 18, y + 18),
        radius=4,
        outline=yellow,
        width=4
    )

    draw.line((x - 18, y - 6, x + 18, y - 6), fill=yellow, width=4)

# ======================================================
# КАРТОЧКИ
# ======================================================

def info_card(draw, x, y, title, value, icon="pin"):

    draw.rounded_rectangle(
        (x, y, x + 360, y + 90),
        radius=18,
        fill=(0, 28, 22, 220),
        outline=(120, 180, 100),
        width=2
    )

    if icon == "pin":
        draw_pin(draw, x + 48, y + 40)
    else:
        draw_calendar(draw, x + 48, y + 40)

    draw.line(
        (x + 100, y + 18, x + 100, y + 70),
        fill=(120, 180, 100),
        width=2
    )

    font_small = get_font(20)
    font_big = get_font(28)

    draw.text(
        (x + 125, y + 15),
        title.upper(),
        font=font_small,
        fill=(240, 240, 240)
    )

    draw.text(
        (x + 125, y + 45),
        value.upper(),
        font=font_big,
        fill=(255, 196, 45)
    )

# ======================================================
# ПОСТЕР
# ======================================================

def render_poster(image_bytes, title):

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    img = cover_crop(img)

    img = ImageEnhance.Contrast(img).enhance(1.05)

    img = add_gradient(img)

    draw = ImageDraw.Draw(img)

    draw_decor(draw)

    draw_label(draw, 78, 205, "НОВОСТИ МИНСКА")

    title_font = get_font(66)

    lines = wrap_text(
        draw,
        title.upper(),
        title_font,
        520
    )

    y = 340

    for i, line in enumerate(lines[:4]):

        color = (255, 255, 255)

        if i == 1:
            color = (170, 210, 100)

        draw.text(
            (80 + 3, y + 3),
            line,
            font=title_font,
            fill=(0, 0, 0)
        )

        draw.text(
            (80, y),
            line,
            font=title_font,
            fill=color
        )

        y += 84

    info_card(draw, 76, 1030, "Где", "Минск", "pin")

    info_card(draw, 76, 1140, "Когда", "Скоро", "calendar")

    output = io.BytesIO()

    img.save(output, format="PNG")

    output.seek(0)

    return output

# ======================================================
# START
# ======================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()

    context.user_data["state"] = "waiting_photo"

    await update.message.reply_text(
        "📸 Отправь фото\n"
        "✍️ Потом отправь заголовок\n\n"
        "Я создам афишу."
    )

# ======================================================
# PHOTO
# ======================================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if context.user_data.get("state") != "waiting_photo":
        return

    photo = update.message.photo[-1]

    file = await context.bot.get_file(photo.file_id)

    photo_bytes = await file.download_as_bytearray()

    context.user_data["photo"] = bytes(photo_bytes)

    context.user_data["state"] = "waiting_title"

    await update.message.reply_text(
        "✍️ Теперь отправь заголовок"
    )

# ======================================================
# TEXT
# ======================================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if context.user_data.get("state") != "waiting_title":
        return

    title = update.message.text

    photo = context.user_data.get("photo")

    msg = await update.message.reply_text(
        "🎨 Создаю афишу..."
    )

    try:

        ai_image = await ai_redraw(photo)

        poster = render_poster(ai_image, title)

        await update.message.reply_photo(
            photo=poster
        )

    except Exception as e:

        logger.exception(e)

        await update.message.reply_text(
            f"❌ Ошибка:\n{e}"
        )

    context.user_data.clear()

    context.user_data["state"] = "waiting_photo"

    try:
        await msg.delete()
    except:
        pass

# ======================================================
# MAIN
# ======================================================

def main():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(filters.PHOTO, handle_photo)
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    print("✅ BOT STARTED")

    app.run_polling(drop_pending_updates=True)

# ======================================================

if __name__ == "__main__":

    main()
