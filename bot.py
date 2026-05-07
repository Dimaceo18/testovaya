# -*- coding: utf-8 -*-

import os
import io
import base64
import logging

import httpx
from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters


BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1")

if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN")

if not OPENAI_API_KEY:
    raise RuntimeError("Нет OPENAI_API_KEY")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def make_prompt(user_text: str) -> str:
    return f"""
Сделай современный вертикальный баннер 4:5 для городского медиа «Афиша Минска».

Используй загруженную фотографию как основу.
Можно художественно переработать фото, но сохранить общий смысл, человека/объект, атмосферу и композицию.

Стиль:
- премиальный городской медиа-дизайн
- dark emerald / deep green
- жёлто-зелёные акценты
- Apple-style editorial
- cinematic
- clean typography
- современные линии
- узоры
- иконки
- плашки
- аккуратная сетка
- дорогой Instagram-пост
- стиль современного медиа 2026 года

Добавь на баннер текст:
«{user_text}»

Важно:
- текст должен быть на русском языке
- крупный читаемый заголовок слева
- выдели ключевые слова жёлтым или зелёным
- добавь декоративные линии, значки места/даты/погоды, если подходят по смыслу
- не добавляй fider.by
- не добавляй лишние логотипы
- не делай дешёвый рекламный баннер
- не делай мультяшный стиль
- итог должен выглядеть как готовый пост современного городского медиа
"""


async def generate_banner(photo_bytes: bytes, user_text: str) -> bytes:
    image_file = io.BytesIO(photo_bytes)
    image_file.name = "input.jpg"

    response = await client.images.edit(
        model=IMAGE_MODEL,
        image=image_file,
        prompt=make_prompt(user_text),
        size="1024x1536",
    )

    item = response.data[0]

    if getattr(item, "b64_json", None):
        return base64.b64decode(item.b64_json)

    if getattr(item, "url", None):
        async with httpx.AsyncClient(timeout=120) as http:
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
        "2. Потом отправь текст/заголовок\n"
        "3. Я сделаю баннер через OpenAI Image API"
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

    msg = await update.message.reply_text("🎛️ Генерирую баннер в стиле Афиша Минска...")

    try:
        result = await generate_banner(photo, user_text)

        await update.message.reply_photo(
            photo=io.BytesIO(result),
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ BOT STARTED")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
