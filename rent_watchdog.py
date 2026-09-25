from __future__ import annotations

import asyncio
import logging
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
        logging.FileHandler(LOG_DIR / "watchdog.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("steam_rent.watchdog")


def send_notices(notices: list[dict[str, str]]) -> None:
    if not notices:
        return

    async def runner() -> None:
        bridge = funpay_bridge.FunPayBridge(bot=None, loop=asyncio.get_running_loop())
        bridge.notify_admins = lambda text: None
        bridge.send_rental_notices(notices)

    asyncio.run(runner())


def main() -> None:
    storage.ensure()
    rent.log_event("info", "Rent watchdog started")
    logger.info("Rent watchdog started")
    while True:
        try:
            notices = rent.expire_and_collect_notifications()
            if notices:
                send_notices(notices)
        except Exception as exc:
            logger.exception("Rent watchdog iteration failed")
            rent.log_event("error", f"Rent watchdog error: {exc}")
        time.sleep(10)


if __name__ == "__main__":
    main()
