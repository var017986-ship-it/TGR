"""Плагин: авто-выдача разового Steam Guard кода для оффлайн-аккаунтов.

Логика:
  1. Продавец включает плагин для конкретного лота (привязывает магазин).
  2. При покупке лота бот автоматически выдает логин и пароль от аккаунта.
  3. Команда !code выдает Steam Guard код РОВНО ОДИН РАЗ за покупку.
  4. Кулдаун между запросами кода — 5 секунд.
"""
from __future__ import annotations

import time
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import rental_service as rent
import storage


# ─────────────────────────────────────────────────────────────────────────────
# Константы плагина
# ─────────────────────────────────────────────────────────────────────────────
PLUGIN_KEY = "offline_one_time_code"
PLUGIN_TITLE = "Авто выдача разового кода для оффлайн аккаунтов"

CODE_COOLDOWN_SECONDS = 5  # Кулдаун между запросами !code в одном чате


# ─────────────────────────────────────────────────────────────────────────────
# Работа с состоянием плагина (per-owner, хранится в settings)
# ─────────────────────────────────────────────────────────────────────────────
def _state() -> tuple[dict[str, Any], dict[str, Any]]:
    """Получить (settings, plugin_state) для текущего владельца."""
    settings = storage.read("settings")
    owner = rent.current_owner_id() or "global"
    user_settings = settings.setdefault("user_settings", {}).setdefault(owner, {})
    plugins = user_settings.setdefault("plugins", {})
    state = plugins.setdefault(
        PLUGIN_KEY,
        {
            "enabled_lot_ids": [],
            "sales": [],
            "chat_current_order_ids": {},
        },
    )
    state.setdefault("enabled_lot_ids", [])
    state.setdefault("sales", [])
    state.setdefault("chat_current_order_ids", {})
    return settings, state


def _save(settings: dict[str, Any], state: dict[str, Any]) -> None:
    """Сохранить состояние плагина в storage."""
    owner = rent.current_owner_id() or "global"
    user_settings = settings.setdefault("user_settings", {}).setdefault(owner, {})
    user_settings.setdefault("plugins", {})[PLUGIN_KEY] = state
    storage.write("settings", settings)


def deactivate_chat_sales(chat_id: str | int) -> bool:
    """Stop old offline sales from answering in a chat now used by regular rent."""
    chat_key = str(chat_id or "")
    if not chat_key:
        return False
    settings, state = _state()
    changed = False
    current_orders = state.setdefault("chat_current_order_ids", {})
    if current_orders.pop(chat_key, None) is not None:
        changed = True
    for sale in state.get("sales", []):
        if isinstance(sale, dict) and sale.get("status") == "active" and str(sale.get("chat_id", "")) == chat_key:
            sale["status"] = "closed"
            changed = True
    if changed:
        _save(settings, state)
    return changed


def offline_account_ids() -> set[str]:
    """Account IDs reserved for enabled offline lots."""
    _, state = _state()
    enabled = {str(item) for item in state.get("enabled_lot_ids", []) if str(item)}
    result: set[str] = set()
    for lot in rent.list_lots():
        if str(lot.get("id", "")) not in enabled:
            continue
        for account_id in lot.get("account_ids", []) or []:
            account_key = str(account_id or "")
            if account_key:
                result.add(account_key)
    return result


def sync_offline_account_flags() -> int:
    """Mark accounts used by enabled offline lots and unmark the rest."""
    ids = offline_account_ids()
    accounts = rent.list_accounts()
    changed = 0
    for account in accounts:
        if not isinstance(account, dict):
            continue
        should_mark = str(account.get("id", "")) in ids
        if bool(account.get("offline_mode")) != should_mark:
            account["offline_mode"] = should_mark
            changed += 1
        if should_mark and account.get("status") == "offline_sold":
            account["status"] = "available"
            changed += 1
    if changed:
        rent.save_accounts(accounts)
    return changed


# ─────────────────────────────────────────────────────────────────────────────
# Управление лотами
# ─────────────────────────────────────────────────────────────────────────────
def enable_lot(lot_id: str) -> dict[str, Any]:
    """Включить оффлайн-выдачу для лота."""
    lot_id = str(lot_id or "").strip()
    if not lot_id:
        return {"ok": False, "message": "Укажи ID лота."}
    if not rent.lot_by_id(rent.list_lots(), lot_id):
        return {"ok": False, "message": "Лот не найден в панели. Сначала добавь лот."}

    settings, state = _state()
    ids = [str(item) for item in state.get("enabled_lot_ids", [])]
    if lot_id not in ids:
        ids.append(lot_id)
    state["enabled_lot_ids"] = ids
    _save(settings, state)
    sync_offline_account_flags()
    return {"ok": True, "message": f"Лот {lot_id} включен для оффлайн-выдачи."}


def disable_lot(lot_id: str) -> dict[str, Any]:
    """Выключить оффлайн-выдачу для лота."""
    lot_id = str(lot_id or "").strip()
    settings, state = _state()
    state["enabled_lot_ids"] = [
        str(item) for item in state.get("enabled_lot_ids", [])
        if str(item) != lot_id
    ]
    _save(settings, state)
    sync_offline_account_flags()
    return {"ok": True, "message": f"Лот {lot_id} выключен из оффлайн-выдачи."}


def is_enabled_lot(lot_id: str) -> bool:
    """Проверить, включена ли оффлайн-выдача для лота."""
    _, state = _state()
    return str(lot_id) in {str(item) for item in state.get("enabled_lot_ids", [])}


# ─────────────────────────────────────────────────────────────────────────────
# Сброс аккаунтов (для продавца)
# ─────────────────────────────────────────────────────────────────────────────
def reset_account(account_id: str) -> dict[str, Any]:
    """Сбросить аккаунт обратно в available (после оффлайн-продажи)."""
    account_id = str(account_id or "").strip()
    if not account_id:
        return {"ok": False, "message": "Укажи ID аккаунта."}
    accounts = rent.list_accounts()
    account = rent.account_by_id(accounts, account_id)
    if not account:
        return {"ok": False, "message": "Аккаунт не найден."}
    account["status"] = "available"
    account["buyer_id"] = ""
    account["buyer_name"] = ""
    account["rented_until"] = ""
    account["notify_sent"] = False
    rent.save_accounts(accounts)
    return {"ok": True, "message": f"Аккаунт {account_id} сброшен в available."}


def sold_accounts() -> list[dict[str, Any]]:
    """Список аккаунтов со статусом offline_sold."""
    return [
        a for a in rent.list_accounts()
        if a.get("status") == "offline_sold" or a.get("offline_mode")
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Отображение (Telegram UI)
# ─────────────────────────────────────────────────────────────────────────────
def status_text() -> str:
    """Текст статуса плагина."""
    _, state = _state()
    enabled = [str(item) for item in state.get("enabled_lot_ids", [])]
    sales = [item for item in state.get("sales", []) if isinstance(item, dict)]
    active_sales = [s for s in sales if s.get("status") == "active"]
    lines = [
        "<b>Плагин: оффлайн-выдача</b>",
        "",
        f"Включено лотов: <b>{len(enabled)}</b>",
        f"Активных выдач: <b>{len(active_sales)}</b>",
        f"Выдач всего: <b>{len(sales)}</b>",
    ]
    if enabled:
        lines.append("\n<b>Лоты:</b>")
        for lot_id in enabled[-20:]:
            lot = rent.lot_by_id(rent.list_lots(), lot_id)
            title = (lot or {}).get("title") or lot_id
            lines.append(f"<code>{rent.esc(lot_id)}</code> | {rent.esc(title)}")
    return "\n".join(lines)


def plugin_text() -> str:
    """Описание плагина для меню."""
    text = status_text()
    return (
        f"<b>{PLUGIN_TITLE}</b>\n\n"
        "Покупатель получает логин и пароль от оффлайн-аккаунта после покупки.\n"
        "Команда <code>!code</code> выдает Steam Guard код один раз.\n\n"
        f"{text}"
    )


def plugin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить оффлайн-лот", callback_data="plugin_offline_add_lot")],
            [InlineKeyboardButton(text="Настроить лоты", callback_data="plugin_offline_lots")],
            [InlineKeyboardButton(text="Назад", callback_data="plugins")],
        ]
    )


def lots_kb() -> InlineKeyboardMarkup:
    lots = rent.list_lots()
    rows = []
    for lot in lots[:40]:
        lot_id = str(lot.get("id", ""))
        mark = "Вкл" if is_enabled_lot(lot_id) else "Выкл"
        title = str(lot.get("title") or lot_id)
        rows.append([InlineKeyboardButton(
            text=f"{mark} | {title}", callback_data=f"plugin_offline_lot:{lot_id}"
        )])
    rows.append([InlineKeyboardButton(text="Добавить оффлайн-лот", callback_data="plugin_offline_add_lot")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="plugin_offline")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lot_detail_text(lot_id: str) -> str:
    """Детальная информация о лоте для плагина."""
    lot_id = str(lot_id or "").strip()
    lot = rent.lot_by_id(rent.list_lots(), lot_id)
    if not lot:
        return "<b>Оффлайн-лот не найден</b>"

    accounts = rent.list_accounts()
    account_ids = [str(item) for item in lot.get("account_ids", [])]
    enabled_text = "включен" if is_enabled_lot(lot_id) else "выключен"
    lines = [
        f"<b>{rent.esc(lot.get('title') or lot_id)}</b>",
        f"ID FunPay: <code>{rent.esc(lot_id)}</code>",
        f"Оффлайн-выдача: <b>{enabled_text}</b>",
        "",
        "<b>Аккаунты на выдаче:</b>",
    ]

    if not account_ids:
        lines.append("Аккаунты не привязаны.")
        return "\n".join(lines)

    for account_id in account_ids:
        account = rent.account_by_id(accounts, account_id)
        if not account:
            lines.append(f"- <code>{rent.esc(account_id)}</code> | не найден")
            continue
        name = account.get("display_name") or account.get("title") or account_id
        login = account.get("login") or "логин не указан"
        status = account.get("status") or "available"
        lines.append(
            f"- <b>{rent.esc(name)}</b>\n"
            f"  Логин: <code>{rent.esc(login)}</code>\n"
            f"  Статус: <b>{rent.esc(status)}</b>"
        )
    return "\n".join(lines)


def lot_detail_kb(lot_id: str) -> InlineKeyboardMarkup:
    toggle_text = "Выключить оффлайн-выдачу" if is_enabled_lot(lot_id) else "Включить оффлайн-выдачу"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f"plugin_offline_toggle:{lot_id}")],
            [InlineKeyboardButton(text="Изменить аккаунты", callback_data=f"plugin_offline_edit_accounts:{lot_id}")],
            [InlineKeyboardButton(text="Назад", callback_data="plugin_offline_lots")],
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Внутренние утилиты поиска продаж
# ─────────────────────────────────────────────────────────────────────────────
def _same_order_id(left: str, right: str) -> bool:
    """Точное сравнение order_id (без startswith — избегаем ложных совпадений)."""
    left_value = str(left or "").upper().strip()
    right_value = str(right or "").upper().strip()
    if not left_value or not right_value:
        return False
    return left_value == right_value


def _sale_by_order(sales: list[dict[str, Any]], order_id: str) -> dict[str, Any] | None:
    """Найти продажу по order_id."""
    target = str(order_id or "").upper().strip()
    if not target:
        return None
    for item in sales:
        if _same_order_id(str(item.get("order_id", "")), target):
            return item
    return None


def _sale_for_chat(
    sales: list[dict[str, Any]],
    buyer_id: str,
    chat_id: str | int = "",
) -> dict[str, Any] | None:
    """Найти активную продажу для чата или покупателя."""
    target_chat = str(chat_id or "")
    target_buyer = str(buyer_id or "")

    # Сначала ищем точное совпадение по чату
    if target_chat:
        chat_matches = [
            s for s in sales
            if s.get("status") == "active"
            and str(s.get("chat_id", "")) == target_chat
        ]
        if chat_matches:
            chat_matches.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            return chat_matches[0]

    # Потом по buyer_id
    if target_buyer:
        buyer_matches = [
            s for s in sales
            if s.get("status") == "active"
            and str(s.get("buyer_id", "")) == target_buyer
        ]
        if buyer_matches:
            buyer_matches.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            return buyer_matches[0]

    return None


def _current_sale_for_chat(
    state: dict[str, Any],
    sales: list[dict[str, Any]],
    buyer_id: str,
    chat_id: str | int = "",
) -> dict[str, Any] | None:
    """Найти текущую продажу для чата (с учётом привязки chat→order)."""
    chat_key = str(chat_id or "")
    current_orders = state.setdefault("chat_current_order_ids", {})
    if chat_key and isinstance(current_orders, dict):
        current_order_id = str(current_orders.get(chat_key, "") or "")
        if current_order_id:
            sale = _sale_by_order(sales, current_order_id)
            if sale and sale.get("status") == "active":
                return sale
    return _sale_for_chat(sales, buyer_id, chat_id)


def active_sales_for_scan() -> list[dict[str, Any]]:
    """Список активных продаж для сканирования чатов FunPay."""
    _, state = _state()
    result = []
    seen_chats: set[str] = set()
    sales = [item for item in state.get("sales", []) if isinstance(item, dict)]
    sales.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    current_orders = state.get("chat_current_order_ids", {})
    if isinstance(current_orders, dict):
        for chat_id, order_id in current_orders.items():
            chat_key = str(chat_id or "")
            sale = _sale_by_order(sales, str(order_id or ""))
            if not chat_key or not sale or sale.get("status") != "active":
                continue
            seen_chats.add(chat_key)
            result.append({
                "status": "active",
                "chat_id": chat_key,
                "buyer_id": str(sale.get("buyer_id", "")),
                "buyer_name": str(sale.get("buyer_name", "") or sale.get("buyer_id", "")),
                "offline_sale": True,
            })

    for sale in sales:
        if not isinstance(sale, dict) or sale.get("status") != "active" or not sale.get("chat_id"):
            continue
        chat_key = str(sale.get("chat_id", ""))
        if chat_key in seen_chats:
            continue
        seen_chats.add(chat_key)
        result.append({
            "status": "active",
            "chat_id": chat_key,
            "buyer_id": str(sale.get("buyer_id", "")),
            "buyer_name": str(sale.get("buyer_name", "") or sale.get("buyer_id", "")),
            "offline_sale": True,
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Главная логика: покупка
# ─────────────────────────────────────────────────────────────────────────────
def handle_purchase(
    *,
    lot_id: str,
    buyer_id: str,
    buyer_name: str,
    order_id: str,
    chat_id: str | int,
) -> dict[str, Any] | None:
    """Обработать покупку оффлайн-лота. Выдать логин и пароль."""
    if not is_enabled_lot(lot_id):
        return None

    settings, state = _state()
    sales = [item for item in state.get("sales", []) if isinstance(item, dict)]

    # Повторный запрос по тому же заказу → отдать те же данные
    existing = _sale_by_order(sales, order_id)
    if existing:
        account = rent.account_by_id(rent.list_accounts(), existing.get("account_id", ""))
        if not account:
            return {
                "handled": True,
                "refund": False,
                "message": "Заказ уже обработан, но аккаунт не найден. Напишите продавцу.",
            }
        chat_key = str(chat_id or existing.get("chat_id", "") or "")
        if chat_key:
            state.setdefault("chat_current_order_ids", {})[chat_key] = str(
                existing.get("order_id") or order_id
            )
            _save(settings, state)
        return {
            "handled": True,
            "refund": False,
            "message": _access_message(account, existing.get("buyer_name", "")),
        }

    # Новый заказ → ищем свободный аккаунт
    accounts = rent.list_accounts()
    lot = rent.lot_by_id(rent.list_lots(), lot_id)
    linked_accounts = []
    for account in accounts:
        if not lot or not lot.get("account_ids") or account.get("id") in lot.get("account_ids", []):
            linked_accounts.append(account)

    if not linked_accounts:
        return {
            "handled": True,
            "refund": True,
            "message": "Свободных аккаунтов для этого товара сейчас нет. Продавец вернет оплату.",
        }

    # Берём только аккаунты со статусом «available» — НЕ даём тот же аккаунт дважды
    free_account = next(
        (
            acc for acc in linked_accounts
            if (acc.get("status") or "available") in {"available", "offline_sold"}
        ),
        linked_accounts[0],
    )
    if not free_account:
        return {
            "handled": True,
            "refund": True,
            "message": "Все аккаунты для этого товара сейчас заняты. Продавец вернет оплату.",
        }

    # Помечаем аккаунт как выданный — больше его никто не получит
    free_account["status"] = "available"
    free_account["offline_mode"] = True
    free_account["buyer_id"] = str(buyer_id)
    free_account["buyer_name"] = str(buyer_name)
    free_account["rented_until"] = ""
    free_account["notify_sent"] = False
    rent.save_accounts(accounts)

    # Создаём запись о продаже
    sales.append({
        "order_id": str(order_id),
        "lot_id": str(lot_id),
        "buyer_id": str(buyer_id),
        "buyer_name": str(buyer_name),
        "chat_id": str(chat_id),
        "account_id": str(free_account.get("id", "")),
        "status": "active",
        "code_sent": False,
        "code_sent_at": "",
        "created_at": rent.iso(rent.now_utc()),
    })
    chat_key = str(chat_id or "")
    if chat_key:
        state.setdefault("chat_current_order_ids", {})[chat_key] = str(order_id)
    state["sales"] = sales[-1000:]
    _save(settings, state)
    rent.log_event("info", f"Оффлайн-аккаунт {free_account.get('id')} выдан по заказу #{order_id}")
    return {
        "handled": True,
        "refund": False,
        "message": _access_message(free_account, buyer_name),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Главная логика: запрос кода (вызывается из funpay_bridge при !code)
# ─────────────────────────────────────────────────────────────────────────────
def handle_customer_text(
    chat_id: str | int,
    buyer_id: str,
    text: str,
    message_id: str | int | None = None,
) -> dict[str, Any] | None:
    """Обработать !code от покупателя. Выдать код РОВНО ОДИН РАЗ."""
    if (text or "").strip().lower() != "!code":
        return None

    # Берём блокировку, чтобы два процесса не выдали код одновременно
    # Regular Steam rent has priority in the same FunPay chat. FunPay may omit
    # or change author_id, but chat_id remains the reliable link to the order.
    if rent.active_rental_for_chat("", chat_id):
        return None

    with rent.exclusive_file_lock(f"{PLUGIN_KEY}_code") as got_lock:
        if not got_lock:
            return {"handled": True, "message": ""}

        settings, state = _state()
        sales = [item for item in state.get("sales", []) if isinstance(item, dict)]
        sale = _current_sale_for_chat(state, sales, buyer_id, chat_id)
        if not sale:
            return None

        chat_key = str(chat_id or sale.get("chat_id", "") or "")
        now_ts = time.time()
        last_request_key = "_last_code_request_ts"
        request_msg_key = str(message_id or "")
        request_ids_key = "_code_request_message_ids"
        request_ids = [str(item) for item in sale.get(request_ids_key, []) if str(item)]
        if request_msg_key and request_msg_key in set(request_ids):
            return {"handled": True, "message": ""}
        if request_msg_key:
            request_ids.append(request_msg_key)
            sale[request_ids_key] = list(dict.fromkeys(request_ids))[-50:]
            state["sales"] = sales
            _save(settings, state)

        # 1. Код уже выдан → второй раз не даём (приоритет выше кулдауна)
        if sale.get("code_sent"):
            last_notice_key = "_last_code_already_notice_ts"
            last_notice_ts = float(sale.get(last_notice_key) or 0)
            sale[last_notice_key] = now_ts
            state["sales"] = sales
            _save(settings, state)
            if last_notice_ts and (now_ts - last_notice_ts) < CODE_COOLDOWN_SECONDS:
                return {"handled": True, "message": ""}
            return {
                "handled": True,
                "message": (
                    "Код для этого оффлайн-аккаунта уже был выдан. "
                    "Если вход не получился, напишите продавцу."
                ),
            }

        # 2. Кулдаун: 5 секунд между запросами в одном чате
        last_ts = float(sale.get(last_request_key) or 0)
        if last_ts and (now_ts - last_ts) < CODE_COOLDOWN_SECONDS:
            wait = int(CODE_COOLDOWN_SECONDS - (now_ts - last_ts)) + 1
            return {
                "handled": True,
                "message": f"Подождите {wait} сек. перед повторным запросом.",
            }

        account = rent.account_by_id(rent.list_accounts(), sale.get("account_id", ""))
        if not account:
            return {"handled": True, "message": "Аккаунт не найден. Напишите продавцу."}

        try:
            ma_data = rent.account_ma_data(account)
        except Exception:
            return {"handled": True, "message": "maFile не настроен. Напишите продавцу."}

        shared_secret = ma_data.get("shared_secret", "")
        code = rent.generate_steam_guard_code(shared_secret) if shared_secret else None
        if not code:
            return {"handled": True, "message": "Не смог сгенерировать Steam Guard код. Напишите продавцу."}

        # Помечаем код выданным (один раз)
        sent_at = rent.iso(rent.now_utc())
        sale["code_sent"] = True
        sale["code_sent_at"] = sent_at
        sale[last_request_key] = now_ts

        # Помечаем все дубликаты продаж (тот же order_id или тот же chat+account)
        sale_order_id = str(sale.get("order_id", ""))
        for item in sales:
            if item is sale:
                continue
            same_order = bool(sale_order_id) and _same_order_id(
                str(item.get("order_id", "")), sale_order_id
            )
            same_chat_account = (
                bool(chat_key)
                and str(item.get("chat_id", "")) == chat_key
                and str(item.get("account_id", "")) == str(sale.get("account_id", ""))
            )
            if same_order or same_chat_account:
                item["code_sent"] = True
                item["code_sent_at"] = item.get("code_sent_at") or sent_at

        state["sales"] = sales
        _save(settings, state)
        rent.log_event("info", f"Разовый Steam Guard код выдан по оффлайн-заказу #{sale_order_id}")
        return {"handled": True, "message": f"Ваш разовый Steam Guard код: {code}"}


# ─────────────────────────────────────────────────────────────────────────────
# Проверка аккаунта: «Это ваш аккаунт?» + ссылка на профиль
# ─────────────────────────────────────────────────────────────────────────────
def _get_steam_profile_url(account: dict[str, Any]) -> str:
    """Получить ссылку на профиль Steam из maFile.

    Приоритет:
      1. Session.SteamID (SteamID64) → https://steamcommunity.com/profiles/{id}
      2. account_name → https://steamcommunity.com/id/{name}
    """
    try:
        ma_data = rent.account_ma_data(account)
        steamid = str(ma_data.get("Session", {}).get("SteamID", "")).strip()
        if steamid and steamid.isdigit():
            return f"https://steamcommunity.com/profiles/{steamid}"
    except Exception:
        pass
    login = str(account.get("login", "")).strip()
    if login:
        from urllib.parse import quote
        return f"https://steamcommunity.com/id/{quote(login)}"
    return ""


def _get_steam_nickname(account: dict[str, Any]) -> str:
    """Получить ник аккаунта для отображения покупателю."""
    name = account.get("display_name") or account.get("title") or ""
    if name:
        return str(name)
    try:
        ma_data = rent.account_ma_data(account)
        persona = ma_data.get("Session", {}).get("PersonaName") or ma_data.get("account_name", "")
        if persona:
            return str(persona)
    except Exception:
        pass
    return str(account.get("login") or account.get("id", "—"))


def verification_message(account: dict[str, Any]) -> str:
    """Текст проверки «Это ваш аккаунт?» со ссылкой на профиль."""
    nickname = _get_steam_nickname(account)
    profile_url = _get_steam_profile_url(account)
    if profile_url:
        link_line = f"Профиль Steam:\n{profile_url}\nНик: {nickname}"
    else:
        link_line = f"Ник: {nickname}"
    return (
        "✅ Вы получили аккаунт. Проверьте, что это именно он:\n\n"
        f"{link_line}\n\n"
        "Зайдите на профиль по ссылке выше — там видно:\n"
        "  • аватар\n"
        "  • имя аккаунта\n"
        "  • уровень, игры, инвентарь\n\n"
        "Это ваш аккаунт?"
    )


def verification_kb(chat_id: str | int) -> "InlineKeyboardMarkup":
    """Клавиатура для проверки аккаунта."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✓ Да, всё верно",
                    callback_data=f"plugin_offline_confirm:{chat_id}",
                ),
                InlineKeyboardButton(
                    text="↻ Поменять аккаунт",
                    callback_data=f"plugin_offline_change:{chat_id}",
                ),
            ]
        ]
    )


def handle_customer_verification(chat_id: str | int, buyer_id: str) -> str | None:
    """Если у покупателя есть активная продажа — показать проверку.

    Вызывается из funpay_bridge при любом сообщении покупателя (не !menu, не !code).
    Показывает один раз, чтобы не спамить.
    """
    with rent.exclusive_file_lock(f"{PLUGIN_KEY}_verify") as got_lock:
        if not got_lock:
            return None

        settings, state = _state()
        sales = [item for item in state.get("sales", []) if isinstance(item, dict)]
        sale = _current_sale_for_chat(state, sales, buyer_id, chat_id)
        if not sale:
            return None

        chat_key = str(chat_id or sale.get("chat_id", "") or "")

        # Показываем проверку только если ещё не показывали этому чату
        verify_key = "_verification_sent"
        if sale.get(verify_key):
            return None

        account = rent.account_by_id(rent.list_accounts(), sale.get("account_id", ""))
        if not account:
            return None

        # Помечаем что показали
        sale[verify_key] = True
        for item in sales:
            if item is sale:
                continue
            same_order = bool(str(sale.get("order_id", ""))) and _same_order_id(
                str(item.get("order_id", "")), str(sale.get("order_id", ""))
            )
            if same_order:
                item[verify_key] = True
        state["sales"] = sales
        _save(settings, state)

        return verification_message(account)


# ─────────────────────────────────────────────────────────────────────────────
# Смена аккаунта: покупатель нажал «Поменять аккаунт»
# ─────────────────────────────────────────────────────────────────────────────
def handle_change_account(chat_id: str | int, buyer_id: str) -> dict[str, Any]:
    """Сменить аккаунт покупателю: вернуть текущий в available, выдать новый.

    Возвращает:
      {"ok": True, "message": "..."} — успех
      {"ok": False, "message": "...", "refund": True/False}
    """
    with rent.exclusive_file_lock(f"{PLUGIN_KEY}_change") as got_lock:
        if not got_lock:
            return {
                "ok": False,
                "refund": False,
                "message": "⚠ Подождите, идёт обработка предыдущего запроса.",
            }

        settings, state = _state()
        sales = [item for item in state.get("sales", []) if isinstance(item, dict)]
        sale = _current_sale_for_chat(state, sales, buyer_id, chat_id)
        if not sale:
            return {
                "ok": False,
                "refund": False,
                "message": "Активная продажа не найдена.",
            }

        old_account_id = str(sale.get("account_id", ""))
        lot_id = str(sale.get("lot_id", ""))
        chat_key = str(chat_id or sale.get("chat_id", "") or "")

        # Список всех аккаунтов, которые уже были выданы этому покупателю.
        # ВАЖНО: накапливаем и сохраняем в продаже, иначе при повторной смене
        # бот забудет старые аккаунты, увидит их снова свободными и выдаст по кругу.
        used_ids = set(str(x) for x in sale.get("used_account_ids", []))
        if old_account_id:
            used_ids.add(old_account_id)
        sale["used_account_ids"] = sorted(used_ids)

        # Возвращаем старый аккаунт в available
        accounts = rent.list_accounts()
        old_account = rent.account_by_id(accounts, old_account_id)
        if old_account:
            old_account["status"] = "available"
            old_account["buyer_id"] = ""
            old_account["buyer_name"] = ""
            old_account["rented_until"] = ""
            old_account["notify_sent"] = False

        # Сбрасываем флаги выдачи кода в текущей продаже
        sale["code_sent"] = False
        sale["code_sent_at"] = ""
        sale["_verification_sent"] = False
        sale["_last_code_request_ts"] = 0

        # Ищем свободный аккаунт в том же лоте (исключая все использованные)
        lot = rent.lot_by_id(rent.list_lots(), lot_id)
        linked_ids = (lot or {}).get("account_ids", []) if lot else []
        new_account = None
        for acc in accounts:
            acc_id = str(acc.get("id", ""))
            if acc_id in used_ids:
                continue
            if linked_ids and acc_id not in linked_ids:
                continue
            if acc.get("status") == "available":
                new_account = acc
                break

        if not new_account:
            # Нет свободных — оставляем старый как available, продавцу refund
            rent.save_accounts(accounts)
            state["sales"] = sales
            _save(settings, state)
            rent.log_event("warning", f"Смена аккаунта: нет свободных для чата {chat_key}")
            return {
                "ok": False,
                "refund": True,
                "message": (
                    "К сожалению, других свободных аккаунтов сейчас нет.\n"
                    "Продавец вернёт оплату или предложит подождать."
                ),
            }

        # Выдаём новый аккаунт
        new_account["status"] = "available"
        new_account["offline_mode"] = True
        new_account["buyer_id"] = str(sale.get("buyer_id", buyer_id))
        new_account["buyer_name"] = str(sale.get("buyer_name", ""))
        new_account["rented_until"] = ""
        new_account["notify_sent"] = False
        rent.save_accounts(accounts)

        # Обновляем запись продажи
        sale["account_id"] = str(new_account.get("id", ""))
        state["sales"] = sales
        _save(settings, state)
        rent.log_event(
            "info",
            f"Смена аккаунта по заказу #{sale.get('order_id', '')}: "
            f"{old_account_id} → {new_account.get('id', '')}",
        )
        return {
            "ok": True,
            "message": _access_message(new_account, sale.get("buyer_name", "")),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Шаблоны сообщений
# ─────────────────────────────────────────────────────────────────────────────
def _access_message(account: dict[str, Any], buyer_name: str = "") -> str:
    """Сообщение с логином/паролем для покупателя."""
    buyer = buyer_name or "покупатель"
    return (
        f"Здравствуйте, {buyer}, спасибо за покупку.\n\n"
        f"Логин:\n{account.get('login', '')}\n\n"
        f"Пароль:\n{account.get('password', '')}\n\n"
        "Чтобы получить код от 2FA, напишите команду !code.\n"
        "Учтите: выдача кода происходит только 1 раз."
    )
