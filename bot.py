# -*- coding: utf-8 -*-

import os
import io
import base64
import logging
from pathlib import Path

import httpx
from openai import AsyncOpenAI
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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1")

STYLE_REFERENCE_PATH = Path("style_reference.png")

if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN")

if not OPENAI_API_KEY:
    raise RuntimeError("Нет OPENAI_API_KEY")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)


def make_prompt(user_text: str) -> str:
    return f"""
Создай вертикальный Instagram-баннер 4:5 для городского медиа «Афиша Минска».

У тебя есть два изображения:
1. Первое изображение — основное фото, которое нужно использовать для баннера.
2. Второе изображение — референс стиля. Его нужно использовать только как пример оформления.

Повтори стиль второго изображения максимально близко:
- тёмно-зелёный полупрозрачный градиент слева
- атмосферная фотография справа
- крупный кремовый заголовок слева
- ключевую фразу выделить светло-зелёным
- мягкие скругления углов
- тонкие светло-зелёные декоративные линии сверху слева
- большая полупрозрачная зелёная дуга снизу слева
- маленькие декоративные точки снизу слева
- современный premium city media стиль
- минимализм
- много воздуха
- чистая типографика
- дорогой Instagram-пост
- стиль современного городского медиа 2026 года

Важно:
- не добавляй fider.by
- не добавляй лишние логотипы
- не добавляй рамки по краям
- не делай винтажный плакат
- не делай открытку
- не делай дешёвый рекламный баннер
- не добавляй объекты, которых нет на фото, если они не нужны по смыслу
- сохрани главный объект/человека/место с первого изображения
- текст должен быть на русском языке
- текст должен быть крупным и читаемым
- итог должен быть максимально похож на референс оформления

Текст для баннера:
«{user_text}»
"""


async def generate_banner(photo_bytes: bytes, user_text: str) -> bytes:
    if not STYLE_REFERENCE_PATH.exists():
        raise RuntimeError(
            "Не найден файл assets/style_reference.png. "
            "Положи туда эталонный баннер стиля."
        )

    main_image = io.BytesIO(photo_bytes)
    main_image.name = "main_photo.jpg"

    style_image = open(STYLE_REFERENCE_PATH, "rb")

    try:
        response = await client.images.edit(
            model=IMAGE_MODEL,
            image=[main_image, style_image],
            prompt=make_prompt(user_text),
            size="1024x1536",
        )
    finally:
        style_image.close()

    item = response.data[0]

    if getattr(item, "b64_json", None):
        return base64.b64decode(item.b64_json)

    if getattr(item, "url", None):
        async with httpx.AsyncClient(timeout=180) as http:
            r = await http.get(item.url)
            r.raise_for_status()
            return r.content

    raise RuntimeError("OpenAI не вернул изображение")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["state"] = "waiting_photo"

    await update.message.reply_text(
        "🎨 Бот Афиша Минска\n\n"
        "1. Отправь фото\n"
        "2. Потом отправь текст новости\n"
        "3. Я сделаю баннер по сохранённому стилю"
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_photo":
        await update.message.reply_text("Нажми /start и отправь фото.")
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()

    context.user_data["photo"] = bytes(photo_bytes)
    context.user_data["state"] = "waiting_text"

    await update.message.reply_text("✅ Фото получил. Теперь отправь текст для баннера.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_photo":
        await update.message.reply_text("Нажми /start и отправь фото.")
        return

    doc = update.message.document

    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Нужен файл изображения.")
        return

    file = await context.bot.get_file(doc.file_id)
    photo_bytes = await file.download_as_bytearray()

    context.user_data["photo"] = bytes(photo_bytes)
    context.user_data["state"] = "waiting_text"

    await update.message.reply_text("✅ Фото получил в хорошем качестве. Теперь отправь текст.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != "waiting_text":
        await update.message.reply_text("Нажми /start и отправь фото.")
        return

    user_text = update.message.text.strip()
    photo = context.user_data.get("photo")

    if not photo:
        context.user_data.clear()
        context.user_data["state"] = "waiting_photo"
        await update.message.reply_text("Фото потерялось. Нажми /start и начни заново.")
        return

    msg = await update.message.reply_text("🎛️ Генерирую баннер по фирменному стилю...")

    try:
        result = await generate_banner(photo, user_text)

        output = io.BytesIO(result)
        output.name = "afisha_minska.png"

        await update.message.reply_photo(
            photo=output,
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


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ BOT STARTED")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
