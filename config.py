from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def load_dotenv() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(item)
    for item in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
    if item.isdigit()
}
DEFAULT_RENT_HOURS = 24
NOTIFY_BEFORE_MINUTES = 20
CHECK_INTERVAL_SECONDS = max(10, int(os.getenv("CHECK_INTERVAL_SECONDS", "30")))
FUNPAY_ORDER_LOOKBACK_MINUTES = max(20, int(os.getenv("FUNPAY_ORDER_LOOKBACK_MINUTES", "360")))
