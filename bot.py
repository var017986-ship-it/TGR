"""Main entry point. Sets up dispatcher, includes all routers, starts background tasks."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent

import config
import funpay_bridge
import rental_service as rent
import storage
import reliability
from keyboards import access_extend_kb
from handlers import access as access_handlers
from handlers import account as account_handlers
from handlers import commands as command_handlers
from handlers import debug as debug_handlers
from handlers import funpay_reply as funpay_reply_handlers
from handlers import funpay_key as funpay_key_handlers
from handlers import lot as lot_handlers
from handlers import plugin as plugin_handlers
from handlers import simulate as simulate_handlers


# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = config.BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
    ],
)


def _build_dispatcher() -> Dispatcher:
    """Build dispatcher with all routers attached."""
    dp = Dispatcher()

    # Учёт всех пользователей (рассылка, баны, статистика)
    from middlewares import UserTrackingMiddleware
    dp.message.outer_middleware(UserTrackingMiddleware())
    dp.callback_query.outer_middleware(UserTrackingMiddleware())

    # Заглушки отключённой Steam-аренды — первыми, чтобы старые кнопки и
    # команды не доходили до логики аренды.
    from callbacks import disabled
    dp.include_router(disabled.router)

    # Главная админ-панель и донаты/подписка — раньше остальных,
    # чтобы их состояния ввода не перехватывались другими роутерами.
    from callbacks import donate, fp_accounts as fp_accounts_cb, superadmin
    dp.include_router(superadmin.router)
    dp.include_router(donate.router)
    dp.include_router(fp_accounts_cb.router)

    # Command routers
    dp.include_router(command_handlers.router)
    dp.include_router(account_handlers.router)
    dp.include_router(lot_handlers.router)
    dp.include_router(funpay_key_handlers.router)
    dp.include_router(funpay_reply_handlers.router)
    dp.include_router(simulate_handlers.router)
    dp.include_router(debug_handlers.router)
    dp.include_router(plugin_handlers.router)
    dp.include_router(access_handlers.router)

    # Callback routers
    from callbacks import admin, funpay, lots, navigation, plugin, smm
    dp.include_router(navigation.router)
    dp.include_router(lots.router)
    dp.include_router(admin.router)
    dp.include_router(plugin.router)
    dp.include_router(smm.router)
    dp.include_router(funpay.router)

    # Error suppression
    @dp.errors()
    async def _ignore_same_edit(event: ErrorEvent) -> bool:
        error = event.exception
        if isinstance(error, TelegramBadRequest) and "message is not modified" in str(error):
            return True
        return False

    return dp


# ── Background tasks ─────────────────────────────────────────────────────────
async def notifier(bot: Bot) -> None:
    """Expire rentals, send warnings, claim notices."""
    while True:
        try:
            access_notices = await asyncio.to_thread(rent.collect_access_expiry_notifications)
            for notice in access_notices:
                user_id = str(notice.get("user_id", ""))
                if not user_id.isdigit():
                    continue
                try:
                    await bot.send_message(
                        int(user_id),
                        notice.get("text", ""),
                        reply_markup=access_extend_kb(),
                    )
                except Exception as exc:
                    rent.log_event("error", f"Не отправил предупреждение доступа пользователю {user_id}: {exc}")

            notices = await asyncio.to_thread(rent.expire_and_collect_notifications)
            if notices:
                try:
                    await asyncio.wait_for(
                        funpay_bridge.send_rental_notices(notices, bot), timeout=20
                    )
                except asyncio.TimeoutError:
                    rent.log_event("warn", "Rental notice send timeout; rent checker will continue")
        except Exception as exc:
            rent.log_event("error", f"Ошибка фоновой проверки: {exc}")
        await asyncio.sleep(10)


async def funpay_sweeper() -> None:
    """Periodic FunPay paid-order scanner."""
    while True:
        try:
            await asyncio.wait_for(
                funpay_bridge.scan_recent_paid_orders_once(), timeout=30
            )
        except asyncio.TimeoutError:
            rent.log_event("warn", "FunPay paid-order scan timeout; rent checker is still running")
        except Exception as exc:
            rent.log_event("error", f"FunPay paid-order scan error: {exc}")
        await asyncio.sleep(config.CHECK_INTERVAL_SECONDS)


# ── Main ─────────────────────────────────────────────────────────────────────
async def auto_raise_worker() -> None:
    """Raise FunPay lots once per hour for owners who enabled it."""
    while True:
        try:
            current = rent.now_utc()
            for owner_id in rent.active_owner_ids():
                rent.set_current_owner(owner_id)
                settings = rent.user_settings()
                if not settings.get("auto_raise_lots_enabled"):
                    continue
                if not (settings.get("funpay_golden_key") or "").strip():
                    continue
                next_at = rent.parse_dt(settings.get("auto_raise_next_at"))
                if next_at and next_at > current:
                    continue
                await asyncio.to_thread(funpay_bridge.auto_raise_lots_once, owner_id)
            rent.set_current_owner("")
        except Exception as exc:
            rent.log_event("error", f"Auto raise worker error: {exc}")
        await asyncio.sleep(60)


async def main() -> None:
    storage.ensure()
    if not config.BOT_TOKEN:
        raise RuntimeError("Заполни BOT_TOKEN в telegram_rent_bot/.env")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = _build_dispatcher()

    reliability.register("funpay_message", funpay_bridge.retry_funpay_message)
    reliability.register("funpay_refund", funpay_bridge.retry_funpay_refund)

    # Steam rental is disabled; expiration notifications are not started.
    asyncio.create_task(funpay_sweeper())
    asyncio.create_task(funpay_bridge.run_funpay_message_scanner(bot))
    asyncio.create_task(funpay_bridge.run_funpay_listener(bot))
    # Steam lot auto-raising is disabled together with rental mode.
    asyncio.create_task(funpay_bridge.run_smm_checker(bot))
    from callbacks.donate import donation_watcher
    asyncio.create_task(donation_watcher(bot))
    asyncio.create_task(reliability.worker())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
