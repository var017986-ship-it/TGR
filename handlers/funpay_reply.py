"""Reply to FunPay chats from Telegram notifications."""
from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import funpay_bridge
import rental_service as rent
from handlers.guards import guard
from states.all import FunPayReply


router = Router()


@router.message(FunPayReply.waiting_text, F.text)
async def funpay_reply_text(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    data = await state.get_data()
    chat_id = str(data.get("funpay_reply_chat_id", "")).strip()
    text = (message.text or "").strip()
    if not chat_id:
        await state.clear()
        await message.answer("Не нашел FunPay-чат для ответа.")
        return
    if not text:
        await message.answer("Ответ пустой. Напиши текст одним сообщением.")
        return

    owner_id = str(data.get("funpay_reply_ws") or "") or rent.current_owner_id() or rent.owner_context_for_user(message.from_user.id)
    rent.set_current_owner(owner_id)
    bridge = funpay_bridge.ACTIVE_BRIDGES.get(owner_id) or funpay_bridge.FunPayBridge(
        bot=None,
        loop=None,
        owner_id=owner_id,
    )
    try:
        await asyncio.to_thread(bridge.send_funpay_message, chat_id, text)
    except Exception as exc:
        await message.answer(f"Не смог отправить ответ в FunPay: <code>{rent.esc(str(exc))}</code>")
        return

    await state.clear()
    await message.answer("Ответ отправлен в FunPay.")
