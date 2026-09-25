"""Очистка базы — с предупреждениями."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards import clean_db_confirm_kb, clean_db_kb, main_kb


def clean_db_text() -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│       <b>ОЧИСТКА БАЗЫ</b>           │\n"
        "└──────────────────────────────┘\n\n"
        "⚠ <b>Будут удалены:</b>\n"
        "  • Все аккаунты и maFile\n"
        "  • Все лоты\n"
        "  • Все аренды\n"
        "  • Все события и сообщения\n"
        "  • FunPay ключи\n"
        "  • Коды доступа\n\n"
        "<b>Не будут затронуты:</b>\n"
        "  • Код бота\n"
        "  • Файл .env\n"
        "  • Логи\n\n"
        "<i>Это действие необратимо!</i>"
    )


def clean_db_kb() -> InlineKeyboardMarkup:
    from keyboards import InlineKeyboardButton
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✗ Очистить bot.sqlite3", callback_data="clean_db_confirm")],
            [InlineKeyboardButton(text="← Назад", callback_data="admin_panel")],
        ]
    )


def clean_db_confirm_text() -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│     <b>ТОЧНО ОЧИСТИТЬ?</b>          │\n"
        "└──────────────────────────────┘\n\n"
        "Все данные будут безвозвратно удалены.\n\n"
        "<b>Это последнее предупреждение.</b>"
    )


def clean_db_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✓ Да, удалить всё", callback_data="clean_db_do")],
            [InlineKeyboardButton(text="✗ Отмена", callback_data="dashboard")],
        ]
    )


def clean_db_done_text(is_owner: bool) -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│      <b>БАЗА ОЧИЩЕНА</b>           │\n"
        "└──────────────────────────────┘\n\n"
        "✓ bot.sqlite3 полностью очищен.\n\n"
        "Можно начать настройку заново."
    )


def clean_db_done_kb(is_owner: bool) -> InlineKeyboardMarkup:
    return main_kb(is_owner)
