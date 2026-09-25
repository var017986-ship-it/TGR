"""Account and FunPay setup callbacks."""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import fp_accounts
from handlers.guards import guard_callback
from keyboards import back_kb
from states.all import AddAccount, FunPayKey, FunPayReply, SimulateOrder


router = Router()


@router.callback_query(lambda c: c.data and c.data.startswith("add_account:"))
async def cb_add_account(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    account_type = callback.data.split(":", 1)[1]
    title = "Steam-аккаунта"
    await state.set_state(AddAccount.waiting_login)
    await state.update_data(account_type=account_type)
    await callback.message.edit_text(
        f"<b>Добавление {title}</b>\n\n"
        "1/4. Кинь логин аккаунта.",
        reply_markup=back_kb(),
    )


@router.callback_query(lambda c: c.data == "funpay_key_legacy")
async def cb_funpay_key(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.set_state(FunPayKey.waiting_key)
    await callback.message.edit_text(
        "<b>Настройка FunPay</b>\n\n"
        "Кинь golden key от FunPay.",
        reply_markup=back_kb(),
    )


@router.callback_query(lambda c: c.data == "simulate_order")
async def cb_simulate_order(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.set_state(SimulateOrder.waiting_line)
    await callback.message.edit_text(
        "<b>Тест покупки</b>\n\n"
        "Введи данные тестовой покупки в формате:\n"
        "<code>lot_id | buyer_id | buyer_name | время</code>",
        reply_markup=back_kb(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("fp_reply:"))
async def cb_funpay_reply(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    parts = callback.data.split(":")
    chat_id = parts[1].strip() if len(parts) > 1 else ""
    owner_ws = parts[2].strip() if len(parts) > 2 else ""
    if not chat_id:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    if owner_ws and not fp_accounts.owns(callback.from_user.id, owner_ws):
        await callback.answer("Это чат чужого аккаунта.", show_alert=True)
        return
    await state.set_state(FunPayReply.waiting_text)
    await state.update_data(funpay_reply_chat_id=chat_id, funpay_reply_ws=owner_ws)
    await callback.answer()
    await callback.message.answer(
        "Напиши ответ одним сообщением. Я отправлю его в этот FunPay-чат."
    )
