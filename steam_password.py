from __future__ import annotations

import asyncio
import base64
import hmac
import json
import secrets
import string
import struct
import threading
import time
from hashlib import sha1
from typing import Any

import aiohttp
import rsa
from pysteamauth.auth import Steam as BaseSteam
from yarl import URL as YarlURL

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - handled at runtime.
    async_playwright = None


STEAMCOMMUNITY = YarlURL("https://steamcommunity.com")


def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(char.isupper() for char in password)
            and any(char.islower() for char in password)
            and any(char.isdigit() for char in password)
        ):
            return password


def _decode_secret(secret: str) -> bytes:
    padded = secret + "=" * ((4 - len(secret) % 4) % 4)
    return base64.b64decode(padded)


def _confirmation_key(identity_secret: str, timestamp: int, tag: str) -> str:
    payload = struct.pack(">Q", timestamp) + tag.encode()
    return base64.b64encode(hmac.new(_decode_secret(identity_secret), payload, sha1).digest()).decode()


async def _steam_time_offset() -> int:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.steampowered.com/ITwoFactorService/QueryTime/v0001",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return int(data["response"]["server_time"]) - int(time.time())
    except Exception:
        return 0
    return 0


def _set_session_cookies(session: aiohttp.ClientSession, cookies: dict[str, str]) -> None:
    session.cookie_jar.update_cookies(cookies, STEAMCOMMUNITY)


class SteamSession(BaseSteam):
    def __init__(self, login: str, password: str, mafile: dict[str, Any]):
        self._login = login
        self._password = password
        super().__init__(
            login=login,
            password=password,
            steamid=int(mafile.get("Session", {}).get("SteamID", 0)),
            shared_secret=mafile.get("shared_secret", ""),
            identity_secret=mafile.get("identity_secret", ""),
            device_id=mafile.get("device_id", ""),
        )

    @property
    def login(self):
        return self._login

    @property
    def password(self):
        return self._password

    async def json_request(self, url: str, method: str = "GET", **kwargs):
        return json.loads(await self.request(url, method, **kwargs))

    async def raw_request(self, url: str, method: str = "GET", **kwargs):
        from urllib3.util import parse_url

        return await self._requests.request(
            url=url,
            method=method,
            cookies=await self.cookies(parse_url(url).host),
            **kwargs,
        )


async def verify_account_async(login: str, password: str, mafile: dict[str, Any], timeout: int = 60) -> None:
    """Check that Steam credentials and maFile can actually log in."""
    login = (login or "").strip()
    password = (password or "").strip()
    if not login:
        raise RuntimeError("Не указан логин Steam.")
    if not password:
        raise RuntimeError("Не указан пароль Steam.")
    if not isinstance(mafile, dict):
        raise RuntimeError("maFile должен быть JSON-объектом.")
    if not mafile.get("shared_secret"):
        raise RuntimeError("В maFile нет shared_secret, Steam Guard код работать не будет.")
    if not mafile.get("Session", {}).get("SteamID"):
        raise RuntimeError("В maFile нет Session.SteamID.")

    session = SteamSession(login, password, mafile)
    try:
        await asyncio.wait_for(session.login_to_steam(), timeout=timeout)
        steamid = str(getattr(session, "_steamid", "") or "")
        mafile_steamid = str(mafile.get("Session", {}).get("SteamID", "") or "")
        if steamid and mafile_steamid and steamid != mafile_steamid:
            raise RuntimeError("maFile принадлежит другому Steam-аккаунту.")
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Steam слишком долго не отвечает, проверка не завершилась.") from exc
    except Exception as exc:
        raise RuntimeError(f"Steam не принял логин/пароль/maFile: {exc}") from exc


class SteamPasswordChanger:
    HELP = "https://help.steampowered.com/en/wizard"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    COOKIE_DOMAINS = ("help.steampowered.com", "store.steampowered.com", "steamcommunity.com")

    def __init__(self, login: str, current_password: str, mafile: dict[str, Any]):
        self.login = login
        self.current_password = current_password
        self.mafile = mafile
        self.identity_secret = mafile.get("identity_secret", "")
        self.device_id = mafile.get("device_id", "")
        self.steamid = str(mafile.get("Session", {}).get("SteamID", ""))
        self.steam: SteamSession | None = None

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://help.steampowered.com",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": self.USER_AGENT,
        }

    async def change_password(self) -> str:
        if async_playwright is None:
            raise RuntimeError("Playwright не установлен, без него Steam не показывает mobile confirmation.")
        if not self.identity_secret:
            raise RuntimeError("В maFile нет identity_secret, подтверждение смены пароля невозможно.")
        new_password = generate_password()
        self.steam = SteamSession(self.login, self.current_password, self.mafile)
        await self._login()
        params = await self._get_change_params()
        await self._trigger_confirmation(params)
        await asyncio.sleep(5)
        if not await self._confirm_recovery(params):
            raise RuntimeError("Steam не подтвердил смену пароля через mobile confirmation.")
        await self._poll_recovery(params)
        await self._verify_current_password(params)
        await self._set_new_password(params, new_password)
        return new_password

    async def _login(self) -> None:
        assert self.steam is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                await asyncio.sleep(1)
                await self.steam.login_to_steam()
                return
            except Exception as exc:
                last_error = exc
                text = str(exc)
                if "TwoFactorCodeMismatch" in text:
                    await asyncio.sleep(5)
                    continue
                if "RateLimitExceeded" in text:
                    await asyncio.sleep(30 * (attempt + 1))
                    continue
                raise
        raise RuntimeError(f"Steam login failed: {last_error}")

    async def _steam_req(self, endpoint: str, method: str = "POST", check_error: bool = True, **kwargs) -> dict:
        assert self.steam is not None
        sessionid = await self.steam.sessionid("help.steampowered.com")
        payload_key = "data" if method == "POST" else "params"
        kwargs.setdefault(payload_key, {})["sessionid"] = sessionid
        result = await self.steam.json_request(
            method=method,
            url=f"{self.HELP}/{endpoint}",
            headers=self.headers(),
            **kwargs,
        )
        if check_error and result.get("errorMsg"):
            raise RuntimeError(f"{endpoint}: {result['errorMsg']}")
        return result

    async def _get_change_params(self) -> dict[str, Any]:
        assert self.steam is not None
        response = await self.steam.raw_request(
            method="GET",
            url="https://help.steampowered.com/wizard/HelpChangePassword?redir=store/account/",
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Referer": "https://store.steampowered.com/",
                "User-Agent": self.USER_AGENT,
            },
            allow_redirects=True,
        )
        if response.history:
            query = dict(YarlURL(response.real_url).query)
            result: dict[str, Any] = {}
            for key, value in query.items():
                try:
                    result[key] = int(value)
                except (TypeError, ValueError):
                    result[key] = value
            for key in ("lost", "s", "account", "reset", "issueid"):
                result.setdefault(key, 0)
            return result
        raise RuntimeError("Steam не выдал параметры смены пароля.")

    async def _cookies_dict(self) -> dict[str, str]:
        assert self.steam is not None
        cookies: dict[str, str] = {}
        for domain in self.COOKIE_DOMAINS:
            try:
                cookies.update(await self.steam.cookies(domain))
            except Exception:
                pass
        return cookies

    async def _trigger_confirmation(self, params: dict[str, Any]) -> None:
        assert self.steam is not None
        cookies = []
        for domain in self.COOKIE_DOMAINS:
            try:
                for name, value in (await self.steam.cookies(domain)).items():
                    cookies.append({"name": name, "value": value, "domain": f".{domain}", "path": "/"})
            except Exception:
                pass
        url = (
            "https://help.steampowered.com/en/wizard/HelpWithLoginInfoEnterCode"
            f"?s={params.get('s', 0)}&account={params.get('account', 0)}"
            f"&reset={params.get('reset', 0)}&lost={params.get('lost', 0)}"
            f"&issueid={params.get('issueid', 0)}"
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=self.USER_AGENT,
                locale="en-US",
                viewport={"width": 1280, "height": 720},
            )
            try:
                if cookies:
                    await context.add_cookies(cookies)
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(5)
            finally:
                await context.close()
                await browser.close()

    async def _confirm_recovery(self, params: dict[str, Any]) -> bool:
        cookies = await self._cookies_dict()
        session = aiohttp.ClientSession()
        _set_session_cookies(session, cookies)
        try:
            try:
                from steamlib.api.trade import SteamTrade
                from steamlib.api.trade.exceptions import NotFoundMobileConfirmationError

                trade = SteamTrade(self.steam)
            except Exception:
                trade = None
                NotFoundMobileConfirmationError = Exception

            for _ in range(25):
                for confirmation in await self._get_confirmations(session):
                    if await self._accept_confirmation(session, confirmation):
                        return True
                if trade is not None:
                    try:
                        if await trade.mobile_confirm_by_creator_id(params.get("s", 0)):
                            return True
                    except NotFoundMobileConfirmationError:
                        pass
                    except Exception:
                        pass
                await asyncio.sleep(3)
        finally:
            await session.close()
        return False

    async def _get_confirmations(self, session: aiohttp.ClientSession) -> list[dict[str, Any]]:
        timestamp = int(time.time()) + await _steam_time_offset()
        key = _confirmation_key(self.identity_secret, timestamp, "getlist")
        params = {
            "p": self.device_id,
            "a": self.steamid,
            "k": key,
            "t": str(timestamp),
            "m": "react",
            "tag": "getlist",
        }
        try:
            async with session.get("https://steamcommunity.com/mobileconf/getlist", params=params) as response:
                data = await response.json()
                return data.get("conf", []) if data.get("success") else []
        except Exception:
            return []

    async def _accept_confirmation(self, session: aiohttp.ClientSession, confirmation: dict[str, Any]) -> bool:
        timestamp = int(time.time()) + await _steam_time_offset()
        key = _confirmation_key(self.identity_secret, timestamp, "allow")
        params = {
            "p": self.device_id,
            "a": self.steamid,
            "k": key,
            "t": str(timestamp),
            "m": "react",
            "tag": "allow",
            "op": "allow",
            "cid": str(confirmation["id"]),
            "ck": confirmation["nonce"],
        }
        try:
            async with session.get("https://steamcommunity.com/mobileconf/ajaxop", params=params) as response:
                data = await response.json()
                return bool(data.get("success"))
        except Exception:
            return False

    async def _poll_recovery(self, params: dict[str, Any]) -> bool:
        for _ in range(15):
            result = await self._steam_req(
                "AjaxPollAccountRecoveryConfirmation",
                check_error=False,
                data={
                    "wizard_ajax": 1,
                    "s": params.get("s"),
                    "reset": params.get("reset"),
                    "lost": params.get("lost", 0),
                    "method": 8,
                    "issueid": params.get("issueid"),
                    "gamepad": 0,
                },
            )
            if result.get("success") or result.get("continue"):
                return True
            if result.get("errorMsg"):
                break
            await asyncio.sleep(2)
        return False

    async def _rsa_key(self) -> dict[str, Any]:
        assert self.steam is not None
        sessionid = await self.steam.sessionid("help.steampowered.com")
        return await self.steam.json_request(
            method="POST",
            url="https://help.steampowered.com/en/login/getrsakey/",
            data={"sessionid": sessionid, "username": self.login},
            headers=self.headers(),
        )

    async def _verify_current_password(self, params: dict[str, Any]) -> None:
        key = await self._rsa_key()
        encrypted = self._encrypt(self.current_password, key["publickey_mod"], key["publickey_exp"])
        await self._steam_req(
            "AjaxVerifyAccountRecoveryCode",
            method="GET",
            params={
                "code": "",
                "s": params.get("s"),
                "reset": params.get("reset"),
                "lost": params.get("lost", 0),
                "method": 8,
                "issueid": params.get("issueid"),
                "wizard_ajax": 1,
                "gamepad": 0,
            },
        )
        await self._steam_req(
            "AjaxAccountRecoveryGetNextStep",
            data={
                "wizard_ajax": 1,
                "s": params.get("s"),
                "account": params.get("account"),
                "reset": params.get("reset"),
                "issueid": params.get("issueid"),
                "lost": 2,
            },
        )
        await self._steam_req(
            "AjaxAccountRecoveryVerifyPassword/",
            data={"s": params.get("s"), "lost": 2, "reset": 1, "password": encrypted, "rsatimestamp": key["timestamp"]},
        )

    async def _set_new_password(self, params: dict[str, Any], new_password: str) -> None:
        key = await self._rsa_key()
        await self._steam_req("AjaxCheckPasswordAvailable/", data={"wizard_ajax": 1, "password": new_password})
        encrypted = self._encrypt(new_password, key["publickey_mod"], key["publickey_exp"])
        await self._steam_req(
            "AjaxAccountRecoveryChangePassword/",
            data={
                "wizard_ajax": 1,
                "s": params.get("s"),
                "account": params.get("account"),
                "password": encrypted,
                "rsatimestamp": key["timestamp"],
            },
        )

    @staticmethod
    def _encrypt(password: str, modulus: str, exponent: str) -> str:
        key = rsa.PublicKey(n=int(modulus, 16), e=int(exponent, 16))
        return base64.b64encode(rsa.encrypt(password.encode("ascii"), key)).decode()


async def change_password_async(login: str, current_password: str, mafile: dict[str, Any]) -> str:
    return await SteamPasswordChanger(login, current_password, mafile).change_password()


def change_password_sync(login: str, current_password: str, mafile: dict[str, Any], timeout: int = 180) -> str:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if not loop or not loop.is_running():
        return asyncio.run(change_password_async(login, current_password, mafile))

    new_loop = asyncio.new_event_loop()
    result: list[str | None] = [None]
    error: list[Exception | None] = [None]

    def run() -> None:
        try:
            result[0] = new_loop.run_until_complete(change_password_async(login, current_password, mafile))
        except Exception as exc:
            error[0] = exc
        finally:
            new_loop.close()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=timeout)
    if error[0]:
        raise error[0]
    if result[0] is None:
        raise RuntimeError("Смена пароля Steam не успела завершиться.")
    return result[0]
