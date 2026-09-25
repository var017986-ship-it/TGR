from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import fp_accounts
import rental_service as rent
import storage
import users
import reliability
from plugins import auto_smm, offline_one_time_code


API_ROOT = Path(__file__).resolve().parent / "FunPayAPI"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from FunPayAPI import Account, Runner
    from FunPayAPI.common.enums import MessageTypes, OrderStatuses
except Exception:  # pragma: no cover - handled at runtime with a clear log entry.
    Account = None
    Runner = None
    MessageTypes = None
    OrderStatuses = None


logger = logging.getLogger("steam_rent.funpay")
ACTIVE_BRIDGE: "FunPayBridge | None" = None
ACTIVE_BRIDGES: dict[str, "FunPayBridge"] = {}
AUTO_RAISE_INTERVAL_SECONDS = 60 * 60


def _normalize(value: str | None) -> str:
    text = (value or "").lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _is_customer_command_text(text: str) -> bool:
    command = (text or "").strip().lower().split(maxsplit=1)[0]
    return command in {
        "!menu", "/menu", "меню",
        "!code", "/code", "!код", "/код", "код",
        "!acc", "!account", "!акк", "!аккаунт", "/acc", "/account",
    }


def _settings() -> dict[str, Any]:
    settings = rent.user_settings()
    if not isinstance(settings, dict):
        settings = {"funpay_golden_key": ""}
    settings.setdefault("processed_order_ids", [])
    settings.setdefault("seen_funpay_message_ids", {})
    return settings


def _save_settings(settings: dict[str, Any]) -> None:
    rent.update_user_settings(**settings)


def _already_processed(order_id: str) -> bool:
    value = str(order_id)
    if value in {str(item) for item in _settings().get("processed_order_ids", [])}:
        return True
    for rental in rent.list_rentals():
        if value in {str(item) for item in rental.get("order_ids", [])}:
            return True
    return False


def _already_processed_display(order_id: str) -> bool:
    value = str(order_id).upper()
    if not value:
        return False
    for item in _settings().get("processed_order_ids", []):
        processed = str(item).upper()
        if processed == value or processed.startswith(value):
            return True
    for rental in rent.list_rentals():
        for item in rental.get("order_ids", []):
            processed = str(item).upper()
            if processed == value or processed.startswith(value):
                return True
    return False


def _mark_processed(order_id: str) -> None:
    settings = _settings()
    processed = [str(item) for item in settings.get("processed_order_ids", []) if str(item)]
    processed.append(str(order_id))
    settings["processed_order_ids"] = list(dict.fromkeys(processed))[-500:]
    processing = settings.get("processing_order_ids", {})
    if isinstance(processing, dict):
        processing.pop(str(order_id), None)
        settings["processing_order_ids"] = processing
    _save_settings(settings)


def _claim_order_processing(order_id: str) -> bool:
    order_key = str(order_id or "")
    if not order_key:
        return False
    with rent.exclusive_file_lock("funpay_order_processing") as got_lock:
        if not got_lock:
            return False
        settings = _settings()
        processed = {str(item) for item in settings.get("processed_order_ids", [])}
        if order_key in processed:
            return False
        now_ts = time.time()
        processing = settings.get("processing_order_ids", {})
        if not isinstance(processing, dict):
            processing = {}
        processing = {
            str(key): float(value)
            for key, value in processing.items()
            if str(key) and now_ts - float(value or 0) < 15 * 60
        }
        if order_key in processing:
            return False
        processing[order_key] = now_ts
        settings["processing_order_ids"] = processing
        _save_settings(settings)
        return True


def _release_order_processing(order_id: str) -> None:
    order_key = str(order_id or "")
    if not order_key:
        return
    with rent.exclusive_file_lock("funpay_order_processing") as got_lock:
        if not got_lock:
            return
        settings = _settings()
        processing = settings.get("processing_order_ids", {})
        if isinstance(processing, dict) and order_key in processing:
            processing.pop(order_key, None)
            settings["processing_order_ids"] = processing
            _save_settings(settings)


def _mark_seen_message(chat_id: str | int, message_id: str | int) -> None:
    chat_key = str(chat_id or "")
    msg_key = str(message_id or "")
    if not chat_key or not msg_key:
        return
    settings = _settings()
    seen: dict[str, list[str]] = settings.get("seen_funpay_message_ids", {})
    known = [str(item) for item in seen.get(chat_key, []) if str(item)]
    known.append(msg_key)
    seen[chat_key] = list(dict.fromkeys(known))[-300:]
    settings["seen_funpay_message_ids"] = seen
    _save_settings(settings)


def _claim_seen_message(chat_id: str | int, message_id: str | int) -> bool:
    chat_key = str(chat_id or "")
    msg_key = str(message_id or "")
    if not chat_key or not msg_key:
        return True
    with rent.exclusive_file_lock("funpay_seen_messages") as got_lock:
        if not got_lock:
            return False
        settings = _settings()
        seen: dict[str, list[str]] = settings.get("seen_funpay_message_ids", {})
        known = [str(item) for item in seen.get(chat_key, []) if str(item)]
        if msg_key in set(known):
            return False
        known.append(msg_key)
        seen[chat_key] = list(dict.fromkeys(known))[-300:]
        settings["seen_funpay_message_ids"] = seen
        _save_settings(settings)
        return True


def _claim_funpay_notification(
    chat_id: str | int,
    buyer_id: str | int,
    text: str,
    message_id: str | int | None = None,
) -> bool:
    chat_key = str(chat_id or "")
    buyer_key = str(buyer_id or "")
    msg_key = str(message_id or "")
    body = str(text or "")
    if not chat_key or not body:
        return True
    source = f"{chat_key}|{buyer_key}|msg:{msg_key}" if msg_key else f"{chat_key}|{buyer_key}|text:{body}|bucket:{int(time.time() // 30)}"
    key = hashlib.sha256(source.encode("utf-8", errors="ignore")).hexdigest()
    with rent.exclusive_file_lock("funpay_notifications") as got_lock:
        if not got_lock:
            return False
        settings = _settings()
        sent = [str(item) for item in settings.get("sent_funpay_notification_keys", []) if str(item)]
        if key in set(sent):
            return False
        sent.append(key)
        settings["sent_funpay_notification_keys"] = list(dict.fromkeys(sent))[-500:]
        _save_settings(settings)
        return True


def _max_seen_message_id(chat_id: str | int) -> int:
    known = _settings().get("seen_funpay_message_ids", {}).get(str(chat_id or ""), [])
    values = []
    for item in known:
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            continue
    return max(values, default=0)


def _message_type_is(message: Any, type_name: str) -> bool:
    if MessageTypes is None:
        return False
    message_type = getattr(message, "type", None)
    target = getattr(MessageTypes, type_name, None)
    if target is None:
        return False
    return (
        message_type == target
        or getattr(message_type, "name", "") == type_name
        or str(message_type).endswith(f".{type_name}")
    )


def auto_raise_lots_once(owner_id: str | int | None = None) -> dict[str, Any]:
    """Raise all FunPay lot categories for one owner and save the result."""
    if owner_id:
        rent.set_current_owner(owner_id)
    settings = _settings()
    now = rent.now_utc()
    next_at = now + timedelta(seconds=AUTO_RAISE_INTERVAL_SECONDS)
    result: dict[str, Any] = {
        "ok": False,
        "raised": [],
        "errors": [],
        "next_at": rent.iso(next_at),
    }

    key = (settings.get("funpay_golden_key") or "").strip()
    if not key:
        result["errors"].append("FunPay golden key не задан.")
    elif Account is None:
        result["errors"].append("FunPayAPI не найден.")
    else:
        try:
            account = fp_accounts.make_account(key, settings.get("funpay_proxy") or None)
            profile = account.get_user(account.id)
            categories: dict[str, Any] = {}
            for subcategory in profile.get_sorted_lots(2).keys():
                category = getattr(subcategory, "category", None)
                category_id = str(getattr(category, "id", "") or "")
                if category_id and category_id not in categories:
                    categories[category_id] = category

            if not categories:
                result["errors"].append("На FunPay не найдено активных лотов для поднятия.")

            for category_id, category in categories.items():
                category_name = str(getattr(category, "name", "") or category_id)
                try:
                    wait_time = account.raise_lots(int(category_id))
                    result["raised"].append(category_name)
                    if wait_time:
                        result.setdefault("wait_times", {})[category_name] = int(wait_time)
                    time.sleep(1)
                except Exception as exc:
                    wait_time = getattr(exc, "wait_time", None)
                    message = getattr(exc, "error_message", None) or str(exc)
                    if wait_time:
                        message = f"{message} (можно будет повторить через {wait_time} сек.)"
                    result["errors"].append(f"{category_name}: {message}")
            result["ok"] = bool(result["raised"]) and not result["errors"]
        except Exception as exc:
            result["errors"].append(str(exc))

    summary_parts = []
    if result["raised"]:
        summary_parts.append("Поднято: " + ", ".join(result["raised"]))
    if result["errors"]:
        summary_parts.append("Ошибки: " + " | ".join(result["errors"][:5]))
    summary = "\n".join(summary_parts) or "Нет результата."
    rent.update_user_settings(
        auto_raise_last_at=rent.iso(now),
        auto_raise_next_at=result["next_at"],
        auto_raise_last_result=summary[:1000],
        auto_raise_last_error=(" | ".join(result["errors"])[:1000] if result["errors"] else ""),
    )
    rent.log_event("info" if result["raised"] else "warn", f"Auto raise lots: {summary}")
    return result


class FunPayBridge:
    def __init__(self, bot: Bot | None, loop: asyncio.AbstractEventLoop | None, owner_id: str | int | None = None):
        self.bot = bot
        self.loop = loop
        self.owner_id = str(owner_id or rent.current_owner_id() or "")
        self.account = None
        self.lock = threading.RLock()
        self.my_lots_cache: dict[int, tuple[float, list[Any]]] = {}
        self.stop_event = threading.Event()

    def activate_owner(self) -> None:
        if self.owner_id:
            rent.set_current_owner(self.owner_id)

    def notification_recipient_ids(self) -> list[int]:
        tid = rent.telegram_id_for_owner(self.owner_id)
        return [tid] if tid else []

    def notify_admins(self, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        if self.bot is None or self.loop is None:
            return
        safe = rent.esc(text)
        tid = rent.telegram_id_for_owner(self.owner_id)
        if tid and len(fp_accounts.accounts(tid)) > 1:
            name = rent.user_settings(self.owner_id).get("funpay_username") or self.owner_id
            safe = f"<b>[{rent.esc(name)}]</b> {safe}"
        for admin_id in self.notification_recipient_ids():
            asyncio.run_coroutine_threadsafe(
                self.bot.send_message(admin_id, safe, reply_markup=reply_markup),
                self.loop,
            )

    def notify_funpay_message(
        self,
        chat_id: str | int,
        buyer_name: str,
        buyer_id: str,
        text: str,
        message_id: str | int | None = None,
    ) -> None:
        if not _claim_funpay_notification(chat_id, buyer_id, text, message_id=message_id):
            return
        chat_key = str(chat_id or "")
        keyboard = None
        if chat_key:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Перейти в чат",
                            url=f"https://funpay.com/chat/?node={chat_key}",
                        ),
                        InlineKeyboardButton(
                            text="Ответить",
                            callback_data=f"fp_reply:{chat_key}:{self.owner_id}",
                        ),
                    ]
                ]
            )
        self.notify_admins(f"FunPay сообщение от {buyer_name} ({buyer_id}):\n{text}", reply_markup=keyboard)

    def connect(self, scan_orders: bool = True, notify: bool = True) -> None:
        self.activate_owner()
        if Account is None or Runner is None:
            raise RuntimeError("FunPayAPI не найдена. Проверь папку FunPayAPI рядом с bot.py.")
        key = (_settings().get("funpay_golden_key") or "").strip()
        if not key:
            raise RuntimeError("FunPay golden key не задан в боте.")
        self.account = fp_accounts.make_account(key, _settings().get("funpay_proxy") or None)
        if self.account is None:
            raise RuntimeError("FunPay не подключился: API вернул пустой аккаунт.")
        logger.info("FunPay connected as %s", getattr(self.account, "username", "unknown"))
        reliability.record_health(self.owner_id, username=getattr(self.account, "username", ""),
                                  last_connected=reliability._iso(), last_error="")
        rent.log_event("info", f"FunPay подключен: {getattr(self.account, 'username', 'unknown')}")
        if notify:
            self.notify_admins("FunPay подключен, слушаю новые покупки и сообщения.")
        if scan_orders:
            self.scan_recent_paid_orders()

    def ensure_connected(self) -> None:
        if self.account is not None:
            return
        with self.lock:
            if self.account is None:
                self.connect(scan_orders=False, notify=False)

    def listen_forever(self) -> None:
        self.activate_owner()
        self.connect()
        runner = Runner(self.account)
        threading.Thread(target=runner.loop, daemon=True).start()
        logger.info("FunPay runner loop started")
        for event in runner.listen(requests_delay=5, ignore_exceptions=True):
            if self.stop_event.is_set():
                logger.info("FunPay listener for %s stopped (key/proxy changed or account removed)", self.owner_id)
                return
            self.handle_event(event)

    def handle_event(self, event: Any) -> None:
        self.activate_owner()
        name = type(event).__name__
        if name in {"NewOrderEvent", "OrderStatusChangedEvent"}:
            self.handle_order(event.order, recent_initial=False)
        elif name == "InitialOrderEvent":
            self.handle_order(event.order, recent_initial=True)
        elif name == "NewMessageEvent":
            self.handle_message(event.message)

    def handle_order(self, order: Any, recent_initial: bool) -> None:
        if not self._is_paid_order(order):
            return
        order_id = str(getattr(order, "id", ""))
        if not order_id or _already_processed(order_id):
            return
        if recent_initial and not self._is_recent_order(order):
            return
        if not _claim_order_processing(order_id):
            return

        try:
            with self.lock:
                full_order = self.account.get_order(order_id)
            if full_order is not None and self._is_paid_order(full_order):
                order = full_order
        except Exception as exc:
            logger.warning("Cannot load full FunPay order %s before processing: %s", order_id, exc)

        if self.try_handle_smm_order(order, order_id):
            return

        # Режим «только Auto SMM»: заказы, не привязанные к SMM-лотам, бот не трогает.
        # Помечаем обработанным, чтобы сканер не присылал это уведомление повторно.
        _mark_processed(order_id)
        rent.log_event("info", f"Заказ FunPay #{order_id} не относится к SMM-лотам, пропущен.")
        self.notify_admins(
            f"Заказ #{order_id} не привязан ни к одному SMM-лоту — обработай его вручную на FunPay."
        )
        return

        lot_id = self.match_lot_id(order)
        if not lot_id:
            lot_id = self.resolve_lot_id(order)
        if not lot_id:
            text = f"Не смог сопоставить заказ #{order_id} с лотом: {getattr(order, 'description', '')}"
            logger.warning(text)
            rent.log_event("warn", text)
            self.notify_admins(text)
            _release_order_processing(order_id)
            return

        buyer_id = str(getattr(order, "buyer_id", "") or "")
        buyer_name = str(getattr(order, "buyer_username", "") or buyer_id or "покупатель")
        chat_id = getattr(order, "chat_id", "") or ""
        offline_result = offline_one_time_code.handle_purchase(
            lot_id=str(lot_id),
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            order_id=order_id,
            chat_id=chat_id,
        )
        if offline_result and offline_result.get("handled"):
            try:
                self.send_funpay_message(chat_id, offline_result.get("message", ""), buyer_name, buyer_id)
                if offline_result.get("refund"):
                    self.refund(order_id)
                _mark_processed(order_id)
                logger.info("Processed FunPay offline order %s", order_id)
                rent.log_event("info", f"Оффлайн-заказ FunPay #{order_id} обработан.")
                self.notify_admins(f"Оффлайн-заказ #{order_id} обработан.")
            except Exception as exc:
                _release_order_processing(order_id)
                logger.exception("Cannot process FunPay offline order %s", order_id)
                rent.log_event("error", f"Ошибка обработки оффлайн-заказа #{order_id}: {exc}")
                self.notify_admins(f"Ошибка обработки оффлайн-заказа #{order_id}: {exc}")
            return

        result = rent.allocate_or_extend(
            lot_id=str(lot_id),
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            order_id=order_id,
            chat_id=chat_id,
            order_title=self.order_title(order),
        )
        message = result.get("message", "")

        try:
            if result.get("ok") and chat_id:
                offline_one_time_code.deactivate_chat_sales(chat_id)
            self.send_funpay_message(chat_id, message, buyer_name, buyer_id)
            if result.get("refund"):
                self.refund(order_id)
            _mark_processed(order_id)
            logger.info("Processed FunPay order %s as %s", order_id, result.get("type"))
            rent.log_event("info", f"Заказ FunPay #{order_id} обработан: {result.get('type')}")
            self.notify_admins(f"Заказ #{order_id} обработан: {result.get('type')}.")
        except Exception as exc:
            _release_order_processing(order_id)
            logger.exception("Cannot process FunPay order %s", order_id)
            rent.log_event("error", f"Ошибка обработки заказа #{order_id}: {exc}")
            self.notify_admins(f"Ошибка обработки заказа #{order_id}: {exc}")

    def scan_recent_paid_orders(self) -> None:
        self.ensure_connected()
        try:
            orders = self.get_recent_paid_sales()
            reliability.record_health(self.owner_id, last_paid_scan=reliability._iso(), last_error="")
        except Exception as exc:
            reliability.record_health(self.owner_id, last_error=str(exc))
            logger.exception("Cannot scan paid FunPay orders")
            rent.log_event("error", f"Не смог проверить оплаченные заказы FunPay: {exc}")
            return

        for order in orders:
            if _already_processed(str(getattr(order, "id", ""))):
                continue
            if not self._is_recent_order(order):
                continue
            logger.info("Found recent paid FunPay order %s during startup scan", getattr(order, "id", ""))
            self.handle_order(order, recent_initial=False)

    def get_recent_paid_sales(self) -> list[Any]:
        self.ensure_connected()
        with self.lock:
            _, orders, _, _ = self.account.get_sales(
                include_paid=True,
                include_closed=True,
                include_refunded=False,
            )
        return list(orders or [])

    def find_paid_order_by_display_id(self, display_order_id: str, chat_id: str | int = "") -> Any | None:
        value = str(display_order_id).upper()
        if not value:
            return None
        chat_value = str(chat_id or "")
        for order in self.get_recent_paid_sales():
            full_id = str(getattr(order, "id", "") or "").upper()
            if not (full_id == value or full_id.startswith(value)):
                continue
            order_chat_id = str(getattr(order, "chat_id", "") or "")
            if chat_value and order_chat_id and order_chat_id != chat_value:
                continue
            if not self._is_paid_order(order):
                continue
            return order
        return None

    def handle_message(self, message: Any, *, already_claimed: bool = False) -> None:
        chat_id_for_seen = getattr(message, "chat_id", "") or ""
        message_id_for_seen = getattr(message, "id", "") or ""
        if not already_claimed and not _claim_seen_message(chat_id_for_seen, message_id_for_seen):
            return
        if getattr(message, "author_id", None) == getattr(self.account, "id", None):
            return
        if getattr(message, "by_bot", False):
            return

        text = (getattr(message, "text", "") or "").strip()
        image_link = getattr(message, "image_link", "") or ""
        image_name = getattr(message, "image_name", "") or "изображение"
        if not text and image_link:
            text = f"[{image_name}] {image_link}"
        if not text:
            _mark_seen_message(getattr(message, "chat_id", ""), getattr(message, "id", ""))
            return
        if _message_type_is(message, "ORDER_PURCHASED"):
            self.handle_purchase_message(message)
            _mark_seen_message(getattr(message, "chat_id", ""), getattr(message, "id", ""))
            return
        if _message_type_is(message, "NEW_FEEDBACK"):
            self.handle_review_message(message)
            _mark_seen_message(getattr(message, "chat_id", ""), getattr(message, "id", ""))
            return
        if _message_type_is(message, "FEEDBACK_DELETED"):
            self.handle_review_deleted_message(message)
            _mark_seen_message(getattr(message, "chat_id", ""), getattr(message, "id", ""))
            return
        buyer_id = str(getattr(message, "author_id", "") or "")
        buyer_name = str(getattr(message, "author", "") or getattr(message, "chat_name", "") or buyer_id)
        chat_id = getattr(message, "chat_id", "") or ""
        if self.process_customer_text(chat_id, buyer_id, buyer_name, text, notify_unknown=True, message_id=message_id_for_seen):
            _mark_seen_message(chat_id, getattr(message, "id", ""))

    def handle_purchase_message(self, message: Any) -> None:
        match = re.search(r"#([A-Z0-9]+)", getattr(message, "text", "") or "")
        if not match:
            return
        order_id = match.group(1)
        buyer_name = str(getattr(message, "author", "") or getattr(message, "chat_name", "") or "покупатель")
        self.notify_admins(f"FunPay покупка от {buyer_name}: заказ #{order_id}\n{getattr(message, 'text', '') or ''}")
        if _already_processed_display(order_id):
            return
        order = None
        try:
            with self.lock:
                order = self.account.get_order(order_id)
        except Exception:
            order = self.find_paid_order_by_display_id(order_id, getattr(message, "chat_id", "") or "")
        if order is None:
            logger.warning("Cannot find full FunPay order for purchase message %s", order_id)
            self.notify_admins(f"Не смог загрузить заказ #{order_id} из FunPay. Проверьте заказ вручную.")
            return
        try:
            self.handle_order(order, recent_initial=False)
            logger.info("Processed FunPay purchase message for order %s as %s", order_id, getattr(order, "id", ""))
        except Exception as exc:
            logger.exception("Cannot process FunPay purchase message %s", order_id)
            rent.log_event("error", f"Не обработал системное сообщение покупки FunPay #{order_id}: {exc}")

    def handle_review_message(self, message: Any) -> None:
        match = re.search(r"#([A-Z0-9]+)", getattr(message, "text", "") or "")
        if not match:
            return
        order_id = match.group(1)
        try:
            order = None
            try:
                with self.lock:
                    order = self.account.get_order(order_id)
            except Exception:
                order = self.find_paid_order_by_display_id(order_id, getattr(message, "chat_id", "") or "")
            if order is None:
                logger.warning("Cannot find full FunPay order for review message %s", order_id)
                return
            review = getattr(order, "review", None)
            if review is None:
                return
            if getattr(review, "author", None) == getattr(self.account, "username", None):
                return
            stars = int(getattr(review, "stars", None) or 5)
            result = rent.apply_review_bonus(order_id, stars) or {}
            if result.get("ok") and result.get("chat_id") and result.get("message"):
                self.send_funpay_message(
                    result["chat_id"],
                    result["message"],
                    result.get("buyer_name", ""),
                    result.get("buyer_id", ""),
                )
                logger.info("Processed FunPay review for order %s as %s", order_id, result.get("reason") or "bonus")
        except Exception as exc:
            logger.exception("Cannot process FunPay review %s", order_id)
            rent.log_event("error", f"Не обработал отзыв FunPay по заказу {order_id}: {exc}")

    def handle_review_deleted_message(self, message: Any) -> None:
        match = re.search(r"#([A-Z0-9]+)", getattr(message, "text", "") or "")
        if not match:
            return
        order_id = match.group(1)
        try:
            result = rent.cancel_review_bonus(order_id) or {}
            if result.get("ok") and result.get("chat_id") and result.get("message"):
                self.send_funpay_message(
                    result["chat_id"],
                    result["message"],
                    result.get("buyer_name", ""),
                    result.get("buyer_id", ""),
                )
                logger.info("Cancelled FunPay review bonus for order %s", order_id)
        except Exception as exc:
            logger.exception("Cannot process deleted FunPay review %s", order_id)
            rent.log_event("error", f"Не обработал удаление отзыва FunPay по заказу {order_id}: {exc}")

    def process_customer_text(
        self,
        chat_id: str | int,
        buyer_id: str,
        buyer_name: str,
        text: str,
        notify_unknown: bool = False,
        message_id: str | int | None = None,
    ) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        rent.log_message(buyer_id, "in", text)

        parts = lowered.split(maxsplit=1)
        command = parts[0] if parts else ""
        selector = parts[1] if len(parts) > 1 else None

        # Плагин Auto SMM: ссылка, подтверждение «+», команды «чек»/«рефилл»
        if self.try_handle_smm_text(chat_id, buyer_id, buyer_name, text):
            return True

        # Steam rental and offline account commands are disabled in SMM-only mode.
        self.notify_funpay_message(chat_id, buyer_name, buyer_id, text, message_id=message_id)
        return True

    def scan_active_chat_commands(self) -> None:
        if self.account is None:
            return
        rentals = [rental for rental in rent.list_rentals() if rental.get("chat_id")]
        if not rentals:
            return

        settings = _settings()
        seen: dict[str, list[str]] = settings.get("seen_funpay_message_ids", {})
        changed = False

        scanned_chat_ids: set[str] = set()
        for rental in rentals:
            chat_id = str(rental.get("chat_id", ""))
            if not chat_id or chat_id in scanned_chat_ids:
                continue
            scanned_chat_ids.add(chat_id)
            buyer_id = str(rental.get("buyer_id", ""))
            buyer_name = str(rental.get("buyer_name", "") or buyer_id)
            known_ids = {str(item) for item in seen.get(chat_id, [])}
            try:
                with self.lock:
                    messages = self.account.get_chat_history(chat_id, interlocutor_username=buyer_name)
            except Exception as exc:
                logger.exception("Cannot scan FunPay chat %s", chat_id)
                rent.log_event("error", f"Не смог проверить чат FunPay {chat_id}: {exc}")
                continue

            for message in sorted(messages, key=lambda item: int(getattr(item, "id", 0) or 0)):
                message_id = str(getattr(message, "id", ""))
                if not message_id or message_id in known_ids:
                    continue
                if not _claim_seen_message(chat_id, message_id):
                    known_ids.add(message_id)
                    changed = True
                    continue
                known_ids.add(message_id)
                changed = True
                text = (getattr(message, "text", "") or "").strip()
                if getattr(message, "author_id", None) == getattr(self.account, "id", None):
                    known_ids.add(message_id)
                    changed = True
                    continue
                if _message_type_is(message, "ORDER_PURCHASED"):
                    try:
                        self.handle_purchase_message(message)
                        logger.info("Processed FunPay purchase from chat %s", chat_id)
                    except Exception as exc:
                        logger.exception("Cannot process FunPay purchase")
                        rent.log_event("error", f"Не обработал покупку FunPay в чате {chat_id}: {exc}")
                    known_ids.add(message_id)
                    changed = True
                    continue
                if _message_type_is(message, "NEW_FEEDBACK"):
                    try:
                        self.handle_review_message(message)
                        logger.info("Processed FunPay review from chat %s", chat_id)
                    except Exception as exc:
                        logger.exception("Cannot process FunPay review")
                        rent.log_event("error", f"Не обработал отзыв FunPay в чате {chat_id}: {exc}")
                    known_ids.add(message_id)
                    changed = True
                    continue
                if _message_type_is(message, "FEEDBACK_DELETED"):
                    try:
                        self.handle_review_deleted_message(message)
                        logger.info("Processed deleted FunPay review from chat %s", chat_id)
                    except Exception as exc:
                        logger.exception("Cannot process deleted FunPay review")
                        rent.log_event("error", f"Не обработал удаление отзыва FunPay в чате {chat_id}: {exc}")
                    known_ids.add(message_id)
                    changed = True
                    continue
                if getattr(message, "author_id", None) == 0:
                    known_ids.add(message_id)
                    changed = True
                    continue
                if getattr(message, "by_bot", False):
                    known_ids.add(message_id)
                    changed = True
                    continue
                if _is_customer_command_text(text):
                    actual_buyer_id = str(getattr(message, "author_id", "") or buyer_id)
                    actual_buyer_name = str(getattr(message, "author", "") or buyer_name)
                    try:
                        self.process_customer_text(chat_id, actual_buyer_id, actual_buyer_name, text, message_id=message_id)
                        logger.info("Processed FunPay command %s from chat %s", text, chat_id)
                    except Exception as exc:
                        logger.exception("Cannot process FunPay command %s", text)
                        rent.log_event("error", f"Не обработал команду {text} в FunPay: {exc}")
                        continue
                elif text:
                    actual_buyer_id = str(getattr(message, "author_id", "") or buyer_id)
                    actual_buyer_name = str(getattr(message, "author", "") or buyer_name)
                    rent.log_message(actual_buyer_id, "in", text)
                    self.notify_funpay_message(chat_id, actual_buyer_name, actual_buyer_id, text, message_id=message_id)
                    logger.info("Forwarded FunPay message from chat %s", chat_id)
                known_ids.add(message_id)
                changed = True
            seen[chat_id] = list(known_ids)[-300:]

        if changed:
            settings["seen_funpay_message_ids"] = seen
            _save_settings(settings)

    def scan_recent_chat_messages(self, limit: int = 50) -> None:
        self.ensure_connected()
        try:
            with self.lock:
                chat_map = self.account.get_chats(update=True)
        except Exception as exc:
            reliability.record_health(self.owner_id, last_error=str(exc))
            logger.exception("Cannot load FunPay chats")
            rent.log_event("error", f"Не смог загрузить список чатов FunPay: {exc}")
            return

        chats = list((chat_map or {}).values())[:limit]
        settings = _settings()
        seen: dict[str, list[str]] = settings.get("seen_funpay_message_ids", {})
        changed = False

        for chat in chats:
            chat_id = str(getattr(chat, "id", "") or "")
            if not chat_id:
                continue
            chat_name = str(getattr(chat, "name", "") or "")
            node_msg_id = int(getattr(chat, "node_msg_id", 0) or 0)
            known_ids = {str(item) for item in seen.get(chat_id, [])}
            max_seen = _max_seen_message_id(chat_id)

            if not known_ids and not getattr(chat, "unread", False):
                if node_msg_id:
                    seen[chat_id] = [str(node_msg_id)]
                    changed = True
                continue

            last_message_id = max_seen
            if node_msg_id and last_message_id and node_msg_id <= last_message_id:
                if str(node_msg_id) not in known_ids:
                    known_ids.add(str(node_msg_id))
                    seen[chat_id] = list(known_ids)[-300:]
                    changed = True
                continue

            try:
                with self.lock:
                    messages = self.account.get_chat_history(
                        int(chat_id) if chat_id.isdigit() else chat_id,
                        last_message_id=last_message_id or None,
                        interlocutor_username=chat_name or None,
                    )
            except Exception as exc:
                logger.exception("Cannot scan recent FunPay chat %s", chat_id)
                rent.log_event("error", f"Не смог проверить чат FunPay {chat_id}: {exc}")
                continue

            for message in sorted(messages, key=lambda item: int(getattr(item, "id", 0) or 0)):
                message_id = str(getattr(message, "id", "") or "")
                if not message_id or message_id in known_ids:
                    continue
                if last_message_id and int(message_id) <= last_message_id:
                    known_ids.add(message_id)
                    changed = True
                    continue
                if not _claim_seen_message(chat_id, message_id):
                    known_ids.add(message_id)
                    changed = True
                    continue
                known_ids.add(message_id)
                changed = True
                if getattr(message, "author_id", None) == getattr(self.account, "id", None):
                    known_ids.add(message_id)
                    changed = True
                    continue
                if getattr(message, "by_bot", False):
                    known_ids.add(message_id)
                    changed = True
                    continue

                try:
                    self.handle_message(message, already_claimed=True)
                    logger.info("Forwarded recent FunPay message %s from chat %s", message_id, chat_id)
                except Exception as exc:
                    logger.exception("Cannot process recent FunPay message %s", message_id)
                    rent.log_event("error", f"Не обработал сообщение FunPay {message_id} в чате {chat_id}: {exc}")
                    continue
                known_ids.add(message_id)
                changed = True

            seen[chat_id] = list(known_ids)[-300:]

        if changed:
            settings["seen_funpay_message_ids"] = seen
            _save_settings(settings)
        reliability.record_health(self.owner_id, last_message_scan=reliability._iso(), last_error="")

    def scan_active_rental_commands_only(self) -> None:
        if self.account is None:
            return
        current = rent.now_utc()
        rentals = [
            rental
            for rental in rent.list_rentals()
            if rental.get("status") == "active"
            and rental.get("chat_id")
            and (rent.parse_dt(rental.get("ends_at")) or current) > current
        ]
        rentals.extend(offline_one_time_code.active_sales_for_scan())
        if not rentals:
            return

        settings = _settings()
        seen: dict[str, list[str]] = settings.get("seen_funpay_message_ids", {})
        changed = False

        scanned_chat_ids: set[str] = set()
        for rental in rentals:
            chat_id = str(rental.get("chat_id", ""))
            if not chat_id or chat_id in scanned_chat_ids:
                continue
            scanned_chat_ids.add(chat_id)
            buyer_id = str(rental.get("buyer_id", ""))
            buyer_name = str(rental.get("buyer_name", "") or buyer_id)
            known_ids = {str(item) for item in seen.get(chat_id, [])}
            try:
                with self.lock:
                    messages = self.account.get_chat_history(chat_id, interlocutor_username=buyer_name or None)
            except Exception as exc:
                logger.exception("Cannot scan active FunPay commands in chat %s", chat_id)
                rent.log_event("error", f"Не смог проверить команды FunPay в чате {chat_id}: {exc}")
                continue

            for message in sorted(messages, key=lambda item: int(getattr(item, "id", 0) or 0)):
                message_id = str(getattr(message, "id", "") or "")
                if not message_id or message_id in known_ids:
                    continue
                text = (getattr(message, "text", "") or "").strip()

                if getattr(message, "author_id", None) == getattr(self.account, "id", None):
                    known_ids.add(message_id)
                    changed = True
                    continue
                if _message_type_is(message, "NEW_FEEDBACK"):
                    if not _claim_seen_message(chat_id, message_id):
                        known_ids.add(message_id)
                        changed = True
                        continue
                    known_ids.add(message_id)
                    changed = True
                    try:
                        self.handle_review_message(message)
                        logger.info("Processed active FunPay review from chat %s", chat_id)
                    except Exception as exc:
                        logger.exception("Cannot process active FunPay review")
                        rent.log_event("error", f"Не обработал отзыв FunPay в чате {chat_id}: {exc}")
                    known_ids.add(message_id)
                    changed = True
                    continue
                if _message_type_is(message, "FEEDBACK_DELETED"):
                    if not _claim_seen_message(chat_id, message_id):
                        known_ids.add(message_id)
                        changed = True
                        continue
                    known_ids.add(message_id)
                    changed = True
                    try:
                        self.handle_review_deleted_message(message)
                        logger.info("Processed active deleted FunPay review from chat %s", chat_id)
                    except Exception as exc:
                        logger.exception("Cannot process active deleted FunPay review")
                        rent.log_event("error", f"Не обработал удаление отзыва FunPay в чате {chat_id}: {exc}")
                    known_ids.add(message_id)
                    changed = True
                    continue
                if getattr(message, "author_id", None) == 0 or getattr(message, "by_bot", False):
                    known_ids.add(message_id)
                    changed = True
                    continue
                if not _is_customer_command_text(text):
                    continue
                if not _claim_seen_message(chat_id, message_id):
                    known_ids.add(message_id)
                    changed = True
                    continue
                known_ids.add(message_id)
                changed = True

                actual_buyer_id = str(getattr(message, "author_id", "") or buyer_id)
                actual_buyer_name = str(getattr(message, "author", "") or buyer_name)
                try:
                    if self.process_customer_text(chat_id, actual_buyer_id, actual_buyer_name, text, message_id=message_id):
                        known_ids.add(message_id)
                        changed = True
                        logger.info("Processed active FunPay command %s from chat %s", text, chat_id)
                except Exception as exc:
                    logger.exception("Cannot process active FunPay command %s", text)
                    rent.log_event("error", f"Не обработал команду {text} в FunPay: {exc}")
                    continue
            seen[chat_id] = list(known_ids)[-300:]

        if changed:
            settings["seen_funpay_message_ids"] = seen
            _save_settings(settings)

    def scan_review_bonus_cancellations(self) -> None:
        if self.account is None:
            return
        for order_id in rent.active_review_bonus_order_ids():
            try:
                with self.lock:
                    order = self.account.get_order(order_id)
                review = getattr(order, "review", None)
                if review is not None:
                    continue
                result = rent.cancel_review_bonus(order_id)
                if result.get("ok") and result.get("chat_id") and result.get("message"):
                    self.send_funpay_message(
                        result["chat_id"],
                        result["message"],
                        result.get("buyer_name", ""),
                        result.get("buyer_id", ""),
                    )
                    logger.info("Cancelled missing FunPay review bonus for order %s", order_id)
            except Exception as exc:
                logger.exception("Cannot verify FunPay review bonus for order %s", order_id)
                rent.log_event("error", f"Не смог проверить бонус отзыва по заказу {order_id}: {exc}")

    def send_funpay_message(
        self,
        chat_id: str | int,
        text: str,
        chat_name: str = "",
        interlocutor_id: str | int = "",
        *,
        retries: int = 4,
        retry_delay: float = 3.0,
    ) -> None:
        if not chat_id:
            raise RuntimeError("У заказа нет chat_id, сообщение FunPay отправить некуда.")
        self.ensure_connected()
        interlocutor = int(interlocutor_id) if str(interlocutor_id).isdigit() else None
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                with self.lock:
                    self.account.send_message(
                        chat_id=chat_id,
                        text=text,
                        chat_name=chat_name or None,
                        interlocutor_id=interlocutor,
                    )
                if attempt > 1:
                    logger.info("Delivered FunPay message to %s on attempt %s/%s", chat_id, attempt, retries)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning("FunPay send to %s failed (attempt %s/%s): %s", chat_id, attempt, retries, exc)
                if attempt == retries:
                    break
                time.sleep(retry_delay * attempt)
                # Соединение могло протухнуть — принудительно переподключаемся перед следующей попыткой.
                if getattr(self.account, "runner", None) is None:
                    with self.lock:
                        self.account = None
                    try:
                        self.ensure_connected()
                    except Exception as reconnect_exc:
                        logger.warning("FunPay reconnect before resend failed: %s", reconnect_exc)
        reliability.enqueue(
            "funpay_message",
            self.owner_id,
            {"chat_id": str(chat_id), "text": text, "chat_name": chat_name, "interlocutor_id": str(interlocutor_id)},
            key=reliability.operation_key("funpay_message", self.owner_id, chat_id, hashlib.sha256(text.encode()).hexdigest()),
        )
        raise RuntimeError(
            f"Не удалось доставить сообщение в FunPay после {retries} попыток: {last_exc}"
        ) from last_exc

    def send_rental_notices(self, notices: list[dict[str, str]]) -> None:
        notices = [notice for notice in notices if notice.get("chat_id") and notice.get("text")]
        if not notices:
            return
        self.ensure_connected()
        for notice in notices:
            chat_id = notice.get("chat_id") or ""
            text = notice.get("text") or ""
            if not rent.claim_notice_for_delivery(notice):
                logger.info("Skipped already claimed rental notice %s for %s", notice.get("kind"), chat_id)
                continue
            try:
                self.send_funpay_message(chat_id, text, notice.get("buyer_name", ""), notice.get("buyer_id", ""))
                rent.mark_notice_delivered(notice)
                logger.info("Sent rental notice %s to %s", notice.get("kind"), chat_id)
            except Exception as exc:
                rent.release_notice_claim(notice)
                logger.exception("Cannot send rental notice")
                rent.log_event("error", f"Не отправил уведомление аренды в FunPay: {exc}")

    def refund(self, order_id: str) -> None:
        self.ensure_connected()
        try:
            with self.lock:
                self.account.refund(order_id)
        except Exception:
            reliability.enqueue("funpay_refund", self.owner_id, {"order_id": str(order_id)},
                                key=reliability.operation_key("funpay_refund", self.owner_id, order_id))
            raise
        rent.log_event("info", f"Сделан возврат по заказу #{order_id}")

    # ── Плагин Auto SMM ──────────────────────────────────────────────────
    def try_handle_smm_order(self, order: Any, order_id: str) -> bool:
        if not auto_smm.is_enabled():
            return False
        buyer_id = str(getattr(order, "buyer_id", "") or "")
        buyer_name = str(getattr(order, "buyer_username", "") or buyer_id or "покупатель")
        chat_id = getattr(order, "chat_id", "") or ""
        result = auto_smm.handle_purchase(order, order_id, self.order_text(order), buyer_id, buyer_name, chat_id)
        if not result or not result.get("handled"):
            return False
        try:
            if result.get("message"):
                self.send_funpay_message(chat_id, result["message"], buyer_name, buyer_id)
            _mark_processed(order_id)
            lot = result.get("lot") or {}
            self.notify_admins(
                f"SMM-заказ #{order_id} от {buyer_name}: {lot.get('name', '')} x{result.get('amount', 1)}. Жду ссылку."
            )
        except Exception as exc:
            _release_order_processing(order_id)
            logger.exception("Cannot process SMM order %s", order_id)
            rent.log_event("error", f"SMM: ошибка обработки заказа #{order_id}: {exc}")
            self.notify_admins(f"SMM: ошибка обработки заказа #{order_id}: {exc}")
        return True

    def try_handle_smm_text(self, chat_id: str | int, buyer_id: str, buyer_name: str, text: str) -> bool:
        try:
            result = auto_smm.handle_customer_text(chat_id, buyer_id, text)
        except Exception as exc:
            logger.exception("SMM text handler failed")
            rent.log_event("error", f"SMM: ошибка обработки сообщения: {exc}")
            return False
        if not result or not result.get("handled"):
            return False
        if result.get("refund"):
            try:
                self.refund(result["refund"])
            except Exception as exc:
                self.notify_admins(f"SMM: не удалось сделать возврат #{result['refund']}: {exc}")
        if result.get("message"):
            self.send_funpay_message(chat_id, result["message"], buyer_name, buyer_id)
        if result.get("admin"):
            self.notify_admins(result["admin"])
        return True

    def check_smm_orders(self) -> None:
        self.activate_owner()
        auto_smm.check_orders_once(
            send=lambda chat_id, text, name, bid: self.send_funpay_message(chat_id, text, name, bid),
            refund=self.refund,
            notify_admin=self.notify_admins,
        )
        reliability.record_health(self.owner_id, last_smm_scan=reliability._iso(), last_error="")

    def match_lot_id(self, order: Any) -> str | None:
        return self.match_lot_id_from_text(self.order_text(order), order)

    def match_lot_id_from_text(self, text: str, order: Any | None = None) -> str | None:
        lots = rent.list_lots()
        if not lots:
            return None

        description = text or ""
        normalized_description = _normalize(description)
        if order is not None:
            lot_id = self.match_lot_id_from_funpay_lots(normalized_description, order)
            if lot_id:
                return lot_id

        for lot in lots:
            lot_id = str(lot.get("id", ""))
            title = _normalize(str(lot.get("title", "")))
            if lot_id and lot_id in description:
                return lot_id
            if title and _contains(title, normalized_description):
                return lot_id

        return None

    def match_lot_id_from_funpay_lots(self, normalized_description: str, order: Any) -> str | None:
        subcategory = getattr(order, "subcategory", None)
        if subcategory is None:
            return None
        lots = rent.list_lots()
        local_ids = {str(lot.get("id", "")) for lot in lots}
        matched_lot_id: str | None = None
        for my_lot in self.get_my_lots(int(subcategory.id)):
            my_lot_id = str(getattr(my_lot, "id", ""))
            my_lot_title_raw = str(getattr(my_lot, "description", "") or getattr(my_lot, "title", "") or "")
            if my_lot_id in local_ids and my_lot_title_raw:
                self.sync_local_lot_title(my_lot_id, my_lot_title_raw)
            my_lot_title = _normalize(my_lot_title_raw)
            if not my_lot_id or my_lot_id not in local_ids or not _contains(my_lot_title, normalized_description):
                continue
            matched_lot_id = matched_lot_id or my_lot_id
        return matched_lot_id

    @staticmethod
    def sync_local_lot_title(lot_id: str, title: str) -> None:
        clean_title = re.sub(r"\s+", " ", str(title or "")).strip(" ,")
        if not lot_id or not clean_title:
            return
        lot = rent.lot_by_id(rent.list_lots(), lot_id)
        if not lot:
            return
        if str(lot.get("title", "") or "") == clean_title and lot.get("funpay_title") == clean_title:
            return
        rent.update_lot(
            lot_id,
            title=clean_title,
            funpay_title=clean_title,
            funpay_synced_at=rent.iso(rent.now_utc()),
        )

    def resolve_lot_id(self, order: Any) -> str | None:
        order_id = str(getattr(order, "id", "") or "")
        if order_id:
            try:
                with self.lock:
                    full_order = self.account.get_order(order_id)
                lot_id = self.match_lot_id(full_order)
                if lot_id:
                    return lot_id
            except Exception as exc:
                logger.warning("Cannot load full FunPay order %s for lot matching: %s", order_id, exc)

        chat_id = getattr(order, "chat_id", "") or ""
        buyer_name = str(getattr(order, "buyer_username", "") or "")
        if order_id and chat_id:
            text = self.find_order_message_text(order_id, chat_id, buyer_name)
            lot_id = self.match_lot_id_from_text(text)
            if lot_id:
                return lot_id

        return None

    def find_order_message_text(self, order_id: str, chat_id: str | int, buyer_name: str = "") -> str:
        try:
            with self.lock:
                messages = self.account.get_chat_history(chat_id, interlocutor_username=buyer_name or None)
        except Exception as exc:
            logger.warning("Cannot load chat %s for order %s lot matching: %s", chat_id, order_id, exc)
            return ""
        needle = f"#{order_id}".upper()
        for message in reversed(messages):
            text = (getattr(message, "text", "") or "").strip()
            if needle in text.upper():
                return text
        return ""

    @staticmethod
    def order_text(order: Any) -> str:
        parts: list[str] = []
        for field in ("description", "full_description", "short_description", "title", "lot_params_text"):
            value = str(getattr(order, field, "") or "").strip()
            if value and value not in parts:
                parts.append(value)
        for field in getattr(order, "fields", {}).values():
            name = str(getattr(field, "name", "") or "").strip()
            value = getattr(field, "value", "")
            values = [str(item).strip() for item in value.values()] if isinstance(value, dict) else [str(value).strip()]
            for item in [name, *values]:
                if item and item not in parts:
                    parts.append(item)
        return "\n".join(parts)

    @staticmethod
    def order_title(order: Any) -> str:
        for field in ("description", "short_description", "title"):
            value = str(getattr(order, field, "") or "").strip()
            if value:
                return value
        text = FunPayBridge.order_text(order)
        patterns = [
            r"заказ\s+#[A-Z0-9]+\.\s*(.+?)(?:\n|$)",
            r"оплатил\s+заказ\s+#[A-Z0-9]+\.\s*(.+?)(?:\n|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).strip(" .")
                if value:
                    return value
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[0] if lines else ""

    def get_my_lots(self, subcategory_id: int) -> list[Any]:
        cached_at, cached_lots = self.my_lots_cache.get(subcategory_id, (0, []))
        if cached_lots and time.time() - cached_at < 600:
            return cached_lots
        with self.lock:
            lots = self.account.get_my_subcategory_lots(subcategory_id)
        self.my_lots_cache[subcategory_id] = (time.time(), lots)
        return lots

    @staticmethod
    def _is_paid_order(order: Any) -> bool:
        status = getattr(order, "status", None)
        return bool(OrderStatuses is not None and status in {OrderStatuses.PAID, OrderStatuses.CLOSED})

    @staticmethod
    def _is_recent_order(order: Any) -> bool:
        created_at = getattr(order, "date", None)
        if not isinstance(created_at, datetime):
            return False
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone(timedelta(hours=3)))
        return datetime.now(timezone.utc) - created_at.astimezone(timezone.utc) <= timedelta(
            minutes=config.FUNPAY_ORDER_LOOKBACK_MINUTES
        )


def _owner_ids_with_funpay_key() -> list[str]:
    owners = []
    for owner_id in rent.active_owner_ids():
        if (rent.user_settings(owner_id).get("funpay_golden_key") or "").strip():
            tid = rent.telegram_id_for_owner(owner_id)
            if tid and users.is_banned(tid):
                continue  # забаненный пользователь — его FunPay не обслуживаем
            owners.append(str(owner_id))
    return owners


def retry_funpay_message(record: dict[str, Any]) -> None:
    payload = record["payload"]
    bridge = FunPayBridge(None, None, owner_id=record["owner_id"])
    bridge.send_funpay_message(payload["chat_id"], payload["text"], payload.get("chat_name", ""),
                               payload.get("interlocutor_id", ""), retries=1, retry_delay=0)


def retry_funpay_refund(record: dict[str, Any]) -> None:
    bridge = FunPayBridge(None, None, owner_id=record["owner_id"])
    bridge.ensure_connected()
    with bridge.lock:
        bridge.account.refund(str(record["payload"]["order_id"]))


def _listener_signature(owner_id: str) -> str:
    s = rent.user_settings(owner_id)
    return f"{(s.get('funpay_golden_key') or '').strip()}|{s.get('funpay_proxy') or ''}"


def _stop_bridge(owner_id: str) -> None:
    bridge = ACTIVE_BRIDGES.pop(owner_id, None)
    if bridge is not None:
        bridge.stop_event.set()


async def _run_owner_funpay_listener(bot: Bot, owner_id: str) -> None:
    global ACTIVE_BRIDGE
    while True:
        rent.set_current_owner(owner_id)
        key = (rent.user_settings(owner_id).get("funpay_golden_key") or "").strip()
        if not key:
            ACTIVE_BRIDGES.pop(owner_id, None)
            await asyncio.sleep(30)
            continue
        bridge = FunPayBridge(bot, asyncio.get_running_loop(), owner_id=owner_id)
        ACTIVE_BRIDGE = bridge
        ACTIVE_BRIDGES[owner_id] = bridge
        try:
            await asyncio.to_thread(bridge.listen_forever)
        except Exception as exc:
            ACTIVE_BRIDGE = None
            ACTIVE_BRIDGES.pop(owner_id, None)
            logger.exception("FunPay listener stopped")
            rent.log_event("error", f"FunPay listener остановлен: {exc}")
            await asyncio.sleep(20)


async def run_funpay_listener(bot: Bot) -> None:
    tasks: dict[str, tuple[asyncio.Task, str]] = {}
    while True:
        owner_ids = set(_owner_ids_with_funpay_key())
        for owner_id in owner_ids:
            sig = _listener_signature(owner_id)
            current = tasks.get(owner_id)
            if current is not None and current[1] != sig:
                # golden key или прокси поменяли — перезапускаем слушателя
                current[0].cancel()
                _stop_bridge(owner_id)
                current = None
            if current is None or current[0].done():
                tasks[owner_id] = (asyncio.create_task(_run_owner_funpay_listener(bot, owner_id)), sig)
        for owner_id in list(tasks):
            if owner_id not in owner_ids:
                tasks[owner_id][0].cancel()
                _stop_bridge(owner_id)
                del tasks[owner_id]
        if not owner_ids:
            rent.log_event("warn", "FunPay listener ждет golden key")
        await asyncio.sleep(15)


async def run_funpay_chat_scanner(bot: Bot) -> None:
    while True:
        key = (_settings().get("funpay_golden_key") or "").strip()
        if not key:
            await asyncio.sleep(30)
            continue
        bridge = FunPayBridge(bot, asyncio.get_running_loop())
        try:
            await asyncio.to_thread(bridge.connect, False, False)
            logger.info("FunPay chat scanner connected")
            while True:
                await asyncio.to_thread(bridge.scan_active_chat_commands)
                await asyncio.sleep(8)
        except Exception as exc:
            logger.exception("FunPay chat scanner stopped")
            rent.log_event("error", f"FunPay chat scanner остановлен: {exc}")
            await asyncio.sleep(15)


async def send_rental_notices(notices: list[dict[str, str]], bot: Bot | None = None) -> None:
    if not notices:
        return
    grouped: dict[str, list[dict[str, str]]] = {}
    for notice in notices:
        grouped.setdefault(str(notice.get("owner_id") or ""), []).append(notice)
    for owner_id, owner_notices in grouped.items():
        if owner_id:
            rent.set_current_owner(owner_id)
        bridge = FunPayBridge(bot, asyncio.get_running_loop(), owner_id=owner_id or None)
        if bot is None:
            bridge.notify_admins = lambda text: None
        await asyncio.to_thread(bridge.send_rental_notices, owner_notices)


async def scan_recent_paid_orders_once() -> None:
    for owner_id in _owner_ids_with_funpay_key():
        rent.set_current_owner(owner_id)
        bridge = FunPayBridge(bot=None, loop=asyncio.get_running_loop(), owner_id=owner_id)
        bridge.notify_admins = lambda text: None
        await asyncio.to_thread(bridge.scan_recent_paid_orders)


async def scan_recent_funpay_chats_once(bot: Bot | None = None) -> None:
    for owner_id in _owner_ids_with_funpay_key():
        if owner_id in ACTIVE_BRIDGES:
            continue
        rent.set_current_owner(owner_id)
        bridge = FunPayBridge(bot=bot, loop=asyncio.get_running_loop(), owner_id=owner_id)
        if bot is None:
            bridge.notify_admins = lambda text: None
        await asyncio.to_thread(bridge.scan_recent_chat_messages)


async def run_funpay_message_scanner(bot: Bot) -> None:
    while True:
        try:
            await asyncio.wait_for(scan_recent_funpay_chats_once(bot), timeout=40)
        except asyncio.TimeoutError:
            rent.log_event("warn", "FunPay message scan timeout; scanner will continue")
        except Exception as exc:
            logger.exception("FunPay message scanner failed")
            rent.log_event("error", f"FunPay message scan error: {exc}")
        await asyncio.sleep(8)


async def run_smm_checker(bot: Bot) -> None:
    """Фоновая проверка статусов SMM-заказов (раз в 2 минуты)."""
    while True:
        for owner_id in _owner_ids_with_funpay_key():
            try:
                rent.set_current_owner(owner_id)
                if not auto_smm.cfg().get("orders"):
                    continue
                bridge = ACTIVE_BRIDGES.get(owner_id) or FunPayBridge(bot, asyncio.get_running_loop(), owner_id=owner_id)
                await asyncio.wait_for(asyncio.to_thread(bridge.check_smm_orders), timeout=90)
            except asyncio.TimeoutError:
                rent.log_event("warn", "SMM status check timeout")
            except Exception as exc:
                logger.exception("SMM checker failed")
                rent.log_event("error", f"SMM checker error: {exc}")
        await asyncio.sleep(120)


async def scan_active_chat_commands() -> None:
    for bridge in list(ACTIVE_BRIDGES.values()):
        await asyncio.to_thread(bridge.scan_active_chat_commands)
