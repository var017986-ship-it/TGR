"""Telegram-панель плагина Auto SMM (кнопки + ввод текста)."""
from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import rental_service as rent
from handlers.guards import guard, guard_callback
from plugins import auto_smm
from states.all import SmmInput


router = Router()

MESSAGE_KEYS = {
    "after_payment": "После оплаты (просьба прислать ссылку)",
    "ask_confirm": "Подтверждение ссылки ({link})",
    "after_start": "Заказ запущен ({smm_id}, {link}, {quantity})",
    "completed": "Заказ выполнен ({smm_id}, {order_id})",
    "refunded": "Ошибка + возврат ({reason})",
    "failed_no_refund": "Ошибка без возврата ({reason})",
}


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def smm_kb() -> InlineKeyboardMarkup:
    smm = auto_smm.cfg()
    on = lambda v: "✅" if v else "❌"
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{on(smm['enabled'])} Плагин включён", "smm_toggle:enabled")],
        [_btn(f"{on(smm['confirm_link'])} Подтверждение ссылки", "smm_toggle:confirm_link"),
         _btn(f"{on(smm['auto_refund'])} Автовозврат", "smm_toggle:auto_refund")],
        [_btn("🔑 Сервисы (API)", "smm_services"), _btn("📦 SMM-лоты", "smm_lots")],
        [_btn("📋 Заказы", "smm_orders"), _btn("💰 Баланс", "smm_balance")],
        [_btn("🌐 Домены ссылок", "smm_domains"), _btn("✏️ Тексты", "smm_messages")],
        [_btn("← Плагины", "plugins")],
    ])


def back_kb(to: str = "plugin_smm") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("← Назад", to)]])


async def _show_panel(target: Message, edit: bool) -> None:
    if edit:
        await target.edit_text(auto_smm.panel_text(), reply_markup=smm_kb())
    else:
        await target.answer(auto_smm.panel_text(), reply_markup=smm_kb())


@router.callback_query(F.data == "plugin_smm")
async def cb_panel(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await _show_panel(callback.message, edit=True)


@router.callback_query(F.data.startswith("smm_toggle:"))
async def cb_toggle(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    name = callback.data.split(":", 1)[1]
    if name not in {"enabled", "confirm_link", "auto_refund"}:
        await callback.answer()
        return
    smm = auto_smm.cfg()
    if name == "enabled" and not smm["enabled"] and (not smm["services"] or not smm["lots"]):
        await callback.answer("Сначала добавь сервис (API) и хотя бы один SMM-лот.", show_alert=True)
        return
    auto_smm.set_flag(name, not smm[name])
    await callback.answer("Сохранено")
    await _show_panel(callback.message, edit=True)


@router.callback_query(F.data == "smm_services")
async def cb_services(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.set_state(SmmInput.waiting_service)
    await callback.message.edit_text(auto_smm.services_text(), reply_markup=back_kb(), disable_web_page_preview=True)


@router.callback_query(F.data == "smm_lots")
async def cb_lots(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.set_state(SmmInput.waiting_lot)
    await callback.message.edit_text(auto_smm.lots_text(), reply_markup=back_kb())


@router.callback_query(F.data == "smm_orders")
async def cb_orders(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(auto_smm.orders_text(), reply_markup=back_kb())


@router.callback_query(F.data == "smm_balance")
async def cb_balance(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer("Запрашиваю баланс…")
    text = await asyncio.to_thread(auto_smm.balances_text)
    await callback.message.edit_text(text, reply_markup=back_kb())


@router.callback_query(F.data == "smm_domains")
async def cb_domains(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.set_state(SmmInput.waiting_domains)
    await callback.message.edit_text(auto_smm.domains_text(), reply_markup=back_kb())


@router.callback_query(F.data == "smm_messages")
async def cb_messages(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    rows = [[_btn(title, f"smm_msg:{key}")] for key, title in MESSAGE_KEYS.items()]
    rows.append([_btn("← Назад", "plugin_smm")])
    await callback.message.edit_text(
        "<b>Тексты сообщений покупателю</b>\nВыбери, какой текст изменить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("smm_msg:"))
async def cb_message_edit(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    key = callback.data.split(":", 1)[1]
    if key not in MESSAGE_KEYS:
        await callback.answer()
        return
    await callback.answer()
    await state.set_state(SmmInput.waiting_message)
    await state.update_data(smm_msg_key=key)
    current = auto_smm.cfg()["messages"].get(key, "")
    await callback.message.edit_text(
        f"<b>{rent.esc(MESSAGE_KEYS[key])}</b>\n\nСейчас:\n<pre>{rent.esc(current)}</pre>\n"
        "Отправь новый текст (переменные в фигурных скобках сохраняй) или <code>default</code> для сброса.",
        reply_markup=back_kb("smm_messages"),
    )


# ── Ввод текста ──────────────────────────────────────────────────────────
@router.message(SmmInput.waiting_service, F.text)
async def in_service(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    text = message.text.strip()
    try:
        await message.delete()  # в сообщении API-ключ
    except Exception:
        pass
    parts = text.split()
    if len(parts) == 2 and parts[0].lower() in {"del", "удалить"}:
        ok = auto_smm.delete_service(parts[1])
        await message.answer(("Удалено." if ok else "Такого сервиса нет.") + "\n\n" + auto_smm.services_text(),
                             reply_markup=back_kb(), disable_web_page_preview=True)
        return
    if len(parts) != 3 or not parts[1].startswith("http"):
        await message.answer("Формат: <code>1 https://twiboost.com/api/v2 API_КЛЮЧ</code>", reply_markup=back_kb())
        return
    num, url, key = parts
    auto_smm.set_service(num, url, key)
    try:
        balance = await asyncio.to_thread(auto_smm.api_balance, num)
        check = f"✅ Подключено, баланс: <b>{rent.esc(balance)}</b>"
    except Exception as exc:
        check = f"⚠️ Сервис сохранён, но проверка не прошла: {rent.esc(exc)}"
    await message.answer(check + "\n\n" + auto_smm.services_text(), reply_markup=back_kb(), disable_web_page_preview=True)


@router.message(SmmInput.waiting_lot, F.text)
async def in_lot(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    text = message.text.strip()
    parts = text.split()
    if len(parts) == 2 and parts[0].lower() in {"del", "удалить"}:
        ok = auto_smm.delete_lot(parts[1])
        await message.answer(("Удалено." if ok else "Такого лота нет.") + "\n\n" + auto_smm.lots_text(), reply_markup=back_kb())
        return
    fields = [p.strip() for p in text.split("|")]
    if len(fields) < 3:
        await message.answer("Формат: <code>Название лота | ID услуги | количество | № сервиса</code>", reply_markup=back_kb())
        return
    name, service_id, quantity = fields[0], fields[1], fields[2]
    service_number = fields[3] if len(fields) > 3 and fields[3] else "1"
    if not name or not service_id.isdigit() or not quantity.isdigit():
        await message.answer("ID услуги и количество должны быть числами.", reply_markup=back_kb())
        return
    if service_number not in auto_smm.cfg()["services"]:
        await message.answer(f"Сервис №{rent.esc(service_number)} не настроен. Сначала добавь его в «Сервисы (API)».",
                             reply_markup=back_kb())
        return
    lot = auto_smm.add_lot(name, int(service_id), int(quantity), service_number)
    await message.answer(f"✅ Лот #{lot['id']} добавлен.\n\n" + auto_smm.lots_text(), reply_markup=back_kb())


@router.message(SmmInput.waiting_domains, F.text)
async def in_domains(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    text = message.text.strip()
    if text.lower() == "any":
        auto_smm.set_domains([])
    elif text.lower() == "default":
        auto_smm.set_domains(list(auto_smm.DEFAULT_DOMAINS))
    else:
        auto_smm.set_domains([d for d in text.replace("\n", ",").split(",")])
    await message.answer("✅ Сохранено.\n\n" + auto_smm.domains_text(), reply_markup=back_kb())


@router.message(SmmInput.waiting_message, F.text)
async def in_message(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    data = await state.get_data()
    key = data.get("smm_msg_key")
    if key not in MESSAGE_KEYS:
        await state.clear()
        return
    text = message.html_text if message.text.strip().lower() != "default" else auto_smm.DEFAULT_MESSAGES[key]
    auto_smm.set_message(key, text)
    await state.clear()
    await message.answer("✅ Текст сохранён.", reply_markup=back_kb("smm_messages"))
