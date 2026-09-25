"""Simulate order handler — manual purchase test."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import config
import rental_service as rent
from handlers.guards import guard, is_owner
from keyboards import back_kb, main_kb
from states.all import SimulateOrder


router = Router()


@router.message(Command("simulate"))
async def cmd_simulate(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    await state.set_state(SimulateOrder.waiting_line)
    await message.answer(
        "<b>Тест покупки</b>\n\n"
        "Введи данные тестовой покупки в формате:\n"
        "<code>lot_id | buyer_id | buyer_name | время</code>\n\n"
        "Пример:\n"
        "<code>123456 | 777 | test_user | 24</code> или <code>123456 | 777 | test_user | 30м</code>",
        reply_markup=back_kb(),
    )


@router.message(SimulateOrder.waiting_line, F.text)
async def simulate_order_line(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    parts = [item.strip() for item in message.text.split("|")]
    if len(parts) < 3:
        await message.answer("Формат: <code>lot_id | buyer_id | buyer_name | время</code>")
        return
    minutes = rent.parse_duration_minutes(parts[3]) if len(parts) > 3 else config.DEFAULT_RENT_HOURS * 60
    if not minutes:
        await message.answer("Время укажи так: <code>24</code>, <code>30м</code> или <code>2ч 30м</code>.")
        return
    result = rent.allocate_or_extend(
        lot_id=parts[0],
        buyer_id=parts[1],
        buyer_name=parts[2],
        rent_hours=rent.rent_hours_from_minutes(minutes),
        rent_minutes=minutes,
        order_id="telegram-test",
    )
    await state.clear()
    await message.answer(
        f"<b>Результат:</b> {rent.esc(result.get('type', 'error'))}\n\n"
        f"<pre>{rent.esc(result['message'])}</pre>",
        reply_markup=main_kb(is_owner(message.from_user.id)),
    )
