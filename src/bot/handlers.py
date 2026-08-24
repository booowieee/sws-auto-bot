import asyncio
import os
import re
from datetime import datetime, UTC
from typing import Any, Optional
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from src.bot.db import BotDatabase
from src.bot.keyboards import (
    get_form_trigger_keyboard,
    get_status_dashboard_keyboard,
    get_whitelist_approval_keyboard,
)
from src.browser import BrowserManager
from src.config import Config, load_profile
from src.logger import logger

router = Router()


def get_admin_id() -> Optional[int]:
    admin_str = os.getenv("TELEGRAM_ADMIN_ID") or Config.TELEGRAM_CHAT_ID
    try:
        return int(admin_str) if admin_str else None
    except ValueError:
        return None


# ==================== Middleware / Auth Helpers ====================


async def check_access(message: Message, db: BotDatabase) -> bool:
    """Verifies user authorization. If not authorized, sends a request to admin."""
    user_id = message.from_user.id
    admin_id = get_admin_id()

    # Automatically grant admin access to configured admin ID
    if admin_id and user_id == admin_id:
        await db.add_or_update_user(
            user_id=user_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            role="admin",
        )
        return True

    is_auth = await db.is_authorized(user_id)
    if is_auth:
        return True

    # User is unauthorized
    await db.add_or_update_user(
        user_id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        role="pending",
    )

    await message.answer(
        "<b>⛔ Доступ ограничен</b>\n\n"
        "Ваш ID не авторизован для управления ботом. Запрос на доступ отправлен администратору.",
        parse_mode="HTML",
    )

    if admin_id and message.bot:
        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"<b>🔔 Запрос доступа к SWS Auto-Bot</b>\n\n"
                    f"<b>Пользователь:</b> {message.from_user.full_name}\n"
                    f"<b>Username:</b> @{message.from_user.username or 'отсутствует'}\n"
                    f"<b>User ID:</b> <code>{user_id}</code>\n"
                ),
                reply_markup=get_whitelist_approval_keyboard(user_id),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Could not notify admin of access request: {e}")

    return False


# ==================== Command Handlers ====================


@router.message(Command("start"))
async def cmd_start(message: Message, db: BotDatabase):
    if not await check_access(message, db):
        return

    await message.answer(
        "<b>SWS Auto-Bot</b>\n\n"
        "Команды:\n"
        "• <code>/status</code> - статус мониторинга\n"
        "• <code>/watch &lt;url&gt; [сек]</code> - запустить слежение\n"
        "• <code>/unwatch &lt;url&gt;</code> - остановить слежение\n"
        "• <code>/fill &lt;url&gt; [--test]</code> - заполнить форму сейчас\n"
        "• <code>/profile</code> - данные профиля\n"
        "• <code>/logs</code> - отчеты\n"
        "• <code>/help</code> - справка",
        reply_markup=get_status_dashboard_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message, db: BotDatabase):
    if not await check_access(message, db):
        return

    help_text = (
        "<b>Справка:</b>\n\n"
        "<b>Мониторинг формы:</b>\n"
        "<code>/watch https://docs.google.com/forms/d/e/.../viewform 20</code>\n"
        "Опрос каждые 20 сек. При открытии заполняет и присылает отчет со скриншотами.\n\n"
        "<b>Заполнение вручную:</b>\n"
        "• <code>/fill &lt;url&gt;</code> - боевое заполнение (отправка)\n"
        "• <code>/fill &lt;url&gt; --test</code> - тест (без отправки)\n\n"
        "<b>Управление доступом:</b>\n"
        "• <code>/whitelist</code> - список пользователей\n"
        "• <code>/whitelist add &lt;id&gt;</code> - добавить оператора\n"
        "• <code>/whitelist remove &lt;id&gt;</code> - удалить оператора\n"
    )
    await message.answer(help_text, parse_mode="HTML")


@router.message(Command("status"))
async def cmd_status(message: Message, db: BotDatabase, watcher_mgr: Optional[Any] = None):
    if not await check_access(message, db):
        return

    active_tasks = await db.get_active_watch_tasks()
    task_count = len(active_tasks)

    status_msg = (
        f"<b>Статус мониторинга:</b>\n\n"
        f"Активных задач: {task_count}\n"
    )

    if active_tasks:
        status_msg += "\n<b>Формы:</b>\n"
        for t in active_tasks[:10]:
            last_chk = t.get("last_checked_at") or "нет проверок"
            status_msg += (
                f"• <b>{t['title']}</b>\n"
                f"  URL: <code>{t['url'][:45]}...</code>\n"
                f"  Интервал: {t['poll_interval']}с | Режим: {'Тест' if t['is_test'] else 'LIVE'}\n"
                f"  Посл. проверка: <i>{last_chk[-8:]}</i>\n"
            )

    await message.answer(
        status_msg, reply_markup=get_status_dashboard_keyboard(), parse_mode="HTML"
    )


@router.message(Command("watch"))
async def cmd_watch(message: Message, command: CommandObject, db: BotDatabase, watcher_mgr: Optional[Any] = None):
    if not await check_access(message, db):
        return

    args = (command.args or "").split()
    if not args:
        await message.answer(
            "<b>Формат:</b> <code>/watch &lt;url&gt; [интервал_сек] [--test]</code>",
            parse_mode="HTML",
        )
        return

    url = args[0]
    if "forms.gle" not in url and "docs.google.com/forms" not in url:
        await message.answer("❌ Ошибка: укажите корректную ссылку на Google Forms.")
        return

    interval = 30
    is_test = False

    for a in args[1:]:
        if a.isdigit():
            interval = max(5, int(a))
        elif a in ("--test", "-t", "test"):
            is_test = True

    title = f"Form_{datetime.now(UTC).strftime('%H%M%S')}"

    if watcher_mgr is not None:
        await watcher_mgr.start_watching(
            url=url,
            title=title,
            poll_interval=interval,
            is_test=is_test,
            created_by=message.from_user.id,
            save_to_db=True,
        )
    else:
        await db.add_watch_task(
            url=url,
            title=title,
            poll_interval=interval,
            is_test=is_test,
            created_by=message.from_user.id,
        )

    await message.answer(
        f"<b>✅ Форма добавлена на круглосуточный мониторинг</b>\n\n"
        f"<b>URL:</b> <code>{url}</code>\n"
        f"<b>Интервал опроса:</b> {interval} сек.\n"
        f"<b>Режим:</b> {'Тестовый (Dry-Run)' if is_test else 'Боевой (Auto-Submit)'}",
        parse_mode="HTML",
    )


@router.message(Command("unwatch"))
async def cmd_unwatch(message: Message, command: CommandObject, db: BotDatabase, watcher_mgr: Optional[Any] = None):
    if not await check_access(message, db):
        return

    url = (command.args or "").strip()
    if not url:
        await message.answer("<b>Формат:</b> <code>/unwatch &lt;url&gt;</code>", parse_mode="HTML")
        return

    if watcher_mgr is not None:
        ok = await watcher_mgr.stop_watching(url)
    else:
        ok = await db.deactivate_watch_task(url)

    if ok:
        await message.answer(f"✅ Форма успешно снята с мониторинга:\n<code>{url}</code>", parse_mode="HTML")
    else:
        await message.answer("❌ Форма не найдена в списке активных задач.", parse_mode="HTML")


@router.message(Command("fill"))
async def cmd_fill(message: Message, command: CommandObject, db: BotDatabase):
    if not await check_access(message, db):
        return

    args = (command.args or "").split()
    if not args:
        await message.answer("<b>Формат:</b> <code>/fill &lt;url&gt; [--test]</code>", parse_mode="HTML")
        return

    url = args[0]
    is_test = "--test" in args or "-t" in args

    status_msg = await message.answer(
        f"⏳ Запуск процесса заполнения формы ({'Тест' if is_test else 'LIVE'})...\n<code>{url}</code>",
        parse_mode="HTML",
    )

    # Launch autofill in background
    from src.__main__ import run_autofill

    async def _do_fill():
        try:
            exit_code = await run_autofill(url=url, is_test=is_test, headless=True)
            res_text = "УСПЕШНО" if exit_code == 0 else "ОШИБКА"
            await status_msg.edit_text(
                f"<b>[SWS Auto-Bot] Завершено: {res_text}</b>\n\nURL: <code>{url}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка заполнения: {e}")

    asyncio.create_task(_do_fill())


@router.message(Command("profile"))
async def cmd_profile(message: Message, db: BotDatabase):
    if not await check_access(message, db):
        return

    try:
        profile = load_profile()
        p_text = (
            f"<b>👤 Профиль кандидата:</b>\n\n"
            f"<b>ФИО:</b> <code>{profile.personal.full_name}</code>\n"
            f"<b>Email:</b> <code>{profile.contacts.email}</code>\n"
            f"<b>Телефон:</b> <code>{profile.contacts.phone}</code>\n"
            f"<b>Дата рождения:</b> <code>{profile.personal.date_of_birth}</code>\n"
            f"<b>Гражданство:</b> <code>{profile.personal.nationality}</code>\n"
            f"<b>Паспорт:</b> <code>{profile.documents.passport_number}</code> (годен до {profile.documents.passport_expiry})\n"
            f"<b>Опыт агро:</b> {'Да' if profile.work.experience_agriculture else 'Нет'}\n"
            f"<b>Размер обуви:</b> <code>{profile.ppe.shoe_size}</code> | <b>Одежда:</b> <code>{profile.ppe.tshirt_size}</code>\n"
        )
        await message.answer(p_text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Не удалось загрузить профиль: {e}")


@router.message(Command("logs"))
async def cmd_logs(message: Message, db: BotDatabase):
    if not await check_access(message, db):
        return

    records = await db.get_recent_executions(limit=5)
    if not records:
        await message.answer("📭 Журнал выполнения пока пуст.")
        return

    log_msg = "<b>📜 Последние выполнения:</b>\n\n"
    for r in records:
        status_emoji = "✅" if r["status"] in ("success", "dry_run") else "❌"
        log_msg += (
            f"{status_emoji} <b>{r['status'].upper()}</b> ({r['duration_sec']:.1f}с)\n"
            f"URL: <code>{r['url'][:45]}...</code>\n"
            f"Полей: {r['filled_fields_count']}/{r['total_fields']}\n"
        )
        if r.get("error_message"):
            log_msg += f"<i>Ошибка: {r['error_message'][:60]}</i>\n"
        log_msg += "\n"

    await message.answer(log_msg, parse_mode="HTML")


@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, command: CommandObject, db: BotDatabase):
    user_id = message.from_user.id
    role = await db.get_user_role(user_id)
    admin_id = get_admin_id()

    if role != "admin" and user_id != admin_id:
        await message.answer("⛔ Только администратор может управлять белым списком.")
        return

    args = (command.args or "").split()
    if not args or args[0] == "list":
        users = await db.get_all_users()
        msg = "<b>👥 Пользователи системы:</b>\n\n"
        for u in users:
            msg += (
                f"• <b>{u['full_name']}</b> (@{u['username'] or 'нет'})\n"
                f"  ID: <code>{u['user_id']}</code> | Роль: <b>{u['role']}</b>\n"
            )
        await message.answer(msg, parse_mode="HTML")
        return

    action = args[0]
    if action == "add" and len(args) > 1 and args[1].isdigit():
        target_id = int(args[1])
        await db.add_or_update_user(user_id=target_id, username="", full_name="Operator", role="operator")
        await message.answer(f"✅ Пользователю <code>{target_id}</code> предоставлен доступ оператора.", parse_mode="HTML")
    elif action == "remove" and len(args) > 1 and args[1].isdigit():
        target_id = int(args[1])
        await db.delete_user(target_id)
        await message.answer(f"✅ Доступ пользователя <code>{target_id}</code> отозван.", parse_mode="HTML")


# ==================== Callback Query Handlers ====================


@router.callback_query(F.data.startswith("wl_approve:"))
async def cb_wl_approve(callback: CallbackQuery, db: BotDatabase):
    target_id = int(callback.data.split(":")[1])
    await db.add_or_update_user(user_id=target_id, username="", full_name="", role="operator")
    await callback.message.edit_text(f"✅ Доступ для <code>{target_id}</code> одобрен.", parse_mode="HTML")

    try:
        await callback.bot.send_message(
            chat_id=target_id,
            text="🎉 <b>Вам предоставлен доступ к SWS Auto-Bot!</b> Нажмите /start для начала работы.",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("wl_reject:"))
async def cb_wl_reject(callback: CallbackQuery, db: BotDatabase):
    target_id = int(callback.data.split(":")[1])
    await db.delete_user(target_id)
    await callback.message.edit_text(f"❌ Запрос пользователя <code>{target_id}</code> отклонен.", parse_mode="HTML")


@router.callback_query(F.data == "dash_refresh")
async def cb_dash_refresh(callback: CallbackQuery, db: BotDatabase):
    active_tasks = await db.get_active_watch_tasks()
    await callback.message.edit_text(
        f"<b>📊 Дашборд состояния SWS Auto-Bot</b>\n\n"
        f"<b>Активных форм на слежении:</b> {len(active_tasks)}\n"
        f"<i>Обновлено: {datetime.now(UTC).strftime('%H:%M:%S UTC')}</i>",
        reply_markup=get_status_dashboard_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer("Дашборд обновлен")


@router.callback_query(F.data == "dash_profile")
async def cb_dash_profile(callback: CallbackQuery):
    try:
        profile = load_profile()
        await callback.message.answer(
            f"<b>👤 Кандидат:</b> {profile.personal.full_name}\n"
            f"<b>Email:</b> {profile.contacts.email}\n"
            f"<b>Телефон:</b> {profile.contacts.phone}\n"
            f"<b>Паспорт:</b> {profile.documents.passport_number}",
            parse_mode="HTML",
        )
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")
    await callback.answer()
