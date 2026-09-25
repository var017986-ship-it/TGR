"""Lot creation, editing, and account selection callbacks."""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import rental_service as rent
from handlers.guards import guard_callback
from keyboards import account_pick_kb, back_kb, lot_account_pick_kb, lot_manage_kb, lot_review_bonus_kb, lots_kb
from plugins import offline_one_time_code
from states.all import AddLot, EditLot
from templates import lots as lots_tmpl


router = Router()


@router.callback_query(lambda c: c.data == "add_lot")
async def cb_add_lot(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.set_state(AddLot.waiting_lot_id)
    await state.update_data(account_ids=[])
    await callback.message.edit_text(
        "<b>Добавление лота</b>\n\nНапиши ID лота FunPay.",
        reply_markup=back_kb(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("lot_view:"))
async def cb_lot_view(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    lot_id = callback.data.split(":", 1)[1]
    if offline_one_time_code.is_enabled_lot(lot_id):
        await callback.message.edit_text(
            offline_one_time_code.lot_detail_text(lot_id),
            reply_markup=offline_one_time_code.lot_detail_kb(lot_id),
        )
        return
    await callback.message.edit_text(lots_tmpl.lot_detail_text(lot_id), reply_markup=lot_manage_kb(lot_id))


@router.callback_query(lambda c: c.data and c.data.startswith("lot_edit_time:"))
async def cb_lot_edit_time(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    lot_id = callback.data.split(":", 1)[1]
    await state.set_state(EditLot.waiting_time)
    await state.update_data(lot_id=lot_id)
    await callback.message.edit_text(
        "Напиши новое время аренды.\n\n"
        "Примеры: <code>24</code>, <code>30м</code>, <code>2ч 30м</code>.",
        reply_markup=lot_manage_kb(lot_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("lot_edit_notify:"))
async def cb_lot_edit_notify(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    lot_id = callback.data.split(":", 1)[1]
    await state.set_state(EditLot.waiting_notify)
    await state.update_data(lot_id=lot_id)
    await callback.message.edit_text(
        "За сколько минут до конца предупреждать?\n\n"
        "Например: <code>20</code>",
        reply_markup=lot_manage_kb(lot_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("lot_edit_bonus:"))
async def cb_lot_edit_bonus(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    lot_id = callback.data.split(":", 1)[1]
    await state.set_state(AddLot.waiting_review_stars)
    await state.update_data(edit_lot_id=lot_id)
    await callback.message.edit_text(
        "За какой отзыв начислять дополнительное время?",
        reply_markup=lot_review_bonus_kb(lot_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("lot_edit_accounts:"))
async def cb_lot_edit_accounts(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    lot_id = callback.data.split(":", 1)[1]
    lot = rent.lot_by_id(rent.list_lots(), lot_id)
    selected = list((lot or {}).get("account_ids", []))
    await state.set_state(EditLot.picking_accounts)
    await state.update_data(lot_id=lot_id, account_ids=selected)
    await callback.message.edit_text(
        "Выбери аккаунты для этого лота. Можно нажать несколько, потом нажми <b>Готово</b>.",
        reply_markup=lot_account_pick_kb(lot_id, rent.list_accounts(), selected),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("review_bonus:"))
async def cb_review_bonus(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    current_state = await state.get_state()
    if current_state != AddLot.waiting_review_stars.state:
        return
    min_stars = int(callback.data.split(":", 1)[1])
    state_data = await state.get_data()
    edit_lot_id = state_data.get("edit_lot_id")
    if edit_lot_id:
        if min_stars:
            await state.set_state(EditLot.waiting_bonus_time)
            await state.update_data(lot_id=edit_lot_id, review_min_stars=min_stars)
            label = "4 или 5 звезд" if min_stars == 4 else "5 звезд"
            await callback.message.edit_text(
                f"Сколько времени добавить за отзыв на {label}?\n\n"
                "Примеры: <code>30м</code>, <code>1ч</code>, <code>2ч 30м</code>.",
                reply_markup=lot_manage_kb(edit_lot_id),
            )
            return
        rent.update_lot(edit_lot_id, review_min_stars=0, review_bonus_minutes=0)
        await state.clear()
        await callback.message.edit_text(
            lots_tmpl.lot_detail_text(edit_lot_id), reply_markup=lot_manage_kb(edit_lot_id)
        )
        return
    await state.update_data(review_min_stars=min_stars)
    if min_stars:
        await state.set_state(AddLot.waiting_review_bonus)
        label = "4 или 5 звезд" if min_stars == 4 else "5 звезд"
        await callback.message.edit_text(
            f"Сколько времени добавить за отзыв на {label}?\n\n"
            "Примеры: <code>30м</code>, <code>1ч</code>, <code>2ч 30м</code>.",
            reply_markup=back_kb(),
        )
        return
    await state.update_data(review_bonus_minutes=0, account_ids=[])
    await state.set_state(AddLot.picking_accounts)
    accounts = rent.list_accounts()
    if not accounts:
        from keyboards import add_account_kb
        await callback.message.edit_text(
            "Аккаунтов пока нет. Сначала добавь Steam-аккаунт, потом вернись к лоту.",
            reply_markup=add_account_kb(),
        )
        await state.clear()
        return
    await callback.message.edit_text(
        "Выбери аккаунты для этого лота. Можно нажать несколько, потом нажми <b>Готово</b>.",
        reply_markup=account_pick_kb(accounts, []),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("pick_acc:"))
async def cb_pick_acc(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    current_state = await state.get_state()
    if current_state != AddLot.picking_accounts.state:
        return
    account_id = callback.data.split(":", 1)[1]
    state_data = await state.get_data()
    if state_data.get("lot_mode") != "offline":
        account = rent.account_by_id(rent.list_accounts(), account_id)
        if account and account.get("offline_mode"):
            await callback.answer("Этот аккаунт уже в авто-оффлайн режиме.", show_alert=True)
            return
    selected = list(state_data.get("account_ids", []))
    if account_id in selected:
        selected.remove(account_id)
    else:
        selected.append(account_id)
    await state.update_data(account_ids=selected)
    await callback.message.edit_reply_markup(reply_markup=account_pick_kb(rent.list_accounts(), selected))


@router.callback_query(lambda c: c.data and c.data.startswith("lot_pick_acc:"))
async def cb_lot_pick_acc(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    current_state = await state.get_state()
    if current_state != EditLot.picking_accounts.state:
        return
    _, lot_id, account_id = callback.data.split(":", 2)
    state_data = await state.get_data()
    if state_data.get("return_to") != "plugin_offline_lot":
        account = rent.account_by_id(rent.list_accounts(), account_id)
        if account and account.get("offline_mode"):
            await callback.answer("Этот аккаунт уже в авто-оффлайн режиме.", show_alert=True)
            return
    selected = list(state_data.get("account_ids", []))
    if account_id in selected:
        selected.remove(account_id)
    else:
        selected.append(account_id)
    await state.update_data(account_ids=selected)
    await callback.message.edit_reply_markup(
        reply_markup=lot_account_pick_kb(lot_id, rent.list_accounts(), selected)
    )


@router.callback_query(lambda c: c.data and c.data.startswith("lot_finish_accounts:"))
async def cb_lot_finish_accounts(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    current_state = await state.get_state()
    if current_state != EditLot.picking_accounts.state:
        return
    lot_id = callback.data.split(":", 1)[1]
    state_data = await state.get_data()
    selected = list(state_data.get("account_ids", []))
    if not selected:
        await callback.message.answer("Выбери хотя бы один аккаунт.")
        return
    rent.update_lot(lot_id, account_ids=selected)
    await state.clear()
    if state_data.get("return_to") == "plugin_offline_lot":
        offline_one_time_code.sync_offline_account_flags()
        await callback.message.edit_text(
            offline_one_time_code.lot_detail_text(lot_id),
            reply_markup=offline_one_time_code.lot_detail_kb(lot_id),
        )
        return
    await callback.message.edit_text(
        lots_tmpl.lot_detail_text(lot_id), reply_markup=lot_manage_kb(lot_id)
    )


@router.callback_query(lambda c: c.data == "finish_lot_accounts")
async def cb_finish_lot_accounts(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    current_state = await state.get_state()
    if current_state != AddLot.picking_accounts.state:
        return
    state_data = await state.get_data()
    selected = state_data.get("account_ids", [])
    if not selected:
        await callback.message.answer("Выбери хотя бы один аккаунт.")
        return
    lot = rent.upsert_lot(
        {
            "id": state_data["lot_id"],
            "title": rent.lot_title_from_accounts(selected),
            "rent_hours": state_data["rent_hours"],
            "rent_minutes": state_data["rent_minutes"],
            "notify_before_minutes": state_data["notify_before_minutes"],
            "review_min_stars": state_data.get("review_min_stars", 0),
            "review_bonus_minutes": state_data.get("review_bonus_minutes", 0),
            "account_ids": selected,
        }
    )
    if state_data.get("lot_mode") == "offline":
        offline_one_time_code.enable_lot(str(lot["id"]))
        await state.clear()
        await callback.message.edit_text(
            f"Оффлайн-лот <b>{rent.esc(lot['title'])}</b> сохранен и включен.\n"
            f"Аккаунтов привязано: <b>{len(lot['account_ids'])}</b>",
            reply_markup=offline_one_time_code.lots_kb(),
        )
        return
    await state.clear()
    await callback.message.edit_text(
        f"Лот <b>{rent.esc(lot['title'])}</b> сохранен.\n"
        f"Аккаунтов привязано: <b>{len(lot['account_ids'])}</b>",
        reply_markup=lots_kb(),
    )
