"""Лог событий — красивый формат с уровнями."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import rental_service as rent


_LEVEL_ICON = {
    "info": "ℹ",
    "warning": "⚠",
    "error": "✗",
    "critical": "✗",
}


def events_text() -> str:
    """Форматированный лог событий."""
    events = storage_read_events()
    if not events:
        return (
            "┌──────────────────────────────┐\n"
            "│        <b>СОБЫТИЯ</b>              │\n"
            "└──────────────────────────────┘\n\n"
            "Событий пока нет.\n\n"
            "<i>Здесь будет видна история действий бота.</i>"
        )

    lines = [
        "┌──────────────────────────────┐",
        "│      <b>ПОСЛЕДНИЕ СОБЫТИЯ</b>       │",
        "└──────────────────────────────┘",
        "",
    ]

    for ev in reversed(events[-30:]):
        level = ev.get("level", "info")
        icon = _LEVEL_ICON.get(level, "•")
        when = rent.fmt(ev.get("created_at")) or "—"
        lines.append(
            f"{icon} <b>{rent.esc(when)}</b>  <i>[{rent.esc(level)}]</i>\n"
            f"  {rent.esc(ev.get('text', ''))}"
        )

    return "\n".join(lines)


def events_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↻ Обновить", callback_data="events")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )


def storage_read_events() -> list[dict]:
    import storage
    all_events = storage.read("events")
    owner = rent.current_owner_id()
    if not owner:
        return all_events
    return [e for e in all_events if str(e.get("owner_id", "")) == owner]
