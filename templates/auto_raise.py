"""Auto raise lots screen."""
from __future__ import annotations

import rental_service as rent


def auto_raise_text() -> str:
    settings = rent.user_settings()
    enabled = bool(settings.get("auto_raise_lots_enabled"))
    key_ok = bool((settings.get("funpay_golden_key") or "").strip())
    last_at = rent.fmt(settings.get("auto_raise_last_at")) if settings.get("auto_raise_last_at") else "-"
    next_at = rent.fmt(settings.get("auto_raise_next_at")) if settings.get("auto_raise_next_at") else "-"
    last_result = settings.get("auto_raise_last_result") or "-"
    status = "включено" if enabled else "выключено"
    key_status = "задан" if key_ok else "не задан"

    return (
        "<b>Авто поднятие лотов</b>\n\n"
        f"Статус: <b>{rent.esc(status)}</b>\n"
        f"FunPay ключ: <b>{rent.esc(key_status)}</b>\n"
        f"Последняя попытка: <b>{rent.esc(last_at)}</b>\n"
        f"Следующая попытка: <b>{rent.esc(next_at)}</b>\n\n"
        "Бот пытается поднять лоты 1 раз в час. Если FunPay пишет, что время еще не прошло "
        "или возвращает ошибку, бот просто сохранит ошибку и попробует снова через час.\n\n"
        f"<b>Последний результат:</b>\n{rent.esc(last_result)}"
    )
