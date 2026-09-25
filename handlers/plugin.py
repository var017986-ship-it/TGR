"""Offline one-time code plugin commands."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers.guards import guard
from keyboards import back_kb
from plugins import offline_one_time_code


router = Router()


@router.message(Command("offline_enable"))
async def cmd_offline_enable(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: <code>/offline_enable lot_id</code>")
        return
    result = offline_one_time_code.enable_lot(parts[1].strip())
    await message.answer(result["message"])


@router.message(Command("offline_disable"))
async def cmd_offline_disable(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: <code>/offline_disable lot_id</code>")
        return
    result = offline_one_time_code.disable_lot(parts[1].strip())
    await message.answer(result["message"])


@router.message(Command("offline_status"))
async def cmd_offline_status(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    await message.answer(offline_one_time_code.status_text(), reply_markup=back_kb())
