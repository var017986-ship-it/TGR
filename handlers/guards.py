"""Shared guard helpers for all handlers."""
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import rental_service as rent
import users
from keyboards import access_extend_kb, access_prompt_kb


BANNED_TEXT = "⛔ Вы заблокированы в этом боте."


def is_owner(user_id: int) -> bool:
    """True only for Telegram IDs explicitly listed in ADMIN_IDS."""
    return user_id in config.ADMIN_IDS


def is_admin(user_id: int) -> bool:
    """All non-banned users may use the public bot features."""
    return True


def subscription_kb(channels: list[dict]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📢 {c.get('title') or 'Канал'}", url=c["url"])]
            for c in channels if c.get("url")]
    rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="sub_check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_text(channels: list[dict]) -> str:
    return (
        "<b>Подпишитесь, чтобы пользоваться ботом</b>\n\n"
        + "\n".join(f"• {rent.esc(c.get('title') or c['chat_id'])}" for c in channels)
        + "\n\nПосле подписки нажмите «Я подписался»."
    )


async def _blocked_by_rules(bot, user_id: int) -> tuple[str, list[dict]] | None:
    """('banned', []) / ('sub', [каналы]) / None. Главного админа правила не касаются."""
    if users.is_super_admin(user_id):
        return None
    if users.is_banned(user_id):
        return "banned", []
    missing = await users.missing_channels(bot, user_id)
    if missing:
        return "sub", missing
    return None


async def guard(message: Message, state: FSMContext) -> bool:
    """Check access for message handlers. Sets owner context if allowed."""
    blocked = await _blocked_by_rules(message.bot, message.from_user.id)
    if blocked:
        await state.clear()
        if blocked[0] == "banned":
            await message.answer(BANNED_TEXT)
        else:
            await message.answer(subscription_text(blocked[1]), reply_markup=subscription_kb(blocked[1]))
        return False
    rent.set_current_owner(rent.owner_context_for_user(message.from_user.id))
    return True


async def guard_callback(callback: CallbackQuery, state: FSMContext) -> bool:
    """Check access for callback handlers."""
    blocked = await _blocked_by_rules(callback.bot, callback.from_user.id)
    if blocked:
        await state.clear()
        if blocked[0] == "banned":
            await callback.answer(BANNED_TEXT, show_alert=True)
        else:
            await callback.answer("Сначала подпишитесь на каналы.", show_alert=True)
            await callback.message.answer(subscription_text(blocked[1]), reply_markup=subscription_kb(blocked[1]))
        return False
    rent.set_current_owner(rent.owner_context_for_user(callback.from_user.id))
    return True


async def show_dashboard(message: Message, state: FSMContext) -> None:
    """Show main dashboard."""
    blocked = await _blocked_by_rules(message.bot, message.from_user.id)
    if blocked:
        await state.clear()
        if blocked[0] == "banned":
            await message.answer(BANNED_TEXT)
        else:
            await message.answer(subscription_text(blocked[1]), reply_markup=subscription_kb(blocked[1]))
        return
    rent.set_current_owner(rent.owner_context_for_user(message.from_user.id))
    await state.clear()
    owner = is_owner(message.from_user.id)
    if not owner:
        warning = rent.access_expiring_soon_text(message.from_user.id)
        if warning:
            await message.answer(warning, reply_markup=access_extend_kb())
    from templates import dashboard as dash_tmpl
    kb = dash_tmpl.dashboard_kb(owner)
    await message.answer(dash_tmpl.dashboard_text(), reply_markup=kb)
