import asyncio
import os
import re
import logging
from typing import Optional

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONITOR_CHANNEL_ID = os.getenv("MONITOR_CHANNEL_ID")  # ID канала для мониторинга
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # Ваш Telegram ID для уведомлений

# Проверка наличия переменных
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не настроен!")
if not MONITOR_CHANNEL_ID:
    raise ValueError("❌ MONITOR_CHANNEL_ID не настроен!")
if not ADMIN_CHAT_ID:
    raise ValueError("❌ ADMIN_CHAT_ID не настроен!")

# Конвертируем в нужные типы
try:
    MONITOR_CHANNEL_ID = int(MONITOR_CHANNEL_ID)
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
except ValueError:
    raise ValueError("❌ MONITOR_CHANNEL_ID и ADMIN_CHAT_ID должны быть числами!")

# ==================== РЕГИОНЫ ДЛЯ ПОИСКА ====================
REGIONS = {
    "Гродно": [r"гродн[оа]?", r"гродненск", r"гродненский", r"гродненская"],
    "Гомель": [r"гомел[ьа]?", r"гомельск", r"гомельский", r"гомельская"],
    "Витебск": [r"витебск", r"витебский", r"витебская"],
    "Могилев": [r"могил[её]в", r"могилевский", r"могилевская"],
    "Брест": [r"брест", r"брестский", r"брестская"],
}

# Компилируем регулярные выражения для скорости
COMPILED_REGIONS = {}
for region, patterns in REGIONS.items():
    combined = "|".join(patterns)
    COMPILED_REGIONS[region] = re.compile(combined, re.IGNORECASE)

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== ФУНКЦИИ ====================
def find_region(text: str) -> Optional[str]:
    """
    Проверяет текст на наличие ключевых слов регионов.
    Возвращает название региона или None.
    """
    if not text:
        return None
    
    for region, pattern in COMPILED_REGIONS.items():
        if pattern.search(text):
            return region
    return None

def create_post_link(channel_id: int, message_id: int) -> str:
    """
    Создает ссылку на пост в канале.
    """
    channel_id_str = str(channel_id).replace("-100", "")
    return f"https://t.me/c/{channel_id_str}/{message_id}"

# ==================== ОБРАБОТЧИКИ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение"""
    await update.message.reply_text(
        f"👋 Привет! Я бот для мониторинга канала.\n\n"
        f"📢 Отслеживаю канал: <code>{MONITOR_CHANNEL_ID}</code>\n"
        f"📍 Ищу упоминания регионов:\n" + "\n".join(f"• {region}" for region in REGIONS.keys()) + "\n\n"
        f"Как только найду пост с упоминанием - сразу пришлю уведомление!\n\n"
        f"🔄 Для проверки статуса используй /status",
        parse_mode="HTML"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса бота"""
    await update.message.reply_text(
        f"✅ Бот работает и следит за каналом.\n\n"
        f"📢 ID канала: <code>{MONITOR_CHANNEL_ID}</code>\n"
        f"📨 Уведомления приходят сюда: <code>{ADMIN_CHAT_ID}</code>\n"
        f"📍 Отслеживаемые регионы:\n" + "\n".join(f"• {region}" for region in REGIONS.keys()) + "\n\n"
        f"🟢 Бот активен и ждёт новые посты!",
        parse_mode="HTML"
    )

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик новых постов в канале.
    Проверяет текст на наличие ключевых слов регионов.
    """
    try:
        # Получаем сообщение из канала
        message = update.channel_post
        
        if not message:
            return
        
        # Проверяем, что пост из нужного канала
        if message.chat.id != MONITOR_CHANNEL_ID:
            return
        
        # Проверяем, что это не служебное сообщение
        if not message.text and not message.caption:
            return
        
        # Получаем текст (для текстовых постов) или caption (для медиа)
        text = message.text or message.caption or ""
        
        # Если пост пустой - пропускаем
        if not text.strip():
            return
        
        # Ищем регион
        region = find_region(text)
        if not region:
            return
        
        # Формируем ссылку на пост
        post_link = create_post_link(message.chat.id, message.message_id)
        
        # Формируем уведомление
        notification = (
            f"📍 <b>Вышел пост про {region}!</b>\n"
            f"🔗 <a href='{post_link}'>Перейти к посту</a>\n"
            f"📝 <i>Найдено в тексте поста</i>\n\n"
            f"📄 <b>Превью:</b>\n"
            f"{text[:300]}{'...' if len(text) > 300 else ''}"
        )
        
        # Отправляем уведомление админу
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=notification,
            parse_mode="HTML",
            disable_web_page_preview=False
        )
        
        logger.info(f"📍 Найден регион '{region}' в посте {message.message_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке поста: {e}")

# ==================== ЗАПУСК ====================
async def main():
    """Запуск бота"""
    logger.info("🚀 Бот запускается...")
    
    # Проверяем, что бот может читать канал
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        bot = Bot(token=BOT_TOKEN)
        
        # Проверяем подключение к каналу
        try:
            channel = await bot.get_chat(MONITOR_CHANNEL_ID)
            logger.info(f"✅ Подключен к каналу: {channel.title} (ID: {channel.id})")
        except Exception as e:
            logger.error(f"❌ Не удалось подключиться к каналу: {e}")
            logger.error("❌ Проверьте: бот добавлен в канал как администратор с правами на чтение сообщений")
            logger.error(f"❌ Канал: {MONITOR_CHANNEL_ID}")
            return
        
        # Проверяем админа
        try:
            admin = await bot.get_chat(ADMIN_CHAT_ID)
            logger.info(f"✅ Уведомления будут приходить: {admin.first_name} (ID: {admin.id})")
        except Exception as e:
            logger.error(f"❌ Не удалось подключиться к админу: {e}")
            logger.error(f"❌ Проверьте ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")
            return
        
        # Регистрируем обработчики
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("status", status))
        
        # Обработчик для постов в канале
        app.add_handler(MessageHandler(
            filters.ALL & filters.Chat(chat_id=MONITOR_CHANNEL_ID),
            handle_channel_post
        ))
        
        logger.info("✅ Обработчики зарегистрированы")
        logger.info("📊 Мониторинг запущен!")
        logger.info(f"📍 Отслеживаются регионы: {', '.join(REGIONS.keys())}")
        
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
        raise

if __name__ == "__main__":
    asyncio.run(main())
