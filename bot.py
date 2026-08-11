# -*- coding: utf-8 -*-

import asyncio  # <-- ДОБАВЛЯЕМ ЭТОТ ИМПОРТ
import os
import re
import logging
import sys
import tempfile
from io import BytesIO
from typing import Optional

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

try:
    from moviepy import VideoFileClip
except ImportError:
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip
    except:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "moviepy==1.0.3"])
        from moviepy import VideoFileClip

try:
    import numpy as np
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "numpy"])
    import numpy as np

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==================== НАСТРОЙКИ (как в ЧП ВМ) ====================
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

# НАСТРОЙКИ 1:1 КАК В ШАБЛОНЕ "ЧП ВМ"
TARGET_W, TARGET_H = 720, 900
CHP_GRADIENT_PCT = 0.48          # Градиент 48% от высоты
MN_TITLE_ZONE_PCT = 0.23         # Максимальная высота текста 23%
BRIGHTNESS_FACTOR = 0.85         # Яркость как в ЧП ВМ
FONT_CHP = "Montserrat-Black.ttf"
FONT_FALLBACK = "Arial.ttf"

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== ФУНКЦИИ (1:1 из ЧП ВМ) ====================

def download_fonts():
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
            except Exception as e:
                logger.error(f"❌ Ошибка скачивания {font_name}: {e}")

def load_font(font_name: str, size: int):
    try:
        return ImageFont.truetype(font_name, size=size)
    except Exception:
        try:
            return ImageFont.truetype(FONT_FALLBACK, size=size)
        except:
            return ImageFont.load_default()

def crop_to_4x5(img: Image.Image) -> Image.Image:
    """Обрезка до пропорции 4:5 (как в ЧП ВМ)"""
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
    """Градиент снизу (как в ЧП ВМ)"""
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
    """Градиент сверху (как в ЧП ВМ)"""
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
    try:
        bbox = draw.textbbox((0, 0), s, font=font)
        return bbox[2] - bbox[0]
    except:
        return len(s) * font.size // 2

def wrap_text(draw, text: str, font, max_width: int, max_lines: int = 6):
    """Перенос текста по словам (как в ЧП ВМ)"""
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
    """Подбор размера шрифта (как в ЧП ВМ)"""
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

def clean_title_for_card(title: str) -> str:
    """Очистка заголовка от эмодзи (как в ЧП ВМ)"""
    if not title:
        return ""
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
    clean = emoji_pattern.sub('', title)
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()

# ==================== ОБРАБОТКА ВИДЕО (КАК В ЧП ВМ) ====================

def process_video_frame(frame: np.ndarray, title_text: str, text_position: str = "bottom") -> np.ndarray:
    """
    Обработка кадра видео с настройками 1:1 как в ЧП ВМ
    """
    try:
        # Конвертируем в PIL Image
        img = Image.fromarray(frame).convert("RGB")
        
        # 1. Обрезка до 4:5 (как в ЧП ВМ)
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        
        # 2. Яркость 0.85 (как в ЧП ВМ)
        img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
        
        # 3. Градиент 48% (как в ЧП ВМ)
        if text_position == "top":
            img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        else:
            img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
        # 4. Текст (как в ЧП ВМ)
        draw = ImageDraw.Draw(img)
        margin_x = int(img.width * 0.06)
        margin_bottom = int(img.height * 0.08)
        margin_top = int(img.height * 0.08)
        safe_w = img.width - 2 * margin_x
        title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
        
        clean_title = clean_title_for_card(title_text)
        text = (clean_title or "").strip().upper()
        
        font, lines, heights, spacing, total_h = fit_text_block(
            draw=draw, text=text, safe_w=safe_w,
            max_block_h=title_max_h, max_lines=6,
            start_size=int(img.height * 0.11), min_size=16
        )
        
        line_height = font.size
        total_text_height = len(lines) * line_height + (len(lines) - 1) * 2
        
        if text_position == "top":
            y = margin_top
        else:
            y = img.height - margin_bottom - total_text_height
        
        for ln in lines:
            draw.text((margin_x, y), ln, font=font, fill="white")
            y += line_height + 2
        
        # Конвертируем обратно в numpy array
        return np.array(img)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки кадра: {e}")
        return frame

def process_video(video_bytes: bytes, title_text: str, text_position: str = "bottom") -> BytesIO:
    """
    Обработка видео с настройками 1:1 как в ЧП ВМ
    """
    temp_input = None
    temp_output = None
    
    try:
        # Сохраняем входное видео
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            f.write(video_bytes)
            temp_input = f.name
        
        # Создаём выходной файл
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            temp_output = f.name
        
        logger.info(f"📹 Загрузка видео для обработки (ЧП ВМ)...")
        video = VideoFileClip(temp_input)
        
        logger.info(f"🎬 Обработка кадров видео (ЧП ВМ)...")
        
        def process_frame(frame):
            return process_video_frame(frame, title_text, text_position)
        
        processed_video = video.fl_image(process_frame)
        
        logger.info(f"💾 Сохранение обработанного видео...")
        processed_video.write_videofile(
            temp_output,
            codec='libx264',
            audio_codec='aac',
            fps=video.fps,
            bitrate='2000k',
            threads=4
        )
        
        video.close()
        processed_video.close()
        
        with open(temp_output, 'rb') as f:
            result_bytes = f.read()
        
        logger.info(f"✅ Видео обработано (ЧП ВМ), размер: {len(result_bytes) / (1024*1024):.2f} MB")
        
        output = BytesIO()
        output.write(result_bytes)
        output.seek(0)
        return output
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке видео: {e}")
        import traceback
        traceback.print_exc()
        return BytesIO(video_bytes)
    
    finally:
        try:
            if temp_input and os.path.exists(temp_input):
                os.unlink(temp_input)
            if temp_output and os.path.exists(temp_output):
                os.unlink(temp_output)
        except:
            pass

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С ТЕКСТОМ ====================

def extract_title_from_text(text: str) -> str:
    """Извлечение заголовка из текста"""
    if not text:
        return ""
    
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
    
    if '\n' in clean_text:
        lines = clean_text.split('\n')
        title = lines[0].strip()
        if len(title) > 200:
            title = title[:197] + "..."
        return title
    
    if '. ' in clean_text and len(clean_text) > 100:
        parts = clean_text.split('. ', 1)
        title = (parts[0] + '.').strip()
        if len(title) > 200:
            title = title[:197] + "..."
        return title
    
    if len(clean_text) > 200:
        return clean_text[:197] + "..."
    return clean_text

def format_text_with_bold_title(text: str) -> str:
    """Форматирует текст с жирным заголовком"""
    if not text:
        return ""
    
    title = extract_title_from_text(text)
    if not title:
        return text
    
    import html
    safe_text = html.escape(text)
    safe_title = html.escape(title)
    
    if safe_title in safe_text:
        formatted = safe_text.replace(safe_title, f"<b>{safe_title}</b>", 1)
        return formatted
    
    return safe_text

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С МЕДИА ====================

async def download_media(bot: Bot, file_id: str) -> Optional[bytes]:
    try:
        file = await bot.get_file(file_id)
        return await file.download_as_bytearray()
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания медиа: {e}")
        return None

def get_text_from_message(message) -> str:
    return message.text or message.caption or ""

# ==================== ОБРАБОТКА ПОСТА ====================

async def process_video_post(message, context: ContextTypes.DEFAULT_TYPE, source: str = "канал"):
    """Обработка поста с видео (ЧП ВМ)"""
    try:
        if not hasattr(message, 'video') or not message.video:
            return
        
        text = get_text_from_message(message)
        
        if not text.strip():
            await context.bot.send_video(
                chat_id=ADMIN_CHAT_ID,
                video=message.video.file_id,
                caption="📷 Видео без текста"
            )
            return
        
        logger.info(f"📝 Текст видео ({source}): {text[:100]}...")
        
        title = extract_title_from_text(text)
        formatted_text = format_text_with_bold_title(text)
        
        video_bytes = await download_media(context.bot, message.video.file_id)
        
        if not video_bytes:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ Не удалось скачать видео\n\n{formatted_text}",
                parse_mode="HTML"
            )
            return
        
        # Обрабатываем видео с настройками ЧП ВМ (текст снизу)
        processed_video = process_video(video_bytes, title, "bottom")
        
        await context.bot.send_video(
            chat_id=ADMIN_CHAT_ID,
            video=BytesIO(processed_video.getvalue()),
            caption=formatted_text[:1024],
            parse_mode="HTML",
            width=TARGET_W,
            height=TARGET_H
        )
        
        logger.info(f"✅ Отправлено обработанное видео (ЧП ВМ) ({source})")
        
        if len(formatted_text) > 1024:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=formatted_text[1024:],
                parse_mode="HTML"
            )
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке видео ({source}): {e}")
        import traceback
        traceback.print_exc()

# ==================== ОБРАБОТЧИКИ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 <b>Бот для обработки видео (ЧП ВМ)</b>\n\n"
        f"📢 Канал: <code>{MONITOR_CHANNEL_ID}</code>\n\n"
        f"🎬 <b>Настройки (ЧП ВМ):</b>\n"
        f"  • Обрезка до 4:5\n"
        f"  • Градиент 48% от высоты\n"
        f"  • Яркость 0.85\n"
        f"  • Белый текст (Montserrat-Black)\n"
        f"  • Текст позиционируется снизу\n\n"
        f"📎 Перешли видео в бота или добавь в канал",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"✅ <b>Бот работает (ЧП ВМ)</b>\n\n"
        f"📢 Канал: <code>{MONITOR_CHANNEL_ID}</code>\n"
        f"📨 Уведомления: <code>{ADMIN_CHAT_ID}</code>\n"
        f"🎬 Параметры ЧП ВМ:\n"
        f"  • Размер: {TARGET_W}x{TARGET_H} (4:5)\n"
        f"  • Градиент: {int(CHP_GRADIENT_PCT*100)}% от высоты\n"
        f"  • Шрифт: Montserrat-Black\n"
        f"  • Текст: снизу",
        parse_mode="HTML"
    )

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post
    if not message:
        return
    
    if message.chat.id != MONITOR_CHANNEL_ID:
        return
    
    if message.video:
        logger.info(f"📨 Получено видео из канала {message.message_id}")
        await process_video_post(message, context, "канал")

async def handle_forwarded_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return
    
    if not message.video:
        return
    
    logger.info(f"📨 Получено видео в бота")
    await process_video_post(message, context, "репост")

# ==================== ЗАПУСК ====================

async def main():
    logger.info("🚀 Бот для видео (ЧП ВМ) запускается...")
    
    download_fonts()
    
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        bot = Bot(token=BOT_TOKEN)
        
        try:
            channel = await bot.get_chat(MONITOR_CHANNEL_ID)
            logger.info(f"✅ Подключен к каналу: {channel.title}")
        except Exception as e:
            logger.error(f"❌ Не удалось подключиться к каналу: {e}")
            return
        
        try:
            admin = await bot.get_chat(ADMIN_CHAT_ID)
            logger.info(f"✅ Уведомления для: {admin.first_name}")
        except Exception as e:
            logger.error(f"❌ Не удалось подключиться к админу: {e}")
            return
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("status", status))
        
        app.add_handler(MessageHandler(
            filters.VIDEO & filters.Chat(chat_id=MONITOR_CHANNEL_ID),
            handle_channel_post
        ))
        
        app.add_handler(MessageHandler(
            filters.VIDEO & ~filters.Chat(chat_id=MONITOR_CHANNEL_ID),
            handle_forwarded_message
        ))
        
        logger.info("✅ Обработчики зарегистрированы")
        logger.info(f"📊 Параметры ЧП ВМ:")
        logger.info(f"  • Размер: {TARGET_W}x{TARGET_H} (4:5)")
        logger.info(f"  • Градиент: {int(CHP_GRADIENT_PCT*100)}% от высоты")
        logger.info(f"  • Шрифт: Montserrat-Black")
        logger.info(f"  • Текст: снизу")
        
        await app.initialize()
        await app.start()
        
        await app.updater.start_polling(
            allowed_updates=["message", "channel_post", "callback_query"],
            drop_pending_updates=True,
            poll_interval=1.0,
            timeout=30
        )
        
        logger.info("🟢 Бот успешно запущен!")
        
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
        logger.info("🛑 Бот остановлен")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)
