"""Главные клавиатуры с чёткой структурой."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_kb(can_manage_codes: bool = False) -> InlineKeyboardMarkup:
    """Основная клавиатура главного меню."""
    rows = [
        [InlineKeyboardButton(text="📈 Auto SMM", callback_data="plugin_smm")],
        [InlineKeyboardButton(text="🔗 FunPay аккаунты", callback_data="fpacc")],
    ]
    if can_manage_codes:
        rows.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="adm")])
    rows.append(
        [
            InlineKeyboardButton(text="↻ Обновить", callback_data="dashboard"),
            InlineKeyboardButton(text="? Помощь", callback_data="help"),
        ]
    )
    rows.append([InlineKeyboardButton(text="💝 Отблагодарить", callback_data="donate")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def access_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="→ Ввести код доступа", callback_data="enter_access_code")]
        ]
    )


def access_extend_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="→ Продлить доступ (1 ключ)", callback_data="enter_access_code")]
        ]
    )


def access_codes_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="＋ Сгенерировать новый код", callback_data="access_code_new")],
            [InlineKeyboardButton(text="◆ Админ-панель", callback_data="admin_panel")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◆ Коды доступа", callback_data="access_codes")],
            [InlineKeyboardButton(text="● Очистка базы", callback_data="clean_db")],
            [InlineKeyboardButton(text="← Главная панель", callback_data="dashboard")],
        ]
    )


def plugins_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📈 Auto SMM (накрутка)", callback_data="plugin_smm")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )


def auto_raise_kb(enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = "Выключить авто поднятие" if enabled else "Включить авто поднятие"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="auto_raise_toggle")],
            [InlineKeyboardButton(text="Попробовать сейчас", callback_data="auto_raise_now")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )


def access_code_duration_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 минута", callback_data="access_code_generate:1m"),
                InlineKeyboardButton(text="1 час", callback_data="access_code_generate:1h"),
            ],
            [
                InlineKeyboardButton(text="1 неделя", callback_data="access_code_generate:1w"),
                InlineKeyboardButton(text="1 месяц", callback_data="access_code_generate:1mo"),
            ],
            [
                InlineKeyboardButton(text="1 год", callback_data="access_code_generate:1y"),
                InlineKeyboardButton(text="∞ Навсегда", callback_data="access_code_generate:forever"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="access_codes")],
        ]
    )


def clean_db_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✗ Очистить bot.sqlite3", callback_data="clean_db_confirm")],
            [InlineKeyboardButton(text="← Назад", callback_data="admin_panel")],
        ]
    )


def clean_db_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✓ Да, очистить", callback_data="clean_db_do")],
            [InlineKeyboardButton(text="✗ Отмена", callback_data="dashboard")],
        ]
    )


def onboarding_kb(has_funpay_key: bool = False, can_manage_codes: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура для первичной настройки (когда нет аккаунтов)."""
    rows = [[InlineKeyboardButton(text="📈 Настроить Auto SMM", callback_data="plugin_smm")]]
    if not has_funpay_key:
        rows.append([InlineKeyboardButton(text="🔗 Подключить FunPay аккаунт", callback_data="fpacc")])
    rows.append([InlineKeyboardButton(text="↻ Обновить", callback_data="dashboard")])
    if can_manage_codes:
        rows.insert(-1, [InlineKeyboardButton(text="👑 Админ-панель", callback_data="adm")])
    rows.append([InlineKeyboardButton(text="💝 Отблагодарить", callback_data="donate")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data="dashboard")]]
    )


def add_account_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="＋ Добавить Steam-аккаунт", callback_data="add_account:steam")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )


def lots_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="＋ Добавить лот", callback_data="add_lot")],
            [InlineKeyboardButton(text="◇ Оффлайн-выдача", callback_data="plugin_offline_add_lot")],
            [InlineKeyboardButton(text="← Назад", callback_data="dashboard")],
        ]
    )


def lots_list_kb(lots: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"▸ {rent_esc(lot.get('title') or lot.get('id'))}",
            callback_data=f"lot_view:{lot.get('id')}"
        )]
        for lot in lots[:40]
    ]
    rows.append([InlineKeyboardButton(text="＋ Добавить лот", callback_data="add_lot")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="dashboard")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lot_manage_kb(lot_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ Изменить время аренды", callback_data=f"lot_edit_time:{lot_id}")],
            [InlineKeyboardButton(text="⏰ Изменить предупреждение", callback_data=f"lot_edit_notify:{lot_id}")],
            [InlineKeyboardButton(text="★ Бонус за отзыв", callback_data=f"lot_edit_bonus:{lot_id}")],
            [InlineKeyboardButton(text="≡ Аккаунты лота", callback_data=f"lot_edit_accounts:{lot_id}")],
            [InlineKeyboardButton(text="← Назад", callback_data="lots")],
        ]
    )


def account_pick_kb(accounts: list[dict], selected: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for account in accounts[:40]:
        account_id = str(account.get("id"))
        mark = "✓ " if account_id in selected else "▫ "
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark}{account.get('display_name') or account_id}{' [ОФЛ]' if account.get('offline_mode') else ''}",
                    callback_data=f"pick_acc:{account_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="✓ Готово", callback_data="finish_lot_accounts")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="lots")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lot_account_pick_kb(lot_id: str, accounts: list[dict], selected: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for account in accounts[:40]:
        account_id = str(account.get("id"))
        mark = "✓ " if account_id in selected else "▫ "
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark}{account.get('display_name') or account_id}{' [ОФЛ]' if account.get('offline_mode') else ''}",
                    callback_data=f"lot_pick_acc:{lot_id}:{account_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="✓ Готово", callback_data=f"lot_finish_accounts:{lot_id}")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"lot_view:{lot_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_bonus_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="★★★★★ за 4 и 5 звезд", callback_data="review_bonus:4")],
            [InlineKeyboardButton(text="★★★★★ только за 5 звезд", callback_data="review_bonus:5")],
            [InlineKeyboardButton(text="✗ Без бонуса", callback_data="review_bonus:0")],
            [InlineKeyboardButton(text="← Назад", callback_data="lots")],
        ]
    )


def lot_review_bonus_kb(lot_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="★★★★★ за 4 и 5 звезд", callback_data="review_bonus:4")],
            [InlineKeyboardButton(text="★★★★★ только за 5 звезд", callback_data="review_bonus:5")],
            [InlineKeyboardButton(text="✗ Без бонуса", callback_data="review_bonus:0")],
            [InlineKeyboardButton(text="← Назад", callback_data=f"lot_view:{lot_id}")],
        ]
    )


def rent_esc(text: str) -> str:
    """Минимальный esc для кнопок (Telegram не поддерживает HTML в callback_data)."""
    s = str(text or "")
    return s.replace("\n", " ").replace("\r", " ")[:60]
