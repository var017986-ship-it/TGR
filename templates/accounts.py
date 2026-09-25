"""Склад аккаунтов — красивый список с рамками."""
from aiogram.types import InlineKeyboardMarkup

import rental_service as rent
from keyboards import back_kb


def _status_icon(status: str) -> str:
    return {
        "available": "●",
        "rented": "●",
        "offline_sold": "●",
        "expired": "○",
    }.get(status, "○")


def _status_color(status: str) -> str:
    """HTML-цвет для статуса."""
    return {
        "available": "🟢",   # fallback to plain marker below
        "rented": "🔴",
        "offline_sold": "🟡",
        "expired": "⚫",
    }.get(status, "⚪")


def accounts_text() -> str:
    """Форматированный список всех аккаунтов."""
    accounts = rent.list_accounts()
    if not accounts:
        return (
            "┌──────────────────────────────┐\n"
            "│      <b>СКЛАД АККАУНТОВ</b>      │\n"
            "└──────────────────────────────┘\n\n"
            "Склад пуст.\n\n"
            "<i>Нажми «＋ Добавить аккаунт» чтобы начать.</i>"
        )

    counts = {"available": 0, "rented": 0, "offline_sold": 0, "offline_mode": 0, "other": 0}
    for a in accounts:
        s = "offline_mode" if a.get("offline_mode") else a.get("status", "available")
        counts[s if s in counts else "other"] += 1

    lines = [
        "┌──────────────────────────────┐",
        "│      <b>СКЛАД АККАУНТОВ</b>      │",
        "└──────────────────────────────┘",
        "",
        f"<b>Всего:</b> {len(accounts)}  │  "
        f"свободно: {counts['available']}  │  "
        f"в аренде: {counts['rented']}  │  "
        f"продано: {counts['offline_sold']}",
        "",
    ]

    for idx, account in enumerate(accounts[:30], 1):
        status = "offline_mode" if account.get("offline_mode") else account.get("status", "available")
        icon = _status_icon(status)
        label = {
            "available": "свободен",
            "rented": "в аренде",
            "offline_sold": "продан",
            "expired": "истёк",
        }.get(status, status or "—")
        name = account.get("display_name") or account.get("title") or account["id"]
        login = account.get("login") or "—"
        until = rent.fmt(account.get("rented_until")) or "—"
        ma_name = account.get("ma_file_name") or "—"

        lines.append(
            f"{icon} <b>{idx}. {rent.esc(name)}</b>\n"
            f"   ID: <code>{rent.esc(account['id'])}</code>\n"
            f"   Логин: <code>{rent.esc(login)}</code>\n"
            f"   maFile: <code>{rent.esc(ma_name)}</code>\n"
            f"   Статус: <b>{label}</b>\n"
            f"   До: <i>{rent.esc(until)}</i>"
        )

    if len(accounts) > 30:
        lines.append(f"\n<i>... и ещё {len(accounts) - 30}</i>")

    return "\n".join(lines)


def accounts_kb() -> InlineKeyboardMarkup:
    """Клавиатура списка аккаунтов."""
    from keyboards import InlineKeyboardButton
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="＋ Добавить аккаунт", callback_data="add_account:steam")],
            [InlineKeyboardButton(text="↻ Обновить", callback_data="accounts")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )
