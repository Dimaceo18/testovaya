# -*- coding: utf-8 -*-

import asyncio
import os
import re
import logging
import sys
import tempfile
import time
from io import BytesIO
from typing import Optional, List, Dict
import subprocess
import traceback
import shutil
import json
from collections import defaultdict
import hashlib

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

# Правильный импорт moviepy
try:
    from moviepy import VideoFileClip, ImageSequenceClip
    from moviepy.video.fx import resize
    from moviepy.video.compositing.concatenate import concatenate_videoclips
    from moviepy.audio.io.AudioFileClip import AudioFileClip
    from moviepy.audio.fx.all import audio_loop
except ImportError:
    try:
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
        from moviepy.video.compositing.concatenate import concatenate_videoclips
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        from moviepy.audio.fx.all import audio_loop
        try:
            from moviepy.video.fx import resize
        except:
            resize = None
    except:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "moviepy==1.0.3"])
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
        from moviepy.video.compositing.concatenate import concatenate_videoclips
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        from moviepy.audio.fx.all import audio_loop
        try:
            from moviepy.video.fx import resize
        except:
            resize = None

try:
    import numpy as np
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "numpy"])
    import numpy as np

from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONITOR_CHANNEL_ID = os.getenv("MONITOR_CHANNEL_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", "50"))
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# URL для аудиофайлов на GitHub
AUDIO_URLS = {
    "важная": "https://raw.githubusercontent.com/Dimaceo18/testovaya/main/vajnoe.mp3",
    "обычная": "https://raw.githubusercontent.com/Dimaceo18/testovaya/main/obychnaya.mp3"
}

# URL для DeepSeek API
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

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

# ==================== СОСТОЯНИЯ ====================
user_sessions = {}
media_groups: Dict[str, Dict] = {}

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

def download_audio_from_github(audio_type: str) -> Optional[bytes]:
    try:
        url = AUDIO_URLS.get(audio_type)
        if not url:
            return None
        
        logger.info(f"⬇️ Скачивание аудио {audio_type}...")
        response = requests.get(url, timeout=30)
        
        if response.status_code == 200:
            logger.info(f"✅ Аудио {audio_type} скачано! Размер: {len(response.content) / 1024:.1f} KB")
            return response.content
        else:
            return None
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания аудио {audio_type}: {e}")
        return None

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

async def improve_title_with_ai(title: str) -> Optional[str]:
    if not DEEPSEEK_API_KEY:
        return None
    
    try:
        prompt = f"""Переделай этот заголовок в новостной, но более интересный и кликбейтный формат. 
Сделай его более ярким, интригующим, добавь эмоциональную окраску. 
Сохрани смысл, но сделай его более привлекательным для читателей.

Оригинальный заголовок: {title}

Ответь только новым заголовком, без пояснений и кавычек."""

        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "Ты профессиональный копирайтер и редактор новостей. Твоя задача - делать заголовки более привлекательными и кликбейтными."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.8,
            "max_tokens": 100
        }
        
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            improved_title = result['choices'][0]['message']['content'].strip()
            improved_title = improved_title.strip('"\'')
            return improved_title
        else:
            logger.error(f"❌ Ошибка DeepSeek API: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Ошибка при работе с DeepSeek: {e}")
        return None

# ==================== ФУНКЦИИ ДЛЯ ОБРАБОТКИ МЕДИА ====================

def process_single_photo(photo_bytes: bytes, title_text: str) -> BytesIO:
    try:
        img = Image.open(BytesIO(photo_bytes)).convert("RGB")
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
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
    try:
        img = Image.open(BytesIO(photo_bytes)).convert("RGB")
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
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

def create_video_with_photos(video_bytes: bytes, photos: List[bytes], title_text: str, audio_bytes: Optional[bytes] = None) -> Optional[BytesIO]:
    temp_dir = tempfile.mkdtemp()
    
    try:
        logger.info(f"📹 Создание видео из видео + {len(photos)} фото")
        
        video_path = os.path.join(temp_dir, "input_video.mp4")
        with open(video_path, 'wb') as f:
            f.write(video_bytes)
        
        cover_img = create_cover_with_title(photos[0], title_text)
        cover_path = os.path.join(temp_dir, "cover.png")
        cover_img.save(cover_path)
        
        photo_paths = [cover_path]
        for i, photo_bytes in enumerate(photos[1:], 1):
            img = Image.open(BytesIO(photo_bytes)).convert("RGB")
            img = crop_to_4x5(img)
            img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
            img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
            img = apply_bottom_gradient(img, height_pct=0.15, max_alpha=80)
            
            path = os.path.join(temp_dir, f"photo_{i}.png")
            img.save(path)
            photo_paths.append(path)
        
        video_clip = VideoFileClip(video_path)
        video_clip = video_clip.resize((TARGET_W, TARGET_H))
        
        duration_per_photo = 3.0
        photo_clips = []
        
        for path in photo_paths:
            clip = ImageSequenceClip([path], durations=[duration_per_photo])
            try:
                if resize:
                    def make_zoom(t):
                        progress = t / duration_per_photo
                        return 1.0 + 0.1 * (progress * progress * (3 - 2 * progress))
                    clip = clip.fx(resize, make_zoom)
            except:
                pass
            photo_clips.append(clip)
        
        slideshow_clip = concatenate_videoclips(photo_clips)
        final_clip = concatenate_videoclips([video_clip, slideshow_clip])
        
        if audio_bytes:
            try:
                audio_path = os.path.join(temp_dir, "audio.mp3")
                with open(audio_path, 'wb') as f:
                    f.write(audio_bytes)
                
                audio_clip = AudioFileClip(audio_path)
                if audio_clip.duration > final_clip.duration:
                    audio_clip = audio_clip.subclip(0, final_clip.duration)
                else:
                    audio_clip = audio_loop(audio_clip, duration=final_clip.duration)
                
                final_clip = final_clip.set_audio(audio_clip)
            except Exception as e:
                logger.warning(f"⚠️ Не удалось добавить аудио: {e}")
        
        output_path = os.path.join(temp_dir, "output.mp4")
        final_clip.write_videofile(
            output_path,
            fps=24,
            codec='libx264',
            audio_codec='aac',
            threads=4,
            preset='medium',
            logger=None
        )
        
        video_clip.close()
        final_clip.close()
        
        with open(output_path, 'rb') as f:
            result_bytes = f.read()
        
        output = BytesIO()
        output.write(result_bytes)
        output.seek(0)
        
        logger.info(f"✅ Видео создано! Размер: {len(result_bytes) / (1024*1024):.2f} MB")
        return output
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания видео: {e}")
        traceback.print_exc()
        return None
    
    finally:
        try:
            shutil.rmtree(temp_dir)
        except:
            pass

def create_slideshow_video(photos: List[bytes], title_text: str, audio_bytes: Optional[bytes] = None) -> Optional[BytesIO]:
    temp_dir = tempfile.mkdtemp()
    
    try:
        logger.info(f"📸 Создание слайдшоу из {len(photos)} фото")
        
        if len(photos) < 3 or len(photos) > 10:
            logger.error(f"❌ Неверное количество фото: {len(photos)}")
            return None
        
        photo_paths = []
        
        cover_img = create_cover_with_title(photos[0], title_text)
        cover_path = os.path.join(temp_dir, "cover.png")
        cover_img.save(cover_path)
        photo_paths.append(cover_path)
        
        for i, photo_bytes in enumerate(photos[1:], 1):
            img = Image.open(BytesIO(photo_bytes)).convert("RGB")
            img = crop_to_4x5(img)
            img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
            img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
            img = apply_bottom_gradient(img, height_pct=0.15, max_alpha=80)
            
            path = os.path.join(temp_dir, f"photo_{i}.png")
            img.save(path)
            photo_paths.append(path)
        
        duration_per_photo = 3.0
        clips = []
        
        for i, path in enumerate(photo_paths):
            clip = ImageSequenceClip([path], durations=[duration_per_photo])
            try:
                if resize:
                    def make_zoom(t):
                        progress = t / duration_per_photo
                        return 1.0 + 0.1 * (progress * progress * (3 - 2 * progress))
                    clip = clip.fx(resize, make_zoom)
            except Exception as e:
                logger.warning(f"⚠️ Эффект приближения не применен для фото {i+1}: {e}")
            clips.append(clip)
        
        final_clip = concatenate_videoclips(clips)
        
        if audio_bytes:
            try:
                audio_path = os.path.join(temp_dir, "audio.mp3")
                with open(audio_path, 'wb') as f:
                    f.write(audio_bytes)
                
                audio_clip = AudioFileClip(audio_path)
                if audio_clip.duration > final_clip.duration:
                    audio_clip = audio_clip.subclip(0, final_clip.duration)
                else:
                    audio_clip = audio_loop(audio_clip, duration=final_clip.duration)
                
                final_clip = final_clip.set_audio(audio_clip)
            except Exception as e:
                logger.warning(f"⚠️ Не удалось добавить аудио: {e}")
        
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

def process_video_frame(frame: np.ndarray, title_text: str) -> np.ndarray:
    try:
        img = Image.fromarray(frame).convert("RGB")
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        img = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
        img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
        
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
    temp_input = None
    temp_output = None
    
    try:
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            f.write(video_bytes)
            temp_input = f.name
        
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            temp_output = f.name
        
        logger.info(f"📹 Загрузка видео...")
        video = VideoFileClip(temp_input)
        logger.info(f"📹 Видео загружено: {video.duration}с, {video.size}")
        
        logger.info(f"🎬 Обработка кадров...")
        
        def process_frame(frame):
            return process_video_frame(frame, title_text)
        
        processed_video = video.fl_image(process_frame)
        
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

async def download_media(bot: Bot, file_id: str) -> Optional[bytes]:
    try:
        file = await bot.get_file(file_id)
        
        if file.file_size and file.file_size > MAX_FILE_SIZE_BYTES:
            logger.error(f"❌ Файл слишком большой: {file.file_size / (1024*1024):.1f} MB")
            return None
        
        logger.info(f"📥 Скачивание, размер: {file.file_size / (1024*1024):.1f} MB")
        result = await file.download_as_bytearray()
        logger.info(f"✅ Скачано: {len(result) / (1024*1024):.1f} MB")
        return result
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания: {e}")
        return None

def get_text_from_message(message) -> str:
    return message.text or message.caption or ""

# ==================== ОБРАБОТКА ВИДЕО ====================

async def process_video_post(message, context: ContextTypes.DEFAULT_TYPE, source: str = "канал"):
    try:
        if not hasattr(message, 'video') or not message.video:
            logger.error("❌ Нет видео!")
            return
        
        logger.info(f"📹 Получено видео: {message.video.file_size / (1024*1024):.1f} MB")
        
        if message.video.file_size and message.video.file_size > MAX_FILE_SIZE_BYTES:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"❌ Видео слишком большое! {message.video.file_size / (1024*1024):.1f} MB > {MAX_VIDEO_SIZE_MB} MB",
                parse_mode="HTML"
            )
            return
        
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
        
        processed_video = process_video(video_bytes, title)
        
        if not processed_video or len(processed_video.getvalue()) == 0:
            await status_msg.edit_text("❌ Ошибка обработки видео")
            return
        
        await status_msg.edit_text("⏳ <b>Отправляю видео...</b>", parse_mode="HTML")
        
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

# ==================== ОБРАБОТКА МЕДИАГРУПП ====================

async def handle_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка медиагрупп (несколько фото или видео+фото)"""
    if not update.effective_user:
        return
    
    message = update.message
    if not message:
        return
    
    media_group_id = getattr(message, 'media_group_id', None)
    
    # Если это не медиагруппа - возвращаем False
    if not media_group_id:
        return False
    
    logger.info(f"📦 Получена часть медиагруппы: {media_group_id}")
    
    # Инициализируем группу
    if media_group_id not in media_groups:
        media_groups[media_group_id] = {
            "photos": [],
            "video": None,
            "caption": "",
            "processed": False,
            "user_id": update.effective_user.id,
            "chat_id": message.chat.id,
            "message_id": message.message_id,
            "timestamp": time.time()
        }
    
    group = media_groups[media_group_id]
    
    # Сохраняем подпись
    if message.caption:
        group["caption"] = message.caption
    
    # Сохраняем медиа
    try:
        if message.photo:
            photo = message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            photo_bytes = await file.download_as_bytearray()
            group["photos"].append(photo_bytes)
            logger.info(f"📸 Добавлено фото в группу, всего: {len(group['photos'])}")
        
        if message.video:
            file = await context.bot.get_file(message.video.file_id)
            video_bytes = await file.download_as_bytearray()
            group["video"] = video_bytes
            logger.info(f"📹 Добавлено видео в группу")
            
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания медиа: {e}")
        return True
    
    # Запускаем таймер
    if not group.get("timer_started"):
        group["timer_started"] = True
        
        async def process_group():
            await asyncio.sleep(5)  # Ждем 5 секунд
            await process_collected_group(media_group_id, context)
        
        asyncio.create_task(process_group())
    
    return True

async def process_collected_group(media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Обработка собранной медиагруппы"""
    group = media_groups.get(media_group_id)
    
    if not group or group.get("processed"):
        return
    
    # Отмечаем как обработанную
    group["processed"] = True
    
    # Проверяем, есть ли фото
    photos = group.get("photos", [])
    if not photos:
        logger.info(f"❌ В группе {media_group_id} нет фото")
        return
    
    has_video = group.get("video") is not None
    
    logger.info(f"📦 Обработка группы: видео={has_video}, фото={len(photos)}")
    
    # Инициализируем сессию пользователя
    user_id = group["user_id"]
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "state": "idle", 
            "audio": None, 
            "audio_selected": None, 
            "auto_title": None, 
            "video": None, 
            "photos": [], 
            "current_title": None
        }
    
    session = user_sessions[user_id]
    session["photos"] = photos.copy()
    session["video"] = group.get("video")
    
    # Проверяем текст
    caption = group.get("caption", "")
    
    if caption.strip():
        auto_title = extract_title_from_text(caption)
        session["auto_title"] = auto_title
        
        keyboard = [
            [
                InlineKeyboardButton("📝 Использовать заголовок из текста", callback_data="title_auto"),
                InlineKeyboardButton("✏️ Свой заголовок", callback_data="title_custom")
            ],
            [
                InlineKeyboardButton("🤖 Улучшить через ИИ", callback_data="title_ai"),
                InlineKeyboardButton("❌ Отмена", callback_data="title_cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        title_preview = auto_title if auto_title else "❌ Не удалось извлечь заголовок"
        
        media_type = "видео + фото" if has_video else f"{len(photos)} фото"
        
        await context.bot.send_message(
            chat_id=group["chat_id"],
            text=f"📸 Получена медиагруппа: {media_type}!\n\n"
                 f"<b>Найденный заголовок:</b>\n{title_preview}\n\n"
                 f"Выберите действие:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        session["state"] = "selecting_title"
        
    else:
        # Нет текста - показываем кнопки
        keyboard = [
            [
                InlineKeyboardButton("✅ Оформить пост", callback_data="photo_post"),
                InlineKeyboardButton("🎬 Сделать видео", callback_data="photo_video")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        media_type = "видео + фото" if has_video else f"{len(photos)} фото"
        
        await context.bot.send_message(
            chat_id=group["chat_id"],
            text=f"📸 Получена медиагруппа: {media_type}!\n\n"
                 f"Выберите действие:",
            reply_markup=reply_markup
        )
        session["state"] = "idle"
    
    # Удаляем группу из хранилища через час
    asyncio.create_task(cleanup_group(media_group_id))

async def cleanup_group(media_group_id: str):
    """Очистка группы через час"""
    await asyncio.sleep(3600)
    if media_group_id in media_groups:
        del media_groups[media_group_id]

# ==================== ОБРАБОТКА ОДИНОЧНЫХ ФОТО ====================

async def handle_single_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка одного фото (без медиагруппы)"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    message = update.message
    
    if not message or not message.photo:
        return
    
    # Если есть media_group_id - пропускаем
    if hasattr(message, 'media_group_id') and message.media_group_id:
        return
    
    photo = message.photo[-1]
    
    try:
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания фото: {e}")
        await message.reply_text("❌ Не удалось скачать фото")
        return
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {"state": "idle", "audio": None, "audio_selected": None, 
                                 "auto_title": None, "video": None, "photos": [], "current_title": None}
    
    session = user_sessions[user_id]
    session["photos"] = [photo_bytes]
    session["video"] = None
    
    caption = message.caption or ""
    
    if caption.strip():
        auto_title = extract_title_from_text(caption)
        session["auto_title"] = auto_title
        
        keyboard = [
            [
                InlineKeyboardButton("📝 Использовать заголовок из текста", callback_data="title_auto"),
                InlineKeyboardButton("✏️ Свой заголовок", callback_data="title_custom")
            ],
            [
                InlineKeyboardButton("🤖 Улучшить через ИИ", callback_data="title_ai"),
                InlineKeyboardButton("❌ Отмена", callback_data="title_cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        title_preview = auto_title if auto_title else "❌ Не удалось извлечь заголовок"
        
        await message.reply_text(
            f"📸 Фото получено с текстом!\n\n"
            f"<b>Найденный заголовок:</b>\n{title_preview}\n\n"
            f"Выберите действие:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        session["state"] = "selecting_title"
        
    else:
        keyboard = [
            [
                InlineKeyboardButton("✅ Оформить пост", callback_data="photo_post"),
                InlineKeyboardButton("🎬 Сделать видео", callback_data="photo_video")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.reply_text(
            "📸 Фото получено!\n\nВыберите действие:",
            reply_markup=reply_markup
        )
        session["state"] = "idle"

# ==================== СБОР ФОТО ДЛЯ ВИДЕО ====================

async def handle_photo_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбор дополнительных фото для видео"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    message = update.message
    
    if not message or not message.photo:
        return
    
    # Если это медиагруппа - пропускаем
    if hasattr(message, 'media_group_id') and message.media_group_id:
        return
    
    if user_id not in user_sessions:
        return
    
    session = user_sessions[user_id]
    
    if session.get("state") != "collecting_photos":
        return
    
    photo = message.photo[-1]
    
    try:
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания фото: {e}")
        await message.reply_text("❌ Не удалось скачать фото")
        return
    
    session["photos"].append(photo_bytes)
    count = len(session["photos"])
    
    if count >= 10:
        await message.reply_text(f"✅ Собрано {count} фото (максимум).\nНажмите /done для создания видео")
        return
    
    await message.reply_text(
        f"✅ Фото {count} добавлено!\n"
        f"Осталось: {max(0, 3 - count)} фото (минимум 3, максимум 10)\n"
        "Нажмите /done когда будете готовы"
    )

# ==================== ОБРАБОТЧИК ВЫБОРА ЗАГОЛОВКА ====================

async def handle_title_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not update.effective_user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = update.effective_user.id
    data = query.data
    
    if user_id not in user_sessions:
        await query.edit_message_text("❌ Сессия не найдена. Отправьте медиа заново.")
        return
    
    session = user_sessions[user_id]
    
    if data == "title_auto":
        auto_title = session.get("auto_title", "")
        if not auto_title:
            await query.edit_message_text("❌ Не удалось извлечь заголовок из текста")
            return
        
        session["current_title"] = auto_title
        await query.edit_message_text(f"✅ Использую заголовок из текста:\n\n<b>{auto_title}</b>", parse_mode="HTML")
        
        # Проверяем, есть ли видео для обработки
        if session.get("video"):
            keyboard = [
                [InlineKeyboardButton("🎬 Обработать видео", callback_data="process_media")]
            ]
            await query.message.reply_text(
                "✅ Заголовок выбран! Нажмите кнопку для обработки:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            session["state"] = "ready_to_process"
        else:
            # Обрабатываем как фото
            if session["photos"]:
                photo_bytes = session["photos"][0]
                status_msg = await query.message.reply_text("⏳ <b>Обрабатываю фото...</b>", parse_mode="HTML")
                
                processed = process_single_photo(photo_bytes, auto_title)
                
                if processed and len(processed.getvalue()) > 0:
                    await query.message.reply_photo(
                        photo=BytesIO(processed.getvalue()),
                        caption=f"<b>{auto_title}</b>",
                        parse_mode="HTML"
                    )
                    await status_msg.delete()
                else:
                    await status_msg.edit_text("❌ Ошибка обработки фото")
            
            session["state"] = "idle"
            session["photos"] = []
            session["auto_title"] = None
            session["current_title"] = None
        
    elif data == "title_custom":
        await query.edit_message_text("✏️ Отправьте свой текст для заголовка (или нажмите /cancel для отмены):")
        session["state"] = "waiting_custom_title"
        
    elif data == "title_ai":
        auto_title = session.get("auto_title", "")
        if not auto_title:
            await query.edit_message_text("❌ Нет заголовка для улучшения")
            return
        
        await query.edit_message_text("🤖 <b>Улучшаю заголовок через ИИ...</b>\n⏳ Это займет несколько секунд", parse_mode="HTML")
        
        improved = await improve_title_with_ai(auto_title)
        
        if improved and improved != auto_title:
            session["current_title"] = improved
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Использовать этот", callback_data="title_use_ai"),
                    InlineKeyboardButton("🔄 Еще раз", callback_data="title_ai"),
                ],
                [
                    InlineKeyboardButton("✏️ Свой вариант", callback_data="title_custom"),
                    InlineKeyboardButton("❌ Отмена", callback_data="title_cancel")
                ]
            ]
            
            await query.edit_message_text(
                f"🤖 <b>ИИ предложил новый заголовок:</b>\n\n"
                f"<b>Оригинал:</b> {auto_title}\n"
                f"<b>Улучшенный:</b> {improved}\n\n"
                f"Выберите действие:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(
                f"❌ Не удалось улучшить заголовок. Используйте оригинал:\n\n{auto_title}\n\n"
                f"Отправьте свой вариант или нажмите /cancel",
                parse_mode="HTML"
            )
            session["state"] = "waiting_custom_title"
    
    elif data == "title_use_ai":
        current_title = session.get("current_title", "")
        if not current_title:
            await query.edit_message_text("❌ Нет заголовка для использования")
            return
        
        await query.edit_message_text(f"✅ Использую улучшенный заголовок:\n\n<b>{current_title}</b>", parse_mode="HTML")
        
        if session.get("video"):
            keyboard = [
                [InlineKeyboardButton("🎬 Обработать видео", callback_data="process_media")]
            ]
            await query.message.reply_text(
                "✅ Заголовок выбран! Нажмите кнопку для обработки:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            session["state"] = "ready_to_process"
        else:
            if session["photos"]:
                photo_bytes = session["photos"][0]
                status_msg = await query.message.reply_text("⏳ <b>Обрабатываю фото...</b>", parse_mode="HTML")
                
                processed = process_single_photo(photo_bytes, current_title)
                
                if processed and len(processed.getvalue()) > 0:
                    await query.message.reply_photo(
                        photo=BytesIO(processed.getvalue()),
                        caption=f"<b>{current_title}</b>",
                        parse_mode="HTML"
                    )
                    await status_msg.delete()
                else:
                    await status_msg.edit_text("❌ Ошибка обработки фото")
            
            session["state"] = "idle"
            session["photos"] = []
            session["auto_title"] = None
            session["current_title"] = None
    
    elif data == "title_cancel":
        session["state"] = "idle"
        session["video"] = None
        session["photos"] = []
        session["auto_title"] = None
        session["current_title"] = None
        session["audio"] = None
        await query.edit_message_text("❌ Действие отменено")

# ==================== ОБРАБОТКА МЕДИА ====================

async def process_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not update.effective_user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await query.edit_message_text("❌ Сессия не найдена. Отправьте медиа заново.")
        return
    
    session = user_sessions[user_id]
    
    if session.get("state") != "ready_to_process":
        await query.edit_message_text("❌ Сначала выберите заголовок")
        return
    
    title = session.get("current_title", "")
    video_bytes = session.get("video")
    photos = session.get("photos", [])
    
    if not video_bytes or not photos:
        await query.edit_message_text("❌ Нет медиа для обработки")
        return
    
    await query.edit_message_text("⏳ <b>Обрабатываю медиа...</b>\n⏳ Это займет ~1-2 минуты", parse_mode="HTML")
    
    result = create_video_with_photos(video_bytes, photos, title, session.get("audio"))
    
    if result and len(result.getvalue()) > 0:
        await query.message.reply_video(
            video=BytesIO(result.getvalue()),
            caption=f"<b>{title}</b>",
            parse_mode="HTML",
            width=TARGET_W,
            height=TARGET_H
        )
        await query.edit_message_text("✅ Видео готово и отправлено!")
    else:
        await query.edit_message_text("❌ Ошибка создания видео")
    
    session["state"] = "idle"
    session["video"] = None
    session["photos"] = []
    session["auto_title"] = None
    session["current_title"] = None
    session["audio"] = None

# ==================== ОБРАБОТЧИКИ КНОПОК ====================

async def handle_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not update.effective_user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = update.effective_user.id
    data = query.data
    
    if user_id not in user_sessions:
        await query.edit_message_text("❌ Сессия не найдена. Отправьте фото заново.")
        return
    
    session = user_sessions[user_id]
    
    if data in ["title_auto", "title_custom", "title_ai", "title_use_ai", "title_cancel"]:
        await handle_title_choice(update, context)
        return
    
    if data == "photo_post":
        if not session["photos"]:
            await query.edit_message_text("❌ Нет фото для обработки")
            return
        
        photo_bytes = session["photos"][0]
        await query.edit_message_text("⏳ <b>Обрабатываю фото...</b>", parse_mode="HTML")
        await query.message.reply_text("✏️ Отправьте текст для заголовка (или нажмите /cancel для отмены):")
        session["state"] = "waiting_post_title"
        
    elif data == "photo_video":
        if not session["photos"]:
            await query.edit_message_text("❌ Нет фото для обработки")
            return
        
        count = len(session["photos"])
        
        if count >= 3:
            await handle_music_choice(update, context)
            session["state"] = "selecting_music"
        else:
            await query.edit_message_text(
                f"🎬 <b>Создание видео из фото</b>\n\n"
                f"У вас {count} фото. Нужно минимум 3.\n"
                f"Отправьте еще {3 - count} фото (можно несколько в одном сообщении).\n"
                "Когда будете готовы, нажмите /done",
                parse_mode="HTML"
            )
            session["state"] = "collecting_photos"
        
    elif data == "process_media":
        await process_media(update, context)

# ==================== ТЕКСТОВЫЙ ВВОД ====================

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    message = update.message
    
    if not message:
        return
    
    if user_id not in user_sessions:
        return
    
    session = user_sessions[user_id]
    state = session.get("state", "idle")
    
    if state == "waiting_post_title":
        title = message.text.strip()
        
        if not title:
            await message.reply_text("❌ Текст не может быть пустым. Отправьте снова или /cancel")
            return
        
        if session["photos"]:
            photo_bytes = session["photos"][0]
            status_msg = await message.reply_text("⏳ <b>Обрабатываю фото...</b>", parse_mode="HTML")
            
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
        
        session["state"] = "idle"
        session["photos"] = []
        session["auto_title"] = None
        session["current_title"] = None
        session["audio"] = None
        session["audio_selected"] = None
    
    elif state == "waiting_custom_title":
        title = message.text.strip()
        
        if not title:
            await message.reply_text("❌ Текст не может быть пустым. Отправьте снова или /cancel")
            return
        
        session["current_title"] = title
        
        if session.get("video"):
            keyboard = [
                [InlineKeyboardButton("🎬 Обработать видео", callback_data="process_media")]
            ]
            await message.reply_text(
                f"✅ Заголовок сохранен:\n\n<b>{title}</b>\n\nНажмите кнопку для обработки:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            session["state"] = "ready_to_process"
        elif session["photos"]:
            photo_bytes = session["photos"][0]
            status_msg = await message.reply_text("⏳ <b>Обрабатываю фото...</b>", parse_mode="HTML")
            
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
            
            session["state"] = "idle"
            session["photos"] = []
            session["auto_title"] = None
            session["current_title"] = None
            session["audio"] = None
            session["audio_selected"] = None
    
    elif state == "waiting_video_title":
        title = message.text.strip()
        
        if not title:
            await message.reply_text("❌ Текст не может быть пустым. Отправьте снова или /cancel")
            return
        
        if len(session["photos"]) >= 3:
            status_msg = await message.reply_text(
                f"🎬 <b>Создаю видео из {len(session['photos'])} фото...</b>\n⏳ Это займет ~1-2 минуты",
                parse_mode="HTML"
            )
            
            audio_bytes = session.get("audio")
            audio_selected = session.get("audio_selected", "без музыки")
            
            video = create_slideshow_video(session["photos"], title, audio_bytes)
            
            if video and len(video.getvalue()) > 0:
                await status_msg.edit_text("⏳ <b>Отправляю видео...</b>", parse_mode="HTML")
                
                caption = f"<b>{title}</b>"
                if audio_selected and audio_selected != "без музыки":
                    caption += f"\n🎵 Музыка: {audio_selected}"
                
                await message.reply_video(
                    video=BytesIO(video.getvalue()),
                    caption=caption,
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
        
        session["state"] = "idle"
        session["photos"] = []
        session["auto_title"] = None
        session["current_title"] = None
        session["audio"] = None
        session["audio_selected"] = None

# ==================== МУЗЫКА ====================

async def handle_music_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not update.effective_user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await query.edit_message_text("❌ Сессия не найдена. Отправьте фото заново.")
        return
    
    keyboard = [
        [
            InlineKeyboardButton("🎵 Обычная мелодия", callback_data="music_обычная"),
            InlineKeyboardButton("📢 Важная новость", callback_data="music_важная")
        ],
        [
            InlineKeyboardButton("🔇 Без музыки", callback_data="music_no_music"),
            InlineKeyboardButton("❌ Отмена", callback_data="music_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🎵 <b>Выберите музыкальное сопровождение для видео:</b>\n\n"
        "• 🎵 Обычная мелодия - спокойный фон\n"
        "• 📢 Важная новость - энергичная/драматичная\n"
        "• 🔇 Без музыки - тишина",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

async def handle_music_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not update.effective_user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = update.effective_user.id
    data = query.data.replace("music_", "")
    
    if user_id not in user_sessions:
        await query.edit_message_text("❌ Сессия не найдена. Отправьте фото заново.")
        return
    
    session = user_sessions[user_id]
    
    if data == "no_music":
        session["audio"] = None
        session["audio_selected"] = "без музыки"
        await query.edit_message_text("🔇 Видео будет без музыки")
        
        await query.message.reply_text(
            f"✅ Собрано {len(session['photos'])} фото.\n\n✏️ Отправьте текст для заголовка:"
        )
        session["state"] = "waiting_video_title"
        
    elif data == "cancel":
        session["audio"] = None
        session["audio_selected"] = None
        await query.edit_message_text("❌ Выбор музыки отменен")
        
        await query.message.reply_text(
            "📸 Продолжайте отправлять фото или нажмите /done для создания видео"
        )
        session["state"] = "collecting_photos"
        
    elif data in ["важная", "обычная"]:
        audio_name = "Важная новость" if data == "важная" else "Обычная мелодия"
        await query.edit_message_text(f"⏳ Скачиваю музыку '{audio_name}' с GitHub...")
        
        audio_bytes = download_audio_from_github(data)
        
        if audio_bytes:
            session["audio"] = audio_bytes
            session["audio_selected"] = audio_name
            
            await query.edit_message_text(f"✅ Музыка '{audio_name}' загружена! Она будет добавлена к видео")
            
            await query.message.reply_text(
                f"✅ Собрано {len(session['photos'])} фото.\n\n✏️ Отправьте текст для заголовка:"
            )
            session["state"] = "waiting_video_title"
        else:
            await query.edit_message_text(f"❌ Не удалось загрузить музыку '{audio_name}'. Попробуйте снова.")
            await handle_music_choice(update, context)

# ==================== КОМАНДЫ ====================

async def handle_video_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    message = update.message
    
    if not message:
        return
    
    if user_id not in user_sessions:
        await message.reply_text("❌ Нет активной сессии. Отправьте фото сначала.")
        return
    
    session = user_sessions[user_id]
    
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
    
    keyboard = [
        [
            InlineKeyboardButton("🎵 Обычная мелодия", callback_data="music_обычная"),
            InlineKeyboardButton("📢 Важная новость", callback_data="music_важная")
        ],
        [
            InlineKeyboardButton("🔇 Без музыки", callback_data="music_no_music"),
            InlineKeyboardButton("❌ Отмена", callback_data="music_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(
        "🎵 <b>Выберите музыкальное сопровождение для видео:</b>\n\n"
        "• 🎵 Обычная мелодия - спокойный фон\n"
        "• 📢 Важная новость - энергичная/драматичная\n"
        "• 🔇 Без музыки - тишина",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

async def handle_video_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not update.effective_user:
        await query.edit_message_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = query.from_user.id
    
    if user_id in user_sessions:
        user_sessions[user_id] = {"state": "idle", "audio": None, "audio_selected": None, 
                                 "auto_title": None, "video": None, "photos": [], "current_title": None}
    
    await query.edit_message_text("❌ Создание видео отменено")

async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        await update.message.reply_text("❌ Ошибка: пользователь не найден")
        return
    
    user_id = update.effective_user.id
    
    if user_id in user_sessions:
        user_sessions[user_id] = {"state": "idle", "audio": None, "audio_selected": None, 
                                 "auto_title": None, "video": None, "photos": [], "current_title": None}
    
    await update.message.reply_text("✅ Действие отменено")

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 <b>Бот для обработки видео и фото (ЧП ВМ)</b>\n\n"
        f"📢 Канал: <code>{MONITOR_CHANNEL_ID}</code>\n"
        f"📊 Макс. размер: {MAX_VIDEO_SIZE_MB} MB\n\n"
        f"🎬 <b>Что умеет бот:</b>\n"
        f"1️⃣ <b>Видео</b> - обрабатывает видео (градиент + текст)\n"
        f"2️⃣ <b>Фото</b> - отправьте фото:\n"
        f"   • С текстом → предложит использовать заголовок из текста или свой\n"
        f"   • Без текста → покажет кнопки: Оформить пост / Сделать видео\n"
        f"3️⃣ <b>Несколько фото</b> - отправьте в одном сообщении → соберет их все\n"
        f"4️⃣ <b>Видео + Фото</b> - отправьте в одном сообщении → объединит в одно видео\n"
        f"5️⃣ <b>🤖 ИИ</b> - улучшает заголовки через DeepSeek AI\n\n"
        f"🎵 <b>Музыка для видео:</b>\n"
        f"   • 🎵 Обычная мелодия\n"
        f"   • 📢 Важная новость\n"
        f"   • 🔇 Без музыки\n\n"
        f"📎 Просто отправьте видео или фото в бот",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"✅ <b>Бот работает</b>\n\n"
        f"📢 Канал: <code>{MONITOR_CHANNEL_ID}</code>\n"
        f"📨 Уведомления: <code>{ADMIN_CHAT_ID}</code>\n"
        f"⚡ Обработка включена!\n"
        f"🤖 ИИ: {'✅ Доступен' if DEEPSEEK_API_KEY else '❌ Не настроен'}",
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

# ==================== УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК ====================

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Универсальный обработчик для всех сообщений"""
    if not update.effective_user:
        return
    
    message = update.message
    if not message:
        return
    
    # Проверяем, есть ли media_group_id
    media_group_id = getattr(message, 'media_group_id', None)
    
    # Если это часть медиагруппы
    if media_group_id:
        await handle_media_group(update, context)
        return
    
    has_video = hasattr(message, 'video') and message.video
    has_photo = hasattr(message, 'photo') and message.photo
    
    # Только видео
    if has_video:
        await handle_forwarded_message(update, context)
        return
    
    # Только фото - проверяем режим сбора
    if has_photo:
        user_id = update.effective_user.id
        if user_id in user_sessions:
            session = user_sessions[user_id]
            if session.get("state") == "collecting_photos":
                await handle_photo_collection(update, context)
                return
        
        # Одиночное фото
        await handle_single_photo(update, context)
        return
    
    # Текст
    if message.text and not message.text.startswith('/'):
        await handle_text_input(update, context)

# ==================== ЗАПУСК ====================

async def main():
    logger.info("🚀 Бот для видео и фото (ЧП ВМ) запускается...")
    
    download_fonts()
    
    app = Application.builder().token(BOT_TOKEN).build()
    bot = Bot(token=BOT_TOKEN)
    
    logger.info("🔄 Очистка старых сессий...")
    try:
        for attempt in range(3):
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                logger.info("✅ Webhook удалён")
                break
            except Exception as e:
                logger.warning(f"⚠️ Попытка {attempt+1}: {e}")
                await asyncio.sleep(1)
    except Exception as e:
        logger.warning(f"⚠️ Webhook: {e}")
    
    await asyncio.sleep(2)
    
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
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("done", handle_video_done))
    
    # Универсальный обработчик
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND & ~filters.Chat(chat_id=MONITOR_CHANNEL_ID),
        handle_all_messages
    ))
    
    # Обработчики для канала
    app.add_handler(MessageHandler(
        filters.VIDEO & filters.Chat(chat_id=MONITOR_CHANNEL_ID),
        handle_channel_post
    ))
    
    # Callback'и
    app.add_handler(CallbackQueryHandler(handle_photo_callback, pattern="^(photo_post|photo_video|title_auto|title_custom|title_ai|title_use_ai|title_cancel|process_media)$"))
    app.add_handler(CallbackQueryHandler(handle_music_callback, pattern="^music_"))
    app.add_handler(CallbackQueryHandler(handle_video_cancel, pattern="^video_cancel$"))
    
    logger.info("✅ Обработчики зарегистрированы")
    logger.info("📊 Параметры ЧП ВМ:")
    logger.info(f"  • Размер: {TARGET_W}x{TARGET_H}")
    logger.info(f"  • Градиент: {int(CHP_GRADIENT_PCT*100)}%")
    logger.info(f"  • Текст: снизу")
    logger.info("📸 Новые функции:")
    logger.info("  • Обработка фото с авто-извлечением заголовка")
    logger.info("  • Слайд-шоу из 3-10 фото с плавным приближением (+10% за 3с)")
    logger.info("  • Объединение видео + фото в одно видео")
    logger.info("  • 🤖 Улучшение заголовков через DeepSeek AI")
    logger.info("  • Поддержка медиагрупп (несколько фото/видео в одном сообщении)")
    logger.info("🎵 Музыка:")
    logger.info("  • Обычная мелодия")
    logger.info("  • Важная новость")
    
    await app.initialize()
    await app.start()
    
    await app.updater.start_polling(
        allowed_updates=["message", "channel_post", "callback_query"],
        drop_pending_updates=True,
        poll_interval=2.0,
        timeout=60,
        read_timeout=60,
        write_timeout=60,
        connect_timeout=60,
        limit=100
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
