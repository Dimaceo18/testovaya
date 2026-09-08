# -*- coding: utf-8 -*-

import os
import logging
import asyncio
from typing import Optional, List
from datetime import datetime
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/your_channel")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не настроен!")

if not ADMIN_CHAT_ID:
    raise ValueError("❌ ADMIN_CHAT_ID не настроен!")

try:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
except ValueError:
    raise ValueError("❌ ADMIN_CHAT_ID должен быть числом!")

# Файл для хранения данных пользователей (для персистентности)
DATA_FILE = "users_data.json"

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ==================== РАБОТА С ДАННЫМИ ====================

def load_users_data():
    """Загрузка данных пользователей из файла"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_users_data(data):
    """Сохранение данных пользователей в файл"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения данных: {e}")

# Загружаем данные при старте
user_requests = load_users_data()

# Состояния для рассылки
broadcast_states = {}

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    user_id = user.id
    username = user.username or "без username"
    first_name = user.first_name or ""
    
    logger.info(f"👤 Новый пользователь: {first_name} (@{username}) ID: {user_id}")
    
    # Проверяем, подавал ли пользователь уже заявку
    if str(user_id) in user_requests:
        status = user_requests[str(user_id)].get("status", "pending")
        
        if status == "approved":
            await update.message.reply_text(
                f"✅ {first_name}, вы уже имеете доступ к каналу!\n\n"
                f"🔗 <a href='{CHANNEL_LINK}'>Перейти в канал</a>",
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            return
        elif status == "pending":
            await update.message.reply_text(
                f"👋 {first_name}, ваша заявка уже подана и находится на рассмотрении.\n\n"
                f"⏳ Пожалуйста, ожидайте одобрения. Мы свяжемся с вами в ближайшее время!",
                parse_mode="HTML"
            )
            return
    
    # Приветственное сообщение с кнопкой подачи заявки
    keyboard = [
        [InlineKeyboardButton("📝 Подать заявку", callback_data="apply")],
        [InlineKeyboardButton("❓ Что это?", callback_data="info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 <b>Добро пожаловать, {first_name}!</b>\n\n"
        f"🎉 Мы рады приветствовать вас в сообществе бесплатных мероприятий города Минска!\n\n"
        f"📌 <b>Что вас ждет:</b>\n"
        f"• Бесплатные концерты и выступления\n"
        f"• Мастер-классы и лекции\n"
        f"• Спортивные события\n"
        f"• Встречи и нетворкинг\n"
        f"• И многое другое!\n\n"
        f"🔒 Для доступа ко всем мероприятиям вам необходимо подать заявку.\n\n"
        f"<b>Нажмите кнопку ниже, чтобы подать заявку:</b>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Что это?'"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        f"📌 <b>О проекте</b>\n\n"
        f"Мы собираем все бесплатные мероприятия города Минска в одном месте!\n\n"
        f"🎯 <b>Наша цель</b> — сделать культурную жизнь города доступной для каждого.\n\n"
        f"📅 <b>Что вы найдете в канале:</b>\n"
        f"• Афиша мероприятий на неделю\n"
        f"• Эксклюзивные приглашения\n"
        f"• Скидки и бонусы от партнеров\n"
        f"• Возможность познакомиться с интересными людьми\n\n"
        f"🔒 <b>Доступ</b> — по заявке, чтобы мы могли сделать сообщество комфортным для всех.\n\n"
        f"Чтобы подать заявку, нажмите /start и выберите 'Подать заявку'",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Подать заявку", callback_data="apply")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_start")]
        ])
    )


async def apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Подать заявку'"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_id = str(user.id)
    username = user.username or "без username"
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    
    # Проверяем, не подавал ли пользователь уже заявку
    if user_id in user_requests:
        status = user_requests[user_id].get("status", "pending")
        
        if status == "approved":
            await query.edit_message_text(
                f"✅ {first_name}, вы уже имеете доступ к каналу!\n\n"
                f"🔗 <a href='{CHANNEL_LINK}'>Перейти в канал</a>",
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            return
        elif status == "pending":
            await query.edit_message_text(
                f"👋 {first_name}, ваша заявка уже подана и находится на рассмотрении.\n\n"
                f"⏳ Пожалуйста, ожидайте одобрения.",
                parse_mode="HTML"
            )
            return
    
    # Сохраняем заявку
    user_requests[user_id] = {
        "status": "pending",
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_users_data(user_requests)
    
    # Отправляем подтверждение пользователю
    await query.edit_message_text(
        f"✅ <b>Заявка подана!</b>\n\n"
        f"Спасибо, {first_name}! 🙏\n\n"
        f"Мы получили вашу заявку на доступ к закрытому каналу с бесплатными мероприятиями города Минска.\n\n"
        f"⏳ <b>Что дальше:</b>\n"
        f"• Ожидайте одобрения заявки\n"
        f"• Мы свяжемся с вами в ближайшее время\n"
        f"• После одобрения вы получите доступ ко всем мероприятиям\n\n"
        f"🔒 Доступ к каналу — <b>{CHANNEL_LINK}</b>\n\n"
        f"Спасибо за интерес! ❤️",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Перейти в канал", url=CHANNEL_LINK)]
        ])
    )
    
    # Отправляем уведомление администратору
    admin_message = (
        f"📨 <b>НОВАЯ ЗАЯВКА!</b>\n\n"
        f"👤 <b>Пользователь:</b> {first_name} {last_name or ''}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"📱 <b>Username:</b> @{username}\n"
        f"🕐 <b>Время:</b> {user_requests[user_id]['applied_at']}\n\n"
        f"📊 <b>Всего заявок:</b> {len(user_requests)}\n"
        f"⏳ <b>В ожидании:</b> {sum(1 for r in user_requests.values() if r['status'] == 'pending')}\n\n"
        f"<b>Действия:</b>\n"
        f"• /approve_{user_id} - одобрить заявку\n"
        f"• /reject_{user_id} - отклонить заявку"
    )
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_message,
            parse_mode="HTML"
        )
        logger.info(f"📨 Уведомление отправлено админу о пользователе {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")


async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Назад'"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📝 Подать заявку", callback_data="apply")],
        [InlineKeyboardButton("❓ Что это?", callback_data="info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"👋 <b>Добро пожаловать!</b>\n\n"
        f"🎉 Мы рады приветствовать вас в сообществе бесплатных мероприятий города Минска!\n\n"
        f"📌 <b>Что вас ждет:</b>\n"
        f"• Бесплатные концерты и выступления\n"
        f"• Мастер-классы и лекции\n"
        f"• Спортивные события\n"
        f"• Встречи и нетворкинг\n"
        f"• И многое другое!\n\n"
        f"🔒 Для доступа ко всем мероприятиям вам необходимо подать заявку.\n\n"
        f"<b>Нажмите кнопку ниже, чтобы подать заявку:</b>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )


# ==================== АДМИН-КОМАНДЫ ДЛЯ РАССЫЛКИ ====================

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админа - начало рассылки"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    keyboard = [
        [InlineKeyboardButton("👥 Всем пользователям", callback_data="broadcast_all")],
        [InlineKeyboardButton("✅ Только одобренным", callback_data="broadcast_approved")],
        [InlineKeyboardButton("⏳ Только ожидающим", callback_data="broadcast_pending")],
        [InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📨 <b>Создание рассылки</b>\n\n"
        f"Выберите, кому отправить сообщение:\n\n"
        f"👥 <b>Всем пользователям</b> — {len(user_requests)} человек\n"
        f"✅ <b>Одобренным</b> — {sum(1 for r in user_requests.values() if r['status'] == 'approved')} человек\n"
        f"⏳ <b>Ожидающим</b> — {sum(1 for r in user_requests.values() if r['status'] == 'pending')} человек",
        parse_mode="HTML",
        reply_markup=reply_markup
    )


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора аудитории для рассылки"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id != ADMIN_CHAT_ID:
        await query.edit_message_text("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    data = query.data.replace("broadcast_", "")
    
    if data == "cancel":
        await query.edit_message_text("❌ Рассылка отменена.")
        return
    
    # Определяем аудиторию
    target_users = []
    target_names = []
    
    for user_id, user_data in user_requests.items():
        if data == "all":
            target_users.append(user_id)
            target_names.append(user_data['first_name'])
        elif data == "approved" and user_data['status'] == 'approved':
            target_users.append(user_id)
            target_names.append(user_data['first_name'])
        elif data == "pending" and user_data['status'] == 'pending':
            target_users.append(user_id)
            target_names.append(user_data['first_name'])
    
    if not target_users:
        await query.edit_message_text(
            f"❌ Нет пользователей в выбранной категории.\n\n"
            f"Попробуйте выбрать другую категорию.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="broadcast_back")]
            ])
        )
        return
    
    # Сохраняем информацию о рассылке в контексте
    context.user_data['broadcast_target'] = target_users
    context.user_data['broadcast_count'] = len(target_users)
    context.user_data['broadcast_type'] = data
    
    # Показываем подтверждение и просим ввести текст
    await query.edit_message_text(
        f"📨 <b>Подготовка к рассылке</b>\n\n"
        f"👥 <b>Аудитория:</b> {len(target_users)} человек\n"
        f"📊 <b>Примеры:</b> {', '.join(target_names[:5])}{'...' if len(target_names) > 5 else ''}\n\n"
        f"✏️ <b>Отправьте текст сообщения</b> (можно с форматированием HTML):\n\n"
        f"<i>Пример:</i>\n"
        f"<b>Новое мероприятие!</b>\n"
        f"Завтра в 19:00 - бесплатный концерт в парке.\n"
        f"Подробности в канале: {CHANNEL_LINK}\n\n"
        f"⚠️ <b>Внимание!</b> Сообщение будет отправлено <b>всем</b> выбранным пользователям.\n"
        f"Чтобы отменить рассылку, отправьте /cancel_broadcast",
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    
    # Устанавливаем состояние ожидания текста для рассылки
    context.user_data['awaiting_broadcast_text'] = True


async def broadcast_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат к выбору аудитории"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id != ADMIN_CHAT_ID:
        await query.edit_message_text("⛔ У вас нет прав.")
        return
    
    keyboard = [
        [InlineKeyboardButton("👥 Всем пользователям", callback_data="broadcast_all")],
        [InlineKeyboardButton("✅ Только одобренным", callback_data="broadcast_approved")],
        [InlineKeyboardButton("⏳ Только ожидающим", callback_data="broadcast_pending")],
        [InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📨 <b>Создание рассылки</b>\n\n"
        f"Выберите, кому отправить сообщение:",
        parse_mode="HTML",
        reply_markup=reply_markup
    )


async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текста для рассылки"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    
    if not context.user_data.get('awaiting_broadcast_text'):
        return
    
    text = update.message.text
    if not text:
        await update.message.reply_text("❌ Сообщение не может быть пустым. Отправьте текст снова.")
        return
    
    target_users = context.user_data.get('broadcast_target', [])
    count = context.user_data.get('broadcast_count', 0)
    
    if not target_users:
        await update.message.reply_text("❌ Нет получателей. Начните заново командой /broadcast")
        context.user_data['awaiting_broadcast_text'] = False
        return
    
    # Подтверждение перед отправкой
    keyboard = [
        [InlineKeyboardButton("✅ Отправить", callback_data="broadcast_send_confirm")],
        [InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Сохраняем текст рассылки
    context.user_data['broadcast_text'] = text
    
    # Показываем превью
    preview_text = text[:500] + ("..." if len(text) > 500 else "")
    
    await update.message.reply_text(
        f"📨 <b>Превью сообщения:</b>\n\n"
        f"{preview_text}\n\n"
        f"👥 <b>Получателей:</b> {count} человек\n\n"
        f"⚠️ <b>Подтвердите отправку:</b>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка рассылки"""
    query = update.callback_query
    await query.answer()
    
    if update.effective_user.id != ADMIN_CHAT_ID:
        await query.edit_message_text("⛔ У вас нет прав.")
        return
    
    target_users = context.user_data.get('broadcast_target', [])
    text = context.user_data.get('broadcast_text', '')
    
    if not target_users or not text:
        await query.edit_message_text("❌ Ошибка: нет получателей или текста.")
        return
    
    # Отправляем сообщение о начале рассылки
    status_msg = await query.edit_message_text(
        f"📨 <b>Начинаю рассылку...</b>\n\n"
        f"👥 Получателей: {len(target_users)}\n"
        f"⏳ Прогресс: 0/{len(target_users)}",
        parse_mode="HTML"
    )
    
    # Отправляем сообщения
    success_count = 0
    fail_count = 0
    failed_users = []
    
    for i, user_id in enumerate(target_users, 1):
        try:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            success_count += 1
            logger.info(f"✅ Отправлено пользователю {user_id}")
        except Exception as e:
            fail_count += 1
            failed_users.append(user_id)
            logger.error(f"❌ Ошибка отправки {user_id}: {e}")
        
        # Обновляем статус каждые 10 сообщений
        if i % 10 == 0 or i == len(target_users):
            try:
                await status_msg.edit_text(
                    f"📨 <b>Идет рассылка...</b>\n\n"
                    f"👥 Получателей: {len(target_users)}\n"
                    f"⏳ Прогресс: {i}/{len(target_users)}\n"
                    f"✅ Успешно: {success_count}\n"
                    f"❌ Ошибок: {fail_count}",
                    parse_mode="HTML"
                )
            except:
                pass
        
        # Небольшая задержка, чтобы не превысить лимиты Telegram
        await asyncio.sleep(0.05)
    
    # Итоговый отчет
    result_text = (
        f"📨 <b>Рассылка завершена!</b>\n\n"
        f"👥 <b>Всего:</b> {len(target_users)}\n"
        f"✅ <b>Успешно:</b> {success_count}\n"
        f"❌ <b>Ошибок:</b> {fail_count}\n"
    )
    
    if failed_users:
        result_text += f"\n⚠️ <b>Не удалось отправить:</b>\n"
        for uid in failed_users[:10]:
            user_data = user_requests.get(str(uid), {})
            name = user_data.get('first_name', 'Unknown')
            result_text += f"• {name} (ID: {uid})\n"
        if len(failed_users) > 10:
            result_text += f"• ... и еще {len(failed_users) - 10} пользователей\n"
    
    await status_msg.edit_text(result_text, parse_mode="HTML")
    
    # Очищаем данные рассылки
    context.user_data['awaiting_broadcast_text'] = False
    context.user_data['broadcast_target'] = []
    context.user_data['broadcast_text'] = ''
    context.user_data['broadcast_count'] = 0


async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена рассылки"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет прав.")
        return
    
    context.user_data['awaiting_broadcast_text'] = False
    context.user_data['broadcast_target'] = []
    context.user_data['broadcast_text'] = ''
    context.user_data['broadcast_count'] = 0
    
    await update.message.reply_text("❌ Рассылка отменена.")


# ==================== АДМИН-КОМАНДЫ ДЛЯ ЗАЯВОК ====================

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админа - одобрить заявку"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    text = update.message.text
    if not text or not text.startswith('/approve_'):
        return
    
    try:
        user_id = text.replace('/approve_', '').strip()
    except ValueError:
        await update.message.reply_text("❌ Неверный формат команды. Используйте: /approve_ID")
        return
    
    if user_id not in user_requests:
        await update.message.reply_text(f"❌ Пользователь с ID {user_id} не найден в заявках.")
        return
    
    if user_requests[user_id]["status"] == "approved":
        await update.message.reply_text(f"✅ Заявка пользователя уже была одобрена.")
        return
    
    # Обновляем статус
    user_requests[user_id]["status"] = "approved"
    user_data = user_requests[user_id]
    save_users_data(user_requests)
    
    # Отправляем уведомление пользователю
    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                f"🎉 <b>Поздравляем, {user_data['first_name']}!</b>\n\n"
                f"Ваша заявка на доступ к закрытому каналу <b>ОДОБРЕНА</b>! ✅\n\n"
                f"🔗 <b>Ссылка на канал:</b>\n"
                f"<a href='{CHANNEL_LINK}'>Перейти в канал</a>\n\n"
                f"🎊 Теперь вы в курсе всех бесплатных мероприятий города Минска!\n\n"
                f"Добро пожаловать в наше сообщество! ❤️"
            ),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        logger.info(f"✅ Пользователю {user_id} отправлено уведомление об одобрении")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки пользователю {user_id}: {e}")
        await update.message.reply_text(f"⚠️ Не удалось отправить уведомление пользователю. Ошибка: {e}")
    
    await update.message.reply_text(
        f"✅ Заявка пользователя {user_data['first_name']} (ID: {user_id}) одобрена!\n\n"
        f"📊 Всего заявок: {len(user_requests)}\n"
        f"✅ Одобрено: {sum(1 for r in user_requests.values() if r['status'] == 'approved')}\n"
        f"⏳ В ожидании: {sum(1 for r in user_requests.values() if r['status'] == 'pending')}"
    )


async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админа - отклонить заявку"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    text = update.message.text
    if not text or not text.startswith('/reject_'):
        return
    
    try:
        user_id = text.replace('/reject_', '').strip()
    except ValueError:
        await update.message.reply_text("❌ Неверный формат команды. Используйте: /reject_ID")
        return
    
    if user_id not in user_requests:
        await update.message.reply_text(f"❌ Пользователь с ID {user_id} не найден в заявках.")
        return
    
    if user_requests[user_id]["status"] == "rejected":
        await update.message.reply_text(f"✅ Заявка пользователя уже была отклонена.")
        return
    
    # Обновляем статус
    user_requests[user_id]["status"] = "rejected"
    user_data = user_requests[user_id]
    save_users_data(user_requests)
    
    # Отправляем уведомление пользователю
    try:
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                f"😔 <b>К сожалению, {user_data['first_name']}</b>\n\n"
                f"Ваша заявка на доступ к закрытому каналу была <b>ОТКЛОНЕНА</b>.\n\n"
                f"Вы можете подать заявку повторно через некоторое время.\n\n"
                f"Если вы считаете, что это ошибка, свяжитесь с нами.\n"
            ),
            parse_mode="HTML"
        )
        logger.info(f"❌ Пользователю {user_id} отправлено уведомление об отклонении")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки пользователю {user_id}: {e}")
        await update.message.reply_text(f"⚠️ Не удалось отправить уведомление пользователю. Ошибка: {e}")
    
    await update.message.reply_text(
        f"❌ Заявка пользователя {user_data['first_name']} (ID: {user_id}) отклонена.\n\n"
        f"📊 Всего заявок: {len(user_requests)}\n"
        f"✅ Одобрено: {sum(1 for r in user_requests.values() if r['status'] == 'approved')}\n"
        f"❌ Отклонено: {sum(1 for r in user_requests.values() if r['status'] == 'rejected')}\n"
        f"⏳ В ожидании: {sum(1 for r in user_requests.values() if r['status'] == 'pending')}"
    )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админа - статистика заявок"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    total = len(user_requests)
    approved = sum(1 for r in user_requests.values() if r['status'] == 'approved')
    pending = sum(1 for r in user_requests.values() if r['status'] == 'pending')
    rejected = sum(1 for r in user_requests.values() if r['status'] == 'rejected')
    
    # Список ожидающих заявок
    pending_list = []
    for uid, data in user_requests.items():
        if data['status'] == 'pending':
            pending_list.append(
                f"• {data['first_name']} (@{data['username']}) - ID: <code>{uid}</code>\n"
                f"  /approve_{uid} | /reject_{uid}"
            )
    
    pending_text = "\n".join(pending_list) if pending_list else "Нет ожидающих заявок"
    
    await update.message.reply_text(
        f"📊 <b>Статистика заявок</b>\n\n"
        f"📌 <b>Всего заявок:</b> {total}\n"
        f"✅ <b>Одобрено:</b> {approved}\n"
        f"⏳ <b>В ожидании:</b> {pending}\n"
        f"❌ <b>Отклонено:</b> {rejected}\n\n"
        f"<b>Ожидающие заявки:</b>\n{pending_text}",
        parse_mode="HTML"
    )


async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для админа - список всех заявок"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    if not user_requests:
        await update.message.reply_text("📭 Нет заявок.")
        return
    
    status_map = {
        "pending": "⏳ Ожидает",
        "approved": "✅ Одобрено",
        "rejected": "❌ Отклонено"
    }
    
    lines = ["<b>📋 Список заявок:</b>\n"]
    for uid, data in user_requests.items():
        lines.append(
            f"🆔 <code>{uid}</code> - {data['first_name']} (@{data['username']})\n"
            f"   Статус: {status_map.get(data['status'], data['status'])}\n"
            f"   {data['applied_at']}\n"
        )
    
    text = "\n".join(lines)
    if len(text) > 4096:
        for i in range(0, len(text), 4096):
            await update.message.reply_text(text[i:i+4096], parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик неизвестных команд"""
    await update.message.reply_text(
        "🤔 Я не понимаю эту команду.\n\n"
        "Используйте /start для начала работы.",
        parse_mode="HTML"
    )


# ==================== ЗАПУСК БОТА ====================

async def main():
    logger.info("🚀 Бот для приема заявок на доступ к каналу запускается...")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Команды для пользователей
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", unknown))
    
    # Команды для админа - заявки
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("list", admin_list))
    app.add_handler(CommandHandler("approve_", admin_approve))
    app.add_handler(CommandHandler("reject_", admin_reject))
    
    # Команды для админа - рассылка
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("cancel_broadcast", cancel_broadcast))
    
    # Callback обработчики
    app.add_handler(CallbackQueryHandler(apply, pattern="^apply$"))
    app.add_handler(CallbackQueryHandler(info, pattern="^info$"))
    app.add_handler(CallbackQueryHandler(back_to_start, pattern="^back_to_start$"))
    app.add_handler(CallbackQueryHandler(broadcast_callback, pattern="^broadcast_"))
    app.add_handler(CallbackQueryHandler(broadcast_send, pattern="^broadcast_send_confirm$"))
    app.add_handler(CallbackQueryHandler(broadcast_back, pattern="^broadcast_back$"))
    
    # Обработчик текста для рассылки
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_text))
    
    # Обработчик неизвестных команд
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    
    logger.info("✅ Обработчики зарегистрированы")
    logger.info("📊 Настройки:")
    logger.info(f"  • Канал: {CHANNEL_LINK}")
    logger.info(f"  • Админ: {ADMIN_CHAT_ID}")
    logger.info(f"  • Всего пользователей: {len(user_requests)}")
    logger.info("📌 Команды администратора:")
    logger.info("  • /stats - статистика заявок")
    logger.info("  • /list - список всех заявок")
    logger.info("  • /approve_ID - одобрить заявку")
    logger.info("  • /reject_ID - отклонить заявку")
    logger.info("  • /broadcast - начать рассылку")
    logger.info("  • /cancel_broadcast - отменить рассылку")
    
    await app.initialize()
    await app.start()
    
    await app.updater.start_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=30,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30
    )
    
    logger.info("🟢 Бот запущен!")
    
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
