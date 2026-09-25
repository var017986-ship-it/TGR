"""Помощь — структурированная справка по командам."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def help_text() -> str:
    """Полная справка по командам."""
    return (
        "┌──────────────────────────────┐\n"
        "│          <b>ПОМОЩЬ</b>              │\n"
        "└──────────────────────────────┘\n\n"
        "<b>Основные команды:</b>\n\n"
        "  <code>/start</code>\n"
        "    Главное меню\n\n"
        "  <code>/addaccount</code>\n"
        "    Добавить Steam-аккаунт\n\n"
        "  <code>/accounts</code>\n"
        "    Склад аккаунтов\n\n"
        "  <code>/lots</code>\n"
        "    Настройка лотов\n\n"
        "  <code>/rentals</code>\n"
        "    Список аренд\n\n"
        "  <code>/simulate</code>\n"
        "    Тест покупки/продления\n\n"
        "<b>Отладка:</b>\n\n"
        "  <code>/buyer_menu buyer_id</code>\n"
        "    Проверить ответ на !menu\n\n"
        "  <code>/buyer_code buyer_id</code>\n"
        "    Проверить ответ на !code\n\n"
        "<b>Плагин оффлайн-выдачи:</b>\n\n"
        "  <code>/offline_enable lot_id</code>\n"
        "    Включить разовую выдачу\n\n"
        "  <code>/offline_disable lot_id</code>\n"
        "    Выключить разовую выдачу\n\n"
        "  <code>/offline_status</code>\n"
        "    Статус плагина\n\n"
        "<b>Добавление аккаунта:</b>\n\n"
        "  1. Логин\n"
        "  2. Пароль\n"
        "  3. maFile (документом)\n"
        "  4. Название для бота"
    )


def help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="● На главную", callback_data="dashboard")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )
