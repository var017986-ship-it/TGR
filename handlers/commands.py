"""Basic bot commands: /start, /admin, /accounts, /rentals, /lots."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import rental_service as rent
from handlers.guards import guard, is_owner, show_dashboard
from states.all import AccessGate
from templates import accounts as acc_tmpl
from templates import rentals as rent_tmpl
from templates import lots as lots_tmpl
from templates import access as access_tmpl
from keyboards import admin_kb


router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await show_dashboard(message, state)


@router.message(Command("activate"))
async def cmd_activate(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Код доступа больше не требуется. Используйте /start.")


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    import users
    from callbacks.superadmin import panel_kb, panel_text

    if not users.is_super_admin(message.from_user.id):
        await message.answer("Админ-панель доступна только владельцу бота.")
        await state.clear()
        return
    await state.clear()
    await message.answer(panel_text(), reply_markup=panel_kb())


@router.message(Command("accounts"))
async def cmd_accounts(message: Message, state: FSMContext):
    await message.answer("Steam-аренда отключена. Доступен раздел Auto SMM.")


@router.message(Command("rentals"))
async def cmd_rentals(message: Message, state: FSMContext):
    await message.answer("Steam-аренда отключена. Доступен раздел Auto SMM.")


@router.message(Command("lots"))
async def cmd_lots(message: Message, state: FSMContext):
    await message.answer("Steam-лоты отключены. Настройте SMM-лоты в разделе Auto SMM.")
