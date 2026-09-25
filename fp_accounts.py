"""FunPay-аккаунты пользователя: прокси, проверка golden key, слоты (до 3 на пользователя).

Каждый FunPay-аккаунт — отдельное рабочее пространство (owner_id):
  слот 1: "<tg_id>" (совместимо со старыми данными), слот 2: "<tg_id>_2", слот 3: "<tg_id>_3".
У каждого пространства свои лоты, Steam-аккаунты, аренды и настройки SMM.
"""
from __future__ import annotations

import re
from typing import Any

import requests

import rental_service as rent
import storage

try:
    from FunPayAPI import Account
    from FunPayAPI.common import exceptions as fp_exc
except Exception:  # pragma: no cover
    Account = None
    fp_exc = None


MAX_ACCOUNTS = 3
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_PROXY_RE = re.compile(r"^(?:(?P<scheme>https?|socks5h?|socks4)://)?(?P<rest>.+)$", re.IGNORECASE)


# ═══════════════════════════════════════════════════════════════════════════
# Прокси
# ═══════════════════════════════════════════════════════════════════════════
def normalize_proxy(text: str) -> str:
    """Принимает ip:port, ip:port:login:pass, login:pass@ip:port (+ схема http/socks5). Возвращает URL."""
    raw = (text or "").strip()
    m = _PROXY_RE.match(raw)
    if not raw or not m:
        raise ValueError("пустая строка")
    scheme = (m.group("scheme") or "http").lower()
    rest = m.group("rest").strip().rstrip("/")
    if "@" in rest:
        auth, host = rest.rsplit("@", 1)
        if ":" not in auth:
            raise ValueError("логин и пароль пишутся как login:password@ip:port")
        login, password = auth.split(":", 1)
    else:
        parts = rest.split(":")
        if len(parts) == 2:
            host, login, password = rest, "", ""
        elif len(parts) == 4:
            host, login, password = f"{parts[0]}:{parts[1]}", parts[2], parts[3]
        else:
            raise ValueError("неверный формат")
    if ":" not in host:
        raise ValueError("не указан порт")
    ip, port = host.rsplit(":", 1)
    if not ip or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("неверный адрес или порт")
    auth_part = f"{login}:{password}@" if login else ""
    return f"{scheme}://{auth_part}{ip}:{port}"


def proxy_dict(proxy_url: str | None) -> dict[str, str] | None:
    return {"http": proxy_url, "https": proxy_url} if proxy_url else None


def mask_proxy(proxy_url: str | None) -> str:
    if not proxy_url:
        return "без прокси"
    return re.sub(r"//([^:@/]+):([^@/]+)@", r"//\1:***@", proxy_url)


def check_proxy(proxy_url: str) -> str:
    """Проверяет, что через прокси открывается FunPay. Возвращает внешний IP (если удалось узнать)."""
    proxies = proxy_dict(proxy_url)
    try:
        resp = requests.get("https://funpay.com/", proxies=proxies, timeout=15, headers={"User-Agent": UA})
    except requests.exceptions.InvalidSchema as exc:
        raise RuntimeError("для SOCKS-прокси нужен пакет PySocks (pip install PySocks)") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"прокси не отвечает: {type(exc).__name__}") from exc
    if resp.status_code >= 500 or resp.status_code in {403, 407}:
        raise RuntimeError(f"FunPay через прокси вернул HTTP {resp.status_code}")
    try:
        return requests.get("https://api.ipify.org", proxies=proxies, timeout=8).text.strip()[:45]
    except requests.RequestException:
        return ""


# ═══════════════════════════════════════════════════════════════════════════
# Golden key
# ═══════════════════════════════════════════════════════════════════════════
def make_account(golden_key: str, proxy_url: str | None = None):
    if Account is None:
        raise RuntimeError("FunPayAPI не найдена рядом с bot.py")
    return Account(golden_key=golden_key.strip(), user_agent=UA, proxy=proxy_dict(proxy_url)).get()


def check_golden_key(golden_key: str, proxy_url: str | None = None) -> dict[str, Any]:
    key = (golden_key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{20,64}", key):
        raise ValueError("golden key — это строка из ~32 латинских букв и цифр (cookie golden_key с funpay.com)")
    try:
        account = make_account(key, proxy_url)
    except Exception as exc:
        if fp_exc is not None and isinstance(exc, fp_exc.UnauthorizedError):
            raise RuntimeError("FunPay не принял ключ: он неверный или устарел (перезайдите на сайте и скопируйте новый)") from exc
        raise RuntimeError(f"не удалось подключиться к FunPay: {exc}") from exc
    balance = ""
    if getattr(account, "total_balance", None) is not None:
        balance = f"{account.total_balance} {getattr(account, 'currency', '') or ''}".strip()
    return {
        "id": int(account.id),
        "username": str(account.username),
        "active_sales": int(getattr(account, "active_sales", 0) or 0),
        "balance": balance,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Слоты пользователя
# ═══════════════════════════════════════════════════════════════════════════
def slots(user_id: int | str) -> list[str]:
    return rent.workspace_slots(user_id)


def owns(user_id: int | str, ws: str) -> bool:
    return str(ws) in slots(user_id)


def accounts(user_id: int | str) -> list[dict[str, Any]]:
    """Подключённые аккаунты пользователя (только слоты с golden key)."""
    result = []
    for n, ws in enumerate(slots(user_id), start=1):
        s = rent.user_settings(ws)
        if (s.get("funpay_golden_key") or "").strip():
            result.append({
                "ws": ws,
                "slot": n,
                "username": s.get("funpay_username") or f"аккаунт {n}",
                "funpay_id": s.get("funpay_user_id"),
                "proxy": s.get("funpay_proxy") or "",
                "checked_at": s.get("funpay_checked_at") or "",
            })
    return result


def free_slot(user_id: int | str) -> str | None:
    used = {a["ws"] for a in accounts(user_id)}
    for ws in slots(user_id):
        if ws not in used:
            return ws
    return None


def active_ws(user_id: int | str) -> str:
    return rent.owner_context_for_user(user_id)


def set_active(user_id: int | str, ws: str) -> None:
    import users
    users.update_user(user_id, active_ws=str(ws))


def find_duplicate(funpay_id: int, except_ws: str = "") -> str | None:
    """Ищет рабочее пространство, где уже подключён этот же аккаунт FunPay."""
    settings = storage.read("settings")
    for ws, data in (settings.get("user_settings") or {}).items():
        if ws == except_ws or not isinstance(data, dict):
            continue
        if (data.get("funpay_golden_key") or "").strip() and str(data.get("funpay_user_id")) == str(funpay_id):
            return ws
    return None


def save_account(ws: str, golden_key: str, proxy_url: str, info: dict[str, Any]) -> None:
    rent.update_owner_settings(
        ws,
        funpay_golden_key=golden_key.strip(),
        funpay_proxy=proxy_url or "",
        funpay_username=info["username"],
        funpay_user_id=info["id"],
        funpay_checked_at=rent.iso(rent.now_utc()),
    )


def remove_account(ws: str) -> None:
    rent.update_owner_settings(ws, funpay_golden_key="", funpay_proxy="", funpay_username="",
                               funpay_user_id=None, funpay_checked_at="")


def recheck(ws: str) -> dict[str, Any]:
    s = rent.user_settings(ws)
    info = check_golden_key(s.get("funpay_golden_key") or "", s.get("funpay_proxy") or None)
    rent.update_owner_settings(ws, funpay_username=info["username"], funpay_user_id=info["id"],
                               funpay_checked_at=rent.iso(rent.now_utc()))
    return info
