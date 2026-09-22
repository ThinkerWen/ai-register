import random
import string
import time

import requests
from requests.exceptions import ConnectionError, ProxyError, ReadTimeout, SSLError

from util.providers.base import MailProvider, MailProviderError

_DUCKMAIL_TIMEOUT = 25
_MAX_RETRIES_PER_ROUTE = 2


def default_generate_password(length=14):
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%&*"
    pwd = [random.choice(lower), random.choice(upper), random.choice(digits), random.choice(special)]
    all_chars = lower + upper + digits + special
    pwd += [random.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


def create_duckmail_session(user_agent=None, proxy=None):
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def request_duckmail(method, url, *, user_agent=None, proxy=None, timeout=_DUCKMAIL_TIMEOUT, **kwargs):
    """DuckMail 请求包装：先走代理，失败后自动回退直连，并进行轻量重试。"""
    route_proxies = [proxy]
    if proxy:
        route_proxies.append(None)

    last_exc = None
    for route_proxy in route_proxies:
        for attempt in range(1, _MAX_RETRIES_PER_ROUTE + 1):
            session = create_duckmail_session(user_agent=user_agent, proxy=route_proxy)
            try:
                return session.request(method, url, timeout=timeout, **kwargs)
            except (ReadTimeout, ProxyError, SSLError, ConnectionError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES_PER_ROUTE:
                    time.sleep(0.6 * attempt)
                    continue
                break
            finally:
                session.close()

    if last_exc:
        raise last_exc
    raise MailProviderError("DuckMail 请求失败")


class DuckMailProvider(MailProvider):
    name = "duckmail"

    def __init__(self, api_base, bearer, user_agent=None, proxy=None, impersonate="chrome131", password_generator=None):
        _ = impersonate  # requests 不支持该参数，保留兼容。
        self.api_base = (api_base or "").rstrip("/")
        self.bearer = bearer or ""
        self.user_agent = user_agent
        self.proxy = proxy
        self.password_generator = password_generator or default_generate_password

        if not self.api_base:
            raise MailProviderError("duckmail.api_base 未配置")

    def create_temp_email(self):
        if not self.bearer:
            raise MailProviderError("duckmail.bearer 未配置")

        chars = string.ascii_lowercase + string.digits
        length = random.randint(8, 13)
        email_local = "".join(random.choice(chars) for _ in range(length))
        email = f"{email_local}@duckmail.sbs"
        password = self.password_generator()

        headers = {"Authorization": f"Bearer {self.bearer}"}
        payload = {"address": email, "password": password}
        res = request_duckmail(
            "POST",
            f"{self.api_base}/accounts",
            user_agent=self.user_agent,
            proxy=self.proxy,
            json=payload,
            headers=headers,
        )
        if res.status_code not in [200, 201]:
            raise MailProviderError(f"创建邮箱失败: {res.status_code} - {res.text[:200]}")

        time.sleep(0.5)
        token_res = request_duckmail(
            "POST",
            f"{self.api_base}/token",
            user_agent=self.user_agent,
            proxy=self.proxy,
            json={"address": email, "password": password},
        )
        if token_res.status_code == 200:
            token_data = token_res.json()
            mail_token = token_data.get("token")
            if mail_token:
                return email, password, mail_token

        raise MailProviderError(f"获取邮件 Token 失败: {token_res.status_code}")

    def fetch_emails(self, mail_token):
        try:
            headers = {"Authorization": f"Bearer {mail_token}"}
            res = request_duckmail(
                "GET",
                f"{self.api_base}/messages",
                user_agent=self.user_agent,
                proxy=self.proxy,
                headers=headers,
            )
            if res.status_code == 200:
                data = res.json()
                return data.get("hydra:member") or data.get("member") or data.get("data") or []
            return []
        except Exception:
            return []

    def fetch_email_detail(self, mail_token, msg_id):
        try:
            headers = {"Authorization": f"Bearer {mail_token}"}
            if isinstance(msg_id, str) and msg_id.startswith("/messages/"):
                msg_id = msg_id.split("/")[-1]

            res = request_duckmail(
                "GET",
                f"{self.api_base}/messages/{msg_id}",
                user_agent=self.user_agent,
                proxy=self.proxy,
                headers=headers,
            )
            if res.status_code == 200:
                return res.json()
        except Exception:
            pass
        return None
