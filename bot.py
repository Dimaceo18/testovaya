# -*- coding: utf-8 -*-
import os
import io
import base64
import logging

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from openai import AsyncOpenAI

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters


BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN")
if not OPENAI_API_KEY:
    raise RuntimeError("Нет OPENAI_API_KEY")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OUT_W, OUT_H = 1080, 1350


STYLE_PROMPT = """
Transform the uploaded photo into a premium cinematic editorial image for a modern city media Instagram poster.

Important:
Do not add text, letters, words, typography, logos, fake signs, captions or numbers.

Style:
- premium city media aesthetic
- cinematic realistic photography
- Apple-style editorial look
- dark emerald and deep green mood
- warm yellow highlights
- glossy cinematic light
- realistic shadows
- realistic people and faces
- soft bokeh
- rich contrast
- modern urban atmosphere
- Belarus / Minsk city media feeling
- premium magazine quality

Keep:
- same main subject
- same pose
- same general framing
- same location feeling

Avoid:
- cartoon
- illustration
- cheap banner design
- distorted face
- broken hands
- artificial text
"""


def get_font(size: int, bold: bool = True):
    paths = [
        "./fonts/Montserrat-Bold.ttf" if bold else "./fonts/Montserrat-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def cover_crop(img: Image.Image, size=(OUT_W, OUT_H)):
    tw, th = size
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


async def ai_redraw(photo_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    img = cover_crop(img, (1024, 1536))

    image_file = io.BytesIO()
    img.save(image_file, format="PNG")
    image_file.seek(0)
    image_file.name = "input.png"

    response = await client.images.edit(
        model="gpt-image-1",
        image=image_file,
        prompt=STYLE_PROMPT,
        size="1024x1536",
    )

    b64 = response.data[0].b64_json
    return base64.b64decode(b64)


def add_gradient(img: Image.Image):
    img = img.convert("RGBA")
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    grad_w = int(w * 0.68)
    for x in range(grad_w):
        t = x / grad_w
        alpha = int(230 * (1 - t) ** 1.65)
        draw.line([(x, 0), (x, h)], fill=(0, 34, 27, alpha), width=1)

    for y in range(h):
        if y > h * 0.58:
            t = (y - h * 0.58) / (h * 0.42)
            alpha = int(120 * t)
            draw.line([(0, y), (w, y)], fill=(0, 28, 22, alpha), width=1)

    return Image.alpha_composite(img, overlay)


def draw_decor(draw: ImageDraw.ImageDraw):
    green = (136, 190, 104)

    for i in range(5):
        draw.arc(
            (-130 + i * 25, -135 + i * 18, 310 + i * 25, 250 + i * 18),
            18, 120,
            fill=green,
            width=3,
        )

    draw.arc(
        (-365, 925, 360, 1640),
        205, 25,
        fill=(48, 92, 54),
        width=58,
    )

    for r in range(3):
        for c in range(3):
            x = 76 + c * 34
            y = 1230 + r * 34
            draw.ellipse((x, y, x + 7, y + 7), fill=(160, 205, 105))


def draw_label(draw, x, y, text):
    font = get_font(22, True)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    draw.rounded_rectangle(
        (x, y, x + tw + 40, y + th + 22),
        radius=18,
        fill=(255, 202, 54),
    )
    draw.text((x + 20, y + 9), text, font=font, fill=(0, 28, 22))


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        width = bbox[2] - bbox[0]

        if width <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def draw_shadow_text(draw, x, y, text, font, fill):
    draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 150))
    draw.text((x, y), text, font=font, fill=fill)


def draw_pin(draw, x, y):
    yellow = (255, 196, 45)
    draw.ellipse((x - 15, y - 24, x + 15, y + 6), fill=yellow)
    draw.polygon([(x - 12, y), (x + 12, y), (x, y + 30)], fill=yellow)
    draw.ellipse((x - 5, y - 14, x + 5, y - 4), fill=(0, 35, 28))


def draw_calendar(draw, x, y):
    yellow = (255, 196, 45)
    draw.rounded_rectangle((x - 20, y - 20, x + 20, y + 22), radius=5, outline=yellow, width=4)
    draw.line((x - 20, y - 7, x + 20, y - 7), fill=yellow, width=4)
    draw.line((x - 10, y - 27, x - 10, y - 15), fill=yellow, width=4)
    draw.line((x + 10, y - 27, x + 10, y - 15), fill=yellow, width=4)


def info_card(draw, x, y, title, value, icon="pin"):
    draw.rounded_rectangle(
        (x, y, x + 365, y + 90),
        radius=18,
        fill=(0, 28, 22, 220),
        outline=(120, 180, 100),
        width=2,
    )

    if icon == "pin":
        draw_pin(draw, x + 48, y + 42)
    else:
        draw_calendar(draw, x + 48, y + 42)

    draw.line((x + 100, y + 18, x + 100, y + 72), fill=(120, 180, 100), width=2)

    small = get_font(20, True)
    big = get_font(28, True)

    draw.text((x + 125, y + 15), title.upper(), font=small, fill=(240, 240, 240))
    draw.text((x + 125, y + 45), value.upper(), font=big, fill=(255, 196, 45))


def render_poster(image_bytes: bytes, title: str):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = cover_crop(img, (OUT_W, OUT_H))

    img = ImageEnhance.Contrast(img).enhance(1.06)
    img = ImageEnhance.Color(img).enhance(1.03)

    img = add_gradient(img)
    draw = ImageDraw.Draw(img)

    draw_decor(draw)
    draw_label(draw, 78, 210, "НОВОСТИ МИНСКА")

    title_font = get_font(64, True)
    lines = wrap_text(draw, title.upper(), title_font, 560)

    y = 340

    for i, line in enumerate(lines[:5]):
        if i == 1:
            color = (160, 205, 105)
        elif i == 2:
            color = (255, 196, 45)
        else:
            color = (255, 255, 255)

        draw_shadow_text(draw, 80, y, line, title_font, color)
        y += 76

    info_card(draw, 76, 1030, "Где", "Минск", "pin")
    info_card(draw, 76, 1140, "Когда", "Скоро", "calendar")

    output = io.BytesIO()
    img.convert("RGB").save(output, format="PNG")
    output.seek(0)
    return output


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "waiting_photo"

    await update.message.reply_text(
        "🎨 Бот Афиша Минска\n\n"
        "1. Отправь фото\n"
        "2. Потом отправь заголовок\n"
        "3. Я сделаю AI-перерисовку и оформление"
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_photo":
        await update.message.reply_text("Нажми /start и отправь фото.")
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()

    context.user_data["photo"] = bytes(photo_bytes)
    context.user_data["state"] = "waiting_title"

    await update.message.reply_text("✅ Фото получил. Теперь отправь заголовок.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_title":
        await update.message.reply_text("Нажми /start и отправь фото.")
        return

    title = update.message.text.strip()
    photo = context.user_data.get("photo")

    if not photo:
        context.user_data.clear()
        context.user_data["state"] = "waiting_photo"
        await update.message.reply_text("Фото потерялось. Нажми /start и начни заново.")
        return

    msg = await update.message.reply_text("🎛️ Делаю AI-перерисовку и оформление...")

    try:
        ai_image = await ai_redraw(photo)
        poster = render_poster(ai_image, title)

        await update.message.reply_photo(photo=poster, caption="Готово ✨")

    except Exception as e:
        logger.exception(e)
        await update.message.reply_text(f"❌ Ошибка:\n{e}")

    context.user_data.clear()
    context.user_data["state"] = "waiting_photo"

    try:
        await msg.delete()
    except Exception:
        pass


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ BOT STARTED")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
