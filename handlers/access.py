"""Legacy access-code handlers kept for old callback compatibility."""
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import rental_service as rent
from handlers.guards import show_dashboard
from keyboards import access_prompt_kb
from states.all import AccessGate


router = Router()


@router.message(AccessGate.waiting_code, F.text)
async def access_code_input(message: Message, state: FSMContext):
    rent.set_current_owner(rent.owner_context_for_user(message.from_user.id))
    await state.clear()
    await message.answer("Код доступа больше не требуется.")
    await show_dashboard(message, state)


@router.message(F.text.regexp(r"(?i)^bot-[a-z0-9]{4}-[a-z0-9]{4}$"))
async def access_code_text_fallback(message: Message, state: FSMContext):
    await message.answer("Код доступа больше не требуется. Откройте /start.")
