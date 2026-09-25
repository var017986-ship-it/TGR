"""Durable retry queue and technical health state.

The module is deliberately independent from FunPay business logic. Operation
handlers are registered by the runtime layer, while records live in the same
JSON-backed SQLite store as the rest of the bot.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import storage

logger = logging.getLogger("steam_rent.reliability")
MAX_EVENTS = 300
MAX_QUEUE = 2000
BACKOFF_SECONDS = (30, 120, 600, 1800, 7200)
Handler = Callable[[dict[str, Any]], Any]
HANDLERS: dict[str, Handler] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _parse(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return _now()


def _data() -> dict[str, Any]:
    data = storage.read("reliability")
    if not isinstance(data, dict):
        data = {}
    data.setdefault("queue", [])
    data.setdefault("events", [])
    data.setdefault("health", {})
    return data


def _save(data: dict[str, Any]) -> None:
    data["queue"] = data.get("queue", [])[-MAX_QUEUE:]
    data["events"] = data.get("events", [])[-MAX_EVENTS:]
    storage.write("reliability", data)


def operation_key(kind: str, owner_id: str | int, *parts: object) -> str:
    raw = "|".join([kind, str(owner_id), *(str(part) for part in parts)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def enqueue(kind: str, owner_id: str | int, payload: dict[str, Any], *, key: str | None = None,
            max_attempts: int = 5) -> dict[str, Any]:
    """Add an operation once, returning the existing active/done record on duplicates."""
    if not str(owner_id).strip():
        raise ValueError("owner_id is required")
    data = _data()
    operation_key_value = key or operation_key(kind, owner_id, sorted(payload.items()))
    for record in data["queue"]:
        if record.get("operation_key") == operation_key_value:
            return dict(record)
    now = _iso()
    record = {
        "id": uuid.uuid4().hex,
        "operation_key": operation_key_value,
        "kind": kind,
        "owner_id": str(owner_id),
        "payload": dict(payload),
        "status": "pending",
        "attempts": 0,
        "max_attempts": max(1, int(max_attempts)),
        "next_retry_at": now,
        "last_error": "",
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
    }
    data["queue"].append(record)
    _save(data)
    return dict(record)


def summary() -> dict[str, int]:
    counts = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    for record in _data()["queue"]:
        status = str(record.get("status", "failed"))
        if status in counts:
            counts[status] += 1
    return counts


def records(status: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    items = _data()["queue"]
    if status:
        items = [item for item in items if item.get("status") == status]
    return [dict(item) for item in reversed(items[-limit:])]


def retry_now(record_id: str) -> bool:
    data = _data()
    for record in data["queue"]:
        if record.get("id") == record_id and record.get("status") in {"failed", "pending"}:
            record.update(status="pending", attempts=0, next_retry_at=_iso(), updated_at=_iso(), last_error="",
                          completed_at="")
            _save(data)
            return True
    return False


def dismiss(record_id: str) -> bool:
    data = _data()
    for record in data["queue"]:
        if record.get("id") == record_id and record.get("status") != "processing":
            record.update(status="done", completed_at=_iso(), updated_at=_iso(), last_error="dismissed")
            _save(data)
            return True
    return False


def record_event(level: str, kind: str, message: str, owner_id: str | int = "") -> None:
    data = _data()
    data["events"].append({"at": _iso(), "level": level, "kind": kind,
                           "owner_id": str(owner_id), "message": str(message)[:1000]})
    _save(data)


def recent_events(limit: int = 20) -> list[dict[str, Any]]:
    return list(reversed(_data()["events"][-limit:]))


def record_health(owner_id: str | int, **fields: Any) -> None:
    data = _data()
    owner = data["health"].setdefault(str(owner_id), {})
    owner.update(fields, updated_at=_iso())
    _save(data)


def health() -> dict[str, dict[str, Any]]:
    return {str(key): dict(value) for key, value in _data()["health"].items()}


def register(kind: str, handler: Handler) -> None:
    HANDLERS[kind] = handler


def _claim_due() -> dict[str, Any] | None:
    data = _data()
    now = _now()
    for record in data["queue"]:
        if record.get("status") != "pending" or _parse(record.get("next_retry_at", "")) > now:
            continue
        record["status"] = "processing"
        record["updated_at"] = _iso()
        _save(data)
        return dict(record)
    return None


def _finish(record_id: str, *, success: bool, error: str = "") -> None:
    data = _data()
    for record in data["queue"]:
        if record.get("id") != record_id:
            continue
        record["updated_at"] = _iso()
        if success:
            record.update(status="done", completed_at=_iso(), last_error="")
        else:
            record["attempts"] = int(record.get("attempts", 0)) + 1
            record["last_error"] = str(error)[:1000]
            if record["attempts"] >= int(record.get("max_attempts", 5)):
                record["status"] = "failed"
                record["completed_at"] = _iso()
            else:
                delay = BACKOFF_SECONDS[min(record["attempts"] - 1, len(BACKOFF_SECONDS) - 1)]
                record["status"] = "pending"
                record["next_retry_at"] = _iso(_now() + timedelta(seconds=delay))
        _save(data)
        return


async def process_once() -> bool:
    record = _claim_due()
    if not record:
        return False
    handler = HANDLERS.get(str(record.get("kind")))
    if handler is None:
        _finish(record["id"], success=False, error=f"Unknown operation kind: {record.get('kind')}")
        return True
    try:
        result = handler(record)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        logger.warning("Retry operation %s failed: %s", record.get("id"), exc)
        _finish(record["id"], success=False, error=f"{type(exc).__name__}: {exc}")
        return True
    _finish(record["id"], success=True)
    return True


async def worker() -> None:
    while True:
        try:
            while await process_once():
                pass
        except Exception as exc:
            logger.exception("Reliability worker error: %s", exc)
        await asyncio.sleep(10)
