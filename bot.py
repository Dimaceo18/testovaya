# -*- coding: utf-8 -*-
import os
import re
import html
import time
import hashlib
import json
import logging
import signal
import sys
import functools
import fcntl
import atexit
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# Импорты для автоматической выгрузки
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz


# =========================
# Проверка на единственный экземпляр
# =========================
lock_file = '/tmp/bot_instance.lock'
lock_fd = None

def check_single_instance():
    global lock_fd
    try:
        lock_fd = open(lock_file, 'w')
        fcntl.lockf(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        
        def unlock():
            try:
                if lock_fd:
                    fcntl.lockf(lock_fd, fcntl.LOCK_UN)
                    lock_fd.close()
                if os.path.exists(lock_file):
                    os.unlink(lock_file)
            except:
                pass
        
        atexit.register(unlock)
        return True
        
    except IOError:
        if lock_fd:
            lock_fd.close()
        return False
    except Exception as e:
        logger.error(f"Error checking single instance: {e}")
        return True


if not check_single_instance():
    print("Another instance is already running. Exiting.")
    sys.exit(1)


# =========================
# Logging setup
# =========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# =========================
# ENV
# =========================
TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHANNEL = (os.getenv("CHANNEL_USERNAME") or "").strip()
BOT_USERNAME = (os.getenv("BOT_USERNAME") or "").strip().lstrip("@")
SUGGEST_URL = (os.getenv("SUGGEST_URL") or "").strip()

# Настройки автоматической выгрузки
AUTO_NEWS_CHAT_ID = os.getenv("AUTO_NEWS_CHAT_ID")
AUTO_NEWS_TIMEZONE = os.getenv("AUTO_NEWS_TIMEZONE", "Europe/Minsk")
NEWS_BATCH_SIZE = 20
NEWS_MORE_SIZE = 10

if CHANNEL and not CHANNEL.startswith("@"):
    CHANNEL = "@" + CHANNEL

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if " " in TOKEN:
    raise ValueError("BOT_TOKEN must not contain spaces")
if not CHANNEL or CHANNEL == "@":
    raise RuntimeError("CHANNEL_USERNAME is not set")

if not SUGGEST_URL and BOT_USERNAME:
    SUGGEST_URL = f"https://t.me/{BOT_USERNAME}?start=suggest"

# Constants
MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_VIDEO_SIZE = 50 * 1024 * 1024
CACHE_TTL = 3600
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3

FDR_POST_PURPLE_COLOR = (122, 58, 240)
FDR_POST_PLATE_HEIGHT_PCT = 0.15
TEXT_POSITION_TOP = "top"
TEXT_POSITION_BOTTOM = "bottom"

# Константы для видео
VIDEO_TARGET_SIZE = (750, 936)
VIDEO_FPS = 24
VIDEO_BITRATE = "2000k"

# Размеры
SQUARE_SIZE = 1080  # 1:1 квадрат
TARGET_W, TARGET_H = 750, 936  # 4:5 (исправлено с 938 на 936)
STORY_W = 720
STORY_H = 1280

# =========================
# UI BUTTONS
# =========================
BTN_POST = "📝 Оформить пост"
BTN_NEWS = "📰 Получить новости"
BTN_NEWS_BY_LINK = "🔗 Новость по ссылке"
BTN_ENHANCE = "✨ Улучшить качество"
BTN_WATERMARK = "💧 Водяные знаки"
BTN_PRICES = "💰 Цены"

def main_menu_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton(BTN_POST), KeyboardButton(BTN_NEWS))
    kb.row(KeyboardButton(BTN_NEWS_BY_LINK), KeyboardButton(BTN_ENHANCE))
    kb.row(KeyboardButton(BTN_WATERMARK), KeyboardButton(BTN_PRICES))
    kb.row(KeyboardButton("🎥 Видео"), KeyboardButton("🎬 Видео в GIF"))
    return kb


def prices_menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💰 Наши цены", callback_data="prices:list"),
        InlineKeyboardButton("📋 Условия размещения", callback_data="prices:terms"),
        InlineKeyboardButton("📊 График аккаунтов", callback_data="prices:schedule")
    )
    kb.add(InlineKeyboardButton("❌ Закрыть", callback_data="prices:close"))
    return kb


# =========================
# FONTS / CARD
# =========================
FONT_MN = "CaviarDreams.ttf"
FONT_MN_BOLD = "CaviarDreams_Bold.ttf"
FONT_CHP = "Montserrat-Black.ttf"
FONT_AM = "IntroInline.ttf"
FONT_MONTSERRAT_BLACK = "Montserrat-Black.ttf"

FOOTER_TEXT = "MINSK NEWS"

MN_TITLE_ZONE_PCT = 0.23
CHP_GRADIENT_PCT = 0.48
AM_TOP_BLUR_PCT = 0.20
AM_BLUR_RADIUS = 18
AM_BLUR_BLEND = 0.50


# =========================
# NEWS SOURCES
# =========================
NEWS_FIRST_BATCH = 20
NEWS_MORE_BATCH = 10
NEWS_CACHE_TTL_SEC = 10 * 60
NEWS_PER_SOURCE_CAP = 20

NEWS_SOURCES = [
    {"id": "onliner", "name": "Onliner", "kind": "rss", "url": "https://www.onliner.by/feed", "alt_url": "https://people.onliner.by/feed", "limit": 20, "timeout": 10},
    {"id": "sputnik", "name": "Sputnik", "kind": "rss", "url": "https://sputnik.by/export/rss2/index.xml", "limit": 20, "timeout": 10},
    {"id": "telegraf", "name": "Telegraf", "kind": "rss", "url": "https://telegraf.news/feed/", "limit": 20, "timeout": 10},
    {"id": "tochka", "name": "Tochka", "kind": "rss", "url": "https://tochka.by/rss/", "limit": 20, "timeout": 10},
    {"id": "smartpress", "name": "Smartpress", "kind": "rss", "url": "https://smartpress.by/rss/", "limit": 20, "timeout": 10},
    {"id": "minsknews", "name": "Minsknews", "kind": "rss", "url": "https://minsknews.by/feed/", "limit": 20, "timeout": 10},
    {"id": "mlyn", "name": "Mlyn", "kind": "rss", "url": "https://mlyn.by/feed/", "limit": 20, "timeout": 10},
    {"id": "ont", "name": "ONT", "kind": "rss", "url": "https://ont.by/rss/", "limit": 20, "timeout": 10},
    {"id": "times", "name": "Times.by", "kind": "rss", "url": "https://times.by/feed/", "alt_url": "https://times.by/rss/", "limit": 20, "timeout": 10},
    {"id": "blizko", "name": "Blizko.by", "kind": "rss", "url": "https://blizko.by/rss/", "alt_url": "https://blizko.by/novosti/rss/", "limit": 20, "timeout": 10},
    {"id": "realt", "name": "Realt.by", "kind": "rss", "url": "https://realt.by/rss/news/", "limit": 20, "timeout": 10},
    {"id": "newgrodno", "name": "NewGrodno.by", "kind": "rss", "url": "https://newgrodno.by/feed", "limit": 20, "timeout": 10},
    {"id": "officelife", "name": "OfficeLife.media", "kind": "rss", "url": "https://officelife.media/last_news/rss/", "limit": 20, "timeout": 10},
    {"id": "belta", "name": "БелТА", "kind": "rss", "url": "https://www.belta.by/all_news/rss/", "alt_url": "https://www.belta.by/feed/", "limit": 20, "timeout": 10},
]

# =========================
# BOT + SESSION
# =========================
bot = telebot.TeleBot(TOKEN)

SESSION = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
SESSION.mount("http://", adapter)
SESSION.mount("https://", adapter)

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
})

URL_RE = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

user_state: Dict[int, Dict] = {}


# =========================
# Helper functions
# =========================
def validate_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc]) and result.scheme in ['http', 'https']
    except Exception:
        return False


def check_file_size(file_bytes: bytes) -> bool:
    return len(file_bytes) <= MAX_FILE_SIZE


def http_get(url: str, timeout: int = REQUEST_TIMEOUT, headers: dict = None) -> Optional[str]:
    if not validate_url(url):
        return None
    try:
        request_headers = SESSION.headers.copy()
        if headers:
            request_headers.update(headers)
        r = SESSION.get(url, timeout=timeout, headers=request_headers)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.debug(f"HTTP error for {url}: {e}")
        return None


def http_get_bytes(url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[bytes]:
    if not validate_url(url):
        return None
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    except Exception as e:
        logger.debug(f"Failed to get bytes from {url}: {e}")
        return None


def normalize_url(base: str, href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(base, href)


def extract_source_url(text: str) -> str:
    m = URL_RE.search(text or "")
    return m.group(1) if m else ""


def ensure_fonts():
    fonts = [FONT_MN, FONT_MN_BOLD, FONT_CHP, FONT_AM, FONT_MONTSERRAT_BLACK]
    for font in fonts:
        if not os.path.exists(font):
            raise RuntimeError(f"Font not found: {font}")


def clear_state(user_id: int):
    if user_id in user_state:
        template = user_state[user_id].get("template", "MN")
        user_state[user_id] = {"template": template, "step": "idle"}


# =========================
# Crop functions
# =========================
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


def crop_to_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    size = min(w, h)
    left = (w - size) // 2
    top = (h - size) // 2
    return img.crop((left, top, left + size, top + size))


# =========================
# Gradient functions
# =========================
def apply_top_gradient(img: Image.Image, height_pct: float, max_alpha: int = 165) -> Image.Image:
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


def apply_bottom_gradient_soft(img: Image.Image, height_pct: float, max_alpha: int = 165) -> Image.Image:
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


def apply_top_blur_band(img: Image.Image, band_pct: float = AM_TOP_BLUR_PCT, radius: int = AM_BLUR_RADIUS, blend: float = AM_BLUR_BLEND) -> Image.Image:
    w, h = img.size
    band_h = max(1, int(h * band_pct))
    base = img.convert("RGB")

    top = base.crop((0, 0, w, band_h))
    blurred = top.filter(ImageFilter.GaussianBlur(radius=radius))
    mixed = Image.blend(top, blurred, blend)

    overlay = Image.new("RGBA", (w, band_h), (0, 0, 0, 95))
    mixed_rgba = mixed.convert("RGBA")
    final_band = Image.alpha_composite(mixed_rgba, overlay).convert("RGB")

    out = base.copy()
    out.paste(final_band, (0, 0))
    return out


# =========================
# Text wrapping functions
# =========================
def text_width(draw: ImageDraw.ImageDraw, s: str, font: ImageFont.FreeTypeFont) -> int:
    bb = draw.textbbox((0, 0), s, font=font)
    return bb[2] - bb[0]


def wrap_no_truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                     max_width: int, max_lines: int = 6) -> Tuple[List[str], bool]:
    words = [w for w in (text or "").split() if w.strip()]
    if not words:
        return [""], True

    lines: List[str] = []
    cur = ""
    i = 0

    while i < len(words):
        w = words[i]
        test = (cur + " " + w).strip()
        if text_width(draw, test, font) <= max_width:
            cur = test
            i += 1
        else:
            if not cur:
                return [words[i]], False
            lines.append(cur)
            cur = ""
            if len(lines) >= max_lines:
                return lines, False

    if cur:
        lines.append(cur)

    if len(lines) > max_lines:
        return lines[:max_lines], False

    return lines, True


def fit_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    safe_w: int,
    max_block_h: int,
    max_lines: int = 6,
    start_size: int = 90,
    min_size: int = 16,
    line_spacing_ratio: float = 0.22,
) -> Tuple[ImageFont.FreeTypeFont, List[str], List[int], int, int]:
    text = (text or "").strip()
    if not text:
        text = " "

    size = start_size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        lines, ok = wrap_no_truncate(draw, text, font, safe_w, max_lines=max_lines)
        spacing = int(size * line_spacing_ratio)

        heights = []
        total_h = 0
        max_w = 0
        for ln in lines:
            bb = draw.textbbox((0, 0), ln, font=font)
            lw = bb[2] - bb[0]
            lh = bb[3] - bb[1]
            heights.append(lh)
            total_h += lh
            max_w = max(max_w, lw)
        total_h += spacing * (len(lines) - 1)

        if ok and max_w <= safe_w and total_h <= max_block_h:
            return font, lines, heights, spacing, total_h

        size -= 2

    font = ImageFont.truetype(font_path, min_size)
    lines, _ = wrap_no_truncate(draw, text, font, safe_w, max_lines=max_lines)
    spacing = int(min_size * line_spacing_ratio)
    heights = []
    total_h = 0
    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=font)
        lh = bb[3] - bb[1]
        heights.append(lh)
        total_h += lh
    total_h += spacing * (len(lines) - 1)
    return font, lines, heights, spacing, total_h


# =========================
# Card making functions
# =========================
def make_card_mn(photo_bytes: bytes, title_text: str, text_position: str = TEXT_POSITION_TOP, is_square: bool = False) -> BytesIO:
    ensure_fonts()

    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), resample=Image.Resampling.LANCZOS)
    else:
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), resample=Image.Resampling.LANCZOS)
    
    img = ImageEnhance.Brightness(img).enhance(0.55)
    
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    else:
        img = apply_bottom_gradient_soft(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    
    draw = ImageDraw.Draw(img)

    margin_x = int(img.width * 0.06)
    margin_top = int(img.height * 0.06)
    margin_bottom = int(img.height * 0.07)
    safe_w = img.width - 2 * margin_x

    footer_size = max(24, int(img.height * 0.034))
    footer_font = ImageFont.truetype(FONT_MN, footer_size)
    fb = draw.textbbox((0, 0), FOOTER_TEXT, font=footer_font)
    footer_w = fb[2] - fb[0]
    footer_h = fb[3] - fb[1]
    
    title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
    text = (title_text or "").strip().upper()

    font, lines, heights, spacing, total_text_height = fit_text_block(
        draw=draw,
        text=text,
        font_path=FONT_MN,
        safe_w=safe_w,
        max_block_h=title_max_h,
        max_lines=6,
        start_size=int(img.height * 0.11),
        min_size=16,
        line_spacing_ratio=0.22
    )

    block_w = 0
    for ln in lines:
        block_w = max(block_w, text_width(draw, ln, font))
    block_x = (img.width - block_w) // 2
    block_x = max(margin_x, block_x)

    if text_position == TEXT_POSITION_TOP:
        title_y = margin_top
        footer_y = img.height - margin_bottom + (margin_bottom - footer_h) // 2
    else:
        title_y = img.height - margin_bottom - total_text_height - 10
        footer_y = margin_top

    y = title_y
    for i, ln in enumerate(lines):
        draw.text((block_x, y), ln, font=font, fill="white")
        y += heights[i] + spacing

    footer_x = (img.width - footer_w) // 2
    draw.text((footer_x, footer_y), FOOTER_TEXT, font=footer_font, fill="white")

    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out


def make_card_mn_no_text(photo_bytes: bytes, text_position: str = TEXT_POSITION_TOP, is_square: bool = False) -> BytesIO:
    """Шаблон МН без текста - только затемнение и логотип MINSK NEWS"""
    ensure_fonts()

    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), resample=Image.Resampling.LANCZOS)
    else:
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), resample=Image.Resampling.LANCZOS)
    
    img = ImageEnhance.Brightness(img).enhance(0.55)
    
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    else:
        img = apply_bottom_gradient_soft(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    
    draw = ImageDraw.Draw(img)

    margin_bottom = int(img.height * 0.07)
    margin_top = int(img.height * 0.06)
    
    footer_size = max(24, int(img.height * 0.034))
    footer_font = ImageFont.truetype(FONT_MN, footer_size)
    fb = draw.textbbox((0, 0), FOOTER_TEXT, font=footer_font)
    footer_w = fb[2] - fb[0]
    footer_h = fb[3] - fb[1]

    if text_position == TEXT_POSITION_TOP:
        footer_y = img.height - margin_bottom + (margin_bottom - footer_h) // 2
    else:
        footer_y = margin_top

    footer_x = (img.width - footer_w) // 2
    draw.text((footer_x, footer_y), FOOTER_TEXT, font=footer_font, fill="white")

    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out


def make_card_mn2(photo_bytes: bytes, title_text: str, text_position: str = TEXT_POSITION_TOP, font_size_multiplier: float = 1.0, is_square: bool = False, bold_phrase: str = "") -> BytesIO:
    ensure_fonts()

    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), resample=Image.Resampling.LANCZOS)
    else:
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), resample=Image.Resampling.LANCZOS)
    
    img = ImageEnhance.Brightness(img).enhance(0.55)
    
    if text_position == TEXT_POSITION_TOP:
        img = apply_top_gradient(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    else:
        img = apply_bottom_gradient_soft(img, height_pct=CHP_GRADIENT_PCT * 0.75, max_alpha=165)
    
    draw = ImageDraw.Draw(img)

    margin_x = int(img.width * 0.06)
    margin_top = int(img.height * 0.06)
    margin_bottom = int(img.height * 0.07)
    safe_w = img.width - 2 * margin_x

    footer_size = max(24, int(img.height * 0.034))
    footer_font = ImageFont.truetype(FONT_MN, footer_size)
    fb = draw.textbbox((0, 0), FOOTER_TEXT, font=footer_font)
    footer_w = fb[2] - fb[0]
    footer_h = fb[3] - fb[1]
    
    title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
    text = (title_text or "").strip().upper()
    bold_phrase_upper = bold_phrase.strip().upper() if bold_phrase else ""
    bold_words = set(bold_phrase_upper.split())

    base_start_size = int(img.height * 0.11)
    adjusted_start_size = int(base_start_size * font_size_multiplier)
    
    font, lines, heights, spacing, total_text_height = fit_text_block(
        draw=draw,
        text=text,
        font_path=FONT_MN,
        safe_w=safe_w,
        max_block_h=title_max_h,
        max_lines=6,
        start_size=adjusted_start_size,
        min_size=16,
        line_spacing_ratio=0.25
    )

    block_w = 0
    for ln in lines:
        block_w = max(block_w, text_width(draw, ln, font))
    block_x = (img.width - block_w) // 2
    block_x = max(margin_x, block_x)

    if text_position == TEXT_POSITION_TOP:
        title_y = margin_top
        footer_y = img.height - margin_bottom + (margin_bottom - footer_h) // 2
    else:
        title_y = img.height - margin_bottom - total_text_height - 10
        footer_y = margin_top

    def draw_line_with_bold(line_text, x_start, y_pos):
        words = line_text.split()
        current_x = x_start
        for word in words:
            if word in bold_words:
                bold_font = ImageFont.truetype(FONT_MN_BOLD, font.size)
                draw.text((current_x, y_pos), word, font=bold_font, fill="white")
            else:
                draw.text((current_x, y_pos), word, font=font, fill="white")
            
            if word != words[-1]:
                space_width = text_width(draw, " ", font)
                current_x += text_width(draw, word, font) + space_width
            else:
                current_x += text_width(draw, word, font)

    y = title_y
    for i, ln in enumerate(lines):
        draw_line_with_bold(ln, block_x, y)
        
        if i < len(lines) - 1:
            line_height = max(heights[i], int(font.size * 0.9))
            y += line_height + spacing
        else:
            y += heights[i]

    footer_x = (img.width - footer_w) // 2
    draw.text((footer_x, footer_y), FOOTER_TEXT, font=footer_font, fill="white")

    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out


def make_card_mn_tg(photo_bytes: bytes, title_text: str, text_position: str = TEXT_POSITION_TOP, is_square: bool = False) -> BytesIO:
    ensure_fonts()

    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), resample=Image.Resampling.LANCZOS)
    else:
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), resample=Image.Resampling.LANCZOS)
    
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    font_size = int(img.width * 0.08)
    font = ImageFont.truetype(FONT_MN, font_size)
    
    text_bbox = draw.textbbox((0, 0), FOOTER_TEXT, font=font)
    text_width_val = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    
    x = (img.width - text_width_val) // 2
    
    if text_position == TEXT_POSITION_TOP:
        y = int(img.height * 0.2) - (text_height // 2)
    else:
        y = int(img.height * 0.8) - (text_height // 2)
    
    draw.text((x, y), FOOTER_TEXT, font=font, fill=(255, 255, 255, 38))
    
    result = Image.alpha_composite(img.convert("RGBA"), overlay)
    result = result.convert("RGB")
    
    out = BytesIO()
    result.save(out, format="JPEG", quality=95, optimize=True)
    out.seek(0)
    return out


def make_card_chp(photo_bytes: bytes, title_text: str, text_position: str = TEXT_POSITION_TOP, is_square: bool = False) -> BytesIO:
    ensure_fonts()

    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), resample=Image.Resampling.LANCZOS)
    else:
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), resample=Image.Resampling.LANCZOS)
    
    img = ImageEnhance.Brightness(img).enhance(0.85)
    
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
    text = (title_text or "").strip().upper()

    font, lines, heights, spacing, total_h = fit_text_block(
        draw=draw,
        text=text,
        font_path=FONT_CHP,
        safe_w=safe_w,
        max_block_h=title_max_h,
        max_lines=6,
        start_size=int(img.height * 0.11),
        min_size=16,
        line_spacing_ratio=0.22
    )

    if text_position == TEXT_POSITION_TOP:
        y = margin_top
    else:
        y = img.height - margin_bottom - total_h
    
    for i, ln in enumerate(lines):
        draw.text((margin_x, y), ln, font=font, fill="white")
        y += heights[i] + spacing

    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out


def make_card_am(photo_bytes: bytes, title_text: str, is_square: bool = False) -> BytesIO:
    ensure_fonts()

    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), resample=Image.Resampling.LANCZOS)
    else:
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), resample=Image.Resampling.LANCZOS)
    
    img = apply_top_blur_band(img)

    draw = ImageDraw.Draw(img)

    margin_x = int(img.width * 0.055)
    band_h = int(img.height * AM_TOP_BLUR_PCT)
    safe_w = img.width - 2 * margin_x
    text = (title_text or "").strip().upper()

    text_zone_top = int(band_h * 0.12)
    text_zone_bottom = int(band_h * 0.12)
    text_zone_h = max(1, band_h - text_zone_top - text_zone_bottom)

    font, lines, heights, spacing, total_h = fit_text_block(
        draw=draw,
        text=text,
        font_path=FONT_AM,
        safe_w=safe_w,
        max_block_h=text_zone_h,
        max_lines=3,
        start_size=int(img.height * 0.060),
        min_size=20,
        line_spacing_ratio=0.16
    )
    
    # Загружаем дополнительный шрифт для символов (Montserrat-Black)
    special_font = ImageFont.truetype(FONT_CHP, font.size)

    y = text_zone_top + max(0, (text_zone_h - total_h) // 2)
    
    for i, ln in enumerate(lines):
        # Разбиваем строку на части для обработки специальных символов
        parts = []
        current_text = ""
        
        j = 0
        while j < len(ln):
            char = ln[j]
            if char == '%' or char == '"':
                if current_text:
                    parts.append(('normal', current_text))
                    current_text = ""
                parts.append(('special', char))
            else:
                current_text += char
            j += 1
        
        if current_text:
            parts.append(('normal', current_text))
        
        # Вычисляем ширину всей строки
        line_w = 0
        for part_type, part_text in parts:
            if part_type == 'normal':
                line_w += text_width(draw, part_text, font)
            else:
                line_w += text_width(draw, part_text, special_font)
        
        x = (img.width - line_w) // 2
        
        current_x = x
        for part_type, part_text in parts:
            if part_type == 'normal':
                draw.text((current_x, y), part_text, font=font, fill="white")
                current_x += text_width(draw, part_text, font)
            else:
                draw.text((current_x, y), part_text, font=special_font, fill="white")
                current_x += text_width(draw, part_text, special_font)
        
        y += heights[i] + spacing

    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out


def make_card_fdr_story(photo_bytes: bytes, title: str, body_text: str) -> BytesIO:
    ensure_fonts()

    canvas = Image.new("RGB", (STORY_W, STORY_H), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    photo_h = 410
    header_h = 220

    photo = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    def fit_cover(im: Image.Image, target_w: int, target_h: int) -> Image.Image:
        src_w, src_h = im.size
        scale = max(target_w / src_w, target_h / src_h)
        nw, nh = int(src_w * scale), int(src_h * scale)
        resized = im.resize((nw, nh), Image.LANCZOS)
        left = max(0, (nw - target_w) // 2)
        top = max(0, (nh - target_h) // 2)
        return resized.crop((left, top, left + target_w, top + target_h))
    
    story_photo = fit_cover(photo, STORY_W, photo_h)
    canvas.paste(story_photo, (0, 0))

    purple_color = (122, 58, 240)
    canvas.paste(Image.new("RGB", (STORY_W, header_h), purple_color), (0, photo_h))

    draw.rectangle([0, photo_h + header_h, STORY_W, STORY_H], fill=(0, 0, 0))

    padding = 34

    header_box = (padding, photo_h + padding, STORY_W - padding, photo_h + header_h - padding)
    body_box = (padding, photo_h + header_h + padding, STORY_W - padding, STORY_H - padding)

    title_font, title_gap, title_paragraph_gap = _fit_story_text(
        draw, title, header_box, min_size=28, max_size=54,
        line_gap_ratio=0.08, paragraph_gap_ratio=0.18
    )

    _draw_story_text(draw, title, header_box, title_font, fill=(255, 255, 255),
                     align="center", valign="center", line_gap=title_gap,
                     paragraph_gap_extra=title_paragraph_gap)

    body_font, body_gap, body_paragraph_gap = _fit_story_text(
        draw, body_text, body_box, min_size=14, max_size=30,
        line_gap_ratio=0.10, paragraph_gap_ratio=0.32
    )

    _draw_story_text(draw, body_text, body_box, body_font, fill=(255, 255, 255),
                     align="left", valign="top", line_gap=body_gap,
                     paragraph_gap_extra=body_paragraph_gap)

    out = BytesIO()
    canvas.save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    return out


def make_card_fdr_post(photo_bytes: bytes, title_text: str, highlight_phrase: str, is_square: bool = False) -> BytesIO:
    ensure_fonts()

    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    
    if is_square:
        img = crop_to_square(img)
        img = img.resize((SQUARE_SIZE, SQUARE_SIZE), resample=Image.Resampling.LANCZOS)
    else:
        img = crop_to_4x5(img)
        img = img.resize((TARGET_W, TARGET_H), resample=Image.Resampling.LANCZOS)
    
    img = ImageEnhance.Brightness(img).enhance(0.85)
    img = apply_bottom_gradient(img, height_pct=CHP_GRADIENT_PCT, max_alpha=220)
    
    draw = ImageDraw.Draw(img)
    
    margin_x = int(img.width * 0.06)
    margin_bottom = int(img.height * 0.08)
    safe_w = img.width - 2 * margin_x
    
    title_text_upper = title_text.strip().upper()
    highlight_phrase_upper = highlight_phrase.strip().upper()
    highlight_words = set(highlight_phrase_upper.split())
    
    title_max_h = int(img.height * MN_TITLE_ZONE_PCT)
    
    font, lines, heights, spacing, total_h = fit_text_block(
        draw=draw,
        text=title_text_upper,
        font_path=FONT_CHP,
        safe_w=safe_w,
        max_block_h=title_max_h,
        max_lines=6,
        start_size=int(img.height * 0.11),
        min_size=16,
        line_spacing_ratio=0.22
    )
    
    base_y = img.height - margin_bottom - total_h
    
    y = base_y
    for line_idx, line in enumerate(lines):
        line_words = line.split()
        current_x = margin_x
        
        for word in line_words:
            word_bbox = draw.textbbox((current_x, y), word, font=font)
            word_x1, word_y1, word_x2, word_y2 = word_bbox
            
            if word in highlight_words:
                padding = 10
                draw.rectangle(
                    [word_x1 - padding, word_y1 - padding,
                     word_x2 + padding, word_y2 + padding],
                    fill=FDR_POST_PURPLE_COLOR
                )
            
            if word != line_words[-1]:
                space_width = text_width(draw, " ", font)
                current_x += text_width(draw, word, font) + space_width
            else:
                current_x += text_width(draw, word, font)
        
        y += heights[line_idx] + spacing
    
    y = base_y
    for line_idx, line in enumerate(lines):
        line_words = line.split()
        current_x = margin_x
        
        for word in line_words:
            draw.text((current_x, y), word, font=font, fill="white")
            if word != line_words[-1]:
                space_width = text_width(draw, " ", font)
                current_x += text_width(draw, word, font) + space_width
            else:
                current_x += text_width(draw, word, font)
        
        y += heights[line_idx] + spacing
    
    out = BytesIO()
    img.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    out.seek(0)
    return out


def _wrap_text_preserve_paragraphs(draw, text, font, max_w):
    paragraphs = [p.strip() for p in (text or "").replace("\r", "\n").split("\n")]
    all_lines = []
    for p in paragraphs:
        if not p:
            if all_lines and all_lines[-1] != "":
                all_lines.append("")
            continue
        words = p.split()
        if not words:
            continue
        current = words[0]
        for word in words[1:]:
            test = current + " " + word
            bbox = draw.textbbox((0, 0), test, font=font)
            if (bbox[2] - bbox[0]) <= max_w:
                current = test
            else:
                all_lines.append(current)
                current = word
        all_lines.append(current)
        all_lines.append("")
    while all_lines and all_lines[-1] == "":
        all_lines.pop()
    return all_lines


def _fit_story_text(draw, text, box, min_size, max_size, line_gap_ratio=0.18, paragraph_gap_ratio=0.35):
    x1, y1, x2, y2 = box
    max_w = x2 - x1
    max_h = y2 - y1

    selected_font = ImageFont.truetype(FONT_MONTSERRAT_BLACK, min_size)
    selected_gap = 8
    selected_paragraph_gap = 12

    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(FONT_MONTSERRAT_BLACK, size)
        lines = _wrap_text_preserve_paragraphs(draw, text, font, max_w)
        if not lines:
            continue

        line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
        gap = max(4, int(line_h * line_gap_ratio))
        paragraph_gap = max(gap + 2, int(line_h * paragraph_gap_ratio))

        total_h = 0
        max_line_w = 0
        for line in lines:
            if line == "":
                total_h += paragraph_gap
                continue
            lw = font.getbbox(line)[2] - font.getbbox(line)[0]
            max_line_w = max(max_line_w, lw)
            total_h += line_h + gap

        if total_h <= max_h and max_line_w <= max_w:
            selected_font = font
            selected_gap = gap
            selected_paragraph_gap = paragraph_gap
            break

    return selected_font, selected_gap, selected_paragraph_gap


def _draw_story_text(draw, text, box, font, fill=(255, 255, 255), align="center", valign="center",
                     line_gap=10, paragraph_gap_extra=10):
    x1, y1, x2, y2 = box
    max_w = x2 - x1
    max_h = y2 - y1

    lines = _wrap_text_preserve_paragraphs(draw, text, font, max_w)
    if not lines:
        return

    line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
    total_h = 0
    for line in lines:
        if line == "":
            total_h += paragraph_gap_extra
        else:
            total_h += line_h + line_gap

    if valign == "top":
        y = y1
    else:
        y = y1 + (max_h - total_h) // 2

    for line in lines:
        if line == "":
            y += paragraph_gap_extra
            continue
        line_w = font.getbbox(line)[2] - font.getbbox(line)[0]
        if align == "center":
            x = x1 + (max_w - line_w) // 2
        elif align == "left":
            x = x1
        else:
            x = x2 - line_w
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h + line_gap


def make_card(photo_bytes: bytes, title_text: str, template: str, body_text: str = "", highlight_phrase: str = "", text_position: str = TEXT_POSITION_TOP, font_size_multiplier: float = 1.0, is_square: bool = False, bold_phrase: str = "") -> BytesIO:
    if template == "CHP":
        return make_card_chp(photo_bytes, title_text, text_position, is_square)
    if template == "AM":
        return make_card_am(photo_bytes, title_text, is_square)
    if template == "FDR_STORY":
        return make_card_fdr_story(photo_bytes, title_text, body_text)
    if template == "FDR_POST":
        return make_card_fdr_post(photo_bytes, title_text, highlight_phrase, is_square)
    if template == "MN_TG":
        return make_card_mn_tg(photo_bytes, title_text, text_position, is_square)
    if template == "MN2":
        return make_card_mn2(photo_bytes, title_text, text_position, font_size_multiplier, is_square, bold_phrase)
    if template == "MN_NO_TEXT":
        return make_card_mn_no_text(photo_bytes, text_position, is_square)
    return make_card_mn(photo_bytes, title_text, text_position, is_square)


# =========================
# Keyboard layouts
# =========================
def template_kb(is_square: bool = False):
    kb = InlineKeyboardMarkup()
    prefix = "square:" if is_square else "tpl:"
    
    if is_square:
        kb.row(
            InlineKeyboardButton("📰 МН", callback_data=f"{prefix}MN"),
            InlineKeyboardButton("🚫 МН без текста", callback_data=f"{prefix}MN_NO_TEXT"),
            InlineKeyboardButton("🚨 ЧП ВМ", callback_data=f"{prefix}CHP"),
        )
        kb.row(
            InlineKeyboardButton("✨ АМ", callback_data=f"{prefix}AM"),
            InlineKeyboardButton("💜 Пост ФДР", callback_data=f"{prefix}FDR_POST"),
        )
        kb.row(
            InlineKeyboardButton("🆕 МН 2", callback_data=f"{prefix}MN2"),
            InlineKeyboardButton("📱 МН ТГ", callback_data=f"{prefix}MN_TG"),
        )
        kb.row(InlineKeyboardButton("◀️ Назад к оформлению", callback_data="square:back"))
    else:
        kb.row(
            InlineKeyboardButton("📰 МН", callback_data=f"{prefix}MN"),
            InlineKeyboardButton("🚫 МН без текста", callback_data=f"{prefix}MN_NO_TEXT"),
            InlineKeyboardButton("🚨 ЧП ВМ", callback_data=f"{prefix}CHP"),
        )
        kb.row(
            InlineKeyboardButton("✨ АМ", callback_data=f"{prefix}AM"),
            InlineKeyboardButton("📱 Сторис ФДР", callback_data=f"{prefix}FDR_STORY"),
        )
        kb.row(
            InlineKeyboardButton("💜 Пост ФДР", callback_data=f"{prefix}FDR_POST"),
            InlineKeyboardButton("📱 МН ТГ", callback_data=f"{prefix}MN_TG"),
        )
        kb.row(
            InlineKeyboardButton("🆕 МН 2", callback_data=f"{prefix}MN2"),
            InlineKeyboardButton("⬛ Квадраты", callback_data="show_squares"),
        )
    return kb


def text_position_kb(is_square: bool = False):
    kb = InlineKeyboardMarkup(row_width=2)
    prefix = "square_pos:" if is_square else "text_pos:"
    kb.add(
        InlineKeyboardButton("⬆️ Сверху", callback_data=f"{prefix}top"),
        InlineKeyboardButton("⬇️ Снизу", callback_data=f"{prefix}bottom")
    )
    return kb


def font_size_kb(current_multiplier: float = 1.0, is_square: bool = False):
    kb = InlineKeyboardMarkup(row_width=3)
    prefix = "square_font:" if is_square else "font_size:"
    kb.add(
        InlineKeyboardButton("➖", callback_data=f"{prefix}minus:{current_multiplier}"),
        InlineKeyboardButton(f"{int(current_multiplier*100)}%", callback_data=f"{prefix}current"),
        InlineKeyboardButton("➕", callback_data=f"{prefix}plus:{current_multiplier}")
    )
    kb.add(InlineKeyboardButton("✅ Готово", callback_data=f"{prefix}done"))
    return kb


def preview_kb(source_url: str = ""):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="publish"),
        InlineKeyboardButton("✏️ Редактировать текст", callback_data="edit_body"),
        InlineKeyboardButton("✏️ Редактировать заголовок", callback_data="edit_title"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    )
    if source_url:
        kb.add(InlineKeyboardButton("🔗 Источник", url=source_url))
    return kb


def preview_kb_no_text():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Опубликовать", callback_data="publish_no_text"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    )
    return kb


def channel_kb():
    kb = InlineKeyboardMarkup()
    if SUGGEST_URL:
        kb.add(InlineKeyboardButton("📝 Предложить новость", url=SUGGEST_URL))
    return kb


# =========================
# Prices and terms
# =========================
def get_prices_text() -> str:
    return """
💰 <b>НАШИ ЦЕНЫ</b>

Можем предложить вам несколько вариантов размещений, от одиночных постов до полного комплекса:

🔻 <b>Размещение только в</b> minsk_news 478.000 чел. 
Пост + stories — 550 руб.

🔻 <b>Пакет «МИНИ»</b> (более 860.000 подписчиков) — 685 рублей.

1. minsk_news
2. afishaminsk
3. tvoyminsk
4. minskgood
5. novostiminska
6. minskhot
7. minsksmile

🔻 <b>Пакет «СТАНДАРТ»</b> (более 1 300.000 подписчиков): 745 рублей.

1. minsk_news
2. minskchp
3. afishaminsk
4. tvoyminsk
5. vestiminska
6. minskpress
7. xxminsk
8. minskgood
9. novostiminska
10. minskhot
11. minsksmile

🔻 <b>Пакет «ПРЕМИУМ»</b> (более 1 700.000 подписчиков): <b>905 рублей</b>.

<b>Instagram:</b> все 11 аккаунтов
<b>Вконтакте:</b> 9 сообществ
<b>Телеграм:</b> 2 канала
"""


def get_terms_text() -> str:
    return """
🔔 <b>УСЛОВИЯ РАЗМЕЩЕНИЯ:</b>

1. Инстаграм и Вконтакте — пост 1 час на первом месте в ленте
2. Телеграм — пост на 30 минут на первом месте

Рекламные посты размещаются на 7 дней в ленте
"""


def get_schedule_text() -> str:
    return "📊 График аккаунтов:\n\nminsk_news -\nminskchp -\nafishaminsk -\ntvoyminsk -\nvestiminska -\nminskpress -\nxxminsk -\nminskgood -\nnovostiminska -\nminskhot -\nminsksmile -"


# =========================
# Caption formatting
# =========================
def build_caption_html(title: str, body: str) -> str:
    return f"<b>📰 {html.escape(title)}</b>\n\n{html.escape(body)}".strip()


# =========================
# Callback handlers (основные)
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("tpl:") or c.data.startswith("square:"))
def on_tpl(c):
    uid = c.from_user.id
    parts = c.data.split(":", 1)
    prefix = parts[0]
    tpl = parts[1]
    
    is_square = (prefix == "square")
    st = user_state.get(uid) or {}
    
    if tpl == "back" and is_square:
        st["step"] = "waiting_template"
        user_state[uid] = st
        try:
            bot.edit_message_text(
                "📝 Выбери шаблон оформления:",
                c.message.chat.id,
                c.message.message_id,
                reply_markup=template_kb(False)
            )
        except:
            bot.send_message(c.message.chat.id, "📝 Выбери шаблон оформления:", reply_markup=template_kb(False))
        bot.answer_callback_query(c.id)
        return
    
    st["is_square"] = is_square
    st["template"] = tpl
    
    if tpl == "MN_NO_TEXT":
        st["step"] = "waiting_text_position"
        user_state[uid] = st
        size_text = "квадратный " if is_square else ""
        bot.answer_callback_query(c.id, "Шаблон 'МН без текста' выбран ✅")
        try:
            bot.edit_message_text(
                f"🚫 Выбран {size_text}шаблон <b>МН без текста</b>\n\nГде разместить логотип?",
                c.message.chat.id,
                c.message.message_id,
                parse_mode="HTML",
                reply_markup=text_position_kb(is_square)
            )
        except:
            bot.send_message(
                c.message.chat.id,
                f"🚫 Выбран {size_text}шаблон <b>МН без текста</b>\n\nГде разместить логотип?",
                parse_mode="HTML",
                reply_markup=text_position_kb(is_square)
            )
    elif tpl in ["MN_TG", "MN2", "CHP"]:
        if tpl == "MN2":
            st["step"] = "waiting_font_size"
            user_state[uid] = st
            bot.answer_callback_query(c.id, "Шаблон МН 2 выбран ✅")
            size_text = "квадратного " if is_square else ""
            try:
                bot.edit_message_text(
                    f"🔤 Настрой размер шрифта для {size_text}заголовка:",
                    c.message.chat.id,
                    c.message.message_id,
                    reply_markup=font_size_kb(1.0, is_square)
                )
            except:
                bot.send_message(
                    c.message.chat.id,
                    f"🔤 Настрой размер шрифта для {size_text}заголовка:",
                    reply_markup=font_size_kb(1.0, is_square)
                )
        else:
            st["step"] = "waiting_text_position"
            user_state[uid] = st
            template_names = {"MN_TG": "МН ТГ", "CHP": "ЧП ВМ"}
            template_name = template_names.get(tpl, tpl)
            size_text = "квадратный " if is_square else ""
            bot.answer_callback_query(c.id, f"Шаблон {template_name} выбран ✅")
            try:
                bot.edit_message_text(
                    f"📰 Выбран {size_text}шаблон <b>{template_name}</b>\n\nГде разместить текст?",
                    c.message.chat.id,
                    c.message.message_id,
                    parse_mode="HTML",
                    reply_markup=text_position_kb(is_square)
                )
            except:
                bot.send_message(
                    c.message.chat.id,
                    f"📰 Выбран {size_text}шаблон <b>{template_name}</b>\n\nГде разместить текст?",
                    parse_mode="HTML",
                    reply_markup=text_position_kb(is_square)
                )
    elif tpl in ["MN", "AM"]:
        st["step"] = "waiting_text_position"
        user_state[uid] = st
        template_names = {"MN": "МН", "AM": "АМ"}
        template_name = template_names.get(tpl, tpl)
        size_text = "квадратный " if is_square else ""
        bot.answer_callback_query(c.id, f"Шаблон {template_name} выбран ✅")
        try:
            bot.edit_message_text(
                f"📰 Выбран {size_text}шаблон <b>{template_name}</b>\n\nГде разместить текст?",
                c.message.chat.id,
                c.message.message_id,
                parse_mode="HTML",
                reply_markup=text_position_kb(is_square)
            )
        except:
            bot.send_message(
                c.message.chat.id,
                f"📰 Выбран {size_text}шаблон <b>{template_name}</b>\n\nГде разместить текст?",
                parse_mode="HTML",
                reply_markup=text_position_kb(is_square)
            )
    elif tpl == "FDR_POST":
        st["step"] = "waiting_photo_fdr_post"
        user_state[uid] = st
        bot.answer_callback_query(c.id, "Шаблон 'Пост ФДР' выбран ✅")
        size_text = "квадратное " if is_square else ""
        try:
            bot.edit_message_text(
                f"💜 Выбран {size_text}шаблон <b>Пост ФДР</b>\n\n📸 Пришли {size_text}фото для поста.",
                c.message.chat.id,
                c.message.message_id,
                parse_mode="HTML"
            )
        except:
            bot.send_message(
                c.message.chat.id,
                f"💜 Выбран {size_text}шаблон <b>Пост ФДР</b>\n\n📸 Пришли {size_text}фото для поста.",
                parse_mode="HTML"
            )
    elif tpl == "FDR_STORY" and not is_square:
        st["step"] = "waiting_photo_fdr_story"
        user_state[uid] = st
        bot.answer_callback_query(c.id, "Шаблон 'Сторис ФДР' выбран ✅")
        try:
            bot.edit_message_text(
                "📱 Выбран шаблон <b>Сторис ФДР</b>\n\n📸 Пришли фото для сторис.",
                c.message.chat.id,
                c.message.message_id,
                parse_mode="HTML"
            )
        except:
            bot.send_message(
                c.message.chat.id,
                "📱 Выбран шаблон <b>Сторис ФДР</b>\n\n📸 Пришли фото для сторис.",
                parse_mode="HTML"
            )
    else:
        bot.answer_callback_query(c.id, "Этот шаблон недоступен")
        return


@bot.callback_query_handler(func=lambda c: c.data.startswith("text_pos:") or c.data.startswith("square_pos:"))
def on_text_position(c):
    uid = c.from_user.id
    parts = c.data.split(":", 1)
    prefix = parts[0]
    position = parts[1]
    
    is_square = (prefix == "square_pos")
    st = user_state.get(uid) or {}
    
    st["text_position"] = position
    st["step"] = "waiting_photo"
    user_state[uid] = st
    
    position_text = "сверху" if position == "top" else "снизу"
    size_text = "квадратное " if is_square else ""
    try:
        bot.edit_message_text(
            f"Логотип будет расположен <b>{position_text}</b>.\n\nТеперь пришли {size_text}фото 📷",
            c.message.chat.id,
            c.message.message_id,
            parse_mode="HTML"
        )
    except:
        bot.send_message(
            c.message.chat.id,
            f"Логотип будет расположен <b>{position_text}</b>.\n\nТеперь пришли {size_text}фото 📷",
            parse_mode="HTML"
        )
    bot.answer_callback_query(c.id, f"Логотип будет {position_text} ✅")


@bot.callback_query_handler(func=lambda c: c.data.startswith("font_size:") or c.data.startswith("square_font:"))
def on_font_size_adjust(c):
    uid = c.from_user.id
    parts = c.data.split(":")
    prefix = parts[0]
    action = parts[1]
    
    is_square = (prefix == "square_font")
    st = user_state.get(uid) or {}
    
    if action == "done":
        st["step"] = "waiting_text_position"
        user_state[uid] = st
        try:
            bot.edit_message_text(
                "✅ Размер шрифта настроен. Теперь выбери расположение текста:",
                c.message.chat.id,
                c.message.message_id,
                reply_markup=text_position_kb(is_square)
            )
        except:
            bot.send_message(
                c.message.chat.id,
                "✅ Размер шрифта настроен. Теперь выбери расположение текста:",
                reply_markup=text_position_kb(is_square)
            )
        bot.answer_callback_query(c.id, "Настройки сохранены")
        return
    
    current = float(parts[2]) if len(parts) > 2 else st.get("font_size_multiplier", 1.0)
    
    if action == "plus":
        new_mult = min(2.0, current + 0.1)
    elif action == "minus":
        new_mult = max(0.5, current - 0.1)
    else:
        bot.answer_callback_query(c.id)
        return
    
    st["font_size_multiplier"] = new_mult
    user_state[uid] = st
    
    try:
        bot.edit_message_text(
            f"🔤 Текущий размер: {int(new_mult*100)}%\nИспользуй кнопки + и - для регулировки.\nНажми «Готово» когда закончишь.",
            c.message.chat.id,
            c.message.message_id,
            reply_markup=font_size_kb(new_mult, is_square)
        )
    except:
        bot.send_message(
            c.message.chat.id,
            f"🔤 Текущий размер: {int(new_mult*100)}%\nИспользуй кнопки + и - для регулировки.\nНажми «Готово» когда закончишь.",
            reply_markup=font_size_kb(new_mult, is_square)
        )
    
    bot.answer_callback_query(c.id)


@bot.callback_query_handler(func=lambda c: c.data in ["publish", "publish_no_text", "edit_body", "edit_title", "cancel"])
def on_action(call):
    uid = call.from_user.id
    st = user_state.get(uid)

    if not st or st.get("step") != "waiting_action":
        bot.answer_callback_query(call.id, "Нет активного превью")
        return

    if call.data == "publish":
        try:
            title_to_use = st.get("title", "")
            caption = build_caption_html(title_to_use, st.get("body_raw", ""))
            bot.send_photo(CHANNEL, BytesIO(st["card_bytes"]), caption=caption, parse_mode="HTML", reply_markup=channel_kb())
            bot.answer_callback_query(call.id, "Опубликовано ✅")
            bot.send_message(call.message.chat.id, "✅ Готово!", reply_markup=main_menu_kb())
            clear_state(uid)
        except Exception as e:
            logger.error(f"Error publishing: {e}")
            bot.answer_callback_query(call.id, "Ошибка публикации")
    
    elif call.data == "publish_no_text":
        try:
            bot.send_photo(CHANNEL, BytesIO(st["card_bytes"]), reply_markup=channel_kb())
            bot.answer_callback_query(call.id, "Опубликовано ✅")
            bot.send_message(call.message.chat.id, "✅ Готово!", reply_markup=main_menu_kb())
            clear_state(uid)
        except Exception as e:
            logger.error(f"Error publishing: {e}")
            bot.answer_callback_query(call.id, "Ошибка публикации")

    elif call.data == "edit_body":
        st["step"] = "waiting_body"
        user_state[uid] = st
        bot.answer_callback_query(call.id, "✏️ Введи новый текст")
        bot.send_message(call.message.chat.id, "📝 Пришли новый ОСНОВНОЙ ТЕКСТ:", reply_markup=main_menu_kb())

    elif call.data == "edit_title":
        st["step"] = "waiting_title"
        user_state[uid] = st
        bot.answer_callback_query(call.id, "✏️ Введи новый заголовок")
        bot.send_message(call.message.chat.id, "📝 Пришли новый ЗАГОЛОВОК:", reply_markup=main_menu_kb())

    elif call.data == "cancel":
        bot.answer_callback_query(call.id, "Отменено ❌")
        clear_state(uid)
        bot.send_message(call.message.chat.id, "❌ Отменено", reply_markup=main_menu_kb())


# =========================
# Message handlers
# =========================
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "👋 <b>Привет! Я бот для оформления постов</b>\n\n"
        "<b>📝 Доступные шаблоны:</b>\n"
        "• 📰 МН — классический\n"
        "• 🚫 МН без текста — только затемнение и логотип\n"
        "• 🚨 ЧП ВМ — яркий, контрастный\n"
        "• ✨ АМ — с размытой верхней полосой\n"
        "• 📱 Сторис ФДР — формат историй\n"
        "• 💜 Пост ФДР — с фиолетовой плашкой\n"
        "• 📱 МН ТГ — для Telegram\n"
        "• 🆕 МН 2 — с жирным выделением\n\n"
        "Нажми «Оформить пост» 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )


@bot.message_handler(func=lambda message: message.text == BTN_POST)
def handle_post_button(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "waiting_template"}
    bot.send_message(message.chat.id, "📝 <b>Выбери шаблон:</b>", parse_mode="HTML", reply_markup=template_kb())


@bot.message_handler(content_types=["photo"])
def on_photo(message):
    uid = message.from_user.id
    st = user_state.get(uid) or {}
    step = st.get("step")
    
    if step in ["waiting_photo", "waiting_photo_fdr_story", "waiting_photo_fdr_post"]:
        try:
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
            photo_bytes = SESSION.get(file_url, timeout=30).content
            
            st["photo_bytes"] = photo_bytes
            
            if step == "waiting_photo_fdr_story":
                st["step"] = "waiting_title_fdr"
                user_state[uid] = st
                bot.reply_to(message, "📸 Фото сохранено!\n\n✏️ Теперь отправь <b>ЗАГОЛОВОК</b> для сторис:", parse_mode="HTML")
            elif step == "waiting_photo_fdr_post":
                st["step"] = "waiting_title_fdr_post"
                user_state[uid] = st
                bot.reply_to(message, "📸 Фото сохранено!\n\n✏️ Теперь отправь <b>ПОЛНЫЙ ЗАГОЛОВОК</b> для поста:", parse_mode="HTML")
            else:
                st["step"] = "waiting_title"
                user_state[uid] = st
                bot.reply_to(message, "📸 Фото сохранено!\n\n✏️ Теперь отправь <b>ЗАГОЛОВОК</b> для поста:", parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    else:
        bot.reply_to(message, "❌ Сначала выбери шаблон через «Оформить пост»")


@bot.message_handler(content_types=["text"])
def on_text(message):
    uid = message.from_user.id
    text = message.text.strip()
    st = user_state.get(uid) or {"step": "idle"}
    
    # Обработка заголовка
    if st.get("step") == "waiting_title":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        
        try:
            template = st.get("template", "MN")
            font_mult = st.get("font_size_multiplier", 1.0) if template == "MN2" else 1.0
            is_square = st.get("is_square", False)
            
            card = make_card(
                st["photo_bytes"], text, template,
                text_position=st.get("text_position", TEXT_POSITION_TOP),
                font_size_multiplier=font_mult,
                is_square=is_square
            )
            st["card_bytes"] = card.getvalue()
            st["title"] = text
            
            if template == "MN_NO_TEXT":
                st["step"] = "waiting_action"
                user_state[uid] = st
                bot.send_photo(
                    message.chat.id, photo=BytesIO(st["card_bytes"]),
                    caption="✅ Пост готов!\n\nНажми кнопку под фото для публикации.",
                    reply_markup=preview_kb_no_text()
                )
            else:
                st["step"] = "waiting_body"
                user_state[uid] = st
                bot.send_document(
                    message.chat.id, document=BytesIO(st["card_bytes"]),
                    visible_file_name="post.jpg",
                    caption="✅ Пост готов!\n\n✏️ Теперь отправь <b>ОСНОВНОЙ ТЕКСТ</b>:",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Error: {e}")
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    # Обработка основного текста
    if st.get("step") == "waiting_body":
        if not text:
            bot.reply_to(message, "❌ Текст не может быть пустым")
            return
        st["body_raw"] = text
        st["step"] = "waiting_action"
        user_state[uid] = st
        caption = build_caption_html(st["title"], text)
        bot.send_photo(
            message.chat.id, photo=BytesIO(st["card_bytes"]),
            caption=caption, parse_mode="HTML", reply_markup=preview_kb()
        )
        bot.reply_to(message, "🎉 <b>Превью готово!</b>\n\nНажми кнопку под фото.", parse_mode="HTML")
        return
    
    # Обработка заголовка для FDR_STORY
    if st.get("step") == "waiting_title_fdr":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        st["title"] = text
        st["step"] = "waiting_body_fdr"
        user_state[uid] = st
        bot.reply_to(message, "✅ Заголовок сохранён!\n\n✏️ Теперь отправь <b>ОСНОВНОЙ ТЕКСТ</b>:", parse_mode="HTML")
        return
    
    # Обработка текста для FDR_STORY
    if st.get("step") == "waiting_body_fdr":
        if not text:
            bot.reply_to(message, "❌ Текст не может быть пустым")
            return
        try:
            card = make_card_fdr_story(st["photo_bytes"], st["title"], text)
            st["card_bytes"] = card.getvalue()
            st["body_raw"] = text
            st["step"] = "waiting_action"
            user_state[uid] = st
            caption = f"<b>📱 {html.escape(st['title'])}</b>\n\n{html.escape(text)}"
            bot.send_photo(
                message.chat.id, photo=BytesIO(st["card_bytes"]),
                caption=caption, parse_mode="HTML", reply_markup=preview_kb()
            )
            bot.reply_to(message, "🎉 <b>Превью сторис готово!</b>\n\nНажми кнопку.", parse_mode="HTML")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    # Обработка заголовка для FDR_POST
    if st.get("step") == "waiting_title_fdr_post":
        if not text:
            bot.reply_to(message, "❌ Заголовок не может быть пустым")
            return
        st["full_title"] = text
        st["step"] = "waiting_highlight_fdr_post"
        user_state[uid] = st
        bot.reply_to(message, "✅ Заголовок сохранён!\n\n✏️ Теперь отправь <b>ФРАЗУ ДЛЯ ВЫДЕЛЕНИЯ</b> (будет в фиолетовой плашке):", parse_mode="HTML")
        return
    
    # Обработка фразы для FDR_POST
    if st.get("step") == "waiting_highlight_fdr_post":
        try:
            card = make_card_fdr_post(st["photo_bytes"], st["full_title"], text, st.get("is_square", False))
            st["card_bytes"] = card.getvalue()
            st["title"] = st["full_title"]
            st["step"] = "waiting_body_fdr_post"
            user_state[uid] = st
            bot.send_document(
                message.chat.id, document=BytesIO(st["card_bytes"]),
                visible_file_name="post_fdr.jpg",
                caption="✅ Пост готов!\n\n✏️ Теперь отправь <b>ОСНОВНОЙ ТЕКСТ</b>:",
                parse_mode="HTML"
            )
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
        return
    
    # Обработка основного текста для FDR_POST
    if st.get("step") == "waiting_body_fdr_post":
        if not text:
            bot.reply_to(message, "❌ Текст не может быть пустым")
            return
        st["body_raw"] = text
        st["step"] = "waiting_action"
        user_state[uid] = st
        caption = build_caption_html(st["title"], text)
        bot.send_photo(
            message.chat.id, photo=BytesIO(st["card_bytes"]),
            caption=caption, parse_mode="HTML", reply_markup=preview_kb()
        )
        bot.reply_to(message, "🎉 <b>Превью готово!</b>\n\nНажми кнопку под фото.", parse_mode="HTML")
        return
    
    if st.get("step") == "waiting_template":
        bot.send_message(message.chat.id, "📝 Выбери шаблон кнопками выше ☝️")
        return
    
    bot.send_message(message.chat.id, "📝 Нажми «Оформить пост»", reply_markup=main_menu_kb())


# =========================
# Main execution
# =========================
if __name__ == "__main__":
    logger.info("🚀 Starting bot...")
    try:
        ensure_fonts()
        logger.info("Fonts loaded successfully")
        
        logger.info("🤖 Bot started polling!")
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise
