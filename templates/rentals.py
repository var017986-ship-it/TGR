"""Список аренд — красивый с рамками и временем до конца."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import rental_service as rent


def _time_left(ends_at: str) -> str:
    """Сколько осталось до конца аренды."""
    dt = rent.parse_dt(ends_at)
    if not dt:
        return "—"
    now = rent.now_utc()
    delta = dt - now
    total_min = int(delta.total_seconds() // 60)
    if total_min < 0:
        return f"<i>истёк {-_fmt_dur(-total_min)} назад</i>"
    if total_min < 60:
        return f"<b>{total_min} мин</b>"
    return f"<b>{_fmt_dur(total_min)}</b>"


def _fmt_dur(total_min: int) -> str:
    h, m = divmod(total_min, 60)
    if h and m:
        return f"{h}ч {m}м"
    if h:
        return f"{h}ч"
    return f"{m}м"


def rentals_text() -> str:
    """Форматированный список аренд."""
    rentals = rent.list_rentals()
    accounts = rent.list_accounts()

    if not rentals:
        return (
            "┌──────────────────────────────┐\n"
            "│        <b>АКТИВНЫЕ АРЕНДЫ</b>       │\n"
            "└──────────────────────────────┘\n\n"
            "Аренд пока нет.\n\n"
            "<i>Используй /simulate для теста покупки.</i>"
        )

    active = [r for r in rentals if r.get("status") == "active"]
    expired = [r for r in rentals if r.get("status") != "active"]

    lines = [
        "┌──────────────────────────────┐",
        "│        <b>АКТИВНЫЕ АРЕНДЫ</b>       │",
        "└──────────────────────────────┘",
        "",
        f"<b>Активных:</b> {len(active)}  │  <b>завершённых:</b> {len(expired)}",
        "",
    ]

    display = list(reversed(rentals[-30:]))
    for idx, rental in enumerate(display, 1):
        status = rental.get("status", "?")
        icon = "●" if status == "active" else "○"
        status_label = "активна" if status == "active" else "завершена"
        account = rent.account_by_id(accounts, rental["account_id"])
        account_name = account.get("display_name") if account else rental["account_id"]
        buyer = rental.get("buyer_name") or rental.get("buyer_id") or "—"
        ends = rent.fmt(rental.get("ends_at")) or "—"

        lines.append(
            f"{icon} <b>{idx}. {rent.esc(buyer)}</b>  <i>[{status_label}]</i>\n"
            f"   ID: <code>{rent.esc(rental['id'])}</code>\n"
            f"   Аккаунт: {rent.esc(account_name or '—')}\n"
            f"   До: <code>{rent.esc(ends)}</code>\n"
            f"   Осталось: {_time_left(rental.get('ends_at', ''))}"
        )

    if len(rentals) > 30:
        lines.append(f"\n<i>... и ещё {len(rentals) - 30}</i>")

    return "\n".join(lines)


def rentals_kb() -> InlineKeyboardMarkup:
    """Клавиатура списка аренд."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="＋ Добавить аккаунт", callback_data="add_account:steam")],
            [InlineKeyboardButton(text="↻ Обновить", callback_data="rentals")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )
