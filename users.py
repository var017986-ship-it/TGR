"""Реестр пользователей бота, баны, обязательная подписка, донаты (Crypto Pay).

Хранится в bot.sqlite3:
  bot_users -> {uid: {...}}      все, кто писал боту
  admin     -> {"subscription": {...}, "donate": {...}, "donations": [...]}
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import config
import storage


LOCK = threading.RLock()
_TOUCH_CACHE: dict[int, float] = {}
TOUCH_EVERY_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def is_super_admin(user_id: int | str) -> bool:
    """Строгая проверка: только ID из ADMIN_IDS (.env)."""
    return str(user_id).isdigit() and int(str(user_id)) in config.ADMIN_IDS


# ═══════════════════════════════════════════════════════════════════════════
# Пользователи
# ═══════════════════════════════════════════════════════════════════════════
def all_users() -> dict[str, dict[str, Any]]:
    data = storage.read("bot_users")
    return data if isinstance(data, dict) else {}


def get_user(user_id: int | str) -> dict[str, Any]:
    return dict(all_users().get(str(user_id)) or {})


def update_user(user_id: int | str, **fields: Any) -> dict[str, Any]:
    with LOCK:
        data = all_users()
        user = data.setdefault(str(user_id), {"id": int(user_id), "first_seen": _iso(_now())})
        user.update(fields)
        storage.write("bot_users", data)
        return user


def touch(user: Any) -> None:
    """Регистрирует пользователя / обновляет last_seen (не чаще раза в 5 минут)."""
    uid = int(user.id)
    now = time.time()
    if now - _TOUCH_CACHE.get(uid, 0) < TOUCH_EVERY_SECONDS:
        return
    _TOUCH_CACHE[uid] = now
    name = " ".join(p for p in [getattr(user, "first_name", ""), getattr(user, "last_name", "")] if p)
    update_user(uid, name=name, username=getattr(user, "username", "") or "", last_seen=_iso(_now()), blocked=False)


def find_user(query: str) -> dict[str, Any] | None:
    q = (query or "").strip().lstrip("@").lower()
    if not q:
        return None
    users = all_users()
    if q.isdigit():
        return users.get(q) or {"id": int(q)}
    for user in users.values():
        if str(user.get("username", "")).lower() == q:
            return user
    return None


def is_banned(user_id: int | str) -> bool:
    return bool(get_user(user_id).get("banned"))


def set_banned(user_id: int | str, banned: bool, reason: str = "") -> None:
    update_user(user_id, banned=banned, ban_reason=reason if banned else "",
                banned_at=_iso(_now()) if banned else "")


def mark_blocked(user_id: int | str) -> None:
    update_user(user_id, blocked=True)


def broadcast_ids() -> list[int]:
    return [int(uid) for uid, u in all_users().items()
            if uid.isdigit() and not u.get("banned") and not u.get("blocked")]


def banned_users() -> list[dict[str, Any]]:
    return [u for u in all_users().values() if u.get("banned")]


def stats() -> dict[str, int]:
    users = all_users().values()
    now = _now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    def seen_after(u: dict, key: str, dt: datetime) -> bool:
        v = _parse(u.get(key))
        return bool(v and v >= dt)
    return {
        "total": len(users),
        "new_today": sum(1 for u in users if seen_after(u, "first_seen", day_start)),
        "active_24h": sum(1 for u in users if seen_after(u, "last_seen", now - timedelta(hours=24))),
        "banned": sum(1 for u in users if u.get("banned")),
        "blocked": sum(1 for u in users if u.get("blocked")),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Админ-настройки (подписка, донат)
# ═══════════════════════════════════════════════════════════════════════════
def admin_cfg() -> dict[str, Any]:
    data = storage.read("admin")
    if not isinstance(data, dict):
        data = {}
    sub = data.setdefault("subscription", {})
    sub.setdefault("enabled", False)
    sub.setdefault("channels", [])
    don = data.setdefault("donate", {})
    don.setdefault("token", "")
    don.setdefault("net", "main")
    don.setdefault("app_name", "")
    data.setdefault("donations", [])
    return data


def save_admin_cfg(data: dict[str, Any]) -> None:
    data["donations"] = list(data.get("donations", []))[-1000:]
    storage.write("admin", data)


# ── Обязательная подписка ────────────────────────────────────────────────
def sub_enabled() -> bool:
    sub = admin_cfg()["subscription"]
    return bool(sub["enabled"] and sub["channels"])


def sub_channels() -> list[dict[str, Any]]:
    return list(admin_cfg()["subscription"]["channels"])


def sub_set_enabled(value: bool) -> None:
    with LOCK:
        data = admin_cfg()
        data["subscription"]["enabled"] = bool(value)
        save_admin_cfg(data)


def sub_add_channel(chat_id: int, title: str, url: str) -> None:
    with LOCK:
        data = admin_cfg()
        chans = [c for c in data["subscription"]["channels"] if int(c["chat_id"]) != int(chat_id)]
        chans.append({"chat_id": int(chat_id), "title": title, "url": url})
        data["subscription"]["channels"] = chans
        save_admin_cfg(data)
    _SUB_OK.clear()


def sub_remove_channel(chat_id: int) -> None:
    with LOCK:
        data = admin_cfg()
        data["subscription"]["channels"] = [c for c in data["subscription"]["channels"]
                                            if int(c["chat_id"]) != int(chat_id)]
        if not data["subscription"]["channels"]:
            data["subscription"]["enabled"] = False
        save_admin_cfg(data)


def sub_clear() -> None:
    with LOCK:
        data = admin_cfg()
        data["subscription"] = {"enabled": False, "channels": []}
        save_admin_cfg(data)


_SUB_OK: dict[int, float] = {}
SUB_CACHE_SECONDS = 120


async def missing_channels(bot: Any, user_id: int) -> list[dict[str, Any]]:
    """Каналы, на которые пользователь не подписан. Ошибки проверки не блокируют пользователя."""
    if not sub_enabled():
        return []
    if time.time() - _SUB_OK.get(user_id, 0) < SUB_CACHE_SECONDS:
        return []
    missing = []
    for ch in sub_channels():
        try:
            member = await bot.get_chat_member(int(ch["chat_id"]), user_id)
            status = str(getattr(member, "status", "")).split(".")[-1].lower()
            if status in {"left", "kicked"} or (status == "restricted" and not getattr(member, "is_member", True)):
                missing.append(ch)
        except Exception:
            continue  # бот не админ в канале/канал удалён — не блокируем людей
    if not missing:
        _SUB_OK[user_id] = time.time()
    return missing


def reset_sub_cache(user_id: int | None = None) -> None:
    if user_id is None:
        _SUB_OK.clear()
    else:
        _SUB_OK.pop(user_id, None)


# ── Донаты через Crypto Pay (@CryptoBot) ─────────────────────────────────
CRYPTO_PAY_URLS = {"main": "https://pay.crypt.bot/api/", "test": "https://testnet-pay.crypt.bot/api/"}


def donate_enabled() -> bool:
    return bool(admin_cfg()["donate"].get("token"))


def _cp(method: str, params: dict[str, Any] | None = None, token: str | None = None, net: str | None = None) -> Any:
    don = admin_cfg()["donate"]
    token = token or don.get("token")
    net = net or don.get("net") or "main"
    if not token:
        raise RuntimeError("Crypto Pay токен не задан")
    resp = requests.post(CRYPTO_PAY_URLS[net] + method, json=params or {},
                         headers={"Crypto-Pay-API-Token": token}, timeout=15)
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Crypto Pay HTTP {resp.status_code}") from exc
    if not data.get("ok"):
        err = data.get("error") or {}
        raise RuntimeError(err.get("name") or err.get("message") or str(err) or "Crypto Pay error")
    return data["result"]


def donate_set_token(token: str) -> str:
    """Проверяет токен в основной и тестовой сети, сохраняет. Возвращает имя приложения."""
    last_exc: Exception | None = None
    for net in ("main", "test"):
        try:
            me = _cp("getMe", token=token, net=net)
        except Exception as exc:
            last_exc = exc
            continue
        with LOCK:
            data = admin_cfg()
            data["donate"].update(token=token, net=net, app_name=str(me.get("name", "")))
            save_admin_cfg(data)
        return f"{me.get('name', '')}{' (testnet)' if net == 'test' else ''}"
    raise RuntimeError(f"Токен не подошёл: {last_exc}")


def donate_disable() -> None:
    with LOCK:
        data = admin_cfg()
        data["donate"].update(token="", app_name="")
        save_admin_cfg(data)


def donate_create(user_id: int, amount_rub: int, bot_username: str = "") -> dict[str, Any]:
    params = {
        "currency_type": "fiat",
        "fiat": "RUB",
        "amount": str(int(amount_rub)),
        "description": "Благодарность разработчику бота ❤️",
        "hidden_message": "Спасибо за поддержку! ❤️",
        "payload": f"donate:{user_id}",
        "allow_comments": True,
        "allow_anonymous": True,
        "expires_in": 3600,
    }
    if bot_username:
        params.update(paid_btn_name="openBot", paid_btn_url=f"https://t.me/{bot_username}")
    inv = _cp("createInvoice", params)
    record = {
        "invoice_id": inv["invoice_id"],
        "user_id": int(user_id),
        "amount": int(amount_rub),
        "status": "active",
        "url": inv.get("bot_invoice_url") or inv.get("pay_url") or "",
        "created_at": _iso(_now()),
    }
    with LOCK:
        data = admin_cfg()
        data["donations"].append(record)
        save_admin_cfg(data)
    return record


def donate_refresh(invoice_id: int) -> str:
    """Запрашивает статус счёта, сохраняет, возвращает статус (active/paid/expired)."""
    res = _cp("getInvoices", {"invoice_ids": str(invoice_id)})
    items = res.get("items", []) if isinstance(res, dict) else res
    status = str(items[0].get("status")) if items else "expired"
    with LOCK:
        data = admin_cfg()
        for d in data["donations"]:
            if int(d["invoice_id"]) == int(invoice_id) and d.get("status") != "paid":
                d["status"] = status
                if status == "paid":
                    d["paid_at"] = _iso(_now())
        save_admin_cfg(data)
    return status


def donation(invoice_id: int) -> dict[str, Any] | None:
    for d in admin_cfg()["donations"]:
        if int(d["invoice_id"]) == int(invoice_id):
            return d
    return None


def pending_donations(max_age_minutes: int = 65) -> list[dict[str, Any]]:
    border = _now() - timedelta(minutes=max_age_minutes)
    return [d for d in admin_cfg()["donations"]
            if d.get("status") == "active" and (_parse(d.get("created_at")) or border) > border]


def mark_donation_notified(invoice_id: int) -> bool:
    """True, если уведомление ещё не отправлялось (и помечает как отправленное)."""
    with LOCK:
        data = admin_cfg()
        for d in data["donations"]:
            if int(d["invoice_id"]) == int(invoice_id):
                if d.get("notified"):
                    return False
                d["notified"] = True
                save_admin_cfg(data)
                return True
    return False


def donate_stats() -> tuple[int, int]:
    paid = [d for d in admin_cfg()["donations"] if d.get("status") == "paid"]
    return len(paid), sum(int(d.get("amount", 0)) for d in paid)
