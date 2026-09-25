"""Плагин Auto SMM: автоматическая накрутка через SMM-панели (API v2: twiboost, smmpanel и т.п.).

Сценарий:
  1. Покупатель оплачивает лот, название которого совпадает с SMM-лотом в настройках.
  2. Бот просит ссылку -> проверяет домен -> (опционально) просит подтвердить "+".
  3. Бот создаёт заказ в SMM-панели (action=add) и присылает ID.
  4. Фоновая проверка (action=status): при завершении — просит подтвердить заказ,
     при ошибке/отмене — делает возврат (если включён автовозврат).
  Команды покупателя в чате FunPay: "чек <id>", "рефилл <id>".

Все данные хранятся в настройках владельца (rental_service.user_settings) -> bot.sqlite3.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Callable
from urllib.parse import quote, urlparse

import requests

import rental_service as rent


logger = logging.getLogger("steam_rent.auto_smm")
LOCK = threading.RLock()
HTTP_TIMEOUT = 20

DEFAULT_DOMAINS = [
    "vk.com", "vk.ru", "t.me", "telegram.me", "instagram.com", "tiktok.com",
    "youtube.com", "youtu.be", "twitch.tv", "twitter.com", "x.com",
    "facebook.com", "ok.ru", "rutube.ru", "dzen.ru", "threads.net",
    "likee.video", "spotify.com", "soundcloud.com", "kick.com", "discord.gg",
]

DEFAULT_MESSAGES = {
    "after_payment": (
        "❤️ Спасибо за оплату!\n\n"
        "Отправьте ссылку на страницу/пост, куда нужна накрутка.\n"
        "Ссылка должна начинаться с https://"
    ),
    "ask_confirm": "✅ Ссылка принята: {link}\nЕсли всё верно, отправьте «+». Чтобы изменить — отправьте «-».",
    "after_start": (
        "🎉 Заказ запущен!\n\n"
        "🔢 ID заказа в сервисе: {smm_id}\n"
        "🔗 Ссылка: {link}\n"
        "📦 Количество: {quantity}\n\n"
        "Команды:\n"
        "🔍 чек {smm_id} — статус заказа\n"
        "🔄 рефилл {smm_id} — восстановить списания (если услуга поддерживает)"
    ),
    "completed": (
        "🎉 Ваш заказ выполнен!\n"
        "🔢 ID в сервисе: {smm_id}\n\n"
        "Пожалуйста, подтвердите заказ: https://funpay.com/orders/{order_id}/\n"
        "Будем благодарны за отзыв ⭐"
    ),
    "refunded": "❌ Заказ не удалось выполнить ({reason}). Средства возвращены.",
    "failed_no_refund": "❌ Заказ не удалось выполнить ({reason}). Продавец свяжется с вами.",
}

COMPLETED_STATUSES = {"completed", "done", "success"}
PARTIAL_STATUSES = {"partial"}
FAILED_STATUSES = {"failed", "error", "canceled", "cancelled", "refunded"}


# ═══════════════════════════════════════════════════════════════════════════
# Настройки
# ═══════════════════════════════════════════════════════════════════════════
def cfg() -> dict[str, Any]:
    settings = rent.user_settings()
    smm = settings.get("smm")
    if not isinstance(smm, dict):
        smm = {}
    smm.setdefault("enabled", False)
    smm.setdefault("confirm_link", True)
    smm.setdefault("auto_refund", True)
    smm.setdefault("services", {})      # {"1": {"api_url": ..., "api_key": ...}}
    smm.setdefault("lots", [])          # [{"id","name","service_id","quantity","service_number"}]
    smm.setdefault("domains", list(DEFAULT_DOMAINS))
    smm.setdefault("pending", {})       # order_id -> ожидание ссылки
    smm.setdefault("orders", [])        # запущенные заказы
    msgs = smm.get("messages") if isinstance(smm.get("messages"), dict) else {}
    for k, v in DEFAULT_MESSAGES.items():
        msgs.setdefault(k, v)
    smm["messages"] = msgs
    return smm


def save(smm: dict[str, Any]) -> None:
    smm["orders"] = list(smm.get("orders", []))[-500:]
    rent.update_user_settings(smm=smm)


def is_enabled() -> bool:
    return bool(cfg().get("enabled"))


def set_flag(name: str, value: bool) -> None:
    with LOCK:
        smm = cfg()
        smm[name] = bool(value)
        save(smm)


def set_service(number: str, api_url: str, api_key: str) -> None:
    with LOCK:
        smm = cfg()
        smm["services"][str(number)] = {"api_url": api_url.strip().rstrip("/"), "api_key": api_key.strip()}
        save(smm)


def delete_service(number: str) -> bool:
    with LOCK:
        smm = cfg()
        removed = smm["services"].pop(str(number), None) is not None
        save(smm)
        return removed


def add_lot(name: str, service_id: int, quantity: int, service_number: str = "1") -> dict[str, Any]:
    with LOCK:
        smm = cfg()
        ids = [int(l.get("id", 0)) for l in smm["lots"] if str(l.get("id", "")).isdigit()]
        lot = {
            "id": str(max(ids, default=0) + 1),
            "name": name.strip(),
            "service_id": int(service_id),
            "quantity": int(quantity),
            "service_number": str(service_number or "1"),
        }
        smm["lots"].append(lot)
        save(smm)
        return lot


def delete_lot(lot_id: str) -> bool:
    with LOCK:
        smm = cfg()
        before = len(smm["lots"])
        smm["lots"] = [l for l in smm["lots"] if str(l.get("id")) != str(lot_id)]
        save(smm)
        return len(smm["lots"]) != before


def set_domains(domains: list[str]) -> None:
    with LOCK:
        smm = cfg()
        smm["domains"] = [_norm_domain(d) for d in domains if _norm_domain(d)]
        save(smm)


def set_message(key: str, text: str) -> None:
    with LOCK:
        smm = cfg()
        smm["messages"][key] = text
        save(smm)


# ═══════════════════════════════════════════════════════════════════════════
# SMM API (стандарт Perfect Panel API v2)
# ═══════════════════════════════════════════════════════════════════════════
def _api(service_number: str, params: dict[str, Any]) -> dict[str, Any]:
    service = cfg()["services"].get(str(service_number))
    if not service:
        raise RuntimeError(f"SMM-сервис №{service_number} не настроен")
    data = {"key": service["api_key"], **params}
    resp = requests.post(service["api_url"], data=data, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    try:
        result = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Сервис вернул не JSON: {resp.text[:200]}") from exc
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(str(result["error"]))
    return result


def api_balance(service_number: str) -> str:
    data = _api(service_number, {"action": "balance"})
    return f"{data.get('balance', '?')} {data.get('currency', '')}".strip()


def api_add(service_number: str, service_id: int, link: str, quantity: int) -> str:
    data = _api(service_number, {"action": "add", "service": service_id, "link": link, "quantity": quantity})
    smm_id = data.get("order")
    if not smm_id:
        raise RuntimeError(f"Сервис не вернул ID заказа: {data}")
    return str(smm_id)


def api_status(service_number: str, smm_id: str) -> dict[str, Any]:
    return _api(service_number, {"action": "status", "order": smm_id})


def api_refill(service_number: str, smm_id: str) -> dict[str, Any]:
    return _api(service_number, {"action": "refill", "order": smm_id})


# ═══════════════════════════════════════════════════════════════════════════
# Сопоставление заказа и ссылки
# ═══════════════════════════════════════════════════════════════════════════
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def _norm_domain(value: str) -> str:
    value = (value or "").strip().lower()
    if "://" in value:
        value = urlparse(value).hostname or ""
    return value.strip("/. ").removeprefix("www.")


def match_lot(order_text: str) -> dict[str, Any] | None:
    text = _norm(order_text)
    if not text:
        return None
    # Самое длинное совпадение побеждает (чтобы "1000 подписчиков" не ловил "100 подписчиков").
    best = None
    for lot in cfg().get("lots", []):
        name = _norm(str(lot.get("name", "")))
        if name and name in text and (best is None or len(name) > len(_norm(best["name"]))):
            best = lot
    return best


def parse_amount(order: Any, order_text: str) -> int:
    amount = getattr(order, "amount", None)
    if isinstance(amount, int) and amount > 0:
        return amount
    m = re.search(r"(\d[\d\s]*)\s*шт", order_text or "", re.IGNORECASE)
    if m:
        try:
            return max(1, int(m.group(1).replace(" ", "")))
        except ValueError:
            pass
    return 1


def validate_link(link: str) -> tuple[bool, str]:
    if not link.startswith(("http://", "https://")):
        return False, "❌ Ссылка должна начинаться с https://"
    host = _norm_domain(urlparse(link).hostname or "")
    if not host:
        return False, "❌ Не удалось определить домен ссылки."
    domains = cfg().get("domains") or []
    if not domains:
        return True, ""
    for d in domains:
        d = _norm_domain(d)
        if host == d or host.endswith("." + d):
            return True, ""
    return False, "❌ Ссылки на этот сайт не принимаются. Отправьте ссылку на нужную соцсеть."


# ═══════════════════════════════════════════════════════════════════════════
# Обработка событий FunPay (вызывается из funpay_bridge в рабочем потоке)
# ═══════════════════════════════════════════════════════════════════════════
def handle_purchase(order: Any, order_id: str, order_text: str, buyer_id: str,
                    buyer_name: str, chat_id: str | int) -> dict[str, Any] | None:
    """Если заказ — SMM-лот: запоминает ожидание ссылки и возвращает сообщение покупателю."""
    if not is_enabled():
        return None
    lot = match_lot(order_text)
    if not lot:
        return None
    amount = parse_amount(order, order_text)
    with LOCK:
        smm = cfg()
        if order_id in smm["pending"] or any(o.get("order_id") == order_id for o in smm["orders"]):
            return {"handled": True, "message": ""}
        smm["pending"][order_id] = {
            "order_id": order_id,
            "buyer_id": str(buyer_id),
            "buyer_name": buyer_name,
            "chat_id": str(chat_id),
            "lot_id": lot["id"],
            "lot_name": lot["name"],
            "service_id": int(lot["service_id"]),
            "service_number": str(lot.get("service_number") or "1"),
            "quantity": int(lot["quantity"]) * amount,
            "step": "await_link",
            "link": "",
            "created_at": rent.iso(rent.now_utc()),
        }
        save(smm)
    rent.log_event("info", f"SMM: заказ #{order_id} ({lot['name']} x{amount}) ждёт ссылку")
    return {"handled": True, "message": smm["messages"]["after_payment"], "lot": lot, "amount": amount}


def _pending_for_chat(smm: dict[str, Any], chat_id: str, buyer_id: str) -> dict[str, Any] | None:
    items = [p for p in smm["pending"].values()
             if str(p.get("chat_id")) == str(chat_id) and (not buyer_id or str(p.get("buyer_id")) == str(buyer_id))]
    items.sort(key=lambda p: p.get("created_at", ""))
    return items[0] if items else None


def handle_customer_text(chat_id: str | int, buyer_id: str, text: str) -> dict[str, Any] | None:
    """Обрабатывает ссылку / подтверждение / команды «чек» и «рефилл».

    Возвращает {"handled": True, "message": str, "refund": order_id|None} или None.
    """
    raw = (text or "").strip()
    lowered = raw.lower()

    m = re.match(r"^(чек|check|статус)\s+(\d+)$", lowered)
    if m:
        return {"handled": True, "message": _customer_check(m.group(2), buyer_id)}
    m = re.match(r"^(рефилл|refill)\s+(\d+)$", lowered)
    if m:
        return {"handled": True, "message": _customer_refill(m.group(2), buyer_id)}

    if not is_enabled():
        return None
    with LOCK:
        smm = cfg()
        pending = _pending_for_chat(smm, str(chat_id), str(buyer_id))
        if not pending:
            return None

        if pending["step"] == "await_confirm":
            if lowered in {"+", "да", "yes", "ок", "ok"}:
                return _start_order(pending["order_id"])
            if lowered in {"-", "нет", "no"}:
                pending["step"] = "await_link"
                pending["link"] = ""
                save(smm)
                return {"handled": True, "message": "Хорошо, отправьте новую ссылку."}
            link_m = re.search(r"https?://\S+", raw)
            if not link_m:
                return {"handled": True, "message": "Отправьте «+» для подтверждения или «-», чтобы изменить ссылку."}
            # Покупатель прислал новую ссылку вместо подтверждения — примем её.
            pending["step"] = "await_link"

        link_m = re.search(r"https?://\S+", raw)
        if not link_m:
            return {"handled": True, "message": "❌ Не вижу ссылки. Отправьте ссылку, начинающуюся с https://"}
        link = link_m.group(0).rstrip(").,")
        ok, reason = validate_link(link)
        if not ok:
            return {"handled": True, "message": reason}
        pending["link"] = link
        if smm.get("confirm_link", True):
            pending["step"] = "await_confirm"
            save(smm)
            return {"handled": True, "message": smm["messages"]["ask_confirm"].format(link=link)}
        save(smm)
    return _start_order(pending["order_id"])


def _start_order(order_id: str) -> dict[str, Any]:
    with LOCK:
        smm = cfg()
        pending = smm["pending"].get(order_id)
        if not pending:
            return {"handled": True, "message": ""}
        try:
            smm_id = api_add(pending["service_number"], pending["service_id"], pending["link"], pending["quantity"])
        except Exception as exc:
            logger.exception("SMM add failed for %s", order_id)
            rent.log_event("error", f"SMM: не удалось создать заказ #{order_id}: {exc}")
            smm["pending"].pop(order_id, None)
            smm["orders"].append({**pending, "smm_id": "", "status": "error", "error": str(exc)[:300],
                                  "finished": True, "refunded": bool(smm.get("auto_refund"))})
            save(smm)
            reason = "ошибка сервиса"
            if smm.get("auto_refund"):
                return {"handled": True, "message": smm["messages"]["refunded"].format(reason=reason),
                        "refund": order_id, "admin": f"SMM: ошибка создания заказа #{order_id}: {exc}. Сделан возврат."}
            return {"handled": True, "message": smm["messages"]["failed_no_refund"].format(reason=reason),
                    "admin": f"SMM: ошибка создания заказа #{order_id}: {exc}. Нужен ручной разбор!"}
        smm["pending"].pop(order_id, None)
        record = {**pending, "smm_id": smm_id, "status": "pending", "finished": False,
                  "started_at": rent.iso(rent.now_utc()), "refunded": False}
        smm["orders"].append(record)
        save(smm)
    rent.log_event("info", f"SMM: заказ #{order_id} запущен, ID сервиса {smm_id}")
    return {
        "handled": True,
        "message": smm["messages"]["after_start"].format(smm_id=smm_id, link=record["link"], quantity=record["quantity"]),
        "admin": f"SMM: заказ #{order_id} запущен ({record['lot_name']}, {record['quantity']} шт.), ID {smm_id}",
    }


def _find_order_by_smm_id(smm_id: str) -> dict[str, Any] | None:
    for o in reversed(cfg().get("orders", [])):
        if str(o.get("smm_id")) == str(smm_id):
            return o
    return None


def _customer_check(smm_id: str, buyer_id: str) -> str:
    order = _find_order_by_smm_id(smm_id)
    if not order or (buyer_id and str(order.get("buyer_id")) != str(buyer_id)):
        return "❌ Заказ не найден."
    try:
        data = api_status(order["service_number"], smm_id)
    except Exception as exc:
        return f"❌ Не удалось проверить статус: {exc}"
    return (f"📊 Заказ {smm_id}\nСтатус: {data.get('status', '?')}\n"
            f"Стартовое значение: {data.get('start_count', '?')}\nОсталось: {data.get('remains', '?')}")


def _customer_refill(smm_id: str, buyer_id: str) -> str:
    order = _find_order_by_smm_id(smm_id)
    if not order or (buyer_id and str(order.get("buyer_id")) != str(buyer_id)):
        return "❌ Заказ не найден."
    try:
        data = api_refill(order["service_number"], smm_id)
    except Exception as exc:
        return f"❌ Рефилл недоступен: {exc}"
    if data.get("refill") or str(data.get("status")) in {"1", "true", "True"}:
        return "✅ Рефилл запущен."
    return f"❌ Рефилл отклонён: {data}"


# ═══════════════════════════════════════════════════════════════════════════
# Фоновая проверка статусов
# ═══════════════════════════════════════════════════════════════════════════
def check_orders_once(send: Callable[[str, str, str, str], None],
                      refund: Callable[[str], None],
                      notify_admin: Callable[[str], None]) -> None:
    """Проверяет незавершённые SMM-заказы текущего владельца."""
    smm = cfg()
    active = [o for o in smm.get("orders", []) if not o.get("finished") and o.get("smm_id")]
    for order in active:
        order_id = order["order_id"]
        try:
            data = api_status(order["service_number"], order["smm_id"])
        except Exception as exc:
            logger.warning("SMM status %s failed: %s", order["smm_id"], exc)
            continue
        status = str(data.get("status", "")).lower()
        remains = str(data.get("remains", ""))
        finished_ok = status in COMPLETED_STATUSES or (status in PARTIAL_STATUSES)
        failed = status in FAILED_STATUSES
        if not finished_ok and not failed:
            if status and status != order.get("status"):
                _update_order(order_id, status=status)
            continue

        msgs = smm["messages"]
        if finished_ok:
            text = msgs["completed"].format(smm_id=order["smm_id"], order_id=order_id)
            if status in PARTIAL_STATUSES:
                text += f"\n\n⚠️ Выполнено частично (не выполнено: {remains})."
            try:
                send(order["chat_id"], text, order.get("buyer_name", ""), order.get("buyer_id", ""))
            except Exception as exc:
                logger.warning("SMM completion notice failed: %s", exc)
                continue
            _update_order(order_id, status=status, finished=True)
            notify_admin(f"SMM: заказ #{order_id} выполнен ({status}).")
            rent.log_event("info", f"SMM: заказ #{order_id} выполнен ({status})")
            continue

        # failed
        reason = f"статус «{status}»"
        refunded = False
        if smm.get("auto_refund"):
            try:
                refund(order_id)
                refunded = True
            except Exception as exc:
                notify_admin(f"SMM: не удалось вернуть деньги по #{order_id}: {exc}")
        text = (msgs["refunded"] if refunded else msgs["failed_no_refund"]).format(reason=reason)
        try:
            send(order["chat_id"], text, order.get("buyer_name", ""), order.get("buyer_id", ""))
        except Exception:
            pass
        _update_order(order_id, status=status, finished=True, refunded=refunded)
        notify_admin(f"SMM: заказ #{order_id} не выполнен ({status}). Возврат: {'да' if refunded else 'нет'}.")
        rent.log_event("warn", f"SMM: заказ #{order_id} не выполнен ({status})")


def _update_order(order_id: str, **fields: Any) -> None:
    with LOCK:
        smm = cfg()
        for o in smm["orders"]:
            if o.get("order_id") == order_id:
                o.update(fields)
        save(smm)


def cancel_pending(order_id: str) -> bool:
    with LOCK:
        smm = cfg()
        removed = smm["pending"].pop(order_id, None) is not None
        save(smm)
        return removed


# ═══════════════════════════════════════════════════════════════════════════
# Тексты для Telegram-панели
# ═══════════════════════════════════════════════════════════════════════════
def panel_text() -> str:
    smm = cfg()
    orders = smm.get("orders", [])
    active = sum(1 for o in orders if not o.get("finished"))
    done = sum(1 for o in orders if o.get("finished") and not o.get("refunded"))
    on = lambda v: "✅" if v else "❌"
    return (
        "┌──────────────────────────────┐\n"
        "│         <b>AUTO SMM</b>             │\n"
        "└──────────────────────────────┘\n\n"
        f"Плагин: {on(smm['enabled'])}\n"
        f"Подтверждение ссылки: {on(smm['confirm_link'])}\n"
        f"Автовозврат при ошибке: {on(smm['auto_refund'])}\n\n"
        f"Сервисов (API): <b>{len(smm['services'])}</b>\n"
        f"SMM-лотов: <b>{len(smm['lots'])}</b>\n"
        f"Ждут ссылку: <b>{len(smm['pending'])}</b>\n"
        f"В работе: <b>{active}</b> · выполнено: <b>{done}</b>\n\n"
        "<i>Лот срабатывает, если его название встречается в описании заказа FunPay.</i>"
    )


def services_text() -> str:
    smm = cfg()
    lines = ["<b>SMM-сервисы (API)</b>\n"]
    if not smm["services"]:
        lines.append("Пока нет ни одного сервиса.")
    for num, s in sorted(smm["services"].items()):
        key = s.get("api_key", "")
        masked = key[:4] + "…" + key[-4:] if len(key) > 10 else "***"
        lines.append(f"<b>№{rent.esc(num)}</b>: {rent.esc(s.get('api_url'))}\n    ключ: <code>{rent.esc(masked)}</code>")
    lines.append(
        "\nДобавить/изменить — отправь сообщением:\n"
        "<code>1 https://twiboost.com/api/v2 ТВОЙ_API_КЛЮЧ</code>\n"
        "Удалить: <code>del 1</code>"
    )
    return "\n".join(lines)


def lots_text() -> str:
    smm = cfg()
    lines = ["<b>SMM-лоты</b>\n"]
    if not smm["lots"]:
        lines.append("Лотов нет.")
    for lot in smm["lots"]:
        lines.append(
            f"<b>#{lot['id']}</b> «{rent.esc(lot['name'])}»\n"
            f"    услуга {lot['service_id']} · {lot['quantity']} шт. за 1 покупку · сервис №{lot['service_number']}"
        )
    lines.append(
        "\nДобавить — отправь сообщением (через | ):\n"
        "<code>Подписчики Telegram 1000 | 1234 | 1000 | 1</code>\n"
        "= часть названия лота FunPay | ID услуги в панели | количество | № сервиса\n"
        "Удалить: <code>del 3</code>"
    )
    return "\n".join(lines)


def orders_text(limit: int = 15) -> str:
    smm = cfg()
    lines = ["<b>Последние SMM-заказы</b>\n"]
    for p in smm["pending"].values():
        lines.append(f"⏳ #{rent.esc(p['order_id'])} {rent.esc(p['lot_name'])} — ждёт {'подтверждения' if p['step']=='await_confirm' else 'ссылку'}")
    for o in list(reversed(smm["orders"]))[:limit]:
        icon = "✅" if o.get("finished") and not o.get("refunded") and o.get("status") != "error" else ("↩️" if o.get("refunded") else ("❌" if o.get("finished") else "🔄"))
        lines.append(f"{icon} #{rent.esc(o['order_id'])} · {rent.esc(o.get('lot_name'))} · {o.get('quantity')} шт. · ID {o.get('smm_id') or '—'} · {rent.esc(o.get('status'))}")
    if len(lines) == 1:
        lines.append("Заказов пока нет.")
    return "\n".join(lines)


def domains_text() -> str:
    domains = cfg().get("domains") or []
    return (
        "<b>Разрешённые домены ссылок</b>\n\n"
        + (", ".join(rent.esc(d) for d in domains) or "не ограничено (любые ссылки)")
        + "\n\nОтправь новый список через запятую, или <code>any</code> — принимать любые ссылки, "
          "или <code>default</code> — стандартный список."
    )


def balances_text() -> str:
    smm = cfg()
    if not smm["services"]:
        return "Сервисы не настроены."
    lines = ["<b>Баланс SMM-сервисов</b>\n"]
    for num in sorted(smm["services"]):
        try:
            lines.append(f"№{num}: <b>{rent.esc(api_balance(num))}</b>")
        except Exception as exc:
            lines.append(f"№{num}: ошибка — {rent.esc(exc)}")
    return "\n".join(lines)
