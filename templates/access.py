"""Коды доступа и админ-панель — красивые карточки."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import rental_service as rent
from keyboards import admin_kb as _admin_kb


def access_prompt_text() -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│       <b>ДОСТУП ЗАКРЫТ</b>          │\n"
        "└──────────────────────────────┘\n\n"
        "Для входа в бота нужен код доступа.\n\n"
        "<b>Формат:</b> <code>BOT-XXXX-XXXX</code>\n\n"
        "<i>Отправь код одним сообщением.</i>"
    )


def access_prompt_kb() -> InlineKeyboardMarkup:
    from keyboards import access_prompt_kb as _kb
    return _kb()


def access_extend_kb() -> InlineKeyboardMarkup:
    from keyboards import access_extend_kb as _kb
    return _kb()


def access_codes_text() -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│      <b>КОДЫ ДОСТУПА</b>            │\n"
        "└──────────────────────────────┘\n\n"
        "Сгенерируй код для покупателя или\n"
        "помощника. Код активируется одноразово\n"
        "при первом использовании.\n\n"
        "<b>Доступные сроки:</b>\n"
        "  • 1 минута  — для теста\n"
        "  • 1 час     — короткий доступ\n"
        "  • 1 неделя  — стандарт\n"
        "  • 1 месяц   — длительный\n"
        "  • 1 год     — постоянный\n"
        "  • Навсегда  — без ограничений"
    )


def access_codes_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="＋ Сгенерировать новый код", callback_data="access_code_new")],
            [InlineKeyboardButton(text="◆ Админ-панель", callback_data="admin_panel")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )


def access_code_duration_kb() -> InlineKeyboardMarkup:
    from keyboards import access_code_duration_kb as _kb
    return _kb()


def admin_panel_text() -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│       <b>АДМИН-ПАНЕЛЬ</b>           │\n"
        "└──────────────────────────────┘\n\n"
        "Здесь владелец бота:\n"
        "  • генерирует коды доступа\n"
        "  • очищает базу данных\n\n"
        "<i>Обычные пользователи эту панель не видят.</i>"
    )


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◆ Коды доступа", callback_data="access_codes")],
            [InlineKeyboardButton(text="● Очистка базы", callback_data="clean_db")],
            [InlineKeyboardButton(text="← Главная панель", callback_data="dashboard")],
        ]
    )


def new_code_text() -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│     <b>НОВЫЙ КОД ДОСТУПА</b>        │\n"
        "└──────────────────────────────┘\n\n"
        "Выбери срок действия кода:"
    )


def code_created_text(code: str, duration_label: str, until: str) -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│    <b>КОД ДОСТУПА СОЗДАН</b>        │\n"
        "└──────────────────────────────┘\n\n"
        f"  <code>{rent.esc(code)}</code>\n\n"
        f"<b>Срок:</b> {rent.esc(duration_label)}\n"
        f"<b>Действует до:</b> {rent.esc(until)}\n\n"
        "───────────────────────────────\n\n"
        "<i>Один код активирует только один аккаунт Telegram.</i>"
    )
