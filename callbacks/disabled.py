"""Steam-аренда отключена: перехватываем старые кнопки и команды раньше их обработчиков.

Роутер подключается в dispatcher первым, поэтому устаревшие callback'и из старых
сообщений и команды вроде /addaccount больше не доходят до логики аренды.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

router = Router()

DISABLED_TEXT = "Steam-аренда отключена. Доступен раздел Auto SMM — откройте /start."

DISABLED_CALLBACKS = {
    "accounts",
    "rentals",
    "lots",
    "add_lot",
    "auto_raise",
    "auto_raise_toggle",
    "auto_raise_now",
    "finish_lot_accounts",
    "simulate_order",
    "plugin_offline",
    "plugin_offline_lots",
    "plugin_offline_add_lot",
    "access_codes",
    "access_code_new",
    "enter_access_code",
}

DISABLED_PREFIXES = (
    "access_code_generate:",
    "add_account:",
    "lot_view:",
    "lot_edit_",
    "lot_pick_acc:",
    "lot_finish_accounts:",
    "pick_acc:",
    "review_bonus:",
    "plugin_offline_",
    "acc_",
    "rental_",
)


def is_disabled_callback(data: str | None) -> bool:
    value = data or ""
    return value in DISABLED_CALLBACKS or value.startswith(DISABLED_PREFIXES)


@router.callback_query(F.data.func(is_disabled_callback))
async def cb_disabled(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer(DISABLED_TEXT, show_alert=True)


@router.message(Command("addaccount", "simulate", "addlot"))
async def cmd_disabled(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(DISABLED_TEXT)
