"""Core rental service — business logic for Steam account rentals.

Structure:
  §1  Time helpers
  §2  File locking (cross-process)
  §3  Owner scoping
  §4  Settings (per-owner)
  §5  Logging
  §6  Data access (accounts / lots / rentals)
  §7  Account management
  §8  Lot management
  §9  Rental lifecycle (allocate, extend, expire)
  §10 FunPay customer commands (!menu, !code)
  §11 Steam Guard TOTP
  §12 Access codes (owner-managed)
  §13 Display text (dashboard, lists)
  §14 Duration parsing
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import struct
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
import steam_password
import storage


# ═══════════════════════════════════════════════════════════════════════════════
# §1 Time helpers
# ═══════════════════════════════════════════════════════════════════════════════
MSK = timezone(timedelta(hours=3))


def now_utc() -> datetime:
    """Current UTC datetime."""
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Convert datetime to UTC ISO 8601 string."""
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    """Parse ISO datetime string back to datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt(value: str | None) -> str:
    """Format datetime to 'DD.MM.YYYY HH:MM' MSK string."""
    dt = parse_dt(value)
    if not dt:
        return "-"
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def hours_left(value: str | None) -> float:
    """Hours remaining until given ISO datetime."""
    dt = parse_dt(value)
    if not dt:
        return 0.0
    return round(max(0.0, (dt - now_utc()).total_seconds() / 3600), 1)


def esc(value: Any) -> str:
    """HTML-escape for Telegram messages."""
    return html.escape(str(value or ""))


def random_password(length: int = 18) -> str:
    """Generate a secure random password."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ═══════════════════════════════════════════════════════════════════════════════
# §2 File locking (cross-process)
# ═══════════════════════════════════════════════════════════════════════════════
@contextmanager
def exclusive_file_lock(name: str, stale_seconds: int = 900):
    """Cross-process exclusive lock using O_CREAT|O_EXCL lockfile."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / f"{name}.lock"
    fd: int | None = None
    locked = False
    try:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                if age > stale_seconds:
                    path.unlink(missing_ok=True)
                    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                else:
                    yield False
                    return
            except FileExistsError:
                yield False
                return
        locked = True
        os.write(fd, f"{os.getpid()} {time.time()}".encode("ascii", errors="ignore"))
        yield True
    finally:
        if fd is not None:
            os.close(fd)
        if locked:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# §3 Owner scoping
# ═══════════════════════════════════════════════════════════════════════════════
CURRENT_OWNER_ID: ContextVar[str] = ContextVar("CURRENT_OWNER_ID", default="")


def set_current_owner(owner_id: int | str | None) -> None:
    CURRENT_OWNER_ID.set(str(owner_id or ""))


def current_owner_id() -> str:
    return CURRENT_OWNER_ID.get()


def _owned_items(name: str) -> list[dict[str, Any]]:
    """Read items from storage, filtered by current owner_id."""
    owner_id = current_owner_id()
    items = storage.read(name)
    if not owner_id:
        return items
    return [item for item in items if str(item.get("owner_id", "")) == owner_id]


def _save_owned_items(name: str, scoped_items: list[dict[str, Any]]) -> None:
    """Save items scoped to current owner_id, preserving other owners' data."""
    owner_id = current_owner_id()
    if not owner_id:
        storage.write(name, scoped_items)
        return
    all_items = storage.read(name)
    kept = [item for item in all_items if str(item.get("owner_id", "")) != owner_id]
    cleaned = [{**item, "owner_id": owner_id} for item in scoped_items]
    storage.write(name, [*kept, *cleaned])


def data_owner_ids() -> set[str]:
    """Return owner IDs that already have saved bot data."""
    owners: set[str] = set()
    for table in ("accounts", "lots", "rentals"):
        try:
            items = storage.read(table)
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            owner_id = str(item.get("owner_id", "") or "").strip()
            if owner_id and owner_id != "global":
                owners.add(owner_id)
    return owners


def workspace_owner_ids() -> set[str]:
    """Return owner IDs that have any meaningful workspace state."""
    owners = set(data_owner_ids())
    settings = storage.read("settings")
    for owner, user_data in settings.get("user_settings", {}).items():
        if not owner or owner == "global" or not isinstance(user_data, dict):
            continue
        has_funpay = bool((user_data.get("funpay_golden_key") or "").strip())
        has_plugins = bool(user_data.get("plugins"))
        if has_funpay or has_plugins:
            owners.add(str(owner))
    return owners


def _legacy_owner_context(user_id: int | str) -> str:
    """Resolve which workspace should be used for a Telegram user (slot 1)."""
    uid = str(user_id)
    workspaces = workspace_owner_ids()
    if uid in workspaces:
        return uid
    if str(user_id).isdigit() and int(str(user_id)) in config.ADMIN_IDS:
        data_owners = sorted(data_owner_ids())
        if len(data_owners) == 1:
            return data_owners[0]
        if not data_owners and len(workspaces) == 1:
            return sorted(workspaces)[0]
    return uid


def workspace_slots(user_id: int | str) -> list[str]:
    """До 3 рабочих пространств (FunPay-аккаунтов) пользователя: "<id>", "<id>_2", "<id>_3"."""
    uid = str(user_id)
    base = _legacy_owner_context(uid)
    result = [base]
    for extra in (f"{uid}_2", f"{uid}_3"):
        if extra not in result:
            result.append(extra)
    return result[:3]


def owner_context_for_user(user_id: int | str) -> str:
    """Активный FunPay-аккаунт пользователя (выбирается в меню «FunPay аккаунты»)."""
    uid = str(user_id)
    record = (storage.read("bot_users") or {}).get(uid) or {}
    active = str(record.get("active_ws") or "")
    if active and active in workspace_slots(uid):
        return active
    return _legacy_owner_context(uid)


def telegram_id_for_owner(owner_id: str | int | None) -> int | None:
    head = str(owner_id or "").split("_", 1)[0]
    return int(head) if head.isdigit() else None


def update_owner_settings(owner_id: str, **fields: Any) -> dict[str, Any]:
    """Как update_user_settings, но для явно указанного рабочего пространства."""
    previous = current_owner_id()
    set_current_owner(owner_id)
    try:
        return update_user_settings(**fields)
    finally:
        set_current_owner(previous)


# ═══════════════════════════════════════════════════════════════════════════════
# §4 Settings (per-owner)
# ═══════════════════════════════════════════════════════════════════════════════
def user_settings(owner_id: str | None = None) -> dict[str, Any]:
    """Get/create current owner's settings dict."""
    owner = str(owner_id or current_owner_id() or "global")
    settings = storage.read("settings")
    all_settings = settings.setdefault("user_settings", {})
    user = all_settings.setdefault(owner, {})
    user.setdefault("funpay_golden_key", "")
    user.setdefault("auto_raise_lots_enabled", False)
    user.setdefault("auto_raise_last_at", "")
    user.setdefault("auto_raise_next_at", "")
    user.setdefault("auto_raise_last_result", "")
    user.setdefault("auto_raise_last_error", "")
    return user


def update_user_settings(**fields: Any) -> dict[str, Any]:
    """Update and persist current owner's settings."""
    owner = current_owner_id() or "global"
    settings = storage.read("settings")
    all_settings = settings.setdefault("user_settings", {})
    user = all_settings.setdefault(owner, {})
    user.update(fields)
    all_settings[owner] = user
    settings["user_settings"] = all_settings
    storage.write("settings", settings)
    return user


def reset_bot_database() -> None:
    """Wipe all data tables to defaults (users/bans/subscription/donations are kept)."""
    for name, default in storage.DEFAULTS.items():
        if name in storage.PRESERVED_ON_RESET:
            continue
        storage.write(name, json.loads(json.dumps(default, ensure_ascii=False)))
    set_current_owner("")


def active_owner_ids() -> list[str]:
    """Return all owner IDs with data, active access codes, or FunPay keys."""
    settings = storage.read("settings")
    owners = {str(item) for item in config.ADMIN_IDS}
    owners.update(workspace_owner_ids())
    for item in settings.get("activated_users", []):
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("user_id", ""))
        if user_id and is_user_activated(user_id):
            owners.add(user_id)
    for owner, user_data in settings.get("user_settings", {}).items():
        has_funpay = bool((user_data or {}).get("funpay_golden_key", "").strip()) if isinstance(user_data, dict) else False
        if owner and owner != "global" and (owner in owners or is_user_activated(owner) or has_funpay):
            owners.add(str(owner))
    return list(owners)


def has_funpay_key() -> bool:
    return bool(user_settings().get("funpay_golden_key"))


def needs_onboarding() -> bool:
    return len(list_accounts()) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# §5 Logging
# ═══════════════════════════════════════════════════════════════════════════════
def log_event(level: str, text: str) -> None:
    """Append an event to the events log (trimmed to 300)."""
    events = storage.read("events")
    event = {"level": level, "text": text, "created_at": iso(now_utc())}
    if current_owner_id():
        event["owner_id"] = current_owner_id()
    events.append(event)
    storage.write("events", events[-300:])


def log_message(buyer_id: str, direction: str, text: str) -> None:
    """Append a message to the messages log (trimmed to 300)."""
    messages = storage.read("messages")
    item = {
        "buyer_id": str(buyer_id),
        "direction": direction,
        "text": text,
        "created_at": iso(now_utc()),
    }
    if current_owner_id():
        item["owner_id"] = current_owner_id()
    messages.append(item)
    storage.write("messages", messages[-300:])


# ═══════════════════════════════════════════════════════════════════════════════
# §6 Data access (accounts / lots / rentals)
# ═══════════════════════════════════════════════════════════════════════════════
def list_accounts() -> list[dict[str, Any]]:
    return _owned_items("accounts")


def list_lots() -> list[dict[str, Any]]:
    return _owned_items("lots")


def list_rentals() -> list[dict[str, Any]]:
    return _owned_items("rentals")


def save_accounts(accounts: list[dict[str, Any]]) -> None:
    _save_owned_items("accounts", accounts)


def save_lots(lots: list[dict[str, Any]]) -> None:
    _save_owned_items("lots", lots)


def save_rentals(rentals: list[dict[str, Any]]) -> None:
    _save_owned_items("rentals", rentals)


def account_by_id(accounts: list[dict[str, Any]], account_id: str) -> dict[str, Any] | None:
    return next((item for item in accounts if str(item.get("id")) == str(account_id)), None)


def lot_by_id(lots: list[dict[str, Any]], lot_id: str) -> dict[str, Any] | None:
    return next((item for item in lots if str(item.get("id")) == str(lot_id)), None)


# ═══════════════════════════════════════════════════════════════════════════════
# §7 Account management
# ═══════════════════════════════════════════════════════════════════════════════
def next_account_id(accounts: list[dict[str, Any]]) -> str:
    nums = [
        int(str(item.get("id", "acc-0")).split("-")[-1])
        for item in accounts
        if str(item.get("id", "")).split("-")[-1].isdigit()
    ]
    return f"acc-{max(nums or [0]) + 1}"


def upsert_account(payload: dict[str, str]) -> dict[str, Any]:
    """Create or update an account."""
    accounts = list_accounts()
    account_id = payload.get("id") or next_account_id(accounts)
    clean = {
        "id": account_id,
        "account_type": payload.get("account_type", "steam").strip() or "steam",
        "display_name": payload.get("display_name", "").strip() or payload["login"].strip(),
        "lot_id": payload.get("lot_id", "").strip(),
        "title": payload.get("title", "").strip() or f"{payload.get('account_type', 'steam').strip() or 'steam'} account",
        "login": payload["login"].strip(),
        "password": payload["password"].strip(),
        "ma_file_path": payload.get("ma_file_path", "").strip(),
        "ma_file_name": payload.get("ma_file_name", "").strip(),
        "ma_file_json": payload.get("ma_file_json", "").strip(),
        "status": payload.get("status", "available"),
        "buyer_id": payload.get("buyer_id", ""),
        "buyer_name": payload.get("buyer_name", ""),
        "rented_until": payload.get("rented_until", ""),
        "notify_sent": bool(payload.get("notify_sent", False)),
        "note": payload.get("note", "").strip(),
    }
    for index, item in enumerate(accounts):
        if str(item.get("id")) == account_id:
            accounts[index] = {**item, **clean}
            break
    else:
        accounts.append(clean)
    save_accounts(accounts)
    log_event("info", f"Сохранен аккаунт {account_id}")
    if clean["lot_id"]:
        attach_account_to_lot(clean["lot_id"], account_id)
    return clean


def attach_account_to_lot(lot_id: str, account_id: str) -> None:
    lots = list_lots()
    lot = lot_by_id(lots, lot_id)
    if not lot:
        lot = {
            "id": lot_id,
            "title": f"Лот {lot_id}",
            "rent_hours": config.DEFAULT_RENT_HOURS,
            "rent_minutes": config.DEFAULT_RENT_HOURS * 60,
            "notify_before_minutes": config.NOTIFY_BEFORE_MINUTES,
            "review_min_stars": 0,
            "review_bonus_minutes": 0,
            "account_ids": [],
        }
        lots.append(lot)
    ids = lot.setdefault("account_ids", [])
    if account_id not in ids:
        ids.append(account_id)
    save_lots(lots)


def account_ma_data(account: dict[str, Any]) -> dict[str, Any]:
    """Extract maFile JSON from account (inline first, then file)."""
    ma_file_json = account.get("ma_file_json", "")
    if ma_file_json:
        try:
            return json.loads(ma_file_json)
        except Exception as exc:
            raise RuntimeError("maFile в базе поврежден.") from exc
    ma_file_path = account.get("ma_file_path", "")
    if ma_file_path and Path(ma_file_path).exists():
        return json.loads(Path(ma_file_path).read_text(encoding="utf-8"))
    raise RuntimeError("maFile не найден, он должен храниться в базе аккаунта.")


def change_steam_password(account: dict[str, Any]) -> str:
    """Change Steam account password via Steam API."""
    ma_data = account_ma_data(account)
    login = account.get("login") or ma_data.get("account_name") or ""
    current_password = account.get("password") or ""
    if not login or not current_password:
        raise RuntimeError("Нет логина или текущего пароля Steam.")
    return steam_password.change_password_sync(login, current_password, ma_data)


# ═══════════════════════════════════════════════════════════════════════════════
# §8 Lot management
# ═══════════════════════════════════════════════════════════════════════════════
def upsert_lot(payload: dict[str, Any]) -> dict[str, Any]:
    """Create or update a lot."""
    lots = list_lots()
    lot_id = str(payload["id"]).strip()
    rent_minutes = int(payload.get("rent_minutes") or payload.get("rent_hours", config.DEFAULT_RENT_HOURS) * 60)
    clean = {
        "id": lot_id,
        "title": str(payload["title"]).strip(),
        "rent_hours": max(1, rent_minutes // 60),
        "rent_minutes": max(1, rent_minutes),
        "notify_before_minutes": max(1, int(payload.get("notify_before_minutes", config.NOTIFY_BEFORE_MINUTES))),
        "review_min_stars": int(payload.get("review_min_stars", 0)),
        "review_bonus_minutes": int(payload.get("review_bonus_minutes", 0)),
        "account_ids": [str(item).strip() for item in payload.get("account_ids", []) if str(item).strip()],
    }
    for index, item in enumerate(lots):
        if str(item.get("id")) == lot_id:
            lots[index] = {**item, **clean}
            break
    else:
        lots.append(clean)
    save_lots(lots)
    log_event("info", f"Сохранен лот {lot_id}: {clean['title']}")
    return clean


def update_lot(lot_id: str, **fields: Any) -> dict[str, Any] | None:
    """Partially update a lot by ID."""
    lots = list_lots()
    for index, lot in enumerate(lots):
        if str(lot.get("id")) != str(lot_id):
            continue
        lots[index] = {**lot, **fields}
        if "rent_minutes" in fields:
            minutes = max(1, int(fields["rent_minutes"]))
            lots[index]["rent_hours"] = rent_hours_from_minutes(minutes)
        save_lots(lots)
        return lots[index]
    return None


def lot_title_from_accounts(account_ids: list[str]) -> str:
    accounts = list_accounts()
    for account_id in account_ids:
        account = account_by_id(accounts, account_id)
        if account:
            return account.get("display_name") or account.get("title") or account.get("login") or f"Аккаунт {account_id}"
    return "Лот"


def lot_rent_minutes(lot: dict[str, Any] | None, fallback_hours: int = config.DEFAULT_RENT_HOURS) -> int:
    """Get effective rent duration in minutes for a lot."""
    if lot and lot.get("rent_minutes"):
        return max(1, int(lot.get("rent_minutes", 1)))
    hours = int((lot or {}).get("rent_hours", fallback_hours))
    return max(1, hours) * 60


# ═══════════════════════════════════════════════════════════════════════════════
# §9 Rental lifecycle (allocate, extend, expire)
# ═══════════════════════════════════════════════════════════════════════════════
def active_rental(rentals: list[dict[str, Any]], buyer_id: str, lot_id: str) -> dict[str, Any] | None:
    """Find the most recent active rental for buyer + lot."""
    current = now_utc()
    active = [
        item
        for item in rentals
        if item.get("status") == "active"
        and str(item.get("buyer_id")) == str(buyer_id)
        and str(item.get("lot_id")) == str(lot_id)
        and (parse_dt(item.get("ends_at")) or current) > current
    ]
    active.sort(key=lambda item: item.get("ends_at") or "", reverse=True)
    return active[0] if active else None


def active_rental_for_chat(buyer_id: str | int, chat_id: str | int = "") -> dict[str, Any] | None:
    current = now_utc()
    chat = str(chat_id or "")
    if chat:
        chat_matches = [
            item for item in list_rentals()
            if item.get("status") == "active"
            and str(item.get("chat_id") or "") == chat
            and (parse_dt(item.get("ends_at")) or current) > current
        ]
        chat_matches.sort(key=lambda item: item.get("ends_at") or "", reverse=True)
        if chat_matches:
            return chat_matches[0]
    matches = [
        item for item in list_rentals()
        if item.get("status") == "active"
        and str(item.get("buyer_id")) == str(buyer_id)
        and (parse_dt(item.get("ends_at")) or current) > current
        and (not chat or str(item.get("chat_id") or "") in {chat, ""})
    ]
    if not matches and chat:
        matches = [
            item for item in list_rentals()
            if item.get("status") == "active"
            and str(item.get("buyer_id")) == str(buyer_id)
            and (parse_dt(item.get("ends_at")) or current) > current
        ]
    matches.sort(key=lambda item: (str(item.get("chat_id") or "") == chat, item.get("ends_at") or ""), reverse=True)
    return matches[0] if matches else None


def access_message(account: dict[str, Any], ends_at: datetime, extended: bool, lot_title: str | None = None) -> str:
    """Format the 'here are your login/password' message."""
    buyer_name = account.get("buyer_name") or "покупатель"
    product_title = lot_title or account["title"]
    prefix = (
        f"спасибо за продление аренды аккаунта \"{product_title}\""
        if extended
        else f"спасибо что купили товар \"{product_title}\""
    )
    return (
        f"Здравствуйте, {buyer_name}, {prefix}.\n"
        "Вот ваш логин и пароль:\n\n"
        f"Логин:\n{account['login']}\n\n"
        f"Пароль:\n{account['password']}\n\n"
        f"Аренда действует до {fmt(iso(ends_at))} МСК.\n\n"
        "Если при входе потребуется Steam Guard, напишите команду !code."
    )


def access_message(account: dict[str, Any], ends_at: datetime, extended: bool, lot_title: str | None = None) -> str:
    """Format the buyer access message with rental menu hints."""
    buyer_name = account.get("buyer_name") or "покупатель"
    product_title = lot_title or account.get("title") or "Steam аккаунт"
    action = (
        f"спасибо за продление аренды аккаунта \"{product_title}\""
        if extended
        else f"спасибо что купили товар \"{product_title}\""
    )
    return (
        f"Здравствуйте, {buyer_name}, {action}.\n"
        "Вот ваш логин и пароль:\n\n"
        f"Логин:\n{account['login']}\n\n"
        f"Пароль:\n{account['password']}\n\n"
        f"Аренда действует до {fmt(iso(ends_at))} МСК.\n\n"
        "Меню ваших аренд: !menu\n"
        "Если при входе потребуется Steam Guard, напишите !code."
    )


def access_message(account: dict[str, Any], ends_at: datetime, extended: bool, lot_title: str | None = None) -> str:
    """Format the buyer access message."""
    buyer_name = account.get("buyer_name") or "покупатель"
    product_title = lot_title or account.get("title") or "Steam аккаунт"
    intro = "Продление аренды успешно применено" if extended else "Ваш заказ успешно выдан"
    return (
        f"Здравствуйте, {buyer_name}.\n"
        f"{intro}: \"{product_title}\".\n\n"
        "Ваш доступ:\n\n"
        f"Логин:\n{account['login']}\n\n"
        f"Пароль:\n{account['password']}\n\n"
        f"Доступ активен до {fmt(iso(ends_at))} МСК.\n\n"
        "Команды:\n"
        "!menu - открыть меню аренды\n"
        "!code - получить Steam Guard код\n\n"
        "Приятного пользования!"
    )


def allocate_or_extend(
    lot_id: str,
    buyer_id: str,
    buyer_name: str,
    rent_hours: int = config.DEFAULT_RENT_HOURS,
    rent_minutes: int | None = None,
    order_id: str = "manual",
    chat_id: str | int = "",
    order_title: str | None = None,
) -> dict[str, Any]:
    """Allocate a free account OR extend an existing rental."""
    accounts = list_accounts()
    lot = lot_by_id(list_lots(), lot_id)
    rentals = list_rentals()
    current = now_utc()

    effective_minutes = rent_minutes or lot_rent_minutes(lot, rent_hours)
    delta = timedelta(minutes=max(1, effective_minutes))

    # Try to extend existing rental
    active = active_rental(rentals, buyer_id, lot_id)
    if active:
        account = account_by_id(accounts, active["account_id"])
        if not account:
            return {"ok": False, "message": "Аккаунт активной аренды не найден."}
        if order_id in (active.get("order_ids") or []):
            if order_title and not active.get("order_title"):
                active["order_title"] = str(order_title)
                save_rentals(rentals)
            ends_at = parse_dt(active.get("ends_at")) or current
            title = active.get("order_title") or order_title or (lot or {}).get("title")
            message = access_message(account, ends_at, extended=True, lot_title=title)
            return {"ok": True, "type": "duplicate", "account": account, "message": message}
        new_end = (parse_dt(active["ends_at"]) or current) + delta
        active["ends_at"] = iso(new_end)
        active["order_ids"] = list(dict.fromkeys([*(active.get("order_ids") or []), order_id]))
        active["notify_sent"] = False
        if chat_id:
            active["chat_id"] = str(chat_id)
        if order_title:
            active["order_title"] = str(order_title)
        account["rented_until"] = iso(new_end)
        account["notify_sent"] = False
        save_accounts(accounts)
        save_rentals(rentals)
        title = active.get("order_title") or order_title or (lot or {}).get("title")
        message = access_message(account, new_end, extended=True, lot_title=title)
        log_event("info", f"{buyer_name or buyer_id} продлил {account['id']} до {fmt(iso(new_end))} МСК")
        log_message(buyer_id, "out", message)
        return {"ok": True, "type": "extended", "account": account, "message": message}

    # Find a free account
    free_account = None
    for account in accounts:
        end = parse_dt(account.get("rented_until"))
        account_allowed = not lot or not lot.get("account_ids") or account.get("id") in lot.get("account_ids", [])
        if str(account.get("lot_id")) != str(lot_id) and not account_allowed:
            continue
        # Аккаунт, выданный оффлайн-плагином, нельзя продавать повторно через аренду.
        if account.get("status") == "offline_sold" or account.get("offline_mode"):
            continue
        if account.get("status") == "available" or not end or end <= current:
            free_account = account
            break

    if not free_account:
        message = (
            f"Здравствуйте, {buyer_name or buyer_id}, попробуйте позже, "
            "сейчас аккаунты все заняты. Деньги будут возвращены автоматически."
        )
        log_event("warn", f"Нет свободного аккаунта для лота {lot_id}; нужен авто-возврат заказа {order_id}")
        log_message(buyer_id, "out", message)
        return {"ok": False, "type": "no_stock", "refund": True, "order_id": order_id, "message": message}

    ends_at = current + delta
    rental = {
        "id": f"rent-{len(rentals) + 1}",
        "account_id": free_account["id"],
        "lot_id": lot_id,
        "buyer_id": str(buyer_id),
        "buyer_name": buyer_name,
        "order_ids": [order_id],
        "chat_id": str(chat_id) if chat_id else "",
        "started_at": iso(current),
        "ends_at": iso(ends_at),
        "status": "active",
        "notify_sent": False,
        "order_title": str(order_title or (lot or {}).get("title") or ""),
    }
    rentals.append(rental)
    free_account["status"] = "rented"
    free_account["buyer_id"] = str(buyer_id)
    free_account["buyer_name"] = buyer_name
    free_account["rented_until"] = iso(ends_at)
    free_account["notify_sent"] = False
    save_accounts(accounts)
    save_rentals(rentals)
    message = access_message(free_account, ends_at, extended=False, lot_title=order_title or (lot or {}).get("title"))
    log_event("info", f"{buyer_name or buyer_id} получил {free_account['id']} до {fmt(iso(ends_at))} МСК")
    log_message(buyer_id, "out", message)
    return {"ok": True, "type": "allocated", "account": free_account, "message": message}


def apply_review_bonus(order_id: str, stars: int) -> dict[str, Any] | None:
    """Extend rental time for positive review.

    Returns dict with keys: ok, chat_id, message, buyer_name, buyer_id.
    Returns None if no bonus applicable (lot without review rules, wrong stars, etc).
    """
    rentals = list_rentals()
    lots = list_lots()
    for rental in rentals:
        if order_id not in (rental.get("order_ids") or []):
            continue
        if rental.get("review_bonus_applied"):
            return None
        lot = lot_by_id(lots, rental.get("lot_id", ""))
        if not lot:
            return None
        min_stars = int(lot.get("review_min_stars", 0))
        bonus_minutes = int(lot.get("review_bonus_minutes", 0))
        if min_stars <= 0 or bonus_minutes <= 0 or stars < min_stars:
            return None
        current_end = parse_dt(rental.get("ends_at")) or now_utc()
        new_end = current_end + timedelta(minutes=bonus_minutes)
        rental["ends_at"] = iso(new_end)
        rental["review_bonus_applied"] = True
        rental["notify_sent"] = False
        accounts = list_accounts()
        account = account_by_id(accounts, rental["account_id"])
        if account:
            account["rented_until"] = iso(new_end)
            account["notify_sent"] = False
        save_accounts(accounts)
        save_rentals(rentals)
        log_event("info", f"Бонус за отзыв {stars}* для {rental['id']}: +{bonus_minutes} мин")
        bonus_hours = bonus_minutes // 60
        bonus_rest = bonus_minutes % 60
        if bonus_hours and bonus_rest:
            bonus_text = f"{bonus_hours} ч {bonus_rest} мин"
        elif bonus_hours:
            bonus_text = f"{bonus_hours} ч"
        else:
            bonus_text = f"{bonus_rest} мин"
        return {
            "ok": True,
            "chat_id": str(rental.get("chat_id", "")),
            "message": f"✅ Бонус за отзыв: +{bonus_text}.\nНовое окончание аренды: {fmt(iso(new_end))} МСК",
            "buyer_name": str(rental.get("buyer_name", "")),
            "buyer_id": str(rental.get("buyer_id", "")),
        }
    return None


def cancel_review_bonus(order_id: str) -> dict[str, Any] | None:
    """Roll back review bonus if review was deleted.

    Returns dict with keys: ok, chat_id, message, buyer_name, buyer_id.
    Returns None if no bonus to cancel.
    """
    rentals = list_rentals()
    changed_rental = None
    for rental in rentals:
        if order_id not in (rental.get("order_ids") or []):
            continue
        if not rental.get("review_bonus_applied"):
            continue
        rental.pop("review_bonus_applied", None)
        changed_rental = rental
        break
    if not changed_rental:
        return None
    save_rentals(rentals)
    log_event("info", f"Бонус за отзыв отменён для {changed_rental['id']}")
    return {
        "ok": True,
        "chat_id": str(changed_rental.get("chat_id", "")),
        "message": "⚠️ Бонус за отзыв отменён, так как отзыв был удалён.",
        "buyer_name": str(changed_rental.get("buyer_name", "")),
        "buyer_id": str(changed_rental.get("buyer_id", "")),
    }


def active_review_bonus_order_ids() -> list[str]:
    """Return order IDs that have an applied review bonus."""
    order_ids: list[str] = []
    for rental in list_rentals():
        if not rental.get("review_bonus_applied"):
            continue
        for order_id in rental.get("order_ids") or []:
            order = str(order_id or "").strip()
            if order and not order.startswith("rent-"):
                order_ids.append(order)
    return list(dict.fromkeys(order_ids))


# ── Expiration ───────────────────────────────────────────────────────────────
def expired_notice_text(rental: dict[str, Any], account: dict[str, Any], lot: dict[str, Any] | None) -> str:
    """Text sent when a rental has expired."""
    return (
        f"Здравствуйте, {rental.get('buyer_name') or rental['buyer_id']}, лимит аренды вышел.\n"
        f"Доступ к товару \"{(lot or {}).get('title') or account['title']}\" закрыт.\n\n"
        "Если хотите продолжить пользоваться аккаунтом, купите этот же лот снова."
    )


def _expire_and_collect_notifications_unlocked() -> list[dict[str, str]]:
    """Core expiration logic (assumes file lock held)."""
    accounts = list_accounts()
    lots = list_lots()
    rentals = list_rentals()
    current = now_utc()
    outgoing: list[dict[str, str]] = []

    for rental in rentals:
        if rental.get("status") != "active":
            continue
        account = account_by_id(accounts, rental["account_id"])
        lot = lot_by_id(lots, rental.get("lot_id", ""))
        ends_at = parse_dt(rental.get("ends_at"))
        if not account or not ends_at:
            continue
        notify_minutes = int((lot or {}).get("notify_before_minutes", config.NOTIFY_BEFORE_MINUTES))

        # Warning before expiry
        if (
            not rental.get("notify_sent")
            and current < ends_at <= current + timedelta(minutes=notify_minutes)
        ):
            text = (
                f"Здравствуйте, {rental.get('buyer_name') or rental['buyer_id']}, скоро аренда закончится. "
                "Вам надо купить этот лот или вы потеряете доступ к аккаунту.\n\n"
                f"Товар: {(lot or {}).get('title') or account['title']}\n"
                f"До конца аренды осталось около {notify_minutes} минут."
            )
            outgoing.append(
                {
                    "kind": "warning",
                    "buyer_id": str(rental.get("buyer_id", "")),
                    "buyer_name": str(rental.get("buyer_name", "")),
                    "chat_id": str(rental.get("chat_id", "")),
                    "text": text,
                }
            )
            log_message(str(rental.get("buyer_id", "")), "out", text)
            rental["notify_sent"] = True
            account["notify_sent"] = True

        # Expired
        if ends_at <= current:
            try:
                account["password"] = random_password()
                account["status"] = "available"
                account["buyer_id"] = ""
                account["buyer_name"] = ""
                account["rented_until"] = ""
                account["notify_sent"] = False
            except Exception as exc:
                rental["status"] = "password_error"
                log_event("error", f"Ошибка смены пароля {account['id']}: {exc}")
                continue
            rental["status"] = "expired"
            text = expired_notice_text(rental, account, lot)
            outgoing.append(
                {
                    "kind": "expired",
                    "buyer_id": str(rental.get("buyer_id", "")),
                    "buyer_name": str(rental.get("buyer_name", "")),
                    "chat_id": str(rental.get("chat_id", "")),
                    "text": text,
                }
            )
            log_message(str(rental.get("buyer_id", "")), "out", text)
            log_event("info", f"Аренда {rental['id']} завершена, пароль {account['id']} заменен")

    save_accounts(accounts)
    save_rentals(rentals)
    return outgoing


def expire_and_collect_notifications() -> list[dict[str, str]]:
    """File-locked wrapper: expire rentals, collect warning/expired notices."""
    with exclusive_file_lock("rental_expire") as got_lock:
        if not got_lock:
            return []
        all_notices: list[dict[str, str]] = []
        for owner_id in active_owner_ids():
            set_current_owner(owner_id)
            all_notices.extend(_expire_and_collect_notifications_unlocked())
        set_current_owner("")
        return all_notices


def mark_notice_delivered(notice: dict[str, str]) -> None:
    """Mark a notice as delivered in storage."""
    pass  # implemented via claim/release below


def claim_notice_for_delivery(notice: dict[str, str]) -> bool:
    """File-locked claim to prevent double-sending of notices."""
    with exclusive_file_lock("notice_delivery") as got:
        return bool(got)


def release_notice_claim(notice: dict[str, str]) -> None:
    """Release a previously claimed notice."""
    pass  # lockfile auto-released by context manager


# ═══════════════════════════════════════════════════════════════════════════════
# §10 FunPay customer commands (!menu, !code)
# ═══════════════════════════════════════════════════════════════════════════════
def buyer_menu(buyer_id: str) -> str:
    """Text response to FunPay !menu command."""
    accounts = list_accounts()
    rentals = list_rentals()
    current = now_utc()
    active = [
        item for item in rentals
        if item.get("status") == "active"
        and str(item.get("buyer_id")) == str(buyer_id)
        and (parse_dt(item.get("ends_at")) or current) > current
    ]
    if not active:
        return "Активных аренд нет."
    lines = ["Ваши активные аренды:"]
    for rental in active:
        account = account_by_id(accounts, rental["account_id"])
        if not account:
            continue
        lines.append(
            f"\n{account['title']}\n"
            f"Логин:\n{account['login']}\n\n"
            f"Пароль:\n{account['password']}\n"
            f"До: {fmt(rental['ends_at'])} МСК\n"
            f"Осталось: {hours_left(rental['ends_at'])} ч"
        )
    return "\n".join(lines)


def code_request(buyer_id: str, chat_id: str | int = "") -> str:
    """Generate and return Steam Guard code for buyer's active rental."""
    accounts = list_accounts()
    rentals = list_rentals()
    current = now_utc()

    active: list[dict[str, Any]] = []
    if chat_id:
        chat = str(chat_id)
        active = [
            r for r in rentals
            if r.get("status") == "active"
            and str(r.get("chat_id") or "") == chat
            and (parse_dt(r.get("ends_at")) or current) > current
        ]

    if not active:
        active = [
            r for r in rentals
            if r.get("status") == "active"
            and str(r.get("buyer_id")) == str(buyer_id)
            and (parse_dt(r.get("ends_at")) or current) > current
        ]
        if chat_id:
            chat = str(chat_id)
            exact_or_empty = [r for r in active if str(r.get("chat_id") or "") in {chat, ""}]
            active = exact_or_empty or active

    if not active:
        text = "У вас нет активных аренд."
        log_event("warn", f"Покупатель {buyer_id} запросил Steam Guard, но активных аренд нет")
        log_message(buyer_id, "out", text)
        return text

    rental = active[0]
    account = account_by_id(accounts, rental["account_id"])
    if not account:
        text = "Аккаунт не найден. Обратитесь в поддержку."
        log_event("error", f"Покупатель {buyer_id}: аккаунт {rental['account_id']} не найден")
        log_message(buyer_id, "out", text)
        return text

    try:
        ma_data = account_ma_data(account)
    except Exception as exc:
        text = f"Ошибка maFile: {exc}. Продавец получил уведомление."
        log_event("error", f"Покупатель {buyer_id}: {exc}")
        log_message(buyer_id, "out", text)
        return text

    shared_secret = ma_data.get("shared_secret", "")
    if not shared_secret:
        text = "В maFile нет shared_secret. Продавец получил уведомление."
        log_event("warn", f"Покупатель {buyer_id}: shared_secret отсутствует в maFile")
        log_message(buyer_id, "out", text)
        return text

    code = generate_steam_guard_code(shared_secret)
    if not code:
        text = "Ошибка генерации кода Steam Guard. Продавец получил уведомление."
        log_event("error", f"Покупатель {buyer_id}: ошибка генерации Steam Guard кода")
        log_message(buyer_id, "out", text)
        return text

    text = f"Ваш код Steam Guard: {code}"
    log_event("info", f"Покупатель {buyer_id} получил Steam Guard код для {account.get('id')}")
    log_message(buyer_id, "out", text)
    return text


def _active_buyer_rentals_v2(buyer_id: str | int, chat_id: str | int = "") -> list[dict[str, Any]]:
    current = now_utc()
    buyer = str(buyer_id or "")
    chat = str(chat_id or "")
    rentals = [
        item for item in list_rentals()
        if item.get("status") == "active"
        and (parse_dt(item.get("ends_at")) or current) > current
        and (
            (buyer and str(item.get("buyer_id")) == buyer)
            or (chat and str(item.get("chat_id") or "") == chat)
        )
    ]
    rentals.sort(
        key=lambda item: (
            str(item.get("chat_id") or "") == chat,
            str(item.get("buyer_id") or "") == buyer,
            item.get("ends_at") or "",
        ),
        reverse=True,
    )
    return rentals


def _rental_title_v2(rental: dict[str, Any], account: dict[str, Any] | None = None) -> str:
    lot = lot_by_id(list_lots(), rental.get("lot_id", ""))
    return (
        str(rental.get("order_title") or "").strip()
        or str((lot or {}).get("title") or "").strip()
        or str((account or {}).get("display_name") or (account or {}).get("title") or "").strip()
        or "Steam аккаунт"
    )


def _select_active_rental_v2(
    buyer_id: str | int,
    chat_id: str | int = "",
    selector: str | int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    rentals = _active_buyer_rentals_v2(buyer_id, chat_id)
    if not rentals:
        return rentals, None, None
    raw = str(selector or "").strip()
    if not raw:
        if len(rentals) == 1:
            return rentals, rentals[0], None
        return rentals, None, "many"
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(rentals):
            return rentals, rentals[index], None
    needle = raw.lower()
    for rental in rentals:
        if needle in {
            str(rental.get("id", "")).lower(),
            str(rental.get("account_id", "")).lower(),
            str(rental.get("lot_id", "")).lower(),
        }:
            return rentals, rental, None
    return rentals, None, "bad_selector"


def buyer_menu(buyer_id: str, chat_id: str | int = "") -> str:
    accounts = list_accounts()
    rentals = _active_buyer_rentals_v2(buyer_id, chat_id)
    if not rentals:
        return "Активных аренд нет."
    lines = ["Меню аренды", "", "Выберите аккаунт по номеру:"]
    for number, rental in enumerate(rentals, start=1):
        account = account_by_id(accounts, rental.get("account_id", ""))
        if not account:
            continue
        title = _rental_title_v2(rental, account)
        lines.extend(
            [
                "",
                f"{number}. {title}",
                f"До: {fmt(rental.get('ends_at'))} МСК",
                f"Данные аккаунта: !acc {number}",
                f"Steam Guard код: !code {number}",
            ]
        )
    lines.extend(["", "Если аренда одна, можно просто написать !code."])
    return "\n".join(lines)


def rental_account_details(buyer_id: str, chat_id: str | int = "", selector: str | int | None = None) -> str:
    rentals, rental, reason = _select_active_rental_v2(buyer_id, chat_id, selector)
    if not rentals:
        return "Активных аренд нет."
    if reason == "many":
        return buyer_menu(buyer_id, chat_id)
    if reason == "bad_selector" or not rental:
        return "Не нашел аккаунт с таким номером. Напишите !menu и выберите номер из списка."

    account = account_by_id(list_accounts(), rental.get("account_id", ""))
    if not account:
        return "Аккаунт не найден. Обратитесь в поддержку."
    title = _rental_title_v2(rental, account)
    suffix = f" {selector}" if selector else ""
    return (
        f"{title}\n\n"
        f"Логин:\n{account['login']}\n\n"
        f"Пароль:\n{account['password']}\n\n"
        f"Аренда действует до {fmt(rental.get('ends_at'))} МСК.\n"
        f"Осталось: {hours_left(rental.get('ends_at'))} ч\n\n"
        f"Steam Guard код: !code{suffix}"
    )


def code_request(buyer_id: str, chat_id: str | int = "", selector: str | int | None = None) -> str:
    accounts = list_accounts()
    rentals, rental, reason = _select_active_rental_v2(buyer_id, chat_id, selector)

    if not rentals:
        text = "У вас нет активных аренд."
        log_event("warn", f"Покупатель {buyer_id} запросил Steam Guard, но активных аренд нет")
        log_message(buyer_id, "out", text)
        return text
    if reason == "many":
        text = buyer_menu(buyer_id, chat_id)
        log_message(buyer_id, "out", text)
        return text
    if reason == "bad_selector" or not rental:
        text = "Не нашел аккаунт с таким номером. Напишите !menu и выберите номер из списка."
        log_message(buyer_id, "out", text)
        return text

    account = account_by_id(accounts, rental.get("account_id", ""))
    if not account:
        text = "Аккаунт не найден. Обратитесь в поддержку."
        log_event("error", f"Покупатель {buyer_id}: аккаунт {rental.get('account_id')} не найден")
        log_message(buyer_id, "out", text)
        return text

    try:
        ma_data = account_ma_data(account)
    except Exception as exc:
        text = f"Ошибка maFile: {exc}. Продавец получил уведомление."
        log_event("error", f"Покупатель {buyer_id}: {exc}")
        log_message(buyer_id, "out", text)
        return text

    shared_secret = ma_data.get("shared_secret", "")
    if not shared_secret:
        text = "В maFile нет shared_secret. Продавец получил уведомление."
        log_event("warn", f"Покупатель {buyer_id}: shared_secret отсутствует в maFile")
        log_message(buyer_id, "out", text)
        return text

    code = generate_steam_guard_code(shared_secret)
    if not code:
        text = "Ошибка генерации кода Steam Guard. Продавец получил уведомление."
        log_event("error", f"Покупатель {buyer_id}: ошибка генерации Steam Guard кода")
        log_message(buyer_id, "out", text)
        return text

    text = f"Ваш код Steam Guard: {code}"
    log_event("info", f"Покупатель {buyer_id} получил Steam Guard код для {account.get('id')}")
    log_message(buyer_id, "out", text)
    return text


def handle_customer_text(buyer_id: str, chat_id: str | int, text: str) -> str | None:
    """Handle FunPay chat commands from buyer."""
    normalized = (text or "").strip().lower()
    parts = normalized.split(maxsplit=1)
    command = parts[0] if parts else ""
    selector = parts[1] if len(parts) > 1 else None
    if command in ("!menu", "/menu", "меню"):
        return buyer_menu(buyer_id, chat_id)
    if command in ("!acc", "!account", "!акк", "!аккаунт", "/acc", "/account"):
        return rental_account_details(buyer_id, chat_id, selector)
    if command in ("!code", "/code", "!код", "/код", "код"):
        return code_request(buyer_id, chat_id, selector)
    if normalized in ("!menu", "/menu", "меню"):
        return buyer_menu(buyer_id)
    if normalized in ("!code", "/code", "код"):
        return code_request(buyer_id, chat_id)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# §11 Steam Guard TOTP
# ═══════════════════════════════════════════════════════════════════════════════
def generate_steam_guard_code(shared_secret_b64: str) -> str | None:
    """Generate a Steam Guard TOTP code from base64-encoded shared_secret."""
    if not shared_secret_b64 or not isinstance(shared_secret_b64, str):
        return None
    try:
        shared_secret = base64.b64decode(shared_secret_b64, validate=True)
    except Exception:
        return None
    if len(shared_secret) < 10:
        return None
    timestamp = int(time.time()) // 30
    time_bytes = struct.pack(">Q", timestamp)
    digest = hmac.new(shared_secret, time_bytes, hashlib.sha1).digest()
    offset = digest[19] & 0xF
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    chars = "23456789BCDFGHJKMNPQRTVWXY"
    result = ""
    for _ in range(5):
        result += chars[code % len(chars)]
        code //= len(chars)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# §12 Access codes (owner-managed)
# ═══════════════════════════════════════════════════════════════════════════════
def normalize_access_code(code: str) -> str:
    """Normalize access code: strip non-alphanumeric, uppercase."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def access_prompt_text() -> str:
    return (
        "<b>Доступ к боту закрыт</b>\n\n"
        "Отправьте код доступа одним сообщением.\n"
        "Формат: <code>BOT-XXXX-XXXX</code>"
    )


def is_user_activated(user_id: str | int) -> bool:
    """True if user has a non-expired active access code."""
    record = active_access_record(user_id)
    return bool(record)


def active_access_record(user_id: str | int) -> dict[str, Any] | None:
    """Return the active access record for a user, or None."""
    uid = str(user_id)
    settings = storage.read("settings")
    current = now_utc()
    for record in settings.get("activated_users", []):
        if not isinstance(record, dict):
            continue
        if str(record.get("user_id")) != uid:
            continue
        expires = parse_dt(record.get("expires_at"))
        if expires and expires <= current:
            continue
        return record
    return None


def access_expiring_soon_text(user_id: str | int, threshold_minutes: int = 15) -> str:
    """Warning text if user's access expires soon."""
    record = active_access_record(user_id)
    if not record:
        return ""
    expires = parse_dt(record.get("expires_at"))
    if not expires:
        return ""
    remaining = (expires - now_utc()).total_seconds() / 60
    if remaining > threshold_minutes:
        return ""
    return (
        f"<b>Доступ истекает через {int(remaining)} мин.</b>\n"
        "Купите новый код у владельца и активируйте его."
    )


def access_expiry_notice_text(remaining_minutes: int) -> str:
    """Automatic warning before bot access expires."""
    minutes = max(1, int(remaining_minutes))
    return (
        f"<b>До конца доступа к боту осталось около {minutes} мин.</b>\n\n"
        "Быстрее напишите продавцу о покупке нового ключа доступа.\n"
        "После покупки нажмите кнопку ниже и активируйте новый код."
    )


def collect_access_expiry_notifications(threshold_minutes: int = 15) -> list[dict[str, str]]:
    """Collect one-time Telegram notices for users whose bot access expires soon."""
    notices: list[dict[str, str]] = []
    with exclusive_file_lock("access_expiry_notices") as got_lock:
        if not got_lock:
            return notices

        settings = storage.read("settings")
        changed = False
        current = now_utc()
        for record in settings.get("activated_users", []):
            if not isinstance(record, dict):
                continue
            user_id = str(record.get("user_id", "") or "")
            expires = parse_dt(record.get("expires_at"))
            if not user_id or not expires:
                continue
            remaining = (expires - current).total_seconds() / 60
            if remaining <= 0 or remaining > threshold_minutes:
                continue
            notice_key = f"access_{threshold_minutes}m_notice_sent_at"
            if record.get(notice_key):
                continue
            record[notice_key] = iso(current)
            changed = True
            notices.append(
                {
                    "user_id": user_id,
                    "text": access_expiry_notice_text(int(remaining) + 1),
                }
            )
        if changed:
            storage.write("settings", settings)
    return notices


ACCESS_DURATIONS: dict[str, dict[str, Any]] = {
    "1m":     {"label": "1 месяц",   "timedelta": timedelta(days=30)},
    "1h":     {"label": "1 час",     "timedelta": timedelta(hours=1)},
    "1w":     {"label": "1 неделя",  "timedelta": timedelta(weeks=1)},
    "1mo":    {"label": "1 месяц",   "timedelta": timedelta(days=30)},
    "1y":     {"label": "1 год",     "timedelta": timedelta(days=365)},
    "forever": {"label": "навсегда", "timedelta": None},
}


def parse_access_duration(value: str) -> dict[str, Any] | None:
    """Parse a duration string to {label, timedelta_or_None}."""
    return ACCESS_DURATIONS.get(str(value or "").strip().lower())


def generate_access_code(created_by: int, duration: str) -> dict[str, Any]:
    """Generate a new access code."""
    spec = parse_access_duration(duration)
    if not spec:
        raise ValueError(f"Неизвестный срок доступа: {duration}")
    code = f"BOT-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
    expires_at = None
    if spec["timedelta"]:
        expires_at = iso(now_utc() + spec["timedelta"])
    record = {
        "code": code,
        "duration": duration,
        "duration_label": spec["label"],
        "expires_at": expires_at,
        "created_by": str(created_by),
        "created_at": iso(now_utc()),
        "used_by": None,
    }
    settings = storage.read("settings")
    settings.setdefault("access_codes", []).append(record)
    storage.write("settings", settings)
    log_event("info", f"Создан код доступа {code} на {spec['label']}")
    return record


def redeem_access_code(code: str, user_id: int, username: str) -> dict[str, Any]:
    """Redeem an access code for a user."""
    normalized = normalize_access_code(code)
    settings = storage.read("settings")
    codes = settings.setdefault("access_codes", [])
    target = None
    for record in codes:
        if normalize_access_code(record.get("code", "")) == normalized:
            target = record
            break
    if not target:
        return {"ok": False, "message": "Код не найден или уже использован."}
    if target.get("used_by"):
        return {"ok": False, "message": "Этот код уже активирован другим пользователем."}

    spec = ACCESS_DURATIONS.get(target.get("duration", ""), {})
    expires_at = None
    if spec.get("timedelta"):
        expires_at = iso(now_utc() + spec["timedelta"])

    target["used_by"] = str(user_id)
    target["used_at"] = iso(now_utc())
    target["username"] = username
    target["activated_expires_at"] = expires_at

    activated = settings.setdefault("activated_users", [])
    activated[:] = [r for r in activated if str(r.get("user_id")) != str(user_id)]
    activated.append({
        "user_id": str(user_id),
        "username": username,
        "code": target["code"],
        "expires_at": expires_at,
        "activated_at": iso(now_utc()),
    })

    storage.write("settings", settings)
    log_event("info", f"Код {target['code']} активирован пользователем {user_id} ({username})")

    label = spec.get("label", "")
    msg = (
        f"Доступ активирован на <b>{esc(label)}</b>."
        if expires_at
        else "Доступ активирован <b>навсегда</b>."
    )
    return {"ok": True, "message": msg, "code": target["code"]}


def access_codes_text() -> str:
    """Text for access codes management view."""
    settings = storage.read("settings")
    codes = settings.get("access_codes", [])
    active = [c for c in codes if not c.get("used_by")]
    used = [c for c in codes if c.get("used_by")]
    lines = [
        "<b>Коды доступа</b>\n",
        f"Активных кодов: <b>{len(active)}</b>",
        f"Использовано: <b>{len(used)}</b>\n",
    ]
    if active:
        lines.append("<b>Не использованы:</b>")
        for record in active[-10:]:
            expires = fmt(record.get("expires_at")) if record.get("expires_at") else "без срока"
            lines.append(f"  <code>{esc(record.get('code'))}</code> — {esc(record.get('duration_label', ''))} (создан {esc(fmt(record.get('created_at')))})")
    if used:
        lines.append("\n<b>Использованы:</b>")
        for record in used[-10:]:
            lines.append(f"  <code>{esc(record.get('code'))}</code> — @{esc(record.get('username', '?'))}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# §13 Display text (dashboard, lists)
# ═══════════════════════════════════════════════════════════════════════════════
def dashboard_text() -> str:
    """Main dashboard text (used by templates/dashboard.py)."""
    from templates.dashboard import dashboard_text as _t
    return _t()


def lots_text() -> str:
    """Formatted list of all lots (used by templates/lots.py)."""
    from templates.lots import lots_text as _t
    return _t()


def lot_detail_text(lot_id: str) -> str:
    """Detail view of a single lot."""
    from templates.lots import lot_detail_text as _t
    return _t(lot_id)


def accounts_text() -> str:
    """Formatted list of accounts (used by templates/accounts.py)."""
    from templates.accounts import accounts_text as _t
    return _t()


def rentals_text() -> str:
    """Formatted list of rentals (used by templates/rentals.py)."""
    from templates.rentals import rentals_text as _t
    return _t()


def events_text() -> str:
    """Formatted event log."""
    from templates.events import events_text as _t
    return _t()


def messages_text() -> str:
    """Formatted message log."""
    from templates.messages import messages_text as _t
    return _t()


# ═══════════════════════════════════════════════════════════════════════════════
# §14 Duration parsing
# ═══════════════════════════════════════════════════════════════════════════════
def parse_duration_minutes(value: str) -> int | None:
    """Parse a duration string ('24', '30м', '2ч 30м', '1:30') to minutes."""
    text = str(value or "").strip().lower().replace(",", ".")
    if not text:
        return None
    if text.isdigit():
        return max(1, int(text) * 60)

    match = re.fullmatch(r"(\d+)\s*:\s*(\d{1,2})", text)
    if match:
        return max(1, int(match.group(1)) * 60 + int(match.group(2)))

    total = 0
    pattern = r"(\d+)\s*(час(?:а|ов)?|ч|h|hour(?:s)?|мин(?:ута|уты|ут)?|м|m|min(?:ute)?(?:s)?)"
    for amount, unit in re.findall(pattern, text):
        number = int(amount)
        if unit.startswith(("ч", "час", "h", "hour")):
            total += number * 60
        else:
            total += number
    return max(1, total) if total else None


def rent_hours_from_minutes(minutes: int) -> int:
    """Convert minutes to rounded-up hours."""
    return max(1, (max(1, int(minutes)) + 59) // 60)


def duration_text(minutes: int) -> str:
    """Format minutes as human-readable string ('2 ч 30 мин')."""
    minutes = max(1, int(minutes))
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} ч {rest} мин"
    if hours:
        return f"{hours} ч"
    return f"{rest} мин"
