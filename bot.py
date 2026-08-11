# -*- coding: utf-8 -*-

import asyncio
import os
import re
import logging
import sys
import tempfile
import time
from io import BytesIO
from typing import Optional, List
import subprocess
import traceback
import shutil

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

try:
    from moviepy import VideoFileClip, ImageSequenceClip, CompositeVideoClip
    from moviepy.video.fx import resize
except ImportError:
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
        from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
    except:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "moviepy==1.0.3"])
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
        from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip

try:
    import numpy as np
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "numpy"])
    import numpy as np

from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONITOR_CHANNEL_ID = os.getenv("MONITOR_CHANNEL_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", "50"))

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
CHP_GRADIENT_PCT = 0.48
MN_TITLE_ZONE_PCT = 0.23
BRIGHTNESS_FACTOR = 0.85
FONT_CHP = "Montserrat-Black.ttf"
FONT_FALLBACK = "Arial.ttf"
MAX_FILE_SIZE_BYTES = MAX_VIDEO_SIZE_MB * 1024 * 1024

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== СОСТОЯНИЯ ДЛЯ ФОТО ====================
user_photo_sessions = {}  # user_id: {"photos": [bytes], "state": "waiting_title" или "collecting"}

# ==================== ФУНКЦИИ ====================

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

# ==================== НОВЫЕ ФУНКЦИИ ДЛЯ ФОТО ====================

def process_single_photo(photo_bytes: bytes, title_text: str) -> BytesIO:
    """Обработка одной фотографии с градиентом и текстом (шаблон ЧП ВМ)"""
    try:
        img = Image.open(BytesIO(photo_bytes)).convert("RGB")
        
        # Обрезка до 4:5
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        
        # Яркость
        img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
        
        # Градиент снизу
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
        # Текст
        draw = ImageDraw.Draw(img)
        margin_x = int(img.width * 0.06)
        margin_bottom = int(img.height * 0.08)
        safe_w = img.width - 2 * margin_x
        title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
        
        clean_title = clean_title_for_card(title_text)
        text = (clean_title or "Без заголовка").strip().upper()
        
        font, lines, heights, spacing, total_h = fit_text_block(
            draw=draw, text=text, safe_w=safe_w,
            max_block_h=title_max_h, max_lines=6,
            start_size=int(img.height * 0.11), min_size=16
        )
        
        line_height = font.size
        total_text_height = len(lines) * line_height + (len(lines) - 1) * 2
        
        y = img.height - margin_bottom - total_text_height
        
        for ln in lines:
            draw.text((margin_x, y), ln, font=font, fill="white")
            y += line_height + 2
        
        output = BytesIO()
        img.save(output, format="PNG")
        output.seek(0)
        return output
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки фото: {e}")
        return BytesIO(photo_bytes)

def create_cover_with_title(photo_bytes: bytes, title_text: str) -> Image.Image:
    """Создает обложку для видео: фото + градиент + заголовок"""
    try:
        img = Image.open(BytesIO(photo_bytes)).convert("RGB")
        
        # Обрезка до 4:5
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        
        # Яркость
        img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
        
        # Градиент снизу
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
        # Текст (заголовок)
        draw = ImageDraw.Draw(img)
        margin_x = int(img.width * 0.06)
        margin_bottom = int(img.height * 0.08)
        safe_w = img.width - 2 * margin_x
        title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
        
        clean_title = clean_title_for_card(title_text)
        text = (clean_title or "Без заголовка").strip().upper()
        
        font, lines, heights, spacing, total_h = fit_text_block(
            draw=draw, text=text, safe_w=safe_w,
            max_block_h=title_max_h, max_lines=6,
            start_size=int(img.height * 0.11), min_size=16
        )
        
        line_height = font.size
        total_text_height = len(lines) * line_height + (len(lines) - 1) * 2
        
        y = img.height - margin_bottom - total_text_height
        
        for ln in lines:
            draw.text((margin_x, y), ln, font=font, fill="white")
            y += line_height + 2
        
        return img
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания обложки: {e}")
        return Image.open(BytesIO(photo_bytes))

def create_slideshow_video(photos: List[bytes], title_text: str) -> Optional[BytesIO]:
    """Создает видео-слайдшоу из фотографий с обложкой"""
    temp_dir = tempfile.mkdtemp()
    
    try:
        logger.info(f"📸 Создание слайдшоу из {len(photos)} фото")
        
        if len(photos) < 3 or len(photos) > 10:
            logger.error(f"❌ Неверное количество фото: {len(photos)}")
            return None
        
        # Создаем обложку из первого фото
        cover_img = create_cover_with_title(photos[0], title_text)
        cover_path = os.path.join(temp_dir, "cover.png")
        cover_img.save(cover_path)
        
        # Обрабатываем остальные фото
        photo_paths = [cover_path]  # первое фото - обложка
        
        for i, photo_bytes in enumerate(photos[1:], 1):
            # Обрабатываем фото как слайд (без текста)
            img = Image.open(BytesIO(photo_bytes)).convert("RGB")
            img = crop_to_4x5(img)
            img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
            img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
            # Добавляем легкий градиент для красоты
            img = apply_bottom_gradient(img, height_pct=0.2, max_alpha=100)
            
            path = os.path.join(temp_dir, f"photo_{i}.png")
            img.save(path)
            photo_paths.append(path)
        
        # Создаем список клипов
        clips = []
        
        # Обложка - 3 секунды
        cover_clip = ImageSequenceClip([cover_path], durations=[3])
        # Для moviepy 1.0.3 используем другой подход для эффекта приближения
        # Создаем эффект через resize с использованием lambda
        from moviepy.video.fx import resize
        cover_clip = cover_clip.fx(resize, lambda t: 1 + 0.05 * (t / 3))
        clips.append(cover_clip)
        
        # Остальные фото - по 2.5 секунды
        for path in photo_paths[1:]:
            duration = 2.5
            clip = ImageSequenceClip([path], durations=[duration])
            # Используем fx для применения эффекта
            clip = clip.fx(resize, lambda t: 1 + 0.03 * (t / duration))
            clips.append(clip)
        
        # Объединяем все клипы
        final_clip = CompositeVideoClip(clips)
        
        # Сохраняем видео
        output_path = os.path.join(temp_dir, "slideshow.mp4")
        final_clip.write_videofile(
            output_path,
            fps=24,
            codec='libx264',
            audio_codec='aac',
            threads=4,
            preset='medium',
            logger=None
        )
        
        # Читаем результат
        with open(output_path, 'rb') as f:
            result_bytes = f.read()
        
        output = BytesIO()
        output.write(result_bytes)
        output.seek(0)
        
        logger.info(f"✅ Слайдшоу создано! Размер: {len(result_bytes) / (1024*1024):.2f} MB")
        return output
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания слайдшоу: {e}")
        traceback.print_exc()
        return None
    
    finally:
        try:
            shutil.rmtree(temp_dir)
        except:
            pass

# ==================== ФУНКЦИИ ДЛЯ ВИДЕО (СУЩЕСТВУЮЩИЕ) ====================

def process_video_frame(frame: np.ndarray, title_text: str) -> np.ndarray:
    """Обработка одного кадра (используется в fl_image)"""
    try:
        img = Image.fromarray(frame).convert("RGB")
        
        # 1. Обрезка до 4:5
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        
        # 2. Яркость
        img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
        
        # 3. Градиент снизу
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
        # 4. Текст
        draw = ImageDraw.Draw(img)
        margin_x = int(img.width * 0.06)
        margin_bottom = int(img.height * 0.08)
        safe_w = img.width - 2 * margin_x
        title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
        
        clean_title = clean_title_for_card(title_text)
        text = (clean_title or "Без заголовка").strip().upper()
        
        font, lines, heights, spacing, total_h = fit_text_block(
            draw=draw, text=text, safe_w=safe_w,
            max_block_h=title_max_h, max_lines=6,
            start_size=int(img.height * 0.11), min_size=16
        )
        
        line_height = font.size
        total_text_height = len(lines) * line_height + (len(lines) - 1) * 2
        
        y = img.height - margin_bottom - total_text_height
        
        for ln in lines:
            draw.text((margin_x, y), ln, font=font, fill="white")
            y += line_height + 2
        
        return np.array(img)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки кадра: {e}")
        return frame

def process_video(video_bytes: bytes, title_text: str) -> BytesIO:
    """Обработка видео (работает!)"""
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
        
        logger.info(f"📹 Загрузка видео...")
        video = VideoFileClip(temp_input)
        logger.info(f"📹 Видео загружено: {video.duration}с, {video.size}")
        
        # Обрабатываем каждый кадр
        logger.info(f"🎬 Обработка кадров...")
        
        def process_frame(frame):
            return process_video_frame(frame, title_text)
        
        processed_video = video.fl_image(process_frame)
        
        # Сохраняем
        logger.info(f"💾 Сохранение видео...")
        processed_video.write_videofile(
            temp_output,
            codec='libx264',
            audio_codec='aac',
            fps=video.fps,
            bitrate='1500k',
            threads=4,
            preset='fast',
            logger=None
        )
        
        video.close()
        processed_video.close()
        
        # Читаем результат
        with open(temp_output, 'rb') as f:
            result_bytes = f.read()
        
        logger.info(f"✅ Видео обработано! Размер: {len(result_bytes) / (1024*1024):.2f} MB")
        
        output = BytesIO()
        output.write(result_bytes)
        output.seek(0)
        return output
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке видео: {e}")
        traceback.print_exc()
        # Возвращаем оригинал в случае ошибки
        output = BytesIO(video_bytes)
        output.seek(0)
        return output
    
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
        
        if file.file_size and file.file_size > MAX_FILE_SIZE_BYTES:
            logger.error(f"❌ Файл слишком большой: {file.file_size / (1024*1024):.1f} MB")
            return None
        
        logger.info(f"📥 Скачивание видео, размер: {file.file_size / (1024*1024):.1f} MB")
        result = await file.download_as_bytearray()
        logger.info(f"✅ Видео скачано: {len(result) / (1024*1024):.1f} MB")
        return result
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания: {e}")
        return None

def get_text_from_message(message) -> str:
    return message.text or message.caption or ""

# ==================== ОБРАБОТКА ПОСТА ====================

async def process_video_post(message, context: ContextTypes.DEFAULT_TYPE, source: str = "канал"):
    try:
        if not hasattr(message, 'video') or not message.video:
            logger.error("❌ Нет видео!")
            return
        
        logger.info(f"📹 Получено видео: {message.video.file_size / (1024*1024):.1f} MB")
        
        # Проверка размера
        if message.video.file_size and message.video.file_size > MAX_FILE_SIZE_BYTES:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ Видео слишком большое! {message.video.file_size / (1024*1024):.1f} MB > {MAX_VIDEO_SIZE_MB} MB",
                parse_mode="HTML"
            )
            return
        
        # Статус
        status_msg = await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="🎬 <b>Начинаю обработку видео...</b>\n⏳ Это займёт ~30-60 секунд",
            parse_mode="HTML"
        )
        
        text = get_text_from_message(message)
        
        if not text.strip():
            await status_msg.edit_text("⚠️ Видео без текста")
            await context.bot.send_video(
                chat_id=ADMIN_CHAT_ID,
                video=message.video.file_id
            )
            return
        
        logger.info(f"📝 Текст: {text[:100]}...")
        
        title = extract_title_from_text(text)
        formatted_text = format_text_with_bold_title(text)
        
        await status_msg.edit_text("⏳ <b>Скачиваю видео...</b>", parse_mode="HTML")
        
        video_bytes = await download_media(context.bot, message.video.file_id)
        
        if not video_bytes:
            await status_msg.edit_text("❌ Не удалось скачать видео")
            return
        
        await status_msg.edit_text("⏳ <b>Обрабатываю видео...</b>", parse_mode="HTML")
        
        # Обрабатываем видео
        processed_video = process_video(video_bytes, title)
        
        if not processed_video or len(processed_video.getvalue()) == 0:
            await status_msg.edit_text("❌ Ошибка обработки видео")
            return
        
        await status_msg.edit_text("⏳ <b>Отправляю видео...</b>", parse_mode="HTML")
        
        # Отправляем обработанное видео
        await context.bot.send_video(
            chat_id=ADMIN_CHAT_ID,
            video=BytesIO(processed_video.getvalue()),
            caption=formatted_text[:1024],
            parse_mode="HTML",
            width=TARGET_W,
            height=TARGET_H
        )
        
        await status_msg.delete()
        logger.info(f"✅ Видео отправлено! ({source})")
        
        if len(formatted_text) > 1024:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=formatted_text[1024:],
                parse_mode="HTML"
            )
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        traceback.print_exc()
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ Ошибка: {str(e)}",
                parse_mode="HTML"
            )
        except:
            pass

# ==================== НОВЫЕ ОБРАБОТЧИКИ ДЛЯ ФОТО ====================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фото с предложением выбора действия"""
    # Проверяем, есть ли effective_user (может быть None для каналов)
    if not update.effective_user:
        logger.info("ℹ️ Пропускаем фото из канала (нет пользователя)")
        return
    
    user_id = update.effective_user.id
    message = update.message
    
    if not message or not message.photo:
        return
    
    # Получаем фото в максимальном качестве
    photo = message.photo[-1]
    
    # Скачиваем фото
    try:
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания фото: {e}")
        await message.reply_text("❌ Не удалось скачать фото")
        return
    
    # Показываем кнопки выбора
    keyboard = [
        [
            InlineKeyboardButton("✅ Оформить пост", callback_data="photo_post"),
            InlineKeyboardButton("🎬 Сделать видео", callback_data="photo_video")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Сохраняем фото в сессию
    if user_id not in user_photo_sessions:
        user_photo_sessions[user_id] = {"photos": [], "state": "idle"}
    
    user_photo_sessions[user_id]["photos"].append(photo_bytes)
    
    await message.reply_text(
        "📸 Фото получено!\n\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

async def handle_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий кнопок для фото"""
    query = update.callback_query
    await query.answer()
    
    # Проверяем, есть ли effective_user
    if not update.effective_user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = update.effective_user.id
    data = query.data
    
    if user_id not in user_photo_sessions:
        await query.edit_message_text("❌ Сессия не найдена. Отправьте фото заново.")
        return
    
    session = user_photo_sessions[user_id]
    
    if data == "photo_post":
        # Обработка одного фото как поста
        if not session["photos"]:
            await query.edit_message_text("❌ Нет фото для обработки")
            return
        
        # Берем последнее фото
        photo_bytes = session["photos"][-1]
        
        await query.edit_message_text("⏳ <b>Обрабатываю фото...</b>", parse_mode="HTML")
        
        # Запрашиваем текст для заголовка
        await query.message.reply_text(
            "✏️ Отправьте текст для заголовка (или нажмите /cancel для отмены):"
        )
        
        # Сохраняем состояние
        session["state"] = "waiting_post_title"
        
    elif data == "photo_video":
        # Начинаем сбор фото для видео
        if not session["photos"]:
            await query.edit_message_text("❌ Нет фото для обработки")
            return
        
        await query.edit_message_text(
            "🎬 <b>Создание видео из фото</b>\n\n"
            "Отправьте еще 2-9 фотографий (всего нужно 3-10).\n"
            "Когда будете готовы, нажмите /done",
            parse_mode="HTML"
        )
        
        session["state"] = "collecting_photos"

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    # Проверяем, есть ли effective_user
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    message = update.message
    
    if not message:
        return
    
    if user_id not in user_photo_sessions:
        return
    
    session = user_photo_sessions[user_id]
    state = session.get("state", "idle")
    
    if state == "waiting_post_title":
        # Получаем заголовок для поста
        title = message.text.strip()
        
        if not title:
            await message.reply_text("❌ Текст не может быть пустым. Отправьте снова или /cancel")
            return
        
        # Обрабатываем последнее фото
        if session["photos"]:
            photo_bytes = session["photos"][-1]
            
            status_msg = await message.reply_text("⏳ <b>Обрабатываю фото...</b>", parse_mode="HTML")
            
            # Обрабатываем фото
            processed = process_single_photo(photo_bytes, title)
            
            if processed and len(processed.getvalue()) > 0:
                await message.reply_photo(
                    photo=BytesIO(processed.getvalue()),
                    caption=f"<b>{title}</b>",
                    parse_mode="HTML"
                )
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Ошибка обработки фото")
        
        # Очищаем сессию
        session["state"] = "idle"
        session["photos"] = []
    
    elif state == "collecting_photos":
        # Игнорируем текст, ждем фото или команду /done
        await message.reply_text(
            "📸 Отправьте фото или нажмите /done для создания видео",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово (создать видео)", callback_data="video_done")],
                [InlineKeyboardButton("❌ Отмена", callback_data="video_cancel")]
            ])
        )
    
    elif state == "waiting_video_title":
        # Получаем заголовок для видео
        title = message.text.strip()
        
        if not title:
            await message.reply_text("❌ Текст не может быть пустым. Отправьте снова или /cancel")
            return
        
        # Создаем видео
        if len(session["photos"]) >= 3:
            status_msg = await message.reply_text(
                f"🎬 <b>Создаю видео из {len(session['photos'])} фото...</b>\n"
                "⏳ Это займет ~1-2 минуты",
                parse_mode="HTML"
            )
            
            video = create_slideshow_video(session["photos"], title)
            
            if video and len(video.getvalue()) > 0:
                await status_msg.edit_text("⏳ <b>Отправляю видео...</b>", parse_mode="HTML")
                
                # Отправляем видео
                await message.reply_video(
                    video=BytesIO(video.getvalue()),
                    caption=f"<b>{title}</b>",
                    parse_mode="HTML",
                    width=TARGET_W,
                    height=TARGET_H
                )
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Ошибка создания видео")
        else:
            await message.reply_text(
                f"❌ Недостаточно фото! Отправлено: {len(session['photos'])}, нужно 3-10"
            )
        
        # Очищаем сессию
        session["state"] = "idle"
        session["photos"] = []

async def handle_photo_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбор фото для видео"""
    # Проверяем, есть ли effective_user
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    message = update.message
    
    if not message or not message.photo:
        return
    
    if user_id not in user_photo_sessions:
        return
    
    session = user_photo_sessions[user_id]
    
    if session.get("state") != "collecting_photos":
        return
    
    # Получаем фото
    photo = message.photo[-1]
    
    try:
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания фото: {e}")
        await message.reply_text("❌ Не удалось скачать фото")
        return
    
    # Добавляем в коллекцию
    session["photos"].append(photo_bytes)
    count = len(session["photos"])
    
    if count >= 10:
        await message.reply_text(
            f"✅ Собрано {count} фото (максимум).\n"
            "Нажмите /done для создания видео"
        )
        return
    
    await message.reply_text(
        f"✅ Фото {count} добавлено!\n"
        f"Осталось: {max(0, 3 - count)} фото (минимум 3, максимум 10)\n"
        "Нажмите /done когда будете готовы"
    )

async def handle_video_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /done для создания видео"""
    # Проверяем, есть ли effective_user
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    message = update.message
    
    if not message:
        return
    
    if user_id not in user_photo_sessions:
        await message.reply_text("❌ Нет активной сессии. Отправьте фото сначала.")
        return
    
    session = user_photo_sessions[user_id]
    
    if session.get("state") != "collecting_photos":
        await message.reply_text("❌ Нет активного сбора фото.")
        return
    
    count = len(session["photos"])
    
    if count < 3:
        await message.reply_text(
            f"❌ Недостаточно фото! Отправлено: {count}, нужно минимум 3.\n"
            "Отправьте еще фото или /cancel для отмены"
        )
        return
    
    # Запрашиваем заголовок
    await message.reply_text(
        f"✅ Собрано {count} фото.\n\n"
        "✏️ Отправьте текст для заголовка:"
    )
    
    session["state"] = "waiting_video_title"

async def handle_video_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена создания видео"""
    query = update.callback_query
    await query.answer()
    
    # Проверяем, есть ли effective_user
    if not update.effective_user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = query.from_user.id
    
    if user_id in user_photo_sessions:
        user_photo_sessions[user_id] = {"photos": [], "state": "idle"}
    
    await query.edit_message_text("❌ Создание видео отменено")

async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /cancel"""
    # Проверяем, есть ли effective_user
    if not update.effective_user:
        await update.message.reply_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = update.effective_user.id
    
    if user_id in user_photo_sessions:
        user_photo_sessions[user_id] = {"photos": [], "state": "idle"}
    
    await update.message.reply_text("✅ Действие отменено")

# ==================== ОБРАБОТЧИКИ (СУЩЕСТВУЮЩИЕ) ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 <b>Бот для обработки видео и фото (ЧП ВМ)</b>\n\n"
        f"📢 Канал: <code>{MONITOR_CHANNEL_ID}</code>\n"
        f"📊 Макс. размер: {MAX_VIDEO_SIZE_MB} MB\n\n"
        f"🎬 <b>Что умеет бот:</b>\n"
        f"1️⃣ <b>Видео</b> - обрабатывает видео (градиент + текст)\n"
        f"2️⃣ <b>Фото</b> - отправьте фото и выберите действие:\n"
        f"   • Оформить пост (градиент + текст)\n"
        f"   • Сделать видео (слайд-шоу из 3-10 фото)\n\n"
        f"📎 Просто отправьте видео или фото в бот",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"✅ <b>Бот работает</b>\n\n"
        f"📢 Канал: <code>{MONITOR_CHANNEL_ID}</code>\n"
        f"📨 Уведомления: <code>{ADMIN_CHAT_ID}</code>\n"
        f"⚡ Обработка включена!",
        parse_mode="HTML"
    )

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post
    if not message:
        return
    
    if message.chat.id != MONITOR_CHANNEL_ID:
        return
    
    if message.video:
        logger.info(f"📨 Получено видео из канала")
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
    logger.info("🚀 Бот для видео и фото (ЧП ВМ) запускается...")
    
    download_fonts()
    
    # Создаём приложение
    app = Application.builder().token(BOT_TOKEN).build()
    bot = Bot(token=BOT_TOKEN)
    
    # Очищаем старые сессии
    logger.info("🔄 Очистка старых сессий...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook удалён")
    except Exception as e:
        logger.warning(f"⚠️ Webhook: {e}")
    
    await asyncio.sleep(2)
    
    # Проверка
    try:
        channel = await bot.get_chat(MONITOR_CHANNEL_ID)
        logger.info(f"✅ Подключен к каналу: {channel.title}")
    except Exception as e:
        logger.error(f"❌ Канал: {e}")
        return
    
    try:
        admin = await bot.get_chat(ADMIN_CHAT_ID)
        logger.info(f"✅ Уведомления для: {admin.first_name}")
    except Exception as e:
        logger.error(f"❌ Админ: {e}")
        return
    
    # Обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("done", handle_video_done))
    
    # Обработчики для фото - только для личных сообщений
    app.add_handler(MessageHandler(
        filters.PHOTO & ~filters.Chat(chat_id=MONITOR_CHANNEL_ID), 
        handle_photo
    ))
    
    # Сбор фото для видео - только в личных сообщениях
    app.add_handler(MessageHandler(
        filters.PHOTO & ~filters.Chat(chat_id=MONITOR_CHANNEL_ID), 
        handle_photo_collection
    ))
    
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Chat(chat_id=MONITOR_CHANNEL_ID),
        handle_text_input
    ))
    
    # Callback обработчики
    app.add_handler(CallbackQueryHandler(handle_photo_callback, pattern="^(photo_post|photo_video)$"))
    app.add_handler(CallbackQueryHandler(handle_video_cancel, pattern="^video_cancel$"))
    app.add_handler(CallbackQueryHandler(handle_video_done, pattern="^video_done$"))
    
    # Обработчики для видео (существующие)
    app.add_handler(MessageHandler(
        filters.VIDEO & filters.Chat(chat_id=MONITOR_CHANNEL_ID),
        handle_channel_post
    ))
    
    app.add_handler(MessageHandler(
        filters.VIDEO & ~filters.Chat(chat_id=MONITOR_CHANNEL_ID),
        handle_forwarded_message
    ))
    
    logger.info("✅ Обработчики зарегистрированы")
    logger.info("📊 Параметры ЧП ВМ:")
    logger.info(f"  • Размер: {TARGET_W}x{TARGET_H}")
    logger.info(f"  • Градиент: {int(CHP_GRADIENT_PCT*100)}%")
    logger.info(f"  • Текст: снизу")
    logger.info("📸 Новые функции:")
    logger.info("  • Обработка фото с градиентом")
    logger.info("  • Создание слайд-шоу из 3-10 фото")
    
    await app.initialize()
    await app.start()
    
    await app.updater.start_polling(
        allowed_updates=["message", "channel_post", "callback_query"],
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=30
    )
    
    logger.info("🟢 Бот запущен!")
    
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        sys.exit(1)
