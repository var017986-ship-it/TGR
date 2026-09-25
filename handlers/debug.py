"""Debug commands: /buyer_menu, /buyer_code."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import rental_service as rent
from handlers.guards import guard


router = Router()


@router.message(Command("buyer_menu"))
async def cmd_buyer_menu(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: <code>/buyer_menu buyer_id</code>")
        return
    await message.answer(f"<pre>{rent.esc(rent.buyer_menu(parts[1].strip()))}</pre>")


@router.message(Command("buyer_code"))
async def cmd_buyer_code(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: <code>/buyer_code buyer_id</code>")
        return
    await message.answer(f"<pre>{rent.esc(rent.code_request(parts[1].strip()))}</pre>")
