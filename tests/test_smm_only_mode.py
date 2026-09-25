"""Регрессионные тесты режима «только Auto SMM» (Steam-аренда отключена)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import storage  # noqa: E402


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "bot.sqlite3")
    storage.ensure()
    yield tmp_path


def _bridge(monkeypatch, smm_handled: bool):
    import funpay_bridge
    import rental_service as rent

    rent.set_current_owner("1001")
    bridge = funpay_bridge.FunPayBridge(bot=None, loop=None, owner_id="1001")
    bridge.account = SimpleNamespace(get_order=lambda order_id: None)
    notices: list[str] = []
    bridge.notify_admins = lambda text, reply_markup=None: notices.append(text)
    monkeypatch.setattr(bridge, "_is_paid_order", lambda order: True)
    monkeypatch.setattr(bridge, "try_handle_smm_order", lambda order, order_id: smm_handled)

    def forbidden(*args, **kwargs):
        raise AssertionError("Steam-аренда не должна вызываться")

    monkeypatch.setattr(rent, "allocate_or_extend", forbidden)
    return funpay_bridge, bridge, notices


def test_non_smm_order_is_skipped_once_without_rental(isolated_db, monkeypatch):
    funpay_bridge, bridge, notices = _bridge(monkeypatch, smm_handled=False)
    order = SimpleNamespace(id="ABC123", description="Steam аренда 1 час")

    bridge.handle_order(order, recent_initial=False)
    bridge.handle_order(order, recent_initial=False)

    assert funpay_bridge._already_processed("ABC123")
    assert len(notices) == 1, "уведомление о пропуске должно прийти ровно один раз"


def test_smm_order_is_still_processed(isolated_db, monkeypatch):
    _, bridge, notices = _bridge(monkeypatch, smm_handled=True)
    bridge.handle_order(SimpleNamespace(id="SMM777", description="Подписчики"), recent_initial=False)
    assert notices == []


def test_rental_commands_in_chat_are_not_answered(isolated_db, monkeypatch):
    _, bridge, _ = _bridge(monkeypatch, smm_handled=False)
    sent: list[str] = []
    monkeypatch.setattr(bridge, "send_funpay_message", lambda *args, **kwargs: sent.append(args[1]))
    monkeypatch.setattr(bridge, "try_handle_smm_text", lambda *args, **kwargs: False)
    monkeypatch.setattr(bridge, "notify_funpay_message", lambda *args, **kwargs: None)

    for command in ("!code", "!acc", "!menu"):
        bridge.process_customer_text("chat1", "42", "buyer", command)

    assert sent == [], "команды аренды не должны отвечать покупателю"


def test_any_user_has_access_without_code(isolated_db):
    from handlers import guards

    assert guards.is_admin(987654321)
    assert not guards.is_owner(987654321)


@pytest.mark.parametrize(
    "data",
    ["accounts", "rentals", "lots", "add_account:steam", "lot_view:1", "access_codes",
     "access_code_generate:1w", "plugin_offline", "review_bonus:5"],
)
def test_legacy_rental_callbacks_are_blocked(data):
    from callbacks.disabled import is_disabled_callback

    assert is_disabled_callback(data)


@pytest.mark.parametrize(
    "data",
    ["dashboard", "plugin_smm", "smm_lots", "fpacc", "fpacc_add", "adm", "donate", "sub_check", "fp_reply:1:2"],
)
def test_working_callbacks_are_not_blocked(data):
    from callbacks.disabled import is_disabled_callback

    assert not is_disabled_callback(data)


def test_disabled_router_is_first():
    import bot
    from callbacks import disabled

    dp = bot._build_dispatcher()
    assert dp.sub_routers[0] is disabled.router
