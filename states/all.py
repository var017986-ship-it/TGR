"""All FSM StatesGroups for the bot."""
from aiogram.fsm.state import State, StatesGroup


class AddAccount(StatesGroup):
    """Step-by-step Steam account wizard."""
    waiting_login = State()
    waiting_password = State()
    waiting_mafile = State()
    waiting_display_name = State()


class AddLot(StatesGroup):
    """Wizard for creating a new FunPay lot."""
    waiting_lot_id = State()
    waiting_hours = State()
    waiting_notify = State()
    waiting_review_stars = State()
    waiting_review_bonus = State()
    picking_accounts = State()


class EditLot(StatesGroup):
    """Wizard for editing an existing lot."""
    waiting_time = State()
    waiting_notify = State()
    waiting_bonus_time = State()
    picking_accounts = State()


class SimulateOrder(StatesGroup):
    """Manual order test input."""
    waiting_line = State()


class FunPayKey(StatesGroup):
    """FunPay golden key input."""
    waiting_key = State()


class FunPayReply(StatesGroup):
    """Reply to a FunPay chat from Telegram."""
    waiting_text = State()


class AccessGate(StatesGroup):
    """Access code gate for non-admin users."""
    waiting_code = State()


class SmmInput(StatesGroup):
    """Auto SMM plugin text inputs."""
    waiting_service = State()
    waiting_lot = State()
    waiting_domains = State()
    waiting_message = State()


class FpAccountAdd(StatesGroup):
    """Подключение FunPay-аккаунта: прокси -> golden key -> проверка."""
    waiting_proxy = State()
    waiting_key = State()


class AdminInput(StatesGroup):
    """Главная админ-панель."""
    broadcast_content = State()
    broadcast_confirm = State()
    ban_query = State()
    unban_query = State()
    user_lookup = State()
    user_message = State()
    sub_channel = State()
    donate_token = State()


class DonateInput(StatesGroup):
    """Своя сумма благодарности."""
    waiting_amount = State()
