"""Старый ввод golden key без проверки заменён мастером «FunPay аккаунты» (callbacks/fp_accounts.py).

Если пользователь застрял в старом состоянии ввода ключа — отправляем его в новый мастер.
"""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from callbacks.fp_accounts import list_kb, list_text
from handlers.guards import guard
from states.all import FunPayKey


router = Router()


@router.message(FunPayKey.waiting_key, F.text)
async def set_funpay_key(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        "Ключи теперь добавляются через «FunPay аккаунты» (с прокси и проверкой).\n\n" + list_text(message.from_user.id),
        reply_markup=list_kb(message.from_user.id),
    )
