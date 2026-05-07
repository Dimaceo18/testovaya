# bot.py
import os
import io
import re
import base64
import logging
from typing import Optional, Tuple, List

from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
from openai import AsyncOpenAI

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

OUT_W, OUT_H = 1080, 1350

COLOR_DARK = (5, 42, 31)
COLOR_DARK_2 = (2, 20, 17)
COLOR_YELLOW = (255, 196, 45)
COLOR_GREEN = (160, 195, 115)
COLOR_CREAM = (244, 239, 224)
COLOR_WHITE = (255, 255, 255)

STYLE_PROMPT = """
Edit the provided city/news photo into a premium vertical editorial background.

Create a realistic cinematic city media poster background.
Do not add any text, letters, logos, signs, words, captions, numbers or typography.

Visual style:
- premium Belarus city media
- Apple-style clean editorial
- dark emerald cinematic overlay on the left
- realistic lighting
- natural photo realism
- atmospheric, modern, elegant
- warm yellow-green accent mood
- clear right side with the original photo preserved
- subtle depth, shadows, contrast
- not cartoon, not illustration, not cheap banner

Composition:
- vertical poster background
- left area darker for text placement
- right area keeps the main photo subject visible
- modern urban magazine look
"""


def only_admin(update: Update) -> bool:
    if ADMIN_ID == 0:
        return True
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


def get_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    paths = [
        name,
        f"./fonts/{name}",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def cover_crop(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
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


def split_title(title: str) -> Tuple[str, str]:
    words = title.strip().split()
    if len(words) <= 4:
        return title.upper(), ""

    # акцентируем самую важную середину/конец
    accent_len = min(5, max(2, len(words) // 2))
    regular = " ".join(words[:-accent_len]).upper()
    accent = " ".join(words[-accent_len:]).upper()
    return regular, accent


def draw_text_with_shadow(draw, xy, text, font, fill, shadow=(0, 0, 0), offset=3):
    x, y = xy
    draw.text((x + offset, y + offset), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def draw_round_box(draw, xy, radius, outline, fill):
    draw.rounded_rectangle(xy, radius=radius, outline=outline, width=2, fill=fill)


def add_left_gradient(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # плотный градиент слева
    grad_w = int(w * 0.72)
    for x in range(grad_w):
        t = x / grad_w
        alpha = int(235 * (1 - t) ** 1.45)
        d.line([(x, 0), (x, h)], fill=(0, 28, 20, alpha), width=1)

    # затемнение сверху и снизу
    for y in range(h):
        top_alpha = int(95 * max(0, 1 - y / 420))
        bottom_alpha = int(120 * max(0, (y - h * 0.58) / (h * 0.42)))
        a = max(top_alpha, bottom_alpha)
        if a > 0:
            d.line([(0, y), (w, y)], fill=(0, 15, 13, a), width=1)

    return Image.alpha_composite(img, overlay).convert("RGB")


def add_decor(draw: ImageDraw.ImageDraw):
    # линии сверху слева
    for i in range(4):
        y = 58 + i * 22
        draw.arc((-95 + i * 22, -130 + i * 16, 260 + i * 22, 210 + i * 16),
                 18, 118, fill=(135, 176, 91), width=3)

    # большая мягкая дуга снизу
    for i in range(2):
        draw.arc((-300 + i * 38, 930 + i * 22, 360 + i * 38, 1580 + i * 22),
                 205, 25, fill=(48, 92, 54), width=55)

    # точки
    dot_x, dot_y = 68, 1130
    for row in range(3):
        for col in range(2):
            draw.ellipse(
                (dot_x + col * 34, dot_y + row * 34, dot_x + col * 34 + 7, dot_y + row * 34 + 7),
                fill=(154, 196, 92),
            )


def draw_label(draw, x, y, text):
    font = get_font("Montserrat-Bold.ttf", 22)
    pad_x, pad_y = 18, 10
    bbox = draw.textbbox((0, 0), text, font=font)
    bw = bbox[2] - bbox[0] + pad_x * 2
    bh = bbox[3] - bbox[1] + pad_y * 2

    draw.rounded_rectangle((x, y, x + bw, y + bh), radius=18, fill=COLOR_YELLOW)
    draw.text((x + pad_x, y + pad_y - 2), text, font=font, fill=(10, 22, 18))


def draw_info_card(draw, x, y, icon, title, value):
    box_w, box_h = 330, 82
    draw.rounded_rectangle(
        (x, y, x + box_w, y + box_h),
        radius=18,
        outline=(120, 135, 120),
        width=2,
        fill=(0, 20, 18),
    )

    icon_font = get_font("DejaVuSans-Bold.ttf", 36)
    small = get_font("Montserrat-Bold.ttf", 20)
    value_font = get_font("Montserrat-Bold.ttf", 25)

    draw.text((x + 22, y + 20), icon, font=icon_font, fill=COLOR_YELLOW)
    draw.line((x + 86, y + 18, x + 86, y + box_h - 18), fill=COLOR_YELLOW, width=2)
    draw.text((x + 112, y + 18), title.upper(), font=small, fill=COLOR_CREAM)
    draw.text((x + 112, y + 44), value.upper(), font=value_font, fill=COLOR_YELLOW)


def render_poster(background_bytes: bytes, title: str, description: str = "") -> io.BytesIO:
    img = Image.open(io.BytesIO(background_bytes)).convert("RGB")
    img = cover_crop(img, (OUT_W, OUT_H))

    # базовое улучшение фото
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Color(img).enhance(0.92)
    img = ImageEnhance.Sharpness(img).enhance(1.15)

    img = add_left_gradient(img)
    draw = ImageDraw.Draw(img)

    add_decor(draw)
    draw_label(draw, 78, 215, "НОВОСТИ МИНСКА")

    title_font = get_font("Montserrat-Bold.ttf", 62)
    title_font_accent = get_font("Montserrat-Bold.ttf", 66)
    desc_font = get_font("Montserrat-Regular.ttf", 30)

    x = 78
    y = 325
    max_width = 640

    regular, accent = split_title(title)

    regular_lines = wrap_text(draw, regular, title_font, max_width)
    accent_lines = wrap_text(draw, accent, title_font_accent, max_width)

    for line in regular_lines[:3]:
        draw_text_with_shadow(draw, (x, y), line, title_font, COLOR_WHITE, offset=2)
        y += 72

    if accent_lines:
        y += 4
        for line in accent_lines[:3]:
            draw_text_with_shadow(draw, (x, y), line, title_font_accent, COLOR_YELLOW, offset=2)
            y += 76

    if description and description.strip() != "-":
        y += 30
        desc_lines = wrap_text(draw, description.strip(), desc_font, 460)
        for line in desc_lines[:4]:
            draw.text((x, y), line, font=desc_font, fill=COLOR_CREAM)
            y += 42

    # инфо-плашки снизу
    draw_info_card(draw, 64, 1120, "📍", "Где", "Минск")
    draw_info_card(draw, 64, 1220, "🗓", "Когда", "Скоро")

    out = io.BytesIO()
    img.save(out, format="PNG", quality=95)
    out.seek(0)
    return out


async def gpt_style_background(photo_bytes: bytes, title: str) -> Optional[bytes]:
    if not client:
        return None

    try:
        base_img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
        base_img = cover_crop(base_img, (1024, 1536))

        image_file = io.BytesIO()
        base_img.save(image_file, format="PNG")
        image_file.seek(0)
        image_file.name = "input.png"

        prompt = STYLE_PROMPT + f"\nNews topic: {title}"

        response = await client.images.edit(
            model="gpt-image-1",
            image=image_file,
            prompt=prompt,
            size="1024x1536",
            quality="medium",
        )

        data = response.data[0]

        if getattr(data, "b64_json", None):
            return base64.b64decode(data.b64_json)

        if getattr(data, "url", None):
            import httpx
            async with httpx.AsyncClient(timeout=90) as http:
                r = await http.get(data.url)
                r.raise_for_status()
                return r.content

    except Exception as e:
        logger.exception(f"OpenAI image edit failed: {e}")

    return None


def fallback_background(photo_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    img = cover_crop(img, (OUT_W, OUT_H))
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(0.9)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=115, threshold=3))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def create_afisha(photo_bytes: bytes, title: str, description: str = "") -> io.BytesIO:
    styled = await gpt_style_background(photo_bytes, title)

    if not styled:
        styled = fallback_background(photo_bytes)

    return render_poster(styled, title, description)


def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Создать афишу", callback_data="create")],
        [InlineKeyboardButton("🔄 Сбросить", callback_data="reset")],
    ])


def result_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Сделать новую", callback_data="create")],
        [InlineKeyboardButton("🏠 Меню", callback_data="menu")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not only_admin(update):
        return

    context.user_data.clear()
    await update.message.reply_text(
        "🎨 Бот для афиш «Афиша Минска»\n\n"
        "Нажми «Создать афишу», отправь фото, потом заголовок и описание.",
        reply_markup=main_keyboard()
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not only_admin(update):
        return

    q = update.callback_query
    await q.answer()

    if q.data == "create":
        context.user_data.clear()
        context.user_data["state"] = "wait_photo"
        await q.message.reply_text("📸 Отправь фото для афиши.")

    elif q.data == "reset":
        context.user_data.clear()
        await q.message.reply_text("Сброшено.", reply_markup=main_keyboard())

    elif q.data == "menu":
        context.user_data.clear()
        await q.message.reply_text("Главное меню:", reply_markup=main_keyboard())


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not only_admin(update):
        return

    if context.user_data.get("state") != "wait_photo":
        await update.message.reply_text("Нажми «Создать афишу» и потом отправь фото.", reply_markup=main_keyboard())
        return

    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    photo_bytes = await tg_file.download_as_bytearray()

    context.user_data["photo"] = bytes(photo_bytes)
    context.user_data["state"] = "wait_title"

    await update.message.reply_text("✅ Фото получил. Теперь отправь заголовок.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not only_admin(update):
        return

    state = context.user_data.get("state")
    text = update.message.text.strip()

    if state == "wait_title":
        context.user_data["title"] = text
        context.user_data["state"] = "wait_desc"
        await update.message.reply_text("Теперь отправь короткое описание или просто «-», если описание не нужно.")
        return

    if state == "wait_desc":
        description = "" if text == "-" else text
        photo = context.user_data.get("photo")
        title = context.user_data.get("title")

        if not photo or not title:
            context.user_data.clear()
            await update.message.reply_text("Что-то потерялось. Начни заново.", reply_markup=main_keyboard())
            return

        msg = await update.message.reply_text("🎛️ Делаю афишу в фирменном стиле...")

        try:
            poster = await create_afisha(photo, title, description)
            context.user_data.clear()

            await update.message.reply_photo(
                photo=poster,
                caption="Готово ✨",
                reply_markup=result_keyboard()
            )
            await msg.delete()

        except Exception as e:
            logger.exception(e)
            await msg.edit_text(f"❌ Ошибка при создании афиши: {e}")

        return

    await update.message.reply_text("Нажми «Создать афишу».", reply_markup=main_keyboard())


def run():
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN")
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY не указан. Бот будет работать без GPT-стилизации, только Pillow.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
