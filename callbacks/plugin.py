"""Plugin callbacks (offline one-time code)."""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import rental_service as rent
from handlers.guards import guard_callback
from keyboards import lot_account_pick_kb
from plugins import offline_one_time_code
from states.all import AddLot, EditLot
from templates import plugin as plugin_tmpl


router = Router()


@router.callback_query(lambda c: c.data == "plugins")
async def cb_plugins(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(plugin_tmpl.plugins_text(), reply_markup=plugin_tmpl.plugins_kb())


@router.callback_query(lambda c: c.data == "plugin_offline")
async def cb_plugin_offline(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        plugin_tmpl.offline_plugin_text(), reply_markup=plugin_tmpl.offline_plugin_kb()
    )


@router.callback_query(lambda c: c.data == "plugin_offline_lots")
async def cb_plugin_offline_lots(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        plugin_tmpl.offline_lots_text(), reply_markup=plugin_tmpl.offline_lots_kb()
    )


@router.callback_query(lambda c: c.data and c.data.startswith("plugin_offline_lot:"))
async def cb_plugin_offline_lot(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    lot_id = callback.data.split(":", 1)[1]
    await state.clear()
    await callback.message.edit_text(
        plugin_tmpl.offline_lot_detail_text(lot_id),
        reply_markup=plugin_tmpl.offline_lot_detail_kb(lot_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("plugin_offline_edit_accounts:"))
async def cb_plugin_offline_edit_accounts(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    lot_id = callback.data.split(":", 1)[1]
    lot = rent.lot_by_id(rent.list_lots(), lot_id)
    selected = list((lot or {}).get("account_ids", []))
    await state.set_state(EditLot.picking_accounts)
    await state.update_data(lot_id=lot_id, account_ids=selected, return_to="plugin_offline_lot")
    await callback.message.edit_text(
        "Выбери аккаунты для оффлайн-выдачи. Можно нажать несколько, потом нажми <b>Готово</b>.",
        reply_markup=lot_account_pick_kb(lot_id, rent.list_accounts(), selected),
    )


@router.callback_query(lambda c: c.data == "plugin_offline_add_lot")
async def cb_plugin_offline_add_lot(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.set_state(AddLot.waiting_lot_id)
    await state.update_data(account_ids=[], lot_mode="offline")
    await callback.message.edit_text(
        "<b>Добавление оффлайн-лота</b>\n\n"
        "Напиши ID лота FunPay. Время аренды для оффлайн-выдачи спрашиваться не будет.",
        reply_markup=offline_one_time_code.plugin_kb(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("plugin_offline_toggle:"))
async def cb_plugin_offline_toggle(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    lot_id = callback.data.split(":", 1)[1]
    if offline_one_time_code.is_enabled_lot(lot_id):
        result = offline_one_time_code.disable_lot(lot_id)
    else:
        result = offline_one_time_code.enable_lot(lot_id)
    await callback.answer(result.get("message", "Готово"), show_alert=False)
    await callback.message.edit_text(
        plugin_tmpl.offline_lot_detail_text(lot_id),
        reply_markup=plugin_tmpl.offline_lot_detail_kb(lot_id),
    )


# ── Обработка кнопок «Это ваш аккаунт?» в чате покупателя ──────────────────
@router.callback_query(lambda c: c.data and c.data.startswith("plugin_offline_confirm:"))
async def cb_plugin_offline_confirm(callback: CallbackQuery, state: FSMContext):
    """Покупатель подтвердил: аккаунт подходит."""
    if not await guard_callback(callback, state):
        return
    await callback.answer("Принято!")
    await callback.message.edit_text(
        "✅ Отлично! Аккаунт подтверждён.\n\n"
        "Когда будете готовы зайти — напишите <code>!code</code> в чат FunPay.\n"
        "Код выдаётся один раз.",
        reply_markup=None,
    )


@router.callback_query(lambda c: c.data and c.data.startswith("plugin_offline_change:"))
async def cb_plugin_offline_change(callback: CallbackQuery, state: FSMContext):
    """Покупатель нажал «Поменять аккаунт»."""
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    chat_id = callback.data.split(":", 1)[1]
    buyer_id = str(callback.from_user.id)
    result = offline_one_time_code.handle_change_account(chat_id, buyer_id)
    msg = result.get("message", "Готово.")
    await callback.message.edit_text(msg, reply_markup=None)
