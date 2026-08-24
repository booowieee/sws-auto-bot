from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_form_trigger_keyboard(task_id: int, url: str) -> InlineKeyboardMarkup:
    """Inline keyboard sent when a watched Google Form opens."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Отправить (LIVE)", callback_data=f"form_submit:{task_id}"
                ),
                InlineKeyboardButton(
                    text="🧪 Тестовый прогон", callback_data=f"form_test:{task_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🛑 Отменить", callback_data=f"form_cancel:{task_id}"
                ),
            ],
        ]
    )


def get_whitelist_approval_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Admin keyboard to approve or reject a new user access request."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Разрешить доступ", callback_data=f"wl_approve:{user_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"wl_reject:{user_id}"
                ),
            ]
        ]
    )


def get_status_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Dashboard quick action buttons."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="dash_refresh"),
                InlineKeyboardButton(text="📋 Последние логи", callback_data="dash_logs"),
            ],
            [
                InlineKeyboardButton(text="👤 Профиль кандидата", callback_data="dash_profile"),
            ],
        ]
    )
