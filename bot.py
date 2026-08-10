# -*- coding: utf-8 -*-

import asyncio
import os
import re
import logging
import sys
from io import BytesIO
from typing import Optional

# Устанавливаем requests если его нет
try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONITOR_CHANNEL_ID = os.getenv("MONITOR_CHANNEL_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не настроен!")
if not MONITOR_CHANNEL_ID:
    raise ValueError("❌ MONITOR_CHANNEL_ID не настроен!")
if not ADMIN_CHAT_ID:
    raise ValueError("❌ ADMIN_CHAT_ID не настроен!")

try:
    MONITOR_CHANNEL_ID = int(MONITOR_CHANNEL_ID)
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
except ValueError:
    raise ValueError("❌ MONITOR_CHANNEL_ID и ADMIN_CHAT_ID должны быть числами!")

# ==================== НАСТРОЙКИ ОФОРМЛЕНИЯ ====================
TARGET_W, TARGET_H = 720, 900
CHP_GRADIENT_PCT = 0.48
MN_TITLE_ZONE_PCT = 0.23
TEXT_POSITION_TOP = "top"
TEXT_POSITION_BOTTOM = "bottom"

FONT_CHP = "Montserrat-Black.ttf"
FONT_FALLBACK = "Arial.ttf"

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С ШРИФТАМИ ====================

def download_fonts():
    """Скачивание шрифтов при первом запуске"""
    fonts_urls = {
        "Montserrat-Black.ttf": "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Black.ttf",
        "Arial.ttf": "https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial.ttf",
    }
    
    for font_name, url in fonts_urls.items():
        if not os.path.exists(font_name):
            try:
                logger.info(f"⬇️ Скачивание шрифта {font_name}...")
                response = requests.get(url, timeout=30)
                if response.status_code == 200:
                    with open(font_name, "wb") as f:
                        f.write(response.content)
                    logger.info(f"✅ Шрифт {font_name} скачан")
                else:
                    logger.warning(f"⚠️ Не удалось скачать {font_name}, статус: {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Ошибка скачивания {font_name}: {e}")

def load_font(font_name: str, size: int):
    """Загрузка шрифта с fallback"""
    try:
        return ImageFont.truetype(font_name, size=size)
    except Exception:
        try:
            return ImageFont.truetype(FONT_FALLBACK, size=size)
        except:
            return ImageFont.load_default()

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С ИЗОБРАЖЕНИЯМИ ====================

def crop_to_4x5(img: Image.Image) -> Image.Image:
    """Обрезка до пропорции 4:5"""
    w, h = img.size
    target_ratio = 4 / 5
    cur_ratio = w / h
    if cur_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))

def apply_bottom_gradient(img: Image.Image, height_pct: float, max_alpha: int = 220) -> Image.Image:
    """Применение градиента снизу"""
    w, h = img.size
    gh = int(h * height_pct)
    if gh <= 0:
        return img
    
    overlay_alpha = Image.new("L", (w, h), 0)
    grad = Image.new("L", (1, gh), 0)
    for y in range(gh):
        a = int(max_alpha * (y / max(1, gh - 1)))
        grad.putpixel((0, y), a)
    grad = grad.resize((w, gh))
    overlay_alpha.paste(grad, (0, h - gh))
    
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base = img.convert("RGBA")
    overlay = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
    out = Image.alpha_composite(base, overlay)
    return out.convert("RGB")

def apply_top_gradient(img: Image.Image, height_pct: float, max_alpha: int = 220) -> Image.Image:
    """Применение градиента сверху"""
    w, h = img.size
    gh = int(h * height_pct)
    if gh <= 0:
        return img
    
    overlay_alpha = Image.new("L", (w, h), 0)
    grad = Image.new("L", (1, gh), 0)
    for y in range(gh):
        a = int(max_alpha * (1 - y / max(1, gh - 1)))
        grad.putpixel((0, y), a)
    grad = grad.resize((w, gh))
    overlay_alpha.paste(grad, (0, 0))
    
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base = img.convert("RGBA")
    overlay = Image.composite(black, Image.new("RGBA", (w, h), (0, 0, 0, 0)), overlay_alpha)
    out = Image.alpha_composite(base, overlay)
    return out.convert("RGB")

def text_width(draw, s: str, font) -> int:
    """Ширина текста"""
    try:
        bbox = draw.textbbox((0, 0), s, font=font)
        return bbox[2] - bbox[0]
    except:
        return len(s) * font.size // 2

def wrap_text(draw, text: str, font, max_width: int, max_lines: int = 6):
    """Перенос текста по словам"""
    words = text.split()
    if not words:
        return [""], True
    
    lines = []
    current = words[0]
    for word in words[1:]:
        test = current + " " + word
        if text_width(draw, test, font) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
            if len(lines) >= max_lines:
                return lines, False
    lines.append(current)
    return lines, True

def fit_text_block(draw, text: str, safe_w: int, max_block_h: int,
                   max_lines: int = 6, start_size: int = 90, min_size: int = 16):
    """Подбор размера шрифта для текста"""
    text = (text or "").strip()
    if not text:
        text = " "
    
    size = start_size
    while size >= min_size:
        font = load_font(FONT_CHP, size)
        lines, ok = wrap_text(draw, text, font, safe_w, max_lines=max_lines)
        spacing = int(size * 0.22)
        heights = []
        total_h = 0
        max_w = 0
        for ln in lines:
            try:
                bb = draw.textbbox((0, 0), ln, font=font)
                lw = bb[2] - bb[0]
                lh = bb[3] - bb[1]
            except:
                lw = len(ln) * size // 2
                lh = size
            heights.append(lh)
            total_h += lh
            max_w = max(max_w, lw)
        total_h += spacing * (len(lines) - 1)
        if ok and max_w <= safe_w and total_h <= max_block_h:
            return font, lines, heights, spacing, total_h
        size -= 2
    
    font = load_font(FONT_CHP, min_size)
    lines, _ = wrap_text(draw, text, font, safe_w, max_lines=max_lines)
    spacing = int(min_size * 0.22)
    heights = []
    total_h = 0
    for ln in lines:
        try:
            bb = draw.textbbox((0, 0), ln, font=font)
            lh = bb[3] - bb[1]
        except:
            lh = min_size
        heights.append(lh)
        total_h += lh
    total_h += spacing * (len(lines) - 1)
    return font, lines, heights, spacing, total_h

# ==================== СОЗДАНИЕ КАРТОЧКИ ====================

def make_card_chp(photo_bytes: bytes, title_text: str, text_position: str = TEXT_POSITION_TOP) -> BytesIO:
    """
    Создание карточки в стиле ЧП ВМ
    """
    try:
        # Очищаем заголовок для карточки
        clean_title = re.sub(r'[^\w\s.,!?-]', '', title_text).strip()
        if not clean_title:
            clean_title = "Без заголовка"
        
        logger.info(f"📝 Создание карточки с заголовком: {clean_title[:50]}...")
        
        # Открываем изображение
        img = Image.open(BytesIO(photo_bytes)).convert("RGB")
        
        # Обрезаем до 4:5
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        
        # Улучшаем яркость
        img = ImageEnhance.Brightness(img).enhance(0.85)
        
        # Применяем градиент
        if text_position == TEXT_POSITION_TOP:
            img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        else:
            img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
        draw = ImageDraw.Draw(img)
        margin_x = int(img.width * 0.06)
        margin_bottom = int(img.height * 0.08)
        margin_top = int(img.height * 0.08)
        safe_w = img.width - 2 * margin_x
        title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
        
        text = clean_title.upper()
        
        # Подбираем шрифт
        font, lines, heights, spacing, total_h = fit_text_block(
            draw=draw, text=text, safe_w=safe_w,
            max_block_h=title_max_h, max_lines=6,
            start_size=int(img.height * 0.11), min_size=16
        )
        
        line_height = font.size
        total_text_height = len(lines) * line_height + (len(lines) - 1) * 2
        
        if text_position == TEXT_POSITION_TOP:
            y = margin_top
        else:
            y = img.height - margin_bottom - total_text_height
        
        # Рисуем текст
        for ln in lines:
            draw.text((margin_x, y), ln, font=font, fill="white")
            y += line_height + 2
        
        # Сохраняем результат
        out = BytesIO()
        img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
        out.seek(0)
        
        logger.info(f"✅ Карточка создана, размер: {len(out.getvalue())} байт")
        return out
        
    except Exception as e:
        logger.error(f"❌ Ошибка при создании карточки ЧП ВМ: {e}")
        import traceback
        traceback.print_exc()
        # Возвращаем оригинальное фото
        return BytesIO(photo_bytes)

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С ТЕКСТОМ ====================

def extract_title_from_text(text: str) -> str:
    """Извлечение заголовка из текста (для карточки)"""
    if not text:
        return ""
    
    # Удаляем эмодзи и специальные символы
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\u2600-\u27BF"
        "]+",
        flags=re.UNICODE
    )
    clean_text = emoji_pattern.sub('', text).strip()
    
    # Если есть перенос строки - первая строка заголовок
    if '\n' in clean_text:
        lines = clean_text.split('\n')
        title = lines[0].strip()
        if len(title) > 200:
            title = title[:197] + "..."
        return title
    
    # Если есть точка с пробелом - первое предложение
    if '. ' in clean_text and len(clean_text) > 100:
        parts = clean_text.split('. ', 1)
        title = (parts[0] + '.').strip()
        if len(title) > 200:
            title = title[:197] + "..."
        return title
    
    # Иначе первые 200 символов
    if len(clean_text) > 200:
        return clean_text[:197] + "..."
    return clean_text

def format_text_with_bold_title(text: str) -> str:
    """
    Форматирует текст: заголовок выделяет жирным, остальной текст без изменений
    """
    if not text:
        return ""
    
    # Ищем заголовок (первая строка или первое предложение)
    title = extract_title_from_text(text)
    
    if not title:
        return text
    
    # Экранируем HTML
    import html
    safe_text = html.escape(text)
    safe_title = html.escape(title)
    
    # Заменяем заголовок на жирный
    if safe_title in safe_text:
        formatted = safe_text.replace(safe_title, f"<b>{safe_title}</b>", 1)
        return formatted
    
    # Если не нашли точное совпадение, пробуем найти без учета регистра
    import re
    pattern = re.compile(re.escape(safe_title), re.IGNORECASE)
    if pattern.search(safe_text):
        formatted = pattern.sub(f"<b>{safe_title}</b>", safe_text, 1)
        return formatted
    
    return safe_text

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С МЕДИА ====================

async def download_media(bot: Bot, file_id: str) -> Optional[bytes]:
    """Скачивание медиа-файла"""
    try:
        file = await bot.get_file(file_id)
        return await file.download_as_bytearray()
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания медиа: {e}")
        return None

def get_best_photo(message) -> Optional[str]:
    """Получение file_id лучшего фото"""
    if hasattr(message, 'photo') and message.photo:
        return message.photo[-1].file_id
    return None

# ==================== ОБРАБОТЧИКИ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение"""
    await update.message.reply_text(
        f"👋 <b>Привет! Я бот для мониторинга канала.</b>\n\n"
        f"📢 <b>Отслеживаю канал:</b> <code>{MONITOR_CHANNEL_ID}</code>\n\n"
        f"🎨 <b>При каждом новом посте:</b>\n"
        f"1️⃣ Извлекаю заголовок\n"
        f"2️⃣ Создаю карточку в стиле <b>ЧП ВМ</b>\n"
        f"3️⃣ Отправляю вам готовый пост (фото + текст одним сообщением)\n\n"
        f"🔄 Для проверки статуса используй /status",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса"""
    await update.message.reply_text(
        f"✅ <b>Бот работает и следит за каналом.</b>\n\n"
        f"📢 <b>ID канала:</b> <code>{MONITOR_CHANNEL_ID}</code>\n"
        f"📨 <b>Уведомления приходят сюда:</b> <code>{ADMIN_CHAT_ID}</code>\n"
        f"🟢 <b>Бот активен и обрабатывает ВСЕ новые посты!</b>",
        parse_mode="HTML"
    )

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка ВСЕХ новых постов в канале
    """
    try:
        message = update.channel_post
        if not message:
            return
        
        # Проверяем, что пост из нужного канала
        if message.chat.id != MONITOR_CHANNEL_ID:
            return
        
        logger.info(f"📨 Получен новый пост {message.message_id}")
        
        # Получаем текст из сообщения или подписи
        text = message.text or message.caption or ""
        
        # Если нет текста - отправляем уведомление
        if not text.strip():
            logger.info(f"📷 Пост {message.message_id} без текста")
            
            # Получаем фото
            photo_file_id = get_best_photo(message)
            if photo_file_id:
                photo_bytes = await download_media(context.bot, photo_file_id)
                if photo_bytes:
                    await context.bot.send_photo(
                        chat_id=ADMIN_CHAT_ID,
                        photo=BytesIO(photo_bytes),
                        caption="📷 Пост без текста"
                    )
                    return
            
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text="📷 Пост без текста"
            )
            return
        
        logger.info(f"📝 Текст поста: {text[:100]}...")
        
        # Извлекаем заголовок для карточки
        title = extract_title_from_text(text)
        logger.info(f"📝 Извлечен заголовок: {title[:50]}...")
        
        # Форматируем текст с жирным заголовком (ОРИГИНАЛЬНЫЙ ТЕКСТ)
        formatted_text = format_text_with_bold_title(text)
        
        # Получаем фото
        photo_file_id = get_best_photo(message)
        
        if photo_file_id:
            # Скачиваем фото
            photo_bytes = await download_media(context.bot, photo_file_id)
            
            if photo_bytes:
                try:
                    # Создаем карточку ЧП ВМ
                    card_bytes = make_card_chp(photo_bytes, title)
                    card_data = card_bytes.getvalue()
                    
                    # Отправляем ОДНИМ сообщением: фото + оригинальный текст в подписи
                    await context.bot.send_photo(
                        chat_id=ADMIN_CHAT_ID,
                        photo=BytesIO(card_data),
                        caption=formatted_text[:1024],  # Telegram лимит 1024 символа
                        parse_mode="HTML"
                    )
                    logger.info(f"✅ Отправлена карточка с текстом для поста {message.message_id}")
                    
                    # Если текст больше 1024 символов, отправляем остаток отдельным сообщением
                    if len(formatted_text) > 1024:
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=formatted_text[1024:],
                            parse_mode="HTML"
                        )
                        logger.info(f"📝 Отправлено продолжение текста")
                    
                    # Если есть видео - отправляем отдельно
                    if hasattr(message, 'video') and message.video:
                        try:
                            await context.bot.send_video(
                                chat_id=ADMIN_CHAT_ID,
                                video=message.video.file_id
                            )
                            logger.info(f"🎬 Отправлено видео")
                        except Exception as e:
                            logger.error(f"❌ Ошибка отправки видео: {e}")
                    
                    return
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка создания/отправки карточки: {e}")
                    import traceback
                    traceback.print_exc()
                    
                    # Отправляем оригинальное фото с текстом
                    await context.bot.send_photo(
                        chat_id=ADMIN_CHAT_ID,
                        photo=BytesIO(photo_bytes),
                        caption=formatted_text[:1024],
                        parse_mode="HTML"
                    )
                    
                    if len(formatted_text) > 1024:
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=formatted_text[1024:],
                            parse_mode="HTML"
                        )
                    
                    # Отправляем видео если есть
                    if hasattr(message, 'video') and message.video:
                        try:
                            await context.bot.send_video(
                                chat_id=ADMIN_CHAT_ID,
                                video=message.video.file_id
                            )
                        except Exception as e:
                            logger.error(f"❌ Ошибка отправки видео: {e}")
                    
                    return
        
        # Если нет фото - отправляем только текст
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=formatted_text,
            parse_mode="HTML"
        )
        
        # Если есть видео - отправляем отдельно
        if hasattr(message, 'video') and message.video:
            try:
                await context.bot.send_video(
                    chat_id=ADMIN_CHAT_ID,
                    video=message.video.file_id
                )
                logger.info(f"🎬 Отправлено видео")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки видео: {e}")
        
        logger.info(f"📝 Отправлен только текст (нет фото)")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке поста: {e}")
        import traceback
        traceback.print_exc()

# ==================== ЗАПУСК ====================

async def main():
    """Запуск бота"""
    logger.info("🚀 Бот запускается...")
    
    # Скачиваем шрифты
    download_fonts()
    
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        bot = Bot(token=BOT_TOKEN)
        
        # Проверяем подключение к каналу
        try:
            channel = await bot.get_chat(MONITOR_CHANNEL_ID)
            logger.info(f"✅ Подключен к каналу: {channel.title} (ID: {channel.id})")
        except Exception as e:
            logger.error(f"❌ Не удалось подключиться к каналу: {e}")
            logger.error("❌ Проверьте: бот добавлен в канал как администратор")
            return
        
        # Проверяем админа
        try:
            admin = await bot.get_chat(ADMIN_CHAT_ID)
            logger.info(f"✅ Уведомления будут приходить: {admin.first_name} (ID: {admin.id})")
        except Exception as e:
            logger.error(f"❌ Не удалось подключиться к админу: {e}")
            return
        
        # Регистрируем обработчики
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("status", status))
        app.add_handler(MessageHandler(
            filters.ALL & filters.Chat(chat_id=MONITOR_CHANNEL_ID),
            handle_channel_post
        ))
        
        logger.info("✅ Обработчики зарегистрированы")
        logger.info("📊 Мониторинг запущен!")
        logger.info("📌 Бот обрабатывает ВСЕ новые посты в канале")
        logger.info("📸 Фото и текст отправляются ОДНИМ сообщением")
        
        # Запускаем бота
        await app.initialize()
        await app.start()
        
        await app.updater.start_polling(
            allowed_updates=["message", "channel_post", "callback_query"],
            drop_pending_updates=True,
            poll_interval=1.0,
            timeout=30
        )
        
        logger.info("🟢 Бот успешно запущен и работает!")
        
        # Держим бота запущенным
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)
