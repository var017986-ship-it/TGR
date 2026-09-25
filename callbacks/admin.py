"""Admin panel callbacks (access codes, database cleanup)."""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import rental_service as rent
from handlers.guards import guard_callback, is_owner
from keyboards import main_kb
from templates import access as access_tmpl
from templates import clean_db as clean_tmpl


router = Router()


@router.callback_query(lambda c: c.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    if not is_owner(callback.from_user.id):
        await callback.answer("Только владелец.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        access_tmpl.admin_panel_text(), reply_markup=access_tmpl.admin_panel_kb()
    )


@router.callback_query(lambda c: c.data == "access_codes")
async def cb_access_codes(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    if not is_owner(callback.from_user.id):
        await callback.answer("Только владелец может генерировать коды.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        access_tmpl.access_codes_text(), reply_markup=access_tmpl.access_codes_kb()
    )


@router.callback_query(lambda c: c.data == "access_code_new")
async def cb_access_code_new(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    if not is_owner(callback.from_user.id):
        await callback.answer("Только владелец может генерировать коды.", show_alert=True)
        return
    await callback.message.edit_text(
        access_tmpl.new_code_text(), reply_markup=access_tmpl.access_code_duration_kb()
    )


@router.callback_query(lambda c: c.data and c.data.startswith("access_code_generate:"))
async def cb_access_code_generate(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    if not is_owner(callback.from_user.id):
        await callback.answer("Только владелец может генерировать коды.", show_alert=True)
        return
    duration = callback.data.split(":", 1)[1]
    result = rent.generate_access_code(callback.from_user.id, duration)
    until = rent.fmt(result.get("expires_at")) if result.get("expires_at") else "навсегда"
    await callback.message.edit_text(
        access_tmpl.code_created_text(
            result["code"], result["duration_label"], until
        ),
        reply_markup=access_tmpl.access_codes_kb(),
    )


@router.callback_query(lambda c: c.data == "clean_db")
async def cb_clean_db(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    if not is_owner(callback.from_user.id):
        await callback.answer("Только владелец может чистить базу.", show_alert=True)
        return
    await callback.message.edit_text(clean_tmpl.clean_db_text(), reply_markup=clean_tmpl.clean_db_kb())


@router.callback_query(lambda c: c.data == "clean_db_confirm")
async def cb_clean_db_confirm(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    if not is_owner(callback.from_user.id):
        await callback.answer("Только владелец может чистить базу.", show_alert=True)
        return
    await callback.message.edit_text(
        clean_tmpl.clean_db_confirm_text(), reply_markup=clean_tmpl.clean_db_confirm_kb()
    )


@router.callback_query(lambda c: c.data == "clean_db_do")
async def cb_clean_db_do(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    if not is_owner(callback.from_user.id):
        await callback.answer("Только владелец может чистить базу.", show_alert=True)
        return
    rent.reset_bot_database()
    await state.clear()
    await callback.message.edit_text(
        clean_tmpl.clean_db_done_text(is_owner(callback.from_user.id)),
        reply_markup=main_kb(is_owner(callback.from_user.id)),
    )


@router.callback_query(lambda c: c.data == "enter_access_code")
async def cb_enter_access_code(callback: CallbackQuery, state: FSMContext):
    from states.all import AccessGate
    await state.set_state(AccessGate.waiting_code)
    await callback.message.answer(
        "<b>Введите код доступа</b>\n\n"
        "Отправьте код одним сообщением. Пример: <code>BOT-ABCD-1234</code>"
    )
    await callback.answer()
