"""Лоты — красивый список и детали с рамками."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import rental_service as rent
from plugins import offline_one_time_code


def _fmt_dur(total_min: int) -> str:
    if not total_min:
        return "—"
    h, m = divmod(int(total_min), 60)
    if h and m:
        return f"{h}ч {m}м"
    if h:
        return f"{h}ч"
    return f"{m}м"


def lots_text() -> str:
    """Список всех лотов."""
    all_lots = rent.list_lots()
    offline_lots = [
        lot for lot in all_lots
        if offline_one_time_code.is_enabled_lot(str(lot.get("id", "")))
    ]
    lots = [
        lot for lot in all_lots
        if not offline_one_time_code.is_enabled_lot(str(lot.get("id", "")))
    ]
    accounts = rent.list_accounts()

    if not lots:
        offline_note = ""
        if offline_lots:
            offline_note = (
                f"\n\nОффлайн-лоты не удалены: <b>{len(offline_lots)}</b> шт.\n"
                "Они находятся в разделе <b>Плагины</b> → <b>Авто Steam оффлайн</b>."
            )
        return (
            "┌──────────────────────────────┐\n"
            "│          <b>МОИ ЛОТЫ</b>            │\n"
            "└──────────────────────────────┘\n\n"
            f"Обычных лотов аренды пока нет.{offline_note}\n\n"
            "<i>Нажми «＋ Добавить лот» чтобы создать первый.</i>"
        )

    lines = [
        "┌──────────────────────────────┐",
        "│          <b>МОИ ЛОТЫ</b>            │",
        "└──────────────────────────────┘",
        "",
        f"<b>Всего лотов:</b> {len(lots)}",
        "",
    ]

    for idx, lot in enumerate(lots[:40], 1):
        linked_ids = lot.get("account_ids", [])
        linked = [rent.account_by_id(accounts, acc_id) for acc_id in linked_ids]
        linked = [a for a in linked if a]
        free = sum(1 for a in linked if a.get("status") == "available" and not a.get("offline_mode"))
        rented = sum(1 for a in linked if a.get("status") == "rented")
        sold = sum(1 for a in linked if a.get("status") == "offline_sold")
        rent_min = lot.get("rent_minutes") or lot.get("rent_hours", 0) * 60
        notify = lot.get("notify_before_minutes", 0)
        bonus = lot.get("review_bonus_minutes", 0)
        stars = lot.get("review_min_stars", 0)

        bar = ""
        if linked:
            bar = f"   Свободно: {'▰' * free}{'▱' * (len(linked) - free)}  {free}/{len(linked)}\n"

        bonus_line = ""
        if bonus and stars:
            bonus_line = f"   ★ Бонус: +{_fmt_dur(bonus)} за {stars}★\n"

        lines.append(
            f"▸ <b>{idx}. {rent.esc(lot.get('title') or lot['id'])}</b>\n"
            f"   ID FunPay: <code>{rent.esc(lot['id'])}</code>\n"
            f"   ⏱ Аренда: <b>{rent.esc(_fmt_dur(rent_min))}</b>  │  ⏰ Предупреждение: за <b>{notify}</b> мин\n"
            f"   Аккаунтов: {len(linked)} (свободно: {free}, в аренде: {rented}, продано: {sold})\n"
            f"{bar}"
            f"{bonus_line}"
        )

    if len(lots) > 40:
        lines.append(f"\n<i>... и ещё {len(lots) - 40}</i>")

    return "\n".join(lines)


def lots_kb_view() -> InlineKeyboardMarkup:
    """Клавиатура списка лотов."""
    rows = []
    all_lots = rent.list_lots()
    offline_count = sum(
        1 for item in all_lots
        if offline_one_time_code.is_enabled_lot(str(item.get("id", "")))
    )
    for lot in [item for item in all_lots if not offline_one_time_code.is_enabled_lot(str(item.get("id", "")))][:40]:
        rows.append(
            [InlineKeyboardButton(
                text=f"▸ {lot.get('title') or lot.get('id')}",
                callback_data=f"lot_view:{lot.get('id')}"
            )]
        )
    if offline_count:
        rows.append([InlineKeyboardButton(text=f"◇ Оффлайн-лоты ({offline_count})", callback_data="plugin_offline_lots")])
    rows.append([InlineKeyboardButton(text="＋ Добавить лот", callback_data="add_lot")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="dashboard")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lots_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="＋ Добавить лот", callback_data="add_lot")],
            [InlineKeyboardButton(text="◇ Добавить оффлайн-лот", callback_data="plugin_offline_add_lot")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )


def lot_detail_text(lot_id: str) -> str:
    """Детальная карточка лота."""
    lot = rent.lot_by_id(rent.list_lots(), lot_id)
    if not lot:
        return (
            "┌──────────────────────────────┐\n"
            "│        <b>ЛОТ НЕ НАЙДЕН</b>        │\n"
            "└──────────────────────────────┘"
        )

    accounts = rent.list_accounts()
    linked_ids = lot.get("account_ids", [])
    linked = [rent.account_by_id(accounts, acc_id) for acc_id in linked_ids]
    linked = [a for a in linked if a]
    free = [a for a in linked if a.get("status") == "available" and not a.get("offline_mode")]
    rented = [a for a in linked if a.get("status") == "rented"]
    sold = [a for a in linked if a.get("status") == "offline_sold"]

    rent_min = lot.get("rent_minutes") or lot.get("rent_hours", 0) * 60
    notify = lot.get("notify_before_minutes", 0)
    bonus = lot.get("review_bonus_minutes", 0)
    stars = lot.get("review_min_stars", 0)

    bonus_text = (
        f"+{_fmt_dur(bonus)} за отзыв на {stars}★" if bonus and stars else "выключен"
    )

    lines = [
        "┌──────────────────────────────┐",
        f"│  <b>{rent.esc(lot.get('title') or lot_id)}</b>",
        "└──────────────────────────────┘",
        "",
        f"<b>ID FunPay:</b> <code>{rent.esc(lot_id)}</code>",
        "",
        f"⏱  <b>Время аренды:</b>  {_fmt_dur(rent_min)}",
        f"⏰  <b>Предупреждение:</b>  за {notify} мин до конца",
        f"★  <b>Бонус за отзыв:</b>  {bonus_text}",
        "",
        f"┌{'─' * 30}┐",
        f"│ <b>АККАУНТЫ ЛОТА</b>            │",
        f"├{'─' * 30}┤",
        f"│ Всего: {len(linked):<22} │",
        f"│ Свободно: {len(free):<19} │",
        f"│ В аренде: {len(rented):<20} │",
        f"│ Продано: {len(sold):<21} │",
        f"└{'─' * 30}┘",
    ]

    if linked:
        lines.append("")
        lines.append("<b>Список аккаунтов:</b>")
        for a in linked[:10]:
            status = a.get("status", "available")
            label = {
                "available": "свободен",
                "rented": "в аренде",
                "offline_sold": "продан",
            }.get(status, status)
            until = rent.fmt(a.get("rented_until")) or "—"
            lines.append(
                f"  • <b>{rent.esc(a.get('display_name') or a['id'])}</b>  "
                f"<i>[{label}]</i>\n"
                f"    До: <code>{rent.esc(until)}</code>"
            )
        if len(linked) > 10:
            lines.append(f"  <i>... и ещё {len(linked) - 10}</i>")

    return "\n".join(lines)


def lot_detail_kb(lot_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ Изменить время", callback_data=f"lot_edit_time:{lot_id}")],
            [InlineKeyboardButton(text="⏰ Изменить предупреждение", callback_data=f"lot_edit_notify:{lot_id}")],
            [InlineKeyboardButton(text="★ Бонус за отзыв", callback_data=f"lot_edit_bonus:{lot_id}")],
            [InlineKeyboardButton(text="≡ Аккаунты лота", callback_data=f"lot_edit_accounts:{lot_id}")],
            [InlineKeyboardButton(text="← Назад к лотам", callback_data="lots")],
        ]
    )
