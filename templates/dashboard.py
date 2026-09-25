"""Dashboard text and keyboard."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

import rental_service as rent


def _regular_lots() -> list[dict]:
    lots = rent.list_lots()
    try:
        from plugins import offline_one_time_code
    except Exception:
        return lots
    return [
        lot for lot in lots
        if not offline_one_time_code.is_enabled_lot(str(lot.get("id", "")))
    ]


def dashboard_text() -> str:
    settings = rent.user_settings()
    funpay_status = "подключён" if settings.get("funpay_golden_key") else "не подключён"
    return (
        "<b>Auto SMM Bot</b>\n\n"
        "Автоаренда Steam отключена.\n"
        "Бот доступен всем пользователям без кода доступа.\n\n"
        f"FunPay: <b>{rent.esc(funpay_status)}</b>\n\n"
        "Подключи FunPay-аккаунт и настрой SMM-услуги."
    )


def dashboard_kb(is_owner: bool) -> InlineKeyboardMarkup:
    from keyboards import main_kb, onboarding_kb

    settings = rent.user_settings()
    return onboarding_kb(bool(settings.get("funpay_golden_key")), is_owner)
