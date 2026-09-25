"""Лог сообщений — красивый формат."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import rental_service as rent


def messages_text() -> str:
    """Форматированный лог сообщений."""
    messages = storage_read_messages()
    if not messages:
        return (
            "┌──────────────────────────────┐\n"
            "│        <b>СООБЩЕНИЯ</b>            │\n"
            "└──────────────────────────────┘\n\n"
            "Сообщений пока нет.\n\n"
            "<i>Здесь будут видны входящие/исходящие сообщения FunPay.</i>"
        )

    lines = [
        "┌──────────────────────────────┐",
        "│     <b>ПОСЛЕДНИЕ СООБЩЕНИЯ</b>     │",
        "└──────────────────────────────┘",
        "",
    ]

    for msg in reversed(messages[-20:]):
        direction = msg.get("direction", "in")
        arrow = "→" if direction == "out" else "←"
        dir_label = "исходящее" if direction == "out" else "входящее"
        when = rent.fmt(msg.get("created_at")) or "—"
        who = msg.get("buyer_id") or "—"

        lines.append(
            f"<b>{when}</b>  {arrow}  <i>{dir_label}</i>\n"
            f"<code>{rent.esc(who)}</code>\n"
            f"<blockquote>{rent.esc(msg.get('text', ''))}</blockquote>"
        )

    return "\n".join(lines)


def messages_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↻ Обновить", callback_data="messages")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )


def storage_read_messages() -> list[dict]:
    import storage
    all_messages = storage.read("messages")
    owner = rent.current_owner_id()
    if not owner:
        return all_messages
    return [m for m in all_messages if str(m.get("owner_id", "")) == owner]
