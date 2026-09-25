"""Главная админ-панель (только ADMIN_IDS): статистика, рассылка, баны, пользователи,
обязательная подписка, настройка благодарностей (Crypto Pay)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import fp_accounts
import rental_service as rent
import users
import reliability
from states.all import AdminInput


router = Router()
logger = logging.getLogger("steam_rent.admin")
BROADCAST: dict[str, object] = {"running": False, "cancel": False}


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _back(to: str = "adm") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("← Назад", to)]])


async def _deny(event: CallbackQuery | Message) -> bool:
    """True = доступ запрещён. Админка только для ADMIN_IDS из .env."""
    if users.is_super_admin(event.from_user.id):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer("Только для администратора бота.", show_alert=True)
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Главное меню админки
# ═══════════════════════════════════════════════════════════════════════════
def panel_text() -> str:
    st = users.stats()
    sub = users.admin_cfg()["subscription"]
    paid_count, paid_sum = users.donate_stats()
    fp_total = sum(len(fp_accounts.accounts(uid)) for uid in users.all_users() if uid.isdigit())
    return (
        "┌──────────────────────────────┐\n"
        "│      <b>👑 АДМИН-ПАНЕЛЬ</b>         │\n"
        "└──────────────────────────────┘\n\n"
        f"👥 Пользователей: <b>{st['total']}</b> (+{st['new_today']} сегодня)\n"
        f"🟢 Активны за 24ч: <b>{st['active_24h']}</b>\n"
        f"⛔ Забанено: <b>{st['banned']}</b> · 🚫 заблокировали бота: <b>{st['blocked']}</b>\n"
        f"🔗 FunPay-аккаунтов подключено: <b>{fp_total}</b>\n\n"
        f"📢 Обязательная подписка: <b>{'включена' if users.sub_enabled() else 'выключена'}</b> "
        f"({len(sub['channels'])} кан.)\n"
        f"💝 Благодарности: <b>{'включены' if users.donate_enabled() else 'не настроены'}</b> · "
        f"оплачено {paid_count} на {paid_sum} ₽"
    )


def panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("📨 Рассылка", "adm_bc"), _btn("👤 Пользователь", "adm_user")],
        [_btn("⛔ Забанить", "adm_ban"), _btn("✅ Разбанить", "adm_unban")],
        [_btn("📋 Список банов", "adm_banlist"), _btn("🔄 Обновить", "adm")],
        [_btn("📢 Обязательная подписка", "adm_sub")],
        [_btn("💝 Благодарности (CryptoBot)", "adm_donate")],
        [_btn("🛠 Состояние и ошибки", "adm_ops")],
        [_btn("● Очистка базы", "clean_db")],
        [_btn("← Главная панель", "dashboard")],
    ])


def ops_text() -> str:
    counts = reliability.summary()
    lines = [
        "<b>🛠 Состояние сервисов</b>",
        "",
        f"Очередь: ⏳ {counts['pending']} · ⚙️ {counts['processing']} · ✅ {counts['done']} · ❌ {counts['failed']}",
        "",
        "<b>FunPay-профили</b>",
    ]
    health = reliability.health()
    if health:
        for owner, item in sorted(health.items()):
            scans = ", ".join(
                label for label, key in (("заказы", "last_paid_scan"), ("чаты", "last_message_scan"), ("SMM", "last_smm_scan"))
                if item.get(key)
            ) or "сканирования ещё не было"
            error = f" · ошибка: {rent.esc(item['last_error'])}" if item.get("last_error") else ""
            lines.append(f"• <code>{rent.esc(owner)}</code>: {rent.esc(item.get('username') or '—')} · "
                         f"подключение {rent.esc(item.get('last_connected') or '—')} · {scans}{error}")
    else:
        lines.append("— данных ещё нет")
    failed = reliability.records("failed", 8)
    if failed:
        lines += ["", "<b>Последние ошибки</b>"]
        for item in failed:
            lines.append(f"• <code>{item['id'][:8]}</code> {rent.esc(item.get('kind'))}: "
                         f"{rent.esc(item.get('last_error') or '—')}")
    return "\n".join(lines)


def ops_kb() -> InlineKeyboardMarkup:
    rows = []
    for item in reliability.records("failed", 8):
        rows.append([_btn(f"↻ {item['kind']} {item['id'][:8]}", f"adm_ops_retry:{item['id']}"),
                     _btn("✕", f"adm_ops_dismiss:{item['id']}")])
    rows += [[_btn("🔄 Обновить", "adm_ops")], [_btn("← Назад", "adm")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adm_ops")
async def cb_ops(callback: CallbackQuery):
    if await _deny(callback):
        return
    await callback.answer()
    await callback.message.edit_text(ops_text(), reply_markup=ops_kb())


@router.callback_query(F.data.startswith("adm_ops_retry:"))
async def cb_ops_retry(callback: CallbackQuery):
    if await _deny(callback):
        return
    ok = reliability.retry_now(callback.data.split(":", 1)[1])
    await callback.answer("Поставлено в очередь" if ok else "Операция не найдена", show_alert=not ok)
    await callback.message.edit_text(ops_text(), reply_markup=ops_kb())


@router.callback_query(F.data.startswith("adm_ops_dismiss:"))
async def cb_ops_dismiss(callback: CallbackQuery):
    if await _deny(callback):
        return
    ok = reliability.dismiss(callback.data.split(":", 1)[1])
    await callback.answer("Скрыто" if ok else "Операция не найдена", show_alert=not ok)
    await callback.message.edit_text(ops_text(), reply_markup=ops_kb())


@router.callback_query(F.data.in_({"adm", "admin_panel"}))
async def cb_panel(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(panel_text(), reply_markup=panel_kb())


@router.message(F.text.in_({"/admin", "/adm"}))
async def cmd_admin(message: Message, state: FSMContext):
    if await _deny(message):
        await message.answer("Админ-панель доступна только администратору бота.")
        return
    await state.clear()
    await message.answer(panel_text(), reply_markup=panel_kb())


# ═══════════════════════════════════════════════════════════════════════════
# Рассылка
# ═══════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data == "adm_bc")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await callback.answer()
    if BROADCAST["running"]:
        await callback.message.edit_text("Рассылка уже идёт.", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_btn("⏹ Остановить", "adm_bc_stop")], [_btn("← Назад", "adm")]]))
        return
    await state.set_state(AdminInput.broadcast_content)
    await callback.message.edit_text(
        "<b>📨 Рассылка</b>\n\n"
        f"Получателей: <b>{len(users.broadcast_ids())}</b> (без забаненных и заблокировавших бота)\n\n"
        "Отправь сообщение для рассылки — текст, фото, видео, файл, голосовое… "
        "Оно будет скопировано всем как есть (с форматированием и кнопками).",
        reply_markup=_back(),
    )


@router.message(AdminInput.broadcast_content)
async def in_broadcast(message: Message, state: FSMContext):
    if await _deny(message):
        return
    await state.update_data(bc_chat=message.chat.id, bc_msg=message.message_id)
    await state.set_state(AdminInput.broadcast_confirm)
    await message.answer("⬆️ Так будет выглядеть сообщение.")
    await message.bot.copy_message(message.chat.id, message.chat.id, message.message_id,
                                   reply_markup=message.reply_markup)
    await message.answer(
        f"Отправить <b>{len(users.broadcast_ids())}</b> пользователям?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [_btn("✅ Отправить", "adm_bc_go")],
            [_btn("✗ Отмена", "adm")],
        ]),
    )


@router.callback_query(F.data == "adm_bc_go", AdminInput.broadcast_confirm)
async def cb_broadcast_go(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    data = await state.get_data()
    await state.clear()
    if BROADCAST["running"]:
        await callback.answer("Рассылка уже идёт.", show_alert=True)
        return
    await callback.answer("Запускаю")
    status = await callback.message.edit_text(
        "⏳ Рассылка запущена…",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn("⏹ Остановить", "adm_bc_stop")]]),
    )
    asyncio.create_task(_run_broadcast(callback.bot, int(data["bc_chat"]), int(data["bc_msg"]), status))


@router.callback_query(F.data == "adm_bc_stop")
async def cb_broadcast_stop(callback: CallbackQuery):
    if await _deny(callback):
        return
    BROADCAST["cancel"] = True
    await callback.answer("Останавливаю…")


async def _run_broadcast(bot: Bot, from_chat: int, msg_id: int, status: Message) -> None:
    BROADCAST.update(running=True, cancel=False)
    ids = users.broadcast_ids()
    ok = failed = blocked = 0
    source = None
    try:
        for i, uid in enumerate(ids, start=1):
            if BROADCAST["cancel"]:
                break
            for _attempt in range(3):
                try:
                    await bot.copy_message(uid, from_chat, msg_id)
                    ok += 1
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after + 1)
                except TelegramForbiddenError:
                    users.mark_blocked(uid)
                    blocked += 1
                    break
                except Exception as exc:
                    source = exc
                    failed += 1
                    break
            await asyncio.sleep(0.05)  # ~20 сообщений/сек — в пределах лимитов Telegram
            if i % 50 == 0:
                try:
                    await status.edit_text(f"⏳ Рассылка: {i}/{len(ids)}\n✅ {ok} · 🚫 {blocked} · ❌ {failed}",
                                           reply_markup=status.reply_markup)
                except Exception:
                    pass
    finally:
        stopped = BROADCAST["cancel"]
        BROADCAST.update(running=False, cancel=False)
        if source:
            logger.warning("Broadcast errors, last: %s", source)
        try:
            await status.edit_text(
                f"{'⏹ Рассылка остановлена' if stopped else '✅ Рассылка завершена'}\n\n"
                f"Доставлено: <b>{ok}</b>\nЗаблокировали бота: <b>{blocked}</b>\nОшибок: <b>{failed}</b>",
                reply_markup=_back(),
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Пользователи и баны
# ═══════════════════════════════════════════════════════════════════════════
def user_card(u: dict) -> str:
    uid = u.get("id")
    accs = fp_accounts.accounts(uid) if uid else []
    return (
        f"<b>Пользователь</b> <code>{uid}</code>\n"
        f"Имя: {rent.esc(u.get('name') or '—')}\n"
        f"Username: {('@' + rent.esc(u['username'])) if u.get('username') else '—'}\n"
        f"Первый визит: {rent.esc(rent.fmt(u.get('first_seen')) if u.get('first_seen') else '—')}\n"
        f"Последний: {rent.esc(rent.fmt(u.get('last_seen')) if u.get('last_seen') else '—')}\n"
        f"FunPay: {', '.join(rent.esc(a['username']) for a in accs) or '—'}\n"
        f"Статус: {'⛔ забанен' + (' — ' + rent.esc(u.get('ban_reason')) if u.get('ban_reason') else '') if u.get('banned') else '✅ активен'}"
        f"{' · 🚫 заблокировал бота' if u.get('blocked') else ''}"
    )


def user_kb(u: dict) -> InlineKeyboardMarkup:
    uid = u.get("id")
    ban = _btn("✅ Разбанить", f"adm_unban_do:{uid}") if u.get("banned") else _btn("⛔ Забанить", f"adm_ban_do:{uid}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [ban, _btn("✉️ Написать", f"adm_msg:{uid}")],
        [_btn("← Админ-панель", "adm")],
    ])


@router.callback_query(F.data.in_({"adm_user", "adm_ban", "adm_unban"}))
async def cb_ask_user(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await callback.answer()
    target = {"adm_user": AdminInput.user_lookup, "adm_ban": AdminInput.ban_query, "adm_unban": AdminInput.unban_query}[callback.data]
    hint = "\n\nДля бана можно указать причину: <code>123456789 спам</code>" if callback.data == "adm_ban" else ""
    await state.set_state(target)
    await callback.message.edit_text("Отправь Telegram ID или @username пользователя." + hint, reply_markup=_back())


async def _resolve(message: Message) -> tuple[dict | None, str]:
    parts = (message.text or "").strip().split(maxsplit=1)
    u = users.find_user(parts[0]) if parts else None
    reason = parts[1] if len(parts) > 1 else ""
    if not u:
        await message.answer("Не нашёл такого пользователя (он должен хотя бы раз написать боту, или укажи числовой ID).",
                             reply_markup=_back())
    return u, reason


@router.message(AdminInput.user_lookup, F.text)
async def in_lookup(message: Message, state: FSMContext):
    if await _deny(message):
        return
    u, _ = await _resolve(message)
    if u:
        await state.clear()
        u = users.get_user(u["id"]) or u
        await message.answer(user_card(u), reply_markup=user_kb(u))


async def _do_ban(message_or_cb, bot: Bot, uid: int, reason: str) -> str:
    if users.is_super_admin(uid):
        return "Администратора забанить нельзя."
    users.set_banned(uid, True, reason)
    try:
        await bot.send_message(uid, "⛔ Вы заблокированы в этом боте." + (f"\nПричина: {rent.esc(reason)}" if reason else ""))
    except Exception:
        pass
    return f"⛔ Пользователь <code>{uid}</code> забанен. Его FunPay-аккаунты больше не обслуживаются."


@router.message(AdminInput.ban_query, F.text)
async def in_ban(message: Message, state: FSMContext):
    if await _deny(message):
        return
    u, reason = await _resolve(message)
    if u:
        await state.clear()
        await message.answer(await _do_ban(message, message.bot, int(u["id"]), reason), reply_markup=_back())


@router.message(AdminInput.unban_query, F.text)
async def in_unban(message: Message, state: FSMContext):
    if await _deny(message):
        return
    u, _ = await _resolve(message)
    if u:
        await state.clear()
        users.set_banned(u["id"], False)
        await message.answer(f"✅ Пользователь <code>{u['id']}</code> разбанен.", reply_markup=_back())


@router.callback_query(F.data.startswith("adm_ban_do:"))
async def cb_ban_do(callback: CallbackQuery):
    if await _deny(callback):
        return
    uid = int(callback.data.split(":", 1)[1])
    text = await _do_ban(callback, callback.bot, uid, "")
    await callback.answer("Готово")
    u = users.get_user(uid) or {"id": uid}
    await callback.message.edit_text(text + "\n\n" + user_card(u), reply_markup=user_kb(u))


@router.callback_query(F.data.startswith("adm_unban_do:"))
async def cb_unban_do(callback: CallbackQuery):
    if await _deny(callback):
        return
    uid = int(callback.data.split(":", 1)[1])
    users.set_banned(uid, False)
    await callback.answer("Разбанен")
    u = users.get_user(uid) or {"id": uid}
    await callback.message.edit_text(user_card(u), reply_markup=user_kb(u))


@router.callback_query(F.data == "adm_banlist")
async def cb_banlist(callback: CallbackQuery):
    if await _deny(callback):
        return
    await callback.answer()
    banned = users.banned_users()
    rows = [[_btn(f"✅ {u.get('name') or u.get('username') or u['id']}", f"adm_unban_do:{u['id']}")] for u in banned[:40]]
    rows.append([_btn("← Назад", "adm")])
    text = "<b>Забаненные</b>\n\n" + ("\n".join(
        f"• <code>{u['id']}</code> {rent.esc(u.get('name') or '')} {rent.esc(u.get('ban_reason') or '')}" for u in banned[:40]
    ) or "Никого.") + ("\n\nНажми на пользователя, чтобы разбанить." if banned else "")
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("adm_msg:"))
async def cb_msg(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await callback.answer()
    await state.set_state(AdminInput.user_message)
    await state.update_data(msg_uid=int(callback.data.split(":", 1)[1]))
    await callback.message.answer("Отправь сообщение — оно будет переслано пользователю от имени бота.", reply_markup=_back())


@router.message(AdminInput.user_message)
async def in_msg(message: Message, state: FSMContext):
    if await _deny(message):
        return
    uid = (await state.get_data()).get("msg_uid")
    await state.clear()
    try:
        await message.bot.copy_message(uid, message.chat.id, message.message_id)
        await message.answer("✅ Отправлено.", reply_markup=_back())
    except Exception as exc:
        await message.answer(f"❌ Не доставлено: {rent.esc(exc)}", reply_markup=_back())


# ═══════════════════════════════════════════════════════════════════════════
# Обязательная подписка
# ═══════════════════════════════════════════════════════════════════════════
def sub_text() -> str:
    sub = users.admin_cfg()["subscription"]
    chans = "\n".join(f"• {rent.esc(c['title'])} (<code>{c['chat_id']}</code>)" for c in sub["channels"]) or "каналов нет"
    return (
        "<b>📢 Обязательная подписка</b>\n\n"
        f"Статус: <b>{'✅ включена' if users.sub_enabled() else '❌ выключена'}</b>\n\n"
        f"{chans}\n\n"
        "Как добавить канал: сделай бота <b>администратором</b> канала, затем нажми «Добавить канал».\n"
        "Отключить подписку — одной кнопкой, каналы при этом сохраняются."
    )


def sub_kb() -> InlineKeyboardMarkup:
    sub = users.admin_cfg()["subscription"]
    rows = []
    if sub["channels"]:
        rows.append([_btn("❌ Выключить подписку" if sub["enabled"] else "✅ Включить подписку", "adm_sub_toggle")])
    rows.append([_btn("＋ Добавить канал", "adm_sub_add")])
    rows += [[_btn(f"🗑 {c['title']}", f"adm_sub_del:{c['chat_id']}")] for c in sub["channels"]]
    if sub["channels"]:
        rows.append([_btn("🧹 Удалить все каналы и выключить", "adm_sub_clear")])
    rows.append([_btn("← Назад", "adm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adm_sub")
async def cb_sub(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(sub_text(), reply_markup=sub_kb(), disable_web_page_preview=True)


@router.callback_query(F.data == "adm_sub_toggle")
async def cb_sub_toggle(callback: CallbackQuery):
    if await _deny(callback):
        return
    users.sub_set_enabled(not users.admin_cfg()["subscription"]["enabled"])
    users.reset_sub_cache()
    await callback.answer("Включена" if users.sub_enabled() else "Выключена")
    await callback.message.edit_text(sub_text(), reply_markup=sub_kb(), disable_web_page_preview=True)


@router.callback_query(F.data == "adm_sub_clear")
async def cb_sub_clear(callback: CallbackQuery):
    if await _deny(callback):
        return
    users.sub_clear()
    await callback.answer("Подписка отключена")
    await callback.message.edit_text(sub_text(), reply_markup=sub_kb())


@router.callback_query(F.data.startswith("adm_sub_del:"))
async def cb_sub_del(callback: CallbackQuery):
    if await _deny(callback):
        return
    users.sub_remove_channel(int(callback.data.split(":", 1)[1]))
    await callback.answer("Удалён")
    await callback.message.edit_text(sub_text(), reply_markup=sub_kb())


@router.callback_query(F.data == "adm_sub_add")
async def cb_sub_add(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await callback.answer()
    await state.set_state(AdminInput.sub_channel)
    await callback.message.edit_text(
        "Отправь <b>@username</b> публичного канала, либо <b>перешли сюда любой пост</b> из канала "
        "(для приватных каналов).\n\nБот должен быть админом канала.",
        reply_markup=_back("adm_sub"),
    )


@router.message(AdminInput.sub_channel)
async def in_sub_channel(message: Message, state: FSMContext):
    if await _deny(message):
        return
    bot = message.bot
    fwd_chat = getattr(getattr(message, "forward_origin", None), "chat", None) or getattr(message, "forward_from_chat", None)
    ref = fwd_chat.id if fwd_chat else (message.text or "").strip()
    if isinstance(ref, str):
        if "t.me/" in ref:
            ref = "@" + ref.rstrip("/").split("t.me/")[-1].split("/")[0]
        if ref.lstrip("-").isdigit():
            ref = int(ref)
        elif ref and not ref.startswith("@"):
            ref = "@" + ref
    try:
        chat = await bot.get_chat(ref)
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)
        if str(member.status).split(".")[-1].lower() not in {"administrator", "creator"}:
            raise RuntimeError("бот не администратор этого канала")
        url = f"https://t.me/{chat.username}" if chat.username else (chat.invite_link or "")
        if not url:
            url = (await bot.create_chat_invite_link(chat.id, name="Обязательная подписка")).invite_link
    except Exception as exc:
        await message.answer(f"❌ Не получилось: {rent.esc(exc)}", reply_markup=_back("adm_sub"))
        return
    users.sub_add_channel(chat.id, chat.title or str(chat.id), url)
    await state.clear()
    await message.answer(f"✅ Канал «{rent.esc(chat.title)}» добавлен.\n\n" + sub_text(), reply_markup=sub_kb(),
                         disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════════════
# Настройка благодарностей (Crypto Pay)
# ═══════════════════════════════════════════════════════════════════════════
def donate_text() -> str:
    don = users.admin_cfg()["donate"]
    paid = [d for d in users.admin_cfg()["donations"] if d.get("status") == "paid"]
    last = "\n".join(f"• {d['amount']} ₽ от <code>{d['user_id']}</code> — {rent.esc(rent.fmt(d.get('paid_at')))}" for d in paid[-10:][::-1])
    return (
        "<b>💝 Благодарности через @CryptoBot</b>\n\n"
        f"Статус: <b>{'✅ ' + rent.esc(don.get('app_name')) if don.get('token') else '❌ не настроено'}</b>"
        f"{' (testnet)' if don.get('token') and don.get('net') == 'test' else ''}\n\n"
        "Как подключить: @CryptoBot → Crypto Pay → Создать приложение → скопировать API-токен "
        "и прислать сюда кнопкой ниже.\n\n"
        f"<b>Последние оплаты:</b>\n{last or '—'}"
    )


def donate_kb() -> InlineKeyboardMarkup:
    rows = [[_btn("🔑 Указать / сменить токен", "adm_donate_token")]]
    if users.donate_enabled():
        rows.append([_btn("❌ Отключить благодарности", "adm_donate_off")])
    rows.append([_btn("← Назад", "adm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adm_donate")
async def cb_donate(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(donate_text(), reply_markup=donate_kb())


@router.callback_query(F.data == "adm_donate_off")
async def cb_donate_off(callback: CallbackQuery):
    if await _deny(callback):
        return
    users.donate_disable()
    await callback.answer("Отключено")
    await callback.message.edit_text(donate_text(), reply_markup=donate_kb())


@router.callback_query(F.data == "adm_donate_token")
async def cb_donate_token(callback: CallbackQuery, state: FSMContext):
    if await _deny(callback):
        return
    await callback.answer()
    await state.set_state(AdminInput.donate_token)
    await callback.message.edit_text("Отправь API-токен Crypto Pay (формат <code>12345:AAAA…</code>).",
                                     reply_markup=_back("adm_donate"))


@router.message(AdminInput.donate_token, F.text)
async def in_donate_token(message: Message, state: FSMContext):
    if await _deny(message):
        return
    token = message.text.strip()
    try:
        await message.delete()
    except Exception:
        pass
    try:
        name = await asyncio.to_thread(users.donate_set_token, token)
    except Exception as exc:
        await message.answer(f"❌ {rent.esc(exc)}", reply_markup=_back("adm_donate"))
        return
    await state.clear()
    await message.answer(f"✅ Подключено приложение «{rent.esc(name)}».\n\n" + donate_text(), reply_markup=donate_kb())
