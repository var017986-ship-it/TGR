"""Dashboard, accounts, rentals, messages, events, help navigation."""
import asyncio

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import rental_service as rent
import funpay_bridge
from handlers.guards import guard_callback, is_owner
from keyboards import auto_raise_kb, back_kb
from templates import accounts as acc_tmpl
from templates import auto_raise as auto_raise_tmpl
from templates import dashboard as dash_tmpl
from templates import events as ev_tmpl
from templates import help as help_tmpl
from templates import lots as lots_tmpl
from templates import messages as msg_tmpl
from templates import rentals as rent_tmpl


router = Router()


@router.callback_query(lambda c: c.data == "dashboard")
async def cb_dashboard(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    owner = is_owner(callback.from_user.id)
    if not owner:
        warning = rent.access_expiring_soon_text(callback.from_user.id)
        if warning:
            await callback.message.edit_text(warning, reply_markup=back_kb())
            await callback.message.answer(dash_tmpl.dashboard_text(), reply_markup=dash_tmpl.dashboard_kb(owner))
            return
    await callback.message.edit_text(dash_tmpl.dashboard_text(), reply_markup=dash_tmpl.dashboard_kb(owner))


@router.callback_query(lambda c: c.data == "accounts")
async def cb_accounts(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(acc_tmpl.accounts_text(), reply_markup=acc_tmpl.accounts_kb())


@router.callback_query(lambda c: c.data == "rentals")
async def cb_rentals(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(rent_tmpl.rentals_text(), reply_markup=rent_tmpl.rentals_kb())


@router.callback_query(lambda c: c.data == "messages")
async def cb_messages(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(msg_tmpl.messages_text(), reply_markup=msg_tmpl.messages_kb())


@router.callback_query(lambda c: c.data == "events")
async def cb_events(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(ev_tmpl.events_text(), reply_markup=ev_tmpl.events_kb())


@router.callback_query(lambda c: c.data == "help")
async def cb_help(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(help_tmpl.help_text(), reply_markup=help_tmpl.help_kb())


@router.callback_query(lambda c: c.data == "lots")
async def cb_lots(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(lots_tmpl.lots_text(), reply_markup=lots_tmpl.lots_kb_view())


@router.callback_query(lambda c: c.data == "auto_raise")
async def cb_auto_raise(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    enabled = bool(rent.user_settings().get("auto_raise_lots_enabled"))
    await callback.message.edit_text(auto_raise_tmpl.auto_raise_text(), reply_markup=auto_raise_kb(enabled))


@router.callback_query(lambda c: c.data == "auto_raise_toggle")
async def cb_auto_raise_toggle(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    settings = rent.user_settings()
    enabled = not bool(settings.get("auto_raise_lots_enabled"))
    update = {"auto_raise_lots_enabled": enabled}
    if enabled and not settings.get("auto_raise_next_at"):
        update["auto_raise_next_at"] = rent.iso(rent.now_utc())
    rent.update_user_settings(**update)
    await callback.answer("Авто поднятие включено" if enabled else "Авто поднятие выключено")
    await callback.message.edit_text(auto_raise_tmpl.auto_raise_text(), reply_markup=auto_raise_kb(enabled))


@router.callback_query(lambda c: c.data == "auto_raise_now")
async def cb_auto_raise_now(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer("Пробую поднять лоты...")
    await state.clear()
    await callback.message.edit_text("Пробую поднять лоты FunPay. Подождите пару секунд...", reply_markup=back_kb())
    await asyncio.to_thread(funpay_bridge.auto_raise_lots_once, rent.current_owner_id())
    enabled = bool(rent.user_settings().get("auto_raise_lots_enabled"))
    await callback.message.edit_text(auto_raise_tmpl.auto_raise_text(), reply_markup=auto_raise_kb(enabled))
