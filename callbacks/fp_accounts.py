"""FunPay-аккаунты пользователя (до 3): добавление (прокси -> golden key -> проверка), выбор, удаление."""
from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import fp_accounts
import rental_service as rent
from handlers.guards import guard, guard_callback
from states.all import FpAccountAdd


router = Router()


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("✗ Отмена", "fpacc")]])


def list_text(user_id: int) -> str:
    accs = fp_accounts.accounts(user_id)
    active = fp_accounts.active_ws(user_id)
    lines = [
        "┌──────────────────────────────┐",
        "│     <b>FUNPAY АККАУНТЫ</b>          │",
        "└──────────────────────────────┘",
        "",
        f"Подключено: <b>{len(accs)}/{fp_accounts.MAX_ACCOUNTS}</b>",
        "",
    ]
    if not accs:
        lines.append("Пока нет ни одного аккаунта. Нажми «Добавить аккаунт».")
    for a in accs:
        mark = "🟢" if a["ws"] == active else "⚪️"
        lines.append(
            f"{mark} <b>{rent.esc(a['username'])}</b> (ID {rent.esc(a['funpay_id'])})\n"
            f"     прокси: <code>{rent.esc(fp_accounts.mask_proxy(a['proxy']))}</code>"
        )
    lines += ["", "🟢 — аккаунт, которым ты сейчас управляешь (лоты, Steam-аккаунты, SMM).",
              "Уведомления приходят по всем подключённым аккаунтам."]
    return "\n".join(lines)


def list_kb(user_id: int) -> InlineKeyboardMarkup:
    accs = fp_accounts.accounts(user_id)
    active = fp_accounts.active_ws(user_id)
    rows = [[_btn(f"{'🟢' if a['ws'] == active else '⚪️'} {a['username']}", f"fpacc_view:{a['ws']}")] for a in accs]
    if len(accs) < fp_accounts.MAX_ACCOUNTS:
        rows.append([_btn("＋ Добавить аккаунт", "fpacc_add")])
    rows.append([_btn("← Главная панель", "dashboard")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def view_text(ws: str) -> str:
    s = rent.user_settings(ws)
    return (
        f"<b>{rent.esc(s.get('funpay_username') or ws)}</b>\n\n"
        f"FunPay ID: <code>{rent.esc(s.get('funpay_user_id'))}</code>\n"
        f"Прокси: <code>{rent.esc(fp_accounts.mask_proxy(s.get('funpay_proxy')))}</code>\n"
        f"Последняя проверка ключа: {rent.esc(rent.fmt(s.get('funpay_checked_at')) if s.get('funpay_checked_at') else '—')}"
    )


def view_kb(user_id: int, ws: str) -> InlineKeyboardMarkup:
    rows = []
    if fp_accounts.active_ws(user_id) != ws:
        rows.append([_btn("🟢 Сделать активным", f"fpacc_use:{ws}")])
    rows += [
        [_btn("🔄 Проверить ключ", f"fpacc_check:{ws}")],
        [_btn("🔁 Сменить прокси / ключ", f"fpacc_edit:{ws}")],
        [_btn("🗑 Отключить аккаунт", f"fpacc_del:{ws}")],
        [_btn("← К списку", "fpacc")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _own_ws(callback: CallbackQuery) -> str | None:
    ws = callback.data.split(":", 1)[1]
    if not fp_accounts.owns(callback.from_user.id, ws):
        await callback.answer("Это не ваш аккаунт.", show_alert=True)
        return None
    return ws


@router.callback_query(F.data.in_({"fpacc", "funpay_key"}))
async def cb_list(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    await callback.answer()
    await state.clear()
    uid = callback.from_user.id
    await callback.message.edit_text(list_text(uid), reply_markup=list_kb(uid))


@router.callback_query(F.data.startswith("fpacc_view:"))
async def cb_view(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    ws = await _own_ws(callback)
    if not ws:
        return
    await callback.answer()
    await callback.message.edit_text(view_text(ws), reply_markup=view_kb(callback.from_user.id, ws))


@router.callback_query(F.data.startswith("fpacc_use:"))
async def cb_use(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    ws = await _own_ws(callback)
    if not ws:
        return
    fp_accounts.set_active(callback.from_user.id, ws)
    rent.set_current_owner(ws)
    await callback.answer("Аккаунт выбран")
    uid = callback.from_user.id
    await callback.message.edit_text(list_text(uid), reply_markup=list_kb(uid))


@router.callback_query(F.data.startswith("fpacc_check:"))
async def cb_check(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    ws = await _own_ws(callback)
    if not ws:
        return
    await callback.answer("Проверяю…")
    try:
        info = await asyncio.to_thread(fp_accounts.recheck, ws)
        result = f"✅ Ключ рабочий: <b>{rent.esc(info['username'])}</b>" + (f", баланс {rent.esc(info['balance'])}" if info["balance"] else "")
    except Exception as exc:
        result = f"❌ {rent.esc(exc)}"
    await callback.message.edit_text(result + "\n\n" + view_text(ws), reply_markup=view_kb(callback.from_user.id, ws))


@router.callback_query(F.data.startswith("fpacc_del:"))
async def cb_delete(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    ws = await _own_ws(callback)
    if not ws:
        return
    await callback.answer()
    name = rent.user_settings(ws).get("funpay_username") or ws
    await callback.message.edit_text(
        f"Отключить <b>{rent.esc(name)}</b>?\n\nБот перестанет обрабатывать его заказы. "
        "Лоты и Steam-аккаунты этого профиля сохранятся — если подключишь ключ снова, всё вернётся.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [_btn("✓ Да, отключить", f"fpacc_delok:{ws}")],
            [_btn("✗ Отмена", f"fpacc_view:{ws}")],
        ]),
    )


@router.callback_query(F.data.startswith("fpacc_delok:"))
async def cb_delete_ok(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    ws = await _own_ws(callback)
    if not ws:
        return
    fp_accounts.remove_account(ws)
    uid = callback.from_user.id
    if fp_accounts.active_ws(uid) == ws:
        rest = fp_accounts.accounts(uid)
        fp_accounts.set_active(uid, rest[0]["ws"] if rest else fp_accounts.slots(uid)[0])
    await callback.answer("Аккаунт отключён")
    await callback.message.edit_text(list_text(uid), reply_markup=list_kb(uid))


# ── Мастер добавления ───────────────────────────────────────────────────
PROXY_PROMPT = (
    "<b>Шаг 1/2 — прокси</b>\n\n"
    "Введите прокси для этого аккаунта FunPay:\n"
    "<code>ip:port</code>\n<code>ip:port:login:password</code>\n<code>login:password@ip:port</code>\n"
    "(можно с префиксом <code>http://</code> или <code>socks5://</code>)\n\n"
    "Без прокси — отправьте <code>-</code>"
)


@router.callback_query(F.data == "fpacc_add")
async def cb_add(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    ws = fp_accounts.free_slot(callback.from_user.id)
    if not ws:
        await callback.answer(f"Максимум {fp_accounts.MAX_ACCOUNTS} аккаунта.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(FpAccountAdd.waiting_proxy)
    await state.update_data(fp_ws=ws)
    await callback.message.edit_text(PROXY_PROMPT, reply_markup=_cancel_kb())


@router.callback_query(F.data.startswith("fpacc_edit:"))
async def cb_edit(callback: CallbackQuery, state: FSMContext):
    if not await guard_callback(callback, state):
        return
    ws = await _own_ws(callback)
    if not ws:
        return
    await callback.answer()
    await state.set_state(FpAccountAdd.waiting_proxy)
    await state.update_data(fp_ws=ws)
    await callback.message.edit_text(PROXY_PROMPT, reply_markup=_cancel_kb())


@router.message(FpAccountAdd.waiting_proxy, F.text)
async def in_proxy(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    text = message.text.strip()
    try:
        await message.delete()  # в прокси может быть пароль
    except Exception:
        pass
    proxy = ""
    if text not in {"-", "нет", "без", "no"}:
        try:
            proxy = fp_accounts.normalize_proxy(text)
        except ValueError as exc:
            await message.answer(f"❌ Прокси не распознан: {rent.esc(exc)}\n\n" + PROXY_PROMPT, reply_markup=_cancel_kb())
            return
        wait = await message.answer("⏳ Проверяю прокси…")
        try:
            ip = await asyncio.to_thread(fp_accounts.check_proxy, proxy)
        except Exception as exc:
            await wait.edit_text(f"❌ {rent.esc(exc)}\n\nОтправьте другой прокси или <code>-</code>.", reply_markup=_cancel_kb())
            return
        await wait.edit_text(f"✅ Прокси работает{f' (IP {rent.esc(ip)})' if ip else ''}.")
    await state.update_data(fp_proxy=proxy)
    await state.set_state(FpAccountAdd.waiting_key)
    await message.answer(
        "<b>Шаг 2/2 — golden key</b>\n\n"
        "Введите golden key от FunPay.\n"
        "<i>Где взять: зайдите на funpay.com → F12 → Application → Cookies → golden_key.</i>",
        reply_markup=_cancel_kb(),
    )


@router.message(FpAccountAdd.waiting_key, F.text)
async def in_key(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    key = message.text.strip()
    try:
        await message.delete()  # ключ = полный доступ к аккаунту, не оставляем в чате
    except Exception:
        pass
    data = await state.get_data()
    ws, proxy = data.get("fp_ws"), data.get("fp_proxy", "")
    uid = message.from_user.id
    if not ws or not fp_accounts.owns(uid, ws):
        await state.clear()
        return
    wait = await message.answer("⏳ Проверяю golden key на FunPay…")
    try:
        info = await asyncio.to_thread(fp_accounts.check_golden_key, key, proxy or None)
    except Exception as exc:
        await wait.edit_text(f"❌ {rent.esc(exc)}\n\nОтправьте ключ ещё раз или нажмите «Отмена».", reply_markup=_cancel_kb())
        return
    dup = fp_accounts.find_duplicate(info["id"], except_ws=ws)
    if dup:
        await wait.edit_text(f"❌ Аккаунт <b>{rent.esc(info['username'])}</b> уже подключён в боте.", reply_markup=_cancel_kb())
        return
    fp_accounts.save_account(ws, key, proxy, info)
    fp_accounts.set_active(uid, ws)
    await state.clear()
    extra = f"\nБаланс: {rent.esc(info['balance'])}" if info["balance"] else ""
    await wait.edit_text(
        f"✅ Ключ рабочий! Подключён аккаунт <b>{rent.esc(info['username'])}</b> (ID {info['id']}){extra}\n"
        f"Прокси: <code>{rent.esc(fp_accounts.mask_proxy(proxy))}</code>\n\n"
        "Через ~15 секунд бот начнёт слушать заказы этого аккаунта. Он выбран активным — "
        "теперь настрой для него лоты / Steam-аккаунты / SMM.",
        reply_markup=list_kb(uid),
    )
