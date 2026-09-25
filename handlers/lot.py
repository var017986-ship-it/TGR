"""Lot creation and editing wizard handlers."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import config
import rental_service as rent
from handlers.guards import guard
from keyboards import account_pick_kb, add_account_kb, lot_manage_kb
from states.all import AddLot, EditLot
from templates import lots as lots_tmpl


router = Router()


@router.message(AddLot.waiting_lot_id, F.text)
async def add_lot_id(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    await state.update_data(lot_id=message.text.strip())
    state_data = await state.get_data()
    if state_data.get("lot_mode") == "offline":
        await state.update_data(
            rent_hours=config.DEFAULT_RENT_HOURS,
            rent_minutes=config.DEFAULT_RENT_HOURS * 60,
            notify_before_minutes=config.NOTIFY_BEFORE_MINUTES,
            review_min_stars=0,
            review_bonus_minutes=0,
            account_ids=[],
        )
        await state.set_state(AddLot.picking_accounts)
        accounts = rent.list_accounts()
        if not accounts:
            await message.answer(
                "Аккаунтов пока нет. Сначала добавь Steam-аккаунт, потом вернись к оффлайн-лоту.",
                reply_markup=add_account_kb(),
            )
            await state.clear()
            return
        await message.answer(
            "Выбери аккаунты для оффлайн-выдачи. Можно нажать несколько, потом нажми <b>Готово</b>.",
            reply_markup=account_pick_kb(accounts, []),
        )
        return
    await state.set_state(AddLot.waiting_hours)
    await message.answer(
        "На сколько выдается аренда?\n\n"
        "Примеры: <code>24</code> = 24 часа, <code>30м</code>, <code>90 мин</code>, <code>2ч 30м</code>."
    )


@router.message(AddLot.waiting_hours, F.text)
async def add_lot_hours(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    rent_minutes = rent.parse_duration_minutes(message.text)
    if not rent_minutes:
        await message.answer("Напиши время аренды, например <code>24</code>, <code>30м</code> или <code>2ч 30м</code>.")
        return
    await state.update_data(
        rent_hours=rent.rent_hours_from_minutes(rent_minutes),
        rent_minutes=rent_minutes,
    )
    await state.set_state(AddLot.waiting_notify)
    await message.answer("За сколько минут до конца предупреждать? Например: <code>20</code>")


@router.message(AddLot.waiting_notify, F.text)
async def add_lot_notify(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    if not message.text.strip().isdigit() or int(message.text.strip()) <= 0:
        await message.answer("Напиши число минут, например <code>20</code>.")
        return
    await state.update_data(notify_before_minutes=int(message.text.strip()))
    await state.set_state(AddLot.waiting_review_stars)
    from keyboards import review_bonus_kb
    await message.answer(
        "За какой отзыв начислять дополнительное время?",
        reply_markup=review_bonus_kb(),
    )


@router.message(AddLot.waiting_review_bonus, F.text)
async def add_lot_review_bonus(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    bonus_minutes = rent.parse_duration_minutes(message.text)
    if not bonus_minutes:
        await message.answer("Напиши бонусное время, например <code>30м</code>, <code>1ч</code> или <code>2ч 30м</code>.")
        return
    await state.update_data(review_bonus_minutes=bonus_minutes, account_ids=[])
    await state.set_state(AddLot.picking_accounts)
    accounts = rent.list_accounts()
    if not accounts:
        await message.answer(
            "Аккаунтов пока нет. Сначала добавь Steam-аккаунт, потом вернись к лоту.",
            reply_markup=add_account_kb(),
        )
        await state.clear()
        return
    await message.answer(
        "Выбери аккаунты для этого лота. Можно нажать несколько, потом нажми <b>Готово</b>.",
        reply_markup=account_pick_kb(accounts, []),
    )


@router.message(EditLot.waiting_time, F.text)
async def edit_lot_time(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    data = await state.get_data()
    lot_id = data.get("lot_id", "")
    minutes = rent.parse_duration_minutes(message.text)
    if not minutes:
        await message.answer("Напиши время аренды, например <code>24</code>, <code>30м</code> или <code>2ч 30м</code>.")
        return
    rent.update_lot(lot_id, rent_minutes=minutes)
    await state.clear()
    await message.answer(lots_tmpl.lot_detail_text(lot_id), reply_markup=lot_manage_kb(lot_id))


@router.message(EditLot.waiting_notify, F.text)
async def edit_lot_notify(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    data = await state.get_data()
    lot_id = data.get("lot_id", "")
    if not message.text.strip().isdigit() or int(message.text.strip()) <= 0:
        await message.answer("Напиши число минут, например <code>20</code>.")
        return
    rent.update_lot(lot_id, notify_before_minutes=int(message.text.strip()))
    await state.clear()
    await message.answer(lots_tmpl.lot_detail_text(lot_id), reply_markup=lot_manage_kb(lot_id))


@router.message(EditLot.waiting_bonus_time, F.text)
async def edit_lot_bonus_time(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    data = await state.get_data()
    lot_id = data.get("lot_id", "")
    min_stars = int(data.get("review_min_stars") or 0)
    minutes = rent.parse_duration_minutes(message.text)
    if not minutes:
        await message.answer("Напиши бонусное время, например <code>30м</code>, <code>1ч</code> или <code>2ч 30м</code>.")
        return
    rent.update_lot(lot_id, review_min_stars=min_stars, review_bonus_minutes=minutes)
    await state.clear()
    await message.answer(lots_tmpl.lot_detail_text(lot_id), reply_markup=lot_manage_kb(lot_id))
