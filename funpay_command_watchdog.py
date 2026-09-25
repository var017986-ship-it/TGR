from __future__ import annotations

import asyncio
import logging
import multiprocessing
import time

import funpay_bridge
import rental_service as rent
import storage


LOG_DIR = rent.config.BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "command_watchdog.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("steam_rent.command_watchdog")

SCAN_INTERVAL_SECONDS = 5
SCAN_TIMEOUT_SECONDS = 20


def scan_once() -> None:
    async def runner() -> None:
        for owner_id in rent.active_owner_ids():
            key = (rent.user_settings(owner_id).get("funpay_golden_key") or "").strip()
            if not key:
                continue
            rent.set_current_owner(owner_id)
            bridge = funpay_bridge.FunPayBridge(bot=None, loop=asyncio.get_running_loop(), owner_id=owner_id)
            bridge.notify_admins = lambda text: None
            bridge.connect(scan_orders=False, notify=False)
            bridge.scan_recent_paid_orders()
            bridge.scan_review_bonus_cancellations()

    asyncio.run(runner())


def run_scan_child() -> None:
    storage.ensure()
    scan_once()


def main() -> None:
    storage.ensure()
    rent.log_event("info", "FunPay command watchdog started")
    logger.info("FunPay command watchdog started")

    while True:
        try:
            has_key = any((rent.user_settings(owner_id).get("funpay_golden_key") or "").strip() for owner_id in rent.active_owner_ids())
            if not has_key:
                time.sleep(30)
                continue

            process = multiprocessing.Process(target=run_scan_child)
            process.start()
            process.join(SCAN_TIMEOUT_SECONDS)
            if process.is_alive():
                process.terminate()
                process.join(5)
                logger.warning("FunPay command scan timed out and was restarted")
                rent.log_event("warn", "FunPay command watchdog: проверка команд зависла и была перезапущена")
        except Exception as exc:
            logger.exception("FunPay command watchdog iteration failed")
            rent.log_event("error", f"FunPay command watchdog error: {exc}")

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
