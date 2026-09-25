"""Add-account wizard handlers."""
import io
import json

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import rental_service as rent
import steam_password
from handlers.guards import guard, is_owner, show_dashboard
from keyboards import back_kb, main_kb
from states.all import AddAccount
from templates import dashboard as dash_tmpl


router = Router()


@router.message(Command("addaccount"))
async def cmd_add_account(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    await state.set_state(AddAccount.waiting_login)
    await state.update_data(account_type="steam")
    await message.answer(
        "<b>Добавление Steam-аккаунта</b>\n\n"
        "1/4. Кинь логин аккаунта.",
        reply_markup=back_kb(),
    )


@router.message(AddAccount.waiting_login, F.text)
async def add_account_login(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    await state.update_data(login=message.text.strip())
    await state.set_state(AddAccount.waiting_password)
    await message.answer("2/4. Теперь кинь пароль аккаунта.")


@router.message(AddAccount.waiting_password, F.text)
async def add_account_password(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    await state.update_data(password=message.text.strip())
    await state.set_state(AddAccount.waiting_mafile)
    await message.answer("3/4. Кинь maFile документом.")


@router.message(AddAccount.waiting_mafile, F.document | F.text)
async def add_account_mafile(message: Message, state: FSMContext, bot: Bot):
    if not await guard(message, state):
        return
    ma_file_path = ""
    ma_file_name = ""
    ma_file_json = ""
    if message.document:
        ma_file_name = message.document.file_name or f"{message.document.file_id}.maFile"
        buffer = io.BytesIO()
        await bot.download(message.document, destination=buffer)
        try:
            ma_data = json.loads(buffer.getvalue().decode("utf-8-sig"))
        except Exception:
            await message.answer("Не смог прочитать maFile. Отправь оригинальный .maFile документом.")
            return
        ma_file_json = json.dumps(ma_data, ensure_ascii=False)
    elif message.text:
        await message.answer("Нужно отправить именно maFile документом.")
        return
    await state.update_data(ma_file_path=ma_file_path, ma_file_name=ma_file_name, ma_file_json=ma_file_json)
    await state.set_state(AddAccount.waiting_display_name)
    await message.answer("4/4. Напиши название аккаунта в боте. Например: <code>GTA основной</code>.")


@router.message(AddAccount.waiting_display_name, F.text)
async def add_account_display_name(message: Message, state: FSMContext):
    if not await guard(message, state):
        return
    state_data = await state.get_data()
    display_name = message.text.strip()
    try:
        ma_data = json.loads(state_data.get("ma_file_json", "") or "{}")
    except Exception:
        await state.clear()
        await message.answer(
            "maFile поврежден или не читается. Аккаунт не сохранен, начни добавление заново.",
            reply_markup=main_kb(is_owner(message.from_user.id)),
        )
        return

    checking = await message.answer("Проверяю Steam-аккаунт: логин, пароль и maFile...")
    try:
        await steam_password.verify_account_async(
            state_data["login"],
            state_data["password"],
            ma_data,
            timeout=60,
        )
    except Exception as exc:
        await state.clear()
        await checking.edit_text(
            "Аккаунт не сохранен.\n\n"
            f"Проверка Steam не прошла: <code>{rent.esc(str(exc))}</code>\n\n"
            "Проверь логин, пароль и что maFile именно от этого аккаунта, потом добавь аккаунт заново.",
            reply_markup=main_kb(is_owner(message.from_user.id)),
        )
        return

    account = rent.upsert_account(
        {
            "account_type": state_data.get("account_type", "steam"),
            "title": "Steam аккаунт",
            "login": state_data["login"],
            "password": state_data["password"],
            "ma_file_path": state_data.get("ma_file_path", ""),
            "ma_file_name": state_data.get("ma_file_name", ""),
            "ma_file_json": state_data.get("ma_file_json", ""),
            "display_name": display_name,
            "note": "",
        }
    )
    await state.clear()
    owner = is_owner(message.from_user.id)
    await checking.edit_text(
        "Проверка прошла: Steam-аккаунт рабочий.\n\n"
        f"Аккаунт <b>{rent.esc(account.get('display_name') or account['id'])}</b> сохранен.\n\n"
        + dash_tmpl.dashboard_text(),
        reply_markup=main_kb(owner),
    )
