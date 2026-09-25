"""Плагины — красивые меню."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards import plugins_kb as _plugins_kb
from plugins import offline_one_time_code


def plugins_text() -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│         <b>ПЛАГИНЫ</b>              │\n"
        "└──────────────────────────────┘\n\n"
        "Расширения для бота. Каждый плагин\n"
        "добавляет отдельный сценарий работы.\n\n"
        "<i>Выбери плагин для настройки:</i>"
    )


def plugins_kb() -> InlineKeyboardMarkup:
    return _plugins_kb()


def offline_plugin_text() -> str:
    text = offline_one_time_code.plugin_text()
    return (
        "┌──────────────────────────────┐\n"
        "│    <b>ОФФЛАЙН-ВЫДАЧА КОДА</b>      │\n"
        "└──────────────────────────────┘\n\n"
        "Покупатель получает логин и пароль\n"
        "от оффлайн-аккаунта сразу после оплаты.\n"
        "Команда <code>!code</code> выдаёт\n"
        "Steam Guard код <b>ровно 1 раз</b>.\n\n"
        "───────────────────────────────\n\n"
        f"{text}"
    )


def offline_plugin_kb() -> InlineKeyboardMarkup:
    return offline_one_time_code.plugin_kb()


def offline_lots_text() -> str:
    return (
        "┌──────────────────────────────┐\n"
        "│   <b>ОФФЛАЙН-ЛОТЫ</b>              │\n"
        "└──────────────────────────────┘\n\n"
        "Нажми на лот, чтобы:\n"
        "  • посмотреть аккаунты на выдаче\n"
        "  • включить/выключить оффлайн\n"
        "  • изменить привязанные аккаунты"
    )


def offline_lots_kb() -> InlineKeyboardMarkup:
    return offline_one_time_code.lots_kb()


def offline_lot_detail_text(lot_id: str) -> str:
    return offline_one_time_code.lot_detail_text(lot_id)


def offline_lot_detail_kb(lot_id: str) -> InlineKeyboardMarkup:
    return offline_one_time_code.lot_detail_kb(lot_id)
