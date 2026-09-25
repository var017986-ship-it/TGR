"""Кнопка «Отблагодарить» (Crypto Pay) и проверка обязательной подписки."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import rental_service as rent
import users
from handlers.guards import show_dashboard, subscription_kb, subscription_text
from states.all import DonateInput


router = Router()
logger = logging.getLogger("steam_rent.donate")
AMOUNTS = [100, 250, 500, 1000]
MIN_RUB, MAX_RUB = 50, 100_000


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# ── Обязательная подписка: «Я подписался» ─────────────────────────────────
@router.callback_query(F.data == "sub_check")
async def cb_sub_check(callback: CallbackQuery, state: FSMContext):
    users.reset_sub_cache(callback.from_user.id)
    missing = await users.missing_channels(callback.bot, callback.from_user.id)
    if missing:
        await callback.answer("Подписка не найдена. Подпишитесь на все каналы.", show_alert=True)
        try:
            await callback.message.edit_text(subscription_text(missing), reply_markup=subscription_kb(missing))
        except Exception:
            pass
        return
    await callback.answer("Спасибо за подписку!")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_dashboard(callback.message.model_copy(update={"from_user": callback.from_user}), state)


# ── Отблагодарить ─────────────────────────────────────────────────────────
def donate_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{a} ₽", f"don_amt:{a}") for a in AMOUNTS[:2]],
        [_btn(f"{a} ₽", f"don_amt:{a}") for a in AMOUNTS[2:]],
        [_btn("✏️ Своя сумма", "don_custom")],
        [_btn("← Назад", "dashboard")],
    ])


DONATE_TEXT = (
    "<b>💝 Отблагодарить разработчика</b>\n\n"
    "Если бот экономит вам время — можно поддержать проект через @CryptoBot "
    "(USDT, TON, BTC и др.). Выберите сумму:"
)


@router.callback_query(F.data == "donate")
async def cb_donate(callback: CallbackQuery, state: FSMContext):
    if users.is_banned(callback.from_user.id):
        await callback.answer("⛔ Вы заблокированы.", show_alert=True)
        return
    await state.clear()
    if not users.donate_enabled():
        await callback.answer("Приём благодарностей пока не настроен. Спасибо за желание помочь ❤️", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(DONATE_TEXT, reply_markup=donate_menu_kb())


@router.callback_query(F.data == "don_custom")
async def cb_custom(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(DonateInput.waiting_amount)
    await callback.message.edit_text(
        f"Введите сумму в рублях (от {MIN_RUB} до {MAX_RUB}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn("← Назад", "donate")]]),
    )


@router.message(DonateInput.waiting_amount, F.text)
async def in_amount(message: Message, state: FSMContext):
    text = message.text.strip().replace(" ", "").replace("₽", "")
    if not text.isdigit() or not MIN_RUB <= int(text) <= MAX_RUB:
        await message.answer(f"Нужно число от {MIN_RUB} до {MAX_RUB}.")
        return
    await state.clear()
    await _create_invoice(message.bot, message, message.from_user.id, int(text), edit=False)


@router.callback_query(F.data.startswith("don_amt:"))
async def cb_amount(callback: CallbackQuery, state: FSMContext):
    amount = int(callback.data.split(":", 1)[1])
    await callback.answer("Создаю счёт…")
    await _create_invoice(callback.bot, callback.message, callback.from_user.id, amount, edit=True)


async def _create_invoice(bot: Bot, msg: Message, user_id: int, amount: int, edit: bool) -> None:
    if not users.donate_enabled():
        await msg.answer("Приём благодарностей отключён.")
        return
    try:
        me = await bot.get_me()
        inv = await asyncio.to_thread(users.donate_create, user_id, amount, me.username or "")
    except Exception as exc:
        logger.warning("Crypto Pay invoice failed: %s", exc)
        await msg.answer(f"Не удалось создать счёт: {rent.esc(exc)}")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {amount} ₽ в @CryptoBot", url=inv["url"])],
        [_btn("✅ Я оплатил", f"don_chk:{inv['invoice_id']}")],
        [_btn("← Назад", "donate")],
    ])
    text = (f"Счёт на <b>{amount} ₽</b> создан.\nОплатите в @CryptoBot любой криптовалютой — "
            "счёт действует 1 час. После оплаты придёт подтверждение.")
    if edit:
        await msg.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("don_chk:"))
async def cb_check(callback: CallbackQuery):
    invoice_id = int(callback.data.split(":", 1)[1])
    d = users.donation(invoice_id)
    if not d or int(d["user_id"]) != callback.from_user.id:
        await callback.answer("Счёт не найден.", show_alert=True)
        return
    try:
        status = await asyncio.to_thread(users.donate_refresh, invoice_id)
    except Exception as exc:
        await callback.answer(f"Ошибка проверки: {exc}", show_alert=True)
        return
    if status == "paid":
        await callback.answer("Оплата получена!")
        await _thank(callback.bot, invoice_id)
    elif status == "expired":
        await callback.answer("Счёт истёк. Создайте новый.", show_alert=True)
    else:
        await callback.answer("Оплата пока не поступила.", show_alert=True)


async def _thank(bot: Bot, invoice_id: int) -> None:
    """Отправляет благодарность пользователю и уведомление админам (ровно один раз)."""
    if not users.mark_donation_notified(invoice_id):
        return
    d = users.donation(invoice_id) or {}
    uid = int(d.get("user_id", 0))
    try:
        await bot.send_message(uid, f"❤️ Спасибо за поддержку ({d.get('amount')} ₽)! Это очень помогает развивать бота.")
    except Exception:
        pass
    u = users.get_user(uid)
    who = f"@{u['username']}" if u.get("username") else (u.get("name") or str(uid))
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"💝 Новая благодарность: <b>{d.get('amount')} ₽</b> от {rent.esc(who)} (<code>{uid}</code>)")
        except Exception:
            pass


async def donation_watcher(bot: Bot) -> None:
    """Фоново проверяет неоплаченные счета (раз в 30 сек), чтобы поймать оплату без кнопки «Я оплатил»."""
    while True:
        try:
            if users.donate_enabled():
                for d in users.pending_donations():
                    status = await asyncio.to_thread(users.donate_refresh, int(d["invoice_id"]))
                    if status == "paid":
                        await _thank(bot, int(d["invoice_id"]))
        except Exception as exc:
            logger.warning("Donation watcher: %s", exc)
        await asyncio.sleep(30)
