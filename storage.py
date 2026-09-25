from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any

from config import DATA_DIR


LOCK = threading.RLock()
DB_PATH = DATA_DIR / "bot.sqlite3"
TABLE_NAMES = {"accounts", "lots", "rentals", "messages", "events", "settings", "bot_users", "admin", "reliability"}

DEFAULTS: dict[str, Any] = {
    "accounts": [],
    "lots": [],
    "rentals": [],
    "messages": [],
    "events": [],
    "settings": {"funpay_golden_key": ""},
    "bot_users": {},
    "admin": {},
    "reliability": {"queue": [], "events": [], "health": {}},
}
# Эти таблицы не стираются кнопкой «Очистка базы» (пользователи, баны, подписка, донаты).
PRESERVED_ON_RESET = {"bot_users", "admin"}


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure() -> None:
    with LOCK:
        with connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kv_store (
                    name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )
            for name, default in DEFAULTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO kv_store (name, payload) VALUES (?, ?)",
                    (name, json.dumps(default, ensure_ascii=False)),
                )


def read(name: str) -> Any:
    if name not in TABLE_NAMES:
        raise KeyError(f"Unknown storage table: {name}")
    ensure()
    with LOCK:
        with connect() as conn:
            row = conn.execute("SELECT payload FROM kv_store WHERE name = ?", (name,)).fetchone()
            if not row:
                return DEFAULTS[name]
            try:
                return json.loads(row["payload"])
            except Exception:
                return DEFAULTS[name]


def write(name: str, value: Any) -> None:
    if name not in TABLE_NAMES:
        raise KeyError(f"Unknown storage table: {name}")
    ensure()
    with LOCK:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO kv_store (name, payload)
                VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET payload = excluded.payload
                """,
                (name, json.dumps(value, ensure_ascii=False)),
            )
