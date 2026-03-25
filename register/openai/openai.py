"""
ChatGPT 批量自动注册工具 (并发版)
依赖: pip install curl_cffi
功能: 使用临时邮箱，并发自动注册 ChatGPT 账号，自动获取 OTP 验证码
"""

import base64
import hashlib
import json
import os
import random
import re
import secrets
import string
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional, cast
from urllib.parse import urlparse, parse_qs, urlencode

from curl_cffi import requests as curl_requests

from register.base import ModelProvider, ModelProviderError
from util import config as config_utils
from util import cpa as cpa_utils
from util import get_logger, setup_logger
from util import mail as mail_utils

setup_logger()
logger = get_logger("openai")


class OpenAIModelProvider(ModelProvider):
    name = "openai"

    def __init__(
        self,
        enable_oauth=True,
        oauth_required=True,
        oauth_issuer="",
        oauth_client_id="",
        oauth_redirect_uri="",
    ):
        self._enable_oauth = bool(enable_oauth)
        self._oauth_required = bool(oauth_required)
        self._oauth_issuer = str(oauth_issuer or "").strip()
        self._oauth_client_id = str(oauth_client_id or "").strip()
        self._oauth_redirect_uri = str(oauth_redirect_uri or "").strip()

        if not self._oauth_issuer:
            raise ModelProviderError("providers.openai.oauth_issuer 未配置")
        if not self._oauth_client_id:
            raise ModelProviderError("providers.openai.oauth_client_id 未配置")
        if not self._oauth_redirect_uri:
            raise ModelProviderError("providers.openai.oauth_redirect_uri 未配置")

    def oauth_enabled(self) -> bool:
        return self._enable_oauth

    def oauth_required(self) -> bool:
        return self._oauth_required

    def oauth_issuer(self) -> str:
        return self._oauth_issuer

    def oauth_client_id(self) -> str:
        return self._oauth_client_id

    def oauth_redirect_uri(self) -> str:
        return self._oauth_redirect_uri

    def run_batch(
        self,
        total_accounts: Optional[int] = None,
        max_workers: Optional[int] = None,
        proxy: Optional[str] = None,
    ):
        config = config_utils.get_register_config(logger=logger)
        resolved_total_accounts = (
            int(total_accounts)
            if total_accounts is not None
            else int(config.get("total_accounts") or 0)
        )
        resolved_max_workers = (
            int(max_workers)
            if max_workers is not None
            else int(config.get("concurrency") or 1)
        )
        resolved_proxy = (
            str(proxy) if proxy is not None else str(config.get("proxy") or "")
        )
        return run_batch(
            total_accounts=resolved_total_accounts,
            max_workers=resolved_max_workers,
            proxy=resolved_proxy,
        )


def _create_openai_provider(config):
    provider_map = (config or {}).get("model_providers") or {}
    provider_cfg = provider_map.get("openai") or {}
    return OpenAIModelProvider(
        enable_oauth=provider_cfg.get("enable_oauth", True),
        oauth_required=provider_cfg.get("oauth_required", True),
        oauth_issuer=provider_cfg.get("oauth_issuer"),
        oauth_client_id=provider_cfg.get("oauth_client_id"),
        oauth_redirect_uri=provider_cfg.get("oauth_redirect_uri"),
    )


_CONFIG = config_utils.get_register_config(logger=logger)
_FALLBACK_OPENAI_PROVIDER = OpenAIModelProvider(
    enable_oauth=True,
    oauth_required=True,
    oauth_issuer="https://auth.openai.com",
    oauth_client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    oauth_redirect_uri="http://localhost:1455/auth/callback",
)


def _cfg(key):
    return _CONFIG[key]


_MODEL_PROVIDER_INFO = {
    "name": str((_CONFIG or {}).get("model_provider") or "openai").strip().lower()
}
try:
    _MODEL_PROVIDER = _create_openai_provider(_CONFIG)
    _model_ok, _model_err = True, "ok"
except Exception as e:
    _MODEL_PROVIDER = _FALLBACK_OPENAI_PROVIDER
    _model_ok, _model_err = False, str(e)
_MODEL_PROVIDER = cast(OpenAIModelProvider, _MODEL_PROVIDER)
if not _model_ok:
    logger.warning(f"模型 provider 配置异常: {_model_err}")
    logger.warning("请检查 config.yaml 的 model_provider / providers 配置")


def _model_provider_name():
    return _MODEL_PROVIDER_INFO.get("name", "openai")


def _oauth_issuer():
    if _MODEL_PROVIDER is None:
        raise ModelProviderError("openai provider 未初始化")
    return str(_MODEL_PROVIDER.oauth_issuer()).rstrip("/")


def _token_base_dir():
    return str(_cfg("token_dir") or "token_dir")


_MAIL_PROVIDER_INFO = mail_utils.get_mail_provider_info(_CONFIG)
_mail_ok, _mail_err = mail_utils.validate_mail_provider_config(_CONFIG)
if not _mail_ok:
    logger.warning(f"邮箱 provider 配置异常: {_mail_err}")
    logger.warning("请检查 config.yaml 的 mail_provider / mail_providers 配置")

# 全局线程锁
_file_lock = threading.Lock()


# Chrome 指纹配置: impersonate 与 sec-ch-ua 必须匹配真实浏览器
_CHROME_PROFILES = [
    {
        "major": 131,
        "impersonate": "chrome131",
        "build": 6778,
        "patch_range": (69, 205),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "major": 133,
        "impersonate": "chrome133a",
        "build": 6943,
        "patch_range": (33, 153),
        "sec_ch_ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    },
    {
        "major": 136,
        "impersonate": "chrome136",
        "build": 7103,
        "patch_range": (48, 175),
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    },
]


def _random_chrome_version() -> tuple[str, int, str, str, str]:
    profile = random.choice(_CHROME_PROFILES)
    major = profile["major"]
    build = profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_ver = f"{major}.0.{build}.{patch}"
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    return profile["impersonate"], major, full_ver, ua, profile["sec_ch_ua"]


def _random_delay(low=0.3, high=1.0):
    time.sleep(random.uniform(low, high))


def _make_trace_headers():
    trace_id = random.randint(10**17, 10**18 - 1)
    parent_id = random.randint(10**17, 10**18 - 1)
    tp = f"00-{uuid.uuid4().hex}-{format(parent_id, '016x')}-01"
    return {
        "traceparent": tp,
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": str(trace_id),
        "x-datadog-parent-id": str(parent_id),
    }


def _generate_pkce():
    code_verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    )
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


class SentinelTokenGenerator:
    """纯 Python 版本 sentinel token 生成器（PoW）"""

    MAX_ATTEMPTS = 500000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id=None, user_agent=None):
        self.device_id = device_id or str(uuid.uuid4())
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        )
        self.requirements_seed = str(random.random())
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str):
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        h &= 0xFFFFFFFF
        return format(h, "08x")

    def _get_config(self):
        now_str = time.strftime(
            "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)",
            time.gmtime(),
        )
        perf_now = random.uniform(1000, 50000)
        time_origin = time.time() * 1000 - perf_now
        nav_prop = random.choice(
            [
                "vendorSub",
                "productSub",
                "vendor",
                "maxTouchPoints",
                "scheduling",
                "userActivation",
                "doNotTrack",
                "geolocation",
                "connection",
                "plugins",
                "mimeTypes",
                "pdfViewerEnabled",
                "webkitTemporaryStorage",
                "webkitPersistentStorage",
                "hardwareConcurrency",
                "cookieEnabled",
                "credentials",
                "mediaDevices",
                "permissions",
                "locks",
                "ink",
            ]
        )
        nav_val = f"{nav_prop}-undefined"

        return [
            "1920x1080",
            now_str,
            4294705152,
            random.random(),
            self.user_agent,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None,
            None,
            "en-US",
            "en-US,en",
            random.random(),
            nav_val,
            random.choice(
                ["location", "implementation", "URL", "documentURI", "compatMode"]
            ),
            random.choice(
                ["Object", "Function", "Array", "Number", "parseFloat", "undefined"]
            ),
            perf_now,
            self.sid,
            "",
            random.choice([4, 8, 12, 16]),
            time_origin,
        ]

    @staticmethod
    def _base64_encode(data):
        raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        return base64.b64encode(raw).decode("ascii")

    def _run_check(self, start_time, seed, difficulty, config, nonce):
        config[3] = nonce
        config[9] = round((time.time() - start_time) * 1000)
        data = self._base64_encode(config)
        hash_hex = self._fnv1a_32(seed + data)
        diff_len = len(difficulty)
        if hash_hex[:diff_len] <= difficulty:
            return data + "~S"
        return None

    def generate_token(self, seed=None, difficulty=None):
        seed = seed if seed is not None else self.requirements_seed
        difficulty = str(difficulty or "0")
        start_time = time.time()
        config = self._get_config()

        for i in range(self.MAX_ATTEMPTS):
            result = self._run_check(start_time, seed, difficulty, config, i)
            if result:
                return "gAAAAAB" + result
        return "gAAAAAB" + self.ERROR_PREFIX + self._base64_encode(str(None))

    def generate_requirements_token(self):
        config = self._get_config()
        config[3] = 1
        config[9] = round(random.uniform(5, 50))
        data = self._base64_encode(config)
        return "gAAAAAC" + data


def fetch_sentinel_challenge(
    session,
    device_id,
    flow="authorize_continue",
    user_agent=None,
    sec_ch_ua=None,
    impersonate=None,
):
    generator = SentinelTokenGenerator(device_id=device_id, user_agent=user_agent)
    req_body = {
        "p": generator.generate_requirements_token(),
        "id": device_id,
        "flow": flow,
    }
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
        "Origin": "https://sentinel.openai.com",
        "User-Agent": user_agent or "Mozilla/5.0",
        "sec-ch-ua": sec_ch_ua
        or '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    kwargs = {
        "data": json.dumps(req_body),
        "headers": headers,
        "timeout": 20,
    }
    if impersonate:
        kwargs["impersonate"] = impersonate

    try:
        resp = session.post(
            "https://sentinel.openai.com/backend-api/sentinel/req", **kwargs
        )
    except Exception:
        return None

    if resp.status_code != 200:
        return None

    try:
        return resp.json()
    except Exception:
        return None


def build_sentinel_token(
    session,
    device_id,
    flow="authorize_continue",
    user_agent=None,
    sec_ch_ua=None,
    impersonate=None,
):
    challenge = fetch_sentinel_challenge(
        session,
        device_id,
        flow=flow,
        user_agent=user_agent,
        sec_ch_ua=sec_ch_ua,
        impersonate=impersonate,
    )
    if not challenge:
        return None

    c_value = challenge.get("token", "")
    if not c_value:
        return None

    pow_data = challenge.get("proofofwork") or {}
    generator = SentinelTokenGenerator(device_id=device_id, user_agent=user_agent)

    if pow_data.get("required") and pow_data.get("seed"):
        p_value = generator.generate_token(
            seed=pow_data.get("seed"),
            difficulty=pow_data.get("difficulty", "0"),
        )
    else:
        p_value = generator.generate_requirements_token()

    return json.dumps(
        {
            "p": p_value,
            "t": "",
            "c": c_value,
            "id": device_id,
            "flow": flow,
        },
        separators=(",", ":"),
    )


def _extract_code_from_url(url: str):
    if not url or "code=" not in url:
        return None
    try:
        return parse_qs(urlparse(url).query).get("code", [None])[0]
    except Exception:
        return None


def _decode_jwt_payload(token: str):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


def _save_codex_tokens(email: str, tokens: Optional[dict[str, Any]]):
    if not isinstance(tokens, dict):
        return

    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    id_token = tokens.get("id_token", "")

    if not access_token:
        return

    payload = _decode_jwt_payload(access_token)
    auth_info = payload.get("https://api.openai.com/auth", {})
    account_id = auth_info.get("chatgpt_account_id", "")

    exp_timestamp = payload.get("exp")
    expired_str = ""
    if isinstance(exp_timestamp, int) and exp_timestamp > 0:
        from datetime import datetime, timezone, timedelta

        exp_dt = datetime.fromtimestamp(exp_timestamp, tz=timezone(timedelta(hours=8)))
        expired_str = exp_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    from datetime import datetime, timezone, timedelta

    now = datetime.now(tz=timezone(timedelta(hours=8)))
    token_data = {
        "type": "codex",
        "email": email,
        "expired": expired_str,
        "id_token": id_token,
        "account_id": account_id,
        "access_token": access_token,
        "last_refresh": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "refresh_token": refresh_token,
    }

    base_dir = os.path.dirname(os.path.abspath(__file__))
    token_base = _token_base_dir()
    model_name = _model_provider_name()
    root_dir = (
        token_base if os.path.isabs(token_base) else os.path.join(base_dir, token_base)
    )
    token_dir = os.path.join(root_dir, model_name)
    os.makedirs(token_dir, exist_ok=True)

    token_path = os.path.join(token_dir, f"{email}.json")
    with _file_lock:
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f, ensure_ascii=False)

    cpa_utils.upload_token_json_from_config(
        filepath=token_path,
        config=_CONFIG,
        proxy=_cfg("proxy"),
        logger=lambda msg: logger.info(msg),
    )


def _generate_password(length=14):
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%&*"
    pwd = [
        random.choice(lower),
        random.choice(upper),
        random.choice(digits),
        random.choice(special),
    ]
    all_chars = lower + upper + digits + special
    pwd += [random.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


def _random_name():
    first = random.choice(
        [
            "James",
            "Emma",
            "Liam",
            "Olivia",
            "Noah",
            "Ava",
            "Ethan",
            "Sophia",
            "Lucas",
            "Mia",
            "Mason",
            "Isabella",
            "Logan",
            "Charlotte",
            "Alexander",
            "Amelia",
            "Benjamin",
            "Harper",
            "William",
            "Evelyn",
            "Henry",
            "Abigail",
            "Sebastian",
            "Emily",
            "Jack",
            "Elizabeth",
        ]
    )
    last = random.choice(
        [
            "Smith",
            "Johnson",
            "Brown",
            "Davis",
            "Wilson",
            "Moore",
            "Taylor",
            "Clark",
            "Hall",
            "Young",
            "Anderson",
            "Thomas",
            "Jackson",
            "White",
            "Harris",
            "Martin",
            "Thompson",
            "Garcia",
            "Robinson",
            "Lewis",
            "Walker",
            "Allen",
            "King",
            "Wright",
            "Scott",
            "Green",
        ]
    )
    return f"{first} {last}"


def _random_birthdate():
    y = random.randint(1985, 2002)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{y}-{m:02d}-{d:02d}"


class ChatGPTRegister:
    BASE = "https://chatgpt.com"
    AUTH = "https://auth.openai.com"

    def __init__(self, proxy: Optional[str] = None, tag: str = ""):
        self.tag = tag  # 线程标识，用于日志
        self.device_id = str(uuid.uuid4())
        self.auth_session_logging_id = str(uuid.uuid4())
        (
            self.impersonate,
            self.chrome_major,
            self.chrome_full,
            self.ua,
            self.sec_ch_ua,
        ) = _random_chrome_version()

        self.session = cast(
            Any, curl_requests.Session(impersonate=cast(Any, self.impersonate))
        )

        self.proxy = proxy
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

        self.session.headers.update(
            {
                "User-Agent": self.ua,
                "Accept-Language": random.choice(
                    [
                        "en-US,en;q=0.9",
                        "en-US,en;q=0.9,zh-CN;q=0.8",
                        "en,en-US;q=0.9",
                        "en-US,en;q=0.8",
                    ]
                ),
                "sec-ch-ua": self.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-ch-ua-arch": '"x86"',
                "sec-ch-ua-bitness": '"64"',
                "sec-ch-ua-full-version": f'"{self.chrome_full}"',
                "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
            }
        )

        self.session.cookies.set("oai-did", self.device_id, domain="chatgpt.com")
        self._callback_url = ""
        self._logger = get_logger(f"register:{self.tag or 'main'}")
        self.mail_provider = mail_utils.create_mail_provider(
            _CONFIG,
            user_agent=self.ua,
            proxy=self.proxy,
            impersonate=cast(Any, self.impersonate),
            password_generator=_generate_password,
        )

    def _log(self, step, method, url, status, body=None):
        prefix = f"[{self.tag}] " if self.tag else ""
        lines = [f"{prefix}{step} | {method} {url} | status={status}"]
        if body:
            try:
                lines.append(
                    f"{prefix}response={json.dumps(body, ensure_ascii=False)[:600]}"
                )
            except Exception:
                lines.append(f"{prefix}response={str(body)[:600]}")
        self._logger.debug(" | ".join(lines))

    def _print(self, msg):
        prefix = f"[{self.tag}] " if self.tag else ""
        # 请求过程细节走 DEBUG，流程步骤保留 INFO。
        debug_markers = (
            " -> ",
            "follow[",
            "请求异常",
            "响应解析失败",
            "cookies=",
            "page=",
            "next=",
            "命中",
            "OTP 等待中",
            "尝试 OTP",
            "无效，继续尝试",
        )
        if any(marker in msg for marker in debug_markers):
            self._logger.debug(f"{prefix}{msg}")
            return
        self._logger.info(f"{prefix}{msg}")

    # ==================== 临时邮箱 Provider ====================

    def create_temp_email(self):
        """创建临时邮箱，返回 (email, password, mail_token)"""
        return mail_utils.create_temp_email(
            provider=self.mail_provider,
        )

    def _fetch_emails(self, mail_token: str):
        """从邮箱 provider 获取邮件列表"""
        return mail_utils.fetch_emails(
            mail_token=mail_token, provider=self.mail_provider
        )

    def _fetch_email_detail(self, mail_token: str, msg_id: str):
        """获取邮箱 provider 单封邮件详情"""
        return mail_utils.fetch_email_detail(
            msg_id=msg_id, mail_token=mail_token, provider=self.mail_provider
        )

    def _extract_verification_code(self, email_content: str):
        """从邮件内容提取 6 位验证码"""
        return mail_utils.extract_verification_code(email_content)

    def wait_for_verification_email(self, mail_token: str, timeout: int = 120):
        """等待并提取 OpenAI 验证码"""
        self._print(f"[OTP] 等待验证码邮件 (最多 {timeout}s)...")
        code = mail_utils.wait_for_verification_email(
            mail_token=mail_token,
            timeout=timeout,
            provider=self.mail_provider,
            logger=lambda msg: self._print(f"[OTP] {msg}"),
        )
        if code:
            self._print(f"[OTP] 验证码: {code}")
            return code

        self._print(f"[OTP] 超时 ({timeout}s)")
        return None

    # ==================== 注册流程 ====================

    def visit_homepage(self):
        url = f"{self.BASE}/"
        r = self.session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            },
            allow_redirects=True,
        )
        self._log(
            "0. Visit homepage",
            "GET",
            url,
            r.status_code,
            {"cookies_count": len(self.session.cookies)},
        )

    def get_csrf(self) -> str:
        url = f"{self.BASE}/api/auth/csrf"
        r = self.session.get(
            url, headers={"Accept": "application/json", "Referer": f"{self.BASE}/"}
        )
        data = r.json()
        token = data.get("csrfToken", "")
        self._log("1. Get CSRF", "GET", url, r.status_code, data)
        if not token:
            raise Exception("Failed to get CSRF token")
        return token

    def signin(self, email: str, csrf: str) -> str:
        url = f"{self.BASE}/api/auth/signin/openai"
        params = {
            "prompt": "login",
            "ext-oai-did": self.device_id,
            "auth_session_logging_id": self.auth_session_logging_id,
            "screen_hint": "login_or_signup",
            "login_hint": email,
        }
        form_data = {"callbackUrl": f"{self.BASE}/", "csrfToken": csrf, "json": "true"}
        r = self.session.post(
            url,
            params=params,
            data=form_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Referer": f"{self.BASE}/",
                "Origin": self.BASE,
            },
        )
        data = r.json()
        authorize_url = data.get("url", "")
        self._log("2. Signin", "POST", url, r.status_code, data)
        if not authorize_url:
            raise Exception("Failed to get authorize URL")
        return authorize_url

    def authorize(self, url: str) -> str:
        r = self.session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{self.BASE}/",
                "Upgrade-Insecure-Requests": "1",
            },
            allow_redirects=True,
        )
        final_url = str(r.url)
        self._log("3. Authorize", "GET", url, r.status_code, {"final_url": final_url})
        return final_url

    def register(self, email: str, password: str):
        url = f"{self.AUTH}/api/accounts/user/register"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": f"{self.AUTH}/create-account/password",
            "Origin": self.AUTH,
        }
        headers.update(_make_trace_headers())
        r = self.session.post(
            url, json={"username": email, "password": password}, headers=headers
        )
        try:
            data = r.json()
        except Exception:
            data = {"text": r.text[:500]}
        self._log("4. Register", "POST", url, r.status_code, data)
        return r.status_code, data

    def send_otp(self):
        url = f"{self.AUTH}/api/accounts/email-otp/send"
        r = self.session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{self.AUTH}/create-account/password",
                "Upgrade-Insecure-Requests": "1",
            },
            allow_redirects=True,
        )
        try:
            data = r.json()
        except Exception:
            data = {"final_url": str(r.url), "status": r.status_code}
        self._log("5. Send OTP", "GET", url, r.status_code, data)
        return r.status_code, data

    def validate_otp(self, code: str):
        url = f"{self.AUTH}/api/accounts/email-otp/validate"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": f"{self.AUTH}/email-verification",
            "Origin": self.AUTH,
        }
        headers.update(_make_trace_headers())
        r = self.session.post(url, json={"code": code}, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"text": r.text[:500]}
        self._log("6. Validate OTP", "POST", url, r.status_code, data)
        return r.status_code, data

    def create_account(self, name: str, birthdate: str):
        url = f"{self.AUTH}/api/accounts/create_account"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": f"{self.AUTH}/about-you",
            "Origin": self.AUTH,
        }
        headers.update(_make_trace_headers())
        r = self.session.post(
            url, json={"name": name, "birthdate": birthdate}, headers=headers
        )
        try:
            data = r.json()
        except Exception:
            data = {"text": r.text[:500]}
        self._log("7. Create Account", "POST", url, r.status_code, data)
        if isinstance(data, dict):
            cb = data.get("continue_url") or data.get("url") or data.get("redirect_url")
            if cb:
                self._callback_url = cb
        return r.status_code, data

    def callback(self, url: str = None):
        if not url:
            url = self._callback_url
        if not url:
            self._print("[!] No callback URL, skipping.")
            return None, None
        r = self.session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            },
            allow_redirects=True,
        )
        self._log("8. Callback", "GET", url, r.status_code, {"final_url": str(r.url)})
        return r.status_code, {"final_url": str(r.url)}

    # ==================== 自动注册主流程 ====================

    def run_register(self, email, password, name, birthdate, mail_token):
        """使用 DuckMail 的注册流程"""
        self.visit_homepage()
        _random_delay(0.3, 0.8)
        csrf = self.get_csrf()
        _random_delay(0.2, 0.5)
        auth_url = self.signin(email, csrf)
        _random_delay(0.3, 0.8)

        final_url = self.authorize(auth_url)
        final_path = urlparse(final_url).path
        _random_delay(0.3, 0.8)

        self._print(f"Authorize → {final_path}")

        need_otp = False

        if "create-account/password" in final_path:
            self._print("全新注册流程")
            _random_delay(0.5, 1.0)
            status, data = self.register(email, password)
            if status != 200:
                raise Exception(f"Register 失败 ({status}): {data}")
            # register 之后可能还需要 send_otp（全新注册流程中 OTP 不一定在 authorize 时发送）
            _random_delay(0.3, 0.8)
            self.send_otp()
            need_otp = True
        elif "email-verification" in final_path or "email-otp" in final_path:
            self._print("跳到 OTP 验证阶段 (authorize 已触发 OTP，不再重复发送)")
            # 不调用 send_otp()，因为 authorize 重定向到 email-verification 时服务器已发送 OTP
            need_otp = True
        elif "about-you" in final_path:
            self._print("跳到填写信息阶段")
            _random_delay(0.5, 1.0)
            self.create_account(name, birthdate)
            _random_delay(0.3, 0.5)
            self.callback()
            return True
        elif "callback" in final_path or "chatgpt.com" in final_url:
            self._print("账号已完成注册")
            return True
        else:
            self._print(f"未知跳转: {final_url}")
            self.register(email, password)
            self.send_otp()
            need_otp = True

        if need_otp:
            # 使用 DuckMail 等待验证码
            otp_code = self.wait_for_verification_email(mail_token)
            if not otp_code:
                raise Exception("未能获取验证码")

            _random_delay(0.3, 0.8)
            status, data = self.validate_otp(otp_code)
            if status != 200:
                self._print("验证码失败，重试...")
                self.send_otp()
                _random_delay(1.0, 2.0)
                otp_code = self.wait_for_verification_email(mail_token, timeout=60)
                if not otp_code:
                    raise Exception("重试后仍未获取验证码")
                _random_delay(0.3, 0.8)
                status, data = self.validate_otp(otp_code)
                if status != 200:
                    raise Exception(f"验证码失败 ({status}): {data}")

        _random_delay(0.5, 1.5)
        status, data = self.create_account(name, birthdate)
        if status != 200:
            raise Exception(f"Create account 失败 ({status}): {data}")
        _random_delay(0.2, 0.5)
        self.callback()
        return True

    def _decode_oauth_session_cookie(self):
        jar = getattr(self.session.cookies, "jar", None)
        if jar is not None:
            cookie_items = list(jar)
        else:
            cookie_items = []

        for c in cookie_items:
            name = getattr(c, "name", "") or ""
            if "oai-client-auth-session" not in name:
                continue

            raw_val = (getattr(c, "value", "") or "").strip()
            if not raw_val:
                continue

            candidates = [raw_val]
            try:
                from urllib.parse import unquote

                decoded = unquote(raw_val)
                if decoded != raw_val:
                    candidates.append(decoded)
            except Exception:
                pass

            for val in candidates:
                try:
                    if (val.startswith('"') and val.endswith('"')) or (
                        val.startswith("'") and val.endswith("'")
                    ):
                        val = val[1:-1]

                    part = val.split(".")[0] if "." in val else val
                    pad = 4 - len(part) % 4
                    if pad != 4:
                        part += "=" * pad
                    raw = base64.urlsafe_b64decode(part)
                    data = json.loads(raw.decode("utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    continue
        return None

    def _oauth_allow_redirect_extract_code(
        self, url: str, referer: Optional[str] = None
    ):
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self.ua,
        }
        if referer:
            headers["Referer"] = referer

        try:
            resp = self.session.get(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=30,
                impersonate=self.impersonate,
            )
            final_url = str(resp.url)
            code = _extract_code_from_url(final_url)
            if code:
                self._print("[OAuth] allow_redirect 命中最终 URL code")
                return code

            for r in getattr(resp, "history", []) or []:
                loc = r.headers.get("Location", "")
                code = _extract_code_from_url(loc)
                if code:
                    self._print("[OAuth] allow_redirect 命中 history Location code")
                    return code
                code = _extract_code_from_url(str(r.url))
                if code:
                    self._print("[OAuth] allow_redirect 命中 history URL code")
                    return code
        except Exception as e:
            maybe_localhost = re.search(r"(https?://localhost[^\s\'\"]+)", str(e))
            if maybe_localhost:
                code = _extract_code_from_url(maybe_localhost.group(1))
                if code:
                    self._print("[OAuth] allow_redirect 从 localhost 异常提取 code")
                    return code
            self._print(f"[OAuth] allow_redirect 异常: {e}")

        return None

    def _oauth_follow_for_code(
        self, start_url: str, referer: Optional[str] = None, max_hops: int = 16
    ):
        oauth_issuer = _oauth_issuer()
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self.ua,
        }
        if referer:
            headers["Referer"] = referer

        current_url = start_url
        last_url = start_url

        for hop in range(max_hops):
            try:
                resp = self.session.get(
                    current_url,
                    headers=headers,
                    allow_redirects=False,
                    timeout=30,
                    impersonate=self.impersonate,
                )
            except Exception as e:
                maybe_localhost = re.search(r"(https?://localhost[^\s\'\"]+)", str(e))
                if maybe_localhost:
                    code = _extract_code_from_url(maybe_localhost.group(1))
                    if code:
                        self._print(f"[OAuth] follow[{hop + 1}] 命中 localhost 回调")
                        return code, maybe_localhost.group(1)
                self._print(f"[OAuth] follow[{hop + 1}] 请求异常: {e}")
                return None, last_url

            last_url = str(resp.url)
            self._print(
                f"[OAuth] follow[{hop + 1}] {resp.status_code} {last_url[:140]}"
            )
            code = _extract_code_from_url(last_url)
            if code:
                return code, last_url

            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location", "")
                if not loc:
                    return None, last_url
                if loc.startswith("/"):
                    loc = f"{oauth_issuer}{loc}"
                code = _extract_code_from_url(loc)
                if code:
                    return code, loc
                current_url = loc
                headers["Referer"] = last_url
                continue

            return None, last_url

        return None, last_url

    def _oauth_submit_workspace_and_org(self, consent_url: str):
        oauth_issuer = _oauth_issuer()
        session_data = self._decode_oauth_session_cookie()
        if not session_data:
            jar = getattr(self.session.cookies, "jar", None)
            if jar is not None:
                cookie_names = [getattr(c, "name", "") for c in list(jar)]
            else:
                cookie_names = list(self.session.cookies.keys())
            self._print(
                f"[OAuth] 无法解码 oai-client-auth-session, cookies={cookie_names[:12]}"
            )
            return None

        workspaces = session_data.get("workspaces", [])
        if not workspaces:
            self._print("[OAuth] session 中没有 workspace 信息")
            return None

        workspace_id = (workspaces[0] or {}).get("id")
        if not workspace_id:
            self._print("[OAuth] workspace_id 为空")
            return None

        h = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": oauth_issuer,
            "Referer": consent_url,
            "User-Agent": self.ua,
            "oai-device-id": self.device_id,
        }
        h.update(_make_trace_headers())

        resp = self.session.post(
            f"{oauth_issuer}/api/accounts/workspace/select",
            json={"workspace_id": workspace_id},
            headers=h,
            allow_redirects=False,
            timeout=30,
            impersonate=self.impersonate,
        )
        self._print(f"[OAuth] workspace/select -> {resp.status_code}")

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "")
            if loc.startswith("/"):
                loc = f"{oauth_issuer}{loc}"
            code = _extract_code_from_url(loc)
            if code:
                return code
            code, _ = self._oauth_follow_for_code(loc, referer=consent_url)
            if not code:
                code = self._oauth_allow_redirect_extract_code(loc, referer=consent_url)
            return code

        if resp.status_code != 200:
            self._print(f"[OAuth] workspace/select 失败: {resp.status_code}")
            return None

        try:
            ws_data = resp.json()
        except Exception:
            self._print("[OAuth] workspace/select 响应不是 JSON")
            return None

        ws_next = ws_data.get("continue_url", "")
        orgs = ws_data.get("data", {}).get("orgs", [])
        ws_page = (ws_data.get("page") or {}).get("type", "")
        self._print(
            f"[OAuth] workspace/select page={ws_page or '-'} next={(ws_next or '-')[:140]}"
        )

        org_id = None
        project_id = None
        if orgs:
            org_id = (orgs[0] or {}).get("id")
            projects = (orgs[0] or {}).get("projects", [])
            if projects:
                project_id = (projects[0] or {}).get("id")

        if org_id:
            org_body = {"org_id": org_id}
            if project_id:
                org_body["project_id"] = project_id

            h_org = dict(h)
            if ws_next:
                h_org["Referer"] = (
                    ws_next
                    if ws_next.startswith("http")
                    else f"{oauth_issuer}{ws_next}"
                )

            resp_org = self.session.post(
                f"{oauth_issuer}/api/accounts/organization/select",
                json=org_body,
                headers=h_org,
                allow_redirects=False,
                timeout=30,
                impersonate=self.impersonate,
            )
            self._print(f"[OAuth] organization/select -> {resp_org.status_code}")
            if resp_org.status_code in (301, 302, 303, 307, 308):
                loc = resp_org.headers.get("Location", "")
                if loc.startswith("/"):
                    loc = f"{oauth_issuer}{loc}"
                code = _extract_code_from_url(loc)
                if code:
                    return code
                code, _ = self._oauth_follow_for_code(loc, referer=h_org.get("Referer"))
                if not code:
                    code = self._oauth_allow_redirect_extract_code(
                        loc, referer=h_org.get("Referer")
                    )
                return code

            if resp_org.status_code == 200:
                try:
                    org_data = resp_org.json()
                except Exception:
                    self._print("[OAuth] organization/select 响应不是 JSON")
                    return None

                org_next = org_data.get("continue_url", "")
                org_page = (org_data.get("page") or {}).get("type", "")
                self._print(
                    f"[OAuth] organization/select page={org_page or '-'} next={(org_next or '-')[:140]}"
                )
                if org_next:
                    if org_next.startswith("/"):
                        org_next = f"{oauth_issuer}{org_next}"
                    code, _ = self._oauth_follow_for_code(
                        org_next, referer=h_org.get("Referer")
                    )
                    if not code:
                        code = self._oauth_allow_redirect_extract_code(
                            org_next, referer=h_org.get("Referer")
                        )
                    return code

        if ws_next:
            if ws_next.startswith("/"):
                ws_next = f"{oauth_issuer}{ws_next}"
            code, _ = self._oauth_follow_for_code(ws_next, referer=consent_url)
            if not code:
                code = self._oauth_allow_redirect_extract_code(
                    ws_next, referer=consent_url
                )
            return code

        return None

    def perform_codex_oauth_login_http(
        self, email: str, password: str, mail_token: str = None
    ):
        oauth_issuer = _oauth_issuer()
        oauth_client_id = _MODEL_PROVIDER.oauth_client_id()
        oauth_redirect_uri = _MODEL_PROVIDER.oauth_redirect_uri()
        self._print("[OAuth] 开始执行 Codex OAuth 纯协议流程...")

        # 兼容两种 domain 形式，确保 auth 域也带 oai-did
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")

        code_verifier, code_challenge = _generate_pkce()
        state = secrets.token_urlsafe(24)

        authorize_params = {
            "response_type": "code",
            "client_id": oauth_client_id,
            "redirect_uri": oauth_redirect_uri,
            "scope": "openid profile email offline_access",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        authorize_url = f"{oauth_issuer}/oauth/authorize?{urlencode(authorize_params)}"

        def _oauth_json_headers(referer: str):
            h = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": oauth_issuer,
                "Referer": referer,
                "User-Agent": self.ua,
                "oai-device-id": self.device_id,
            }
            h.update(_make_trace_headers())
            return h

        def _bootstrap_oauth_session():
            self._print("[OAuth] 1/7 GET /oauth/authorize")
            try:
                r = self.session.get(
                    authorize_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": f"{self.BASE}/",
                        "Upgrade-Insecure-Requests": "1",
                        "User-Agent": self.ua,
                    },
                    allow_redirects=True,
                    timeout=30,
                    impersonate=self.impersonate,
                )
            except Exception as e:
                self._print(f"[OAuth] /oauth/authorize 异常: {e}")
                return False, ""

            final_url = str(r.url)
            redirects = len(getattr(r, "history", []) or [])
            self._print(
                f"[OAuth] /oauth/authorize -> {r.status_code}, final={(final_url or '-')[:140]}, redirects={redirects}"
            )

            has_login = any(
                getattr(c, "name", "") == "login_session" for c in self.session.cookies
            )
            self._print(f"[OAuth] login_session: {'已获取' if has_login else '未获取'}")

            if not has_login:
                self._print("[OAuth] 未拿到 login_session，尝试访问 oauth2 auth 入口")
                oauth2_url = f"{oauth_issuer}/api/oauth/oauth2/auth"
                try:
                    r2 = self.session.get(
                        oauth2_url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Referer": authorize_url,
                            "Upgrade-Insecure-Requests": "1",
                            "User-Agent": self.ua,
                        },
                        params=authorize_params,
                        allow_redirects=True,
                        timeout=30,
                        impersonate=self.impersonate,
                    )
                    final_url = str(r2.url)
                    redirects2 = len(getattr(r2, "history", []) or [])
                    self._print(
                        f"[OAuth] /api/oauth/oauth2/auth -> {r2.status_code}, final={(final_url or '-')[:140]}, redirects={redirects2}"
                    )
                except Exception as e:
                    self._print(f"[OAuth] /api/oauth/oauth2/auth 异常: {e}")

                has_login = any(
                    getattr(c, "name", "") == "login_session"
                    for c in self.session.cookies
                )
                self._print(
                    f"[OAuth] login_session(重试): {'已获取' if has_login else '未获取'}"
                )

            return has_login, final_url

        def _post_authorize_continue(referer_url: str):
            sentinel_authorize = build_sentinel_token(
                self.session,
                self.device_id,
                flow="authorize_continue",
                user_agent=self.ua,
                sec_ch_ua=self.sec_ch_ua,
                impersonate=self.impersonate,
            )
            if not sentinel_authorize:
                self._print("[OAuth] authorize_continue 的 sentinel token 获取失败")
                return None

            headers_continue = _oauth_json_headers(referer_url)
            headers_continue["openai-sentinel-token"] = sentinel_authorize

            try:
                return self.session.post(
                    f"{oauth_issuer}/api/accounts/authorize/continue",
                    json={"username": {"kind": "email", "value": email}},
                    headers=headers_continue,
                    timeout=30,
                    allow_redirects=False,
                    impersonate=self.impersonate,
                )
            except Exception as e:
                self._print(f"[OAuth] authorize/continue 异常: {e}")
                return None

        has_login_session, authorize_final_url = _bootstrap_oauth_session()
        if not authorize_final_url:
            return None

        continue_referer = (
            authorize_final_url
            if authorize_final_url.startswith(oauth_issuer)
            else f"{oauth_issuer}/log-in"
        )

        self._print("[OAuth] 2/7 POST /api/accounts/authorize/continue")
        resp_continue = _post_authorize_continue(continue_referer)
        if resp_continue is None:
            return None

        self._print(f"[OAuth] /authorize/continue -> {resp_continue.status_code}")
        if resp_continue.status_code == 400 and "invalid_auth_step" in (
            resp_continue.text or ""
        ):
            self._print("[OAuth] invalid_auth_step，重新 bootstrap 后重试一次")
            has_login_session, authorize_final_url = _bootstrap_oauth_session()
            if not authorize_final_url:
                return None
            continue_referer = (
                authorize_final_url
                if authorize_final_url.startswith(oauth_issuer)
                else f"{oauth_issuer}/log-in"
            )
            resp_continue = _post_authorize_continue(continue_referer)
            if resp_continue is None:
                return None
            self._print(
                f"[OAuth] /authorize/continue(重试) -> {resp_continue.status_code}"
            )

        if resp_continue.status_code != 200:
            self._print(f"[OAuth] 邮箱提交失败: {resp_continue.text[:180]}")
            return None

        try:
            continue_data = resp_continue.json()
        except Exception:
            self._print("[OAuth] authorize/continue 响应解析失败")
            return None

        continue_url = continue_data.get("continue_url", "")
        page_type = (continue_data.get("page") or {}).get("type", "")
        self._print(
            f"[OAuth] continue page={page_type or '-'} next={(continue_url or '-')[:140]}"
        )

        self._print("[OAuth] 3/7 POST /api/accounts/password/verify")
        sentinel_pwd = build_sentinel_token(
            self.session,
            self.device_id,
            flow="password_verify",
            user_agent=self.ua,
            sec_ch_ua=self.sec_ch_ua,
            impersonate=self.impersonate,
        )
        if not sentinel_pwd:
            self._print("[OAuth] password_verify 的 sentinel token 获取失败")
            return None

        headers_verify = _oauth_json_headers(f"{oauth_issuer}/log-in/password")
        headers_verify["openai-sentinel-token"] = sentinel_pwd

        try:
            resp_verify = self.session.post(
                f"{oauth_issuer}/api/accounts/password/verify",
                json={"password": password},
                headers=headers_verify,
                timeout=30,
                allow_redirects=False,
                impersonate=self.impersonate,
            )
        except Exception as e:
            self._print(f"[OAuth] password/verify 异常: {e}")
            return None

        self._print(f"[OAuth] /password/verify -> {resp_verify.status_code}")
        if resp_verify.status_code != 200:
            self._print(f"[OAuth] 密码校验失败: {resp_verify.text[:180]}")
            return None

        try:
            verify_data = resp_verify.json()
        except Exception:
            self._print("[OAuth] password/verify 响应解析失败")
            return None

        continue_url = verify_data.get("continue_url", "") or continue_url
        page_type = (verify_data.get("page") or {}).get("type", "") or page_type
        self._print(
            f"[OAuth] verify page={page_type or '-'} next={(continue_url or '-')[:140]}"
        )

        need_oauth_otp = (
            page_type == "email_otp_verification"
            or "email-verification" in (continue_url or "")
            or "email-otp" in (continue_url or "")
        )

        if need_oauth_otp:
            self._print("[OAuth] 4/7 检测到邮箱 OTP 验证")
            if not mail_token:
                self._print("[OAuth] OAuth 阶段需要邮箱 OTP，但未提供 mail_token")
                return None

            headers_otp = _oauth_json_headers(f"{oauth_issuer}/email-verification")
            tried_codes = set()
            otp_success = False
            otp_deadline = time.time() + 120

            while time.time() < otp_deadline and not otp_success:
                messages = self._fetch_emails(mail_token) or []
                candidate_codes = []

                for msg in messages[:12]:
                    # 先从列表字段提取，兼容不同 provider（有些只在 subject 返回验证码）。
                    list_content = "\n".join(
                        [
                            msg.get("subject") or "",
                            msg.get("text") or msg.get("body") or "",
                            msg.get("html") or "",
                        ]
                    ).strip()
                    if list_content:
                        code = self._extract_verification_code(list_content)
                        if (
                            code
                            and code not in tried_codes
                            and code not in candidate_codes
                        ):
                            candidate_codes.append(code)

                    msg_id = msg.get("id") or msg.get("@id")
                    if not msg_id:
                        continue

                    detail = self._fetch_email_detail(mail_token, msg_id)
                    if not detail:
                        continue

                    content = "\n".join(
                        [
                            detail.get("subject") or "",
                            detail.get("text") or detail.get("body") or "",
                            detail.get("html") or "",
                        ]
                    ).strip()
                    code = self._extract_verification_code(content)
                    if code and code not in tried_codes and code not in candidate_codes:
                        candidate_codes.append(code)

                if not candidate_codes:
                    elapsed = int(120 - max(0, otp_deadline - time.time()))
                    self._print(f"[OAuth] OTP 等待中... ({elapsed}s/120s)")
                    time.sleep(2)
                    continue

                for otp_code in candidate_codes:
                    tried_codes.add(otp_code)
                    self._print(f"[OAuth] 尝试 OTP: {otp_code}")
                    try:
                        resp_otp = self.session.post(
                            f"{oauth_issuer}/api/accounts/email-otp/validate",
                            json={"code": otp_code},
                            headers=headers_otp,
                            timeout=30,
                            allow_redirects=False,
                            impersonate=self.impersonate,
                        )
                    except Exception as e:
                        self._print(f"[OAuth] email-otp/validate 异常: {e}")
                        continue

                    self._print(
                        f"[OAuth] /email-otp/validate -> {resp_otp.status_code}"
                    )
                    if resp_otp.status_code != 200:
                        self._print(
                            f"[OAuth] OTP 无效，继续尝试下一条: {resp_otp.text[:160]}"
                        )
                        continue

                    try:
                        otp_data = resp_otp.json()
                    except Exception:
                        self._print("[OAuth] email-otp/validate 响应解析失败")
                        continue

                    continue_url = otp_data.get("continue_url", "") or continue_url
                    page_type = (otp_data.get("page") or {}).get(
                        "type", ""
                    ) or page_type
                    self._print(
                        f"[OAuth] OTP 验证通过 page={page_type or '-'} next={(continue_url or '-')[:140]}"
                    )
                    otp_success = True
                    break

                if not otp_success:
                    time.sleep(2)

            if not otp_success:
                self._print(
                    f"[OAuth] OAuth 阶段 OTP 验证失败，已尝试 {len(tried_codes)} 个验证码"
                )
                return None

        code = None
        consent_url = continue_url
        if consent_url and consent_url.startswith("/"):
            consent_url = f"{oauth_issuer}{consent_url}"

        if not consent_url and "consent" in page_type:
            consent_url = f"{oauth_issuer}/sign-in-with-chatgpt/codex/consent"

        if consent_url:
            code = _extract_code_from_url(consent_url)

        if not code and consent_url:
            self._print("[OAuth] 5/7 跟随 continue_url 提取 code")
            code, _ = self._oauth_follow_for_code(
                consent_url, referer=f"{oauth_issuer}/log-in/password"
            )

        consent_hint = (
            ("consent" in (consent_url or ""))
            or ("sign-in-with-chatgpt" in (consent_url or ""))
            or ("workspace" in (consent_url or ""))
            or ("organization" in (consent_url or ""))
            or ("consent" in page_type)
            or ("organization" in page_type)
        )

        if not code and consent_hint:
            if not consent_url:
                consent_url = f"{oauth_issuer}/sign-in-with-chatgpt/codex/consent"
            self._print("[OAuth] 6/7 执行 workspace/org 选择")
            code = self._oauth_submit_workspace_and_org(consent_url)

        if not code:
            fallback_consent = f"{oauth_issuer}/sign-in-with-chatgpt/codex/consent"
            self._print("[OAuth] 6/7 回退 consent 路径重试")
            code = self._oauth_submit_workspace_and_org(fallback_consent)
            if not code:
                code, _ = self._oauth_follow_for_code(
                    fallback_consent, referer=f"{oauth_issuer}/log-in/password"
                )

        if not code:
            self._print("[OAuth] 未获取到 authorization code")
            return None

        self._print("[OAuth] 7/7 POST /oauth/token")
        token_resp = self.session.post(
            f"{oauth_issuer}/oauth/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.ua,
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": oauth_redirect_uri,
                "client_id": oauth_client_id,
                "code_verifier": code_verifier,
            },
            timeout=60,
            impersonate=self.impersonate,
        )
        self._print(f"[OAuth] /oauth/token -> {token_resp.status_code}")

        if token_resp.status_code != 200:
            self._print(
                f"[OAuth] token 交换失败: {token_resp.status_code} {token_resp.text[:200]}"
            )
            return None

        try:
            data = token_resp.json()
        except Exception:
            self._print("[OAuth] token 响应解析失败")
            return None

        if not data.get("access_token"):
            self._print("[OAuth] token 响应缺少 access_token")
            return None

        self._print("[OAuth] Codex Token 获取成功")
        return data


# ==================== 并发批量注册 ====================


def _register_one(idx, total, proxy):
    """单个注册任务 (在线程中运行) - 使用 DuckMail 临时邮箱"""
    reg = None
    try:
        reg = ChatGPTRegister(proxy=proxy, tag=f"{idx}")

        # 1. 创建临时邮箱
        reg._print(
            f"[Mail:{_MAIL_PROVIDER_INFO.get('name', 'unknown')}] 创建临时邮箱..."
        )
        email, email_pwd, mail_token = reg.create_temp_email()
        tag = email.split("@")[0]
        reg.tag = tag  # 更新 tag
        reg._logger = get_logger(f"register:{reg.tag}")

        chatgpt_password = _generate_password()
        name = _random_name()
        birthdate = _random_birthdate()

        logger.info("[{}/{}] 开始注册: {}", idx, total, email)
        logger.debug(
            "注册详情 email={} chatgpt_password={} email_password={} name={} birthdate={}",
            email,
            chatgpt_password,
            email_pwd,
            name,
            birthdate,
        )

        # 2. 执行注册流程
        reg.run_register(email, chatgpt_password, name, birthdate, mail_token)

        # 3. OAuth（可选）
        oauth_ok = True
        if _MODEL_PROVIDER.oauth_enabled():
            reg._print("[OAuth] 开始获取 Codex Token...")
            tokens = reg.perform_codex_oauth_login_http(
                email, chatgpt_password, mail_token=mail_token
            )
            oauth_ok = bool(tokens and tokens.get("access_token"))
            if oauth_ok:
                _save_codex_tokens(email, tokens)
                reg._print("[OAuth] Token 已保存")
            else:
                msg = "OAuth 获取失败"
                if _MODEL_PROVIDER.oauth_required():
                    raise Exception(f"{msg}（oauth_required=true）")
                reg._print(f"[OAuth] {msg}（按配置继续）")

        logger.success(f"[OK] [{tag}] {email} 注册成功!")
        return True, email, None

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[FAIL] [{idx}] 注册失败: {error_msg}")
        logger.opt(exception=True).debug("注册任务异常堆栈")
        return False, None, error_msg


def run_batch(
    total_accounts: Optional[int] = None,
    max_workers: Optional[int] = None,
    proxy: Optional[str] = None,
):
    """并发批量注册 - DuckMail 临时邮箱版"""

    if total_accounts is None:
        total_accounts = _cfg("total_accounts")
    if max_workers is None:
        max_workers = _cfg("concurrency")
    if proxy is None:
        proxy = _cfg("proxy")

    total_accounts = int(total_accounts)
    max_workers = int(max_workers)
    proxy = str(proxy)

    ok, err = mail_utils.validate_mail_provider_config(_CONFIG)
    if not ok:
        logger.error(f"邮箱 provider 配置无效: {err}")
        logger.error("请检查 config.yaml 的 mail_provider / mail_providers 配置")
        return

    actual_workers = min(max_workers, total_accounts)
    logger.info("开始批量注册: total={} workers={}", total_accounts, actual_workers)
    logger.info(
        "Mail provider: {} {} | Model provider: {} | OAuth: {}",
        _MAIL_PROVIDER_INFO.get("name", "unknown"),
        _MAIL_PROVIDER_INFO.get("api_base", ""),
        _model_provider_name(),
        "开启" if _MODEL_PROVIDER.oauth_enabled() else "关闭",
    )
    if _MODEL_PROVIDER.oauth_enabled():
        logger.debug(
            f"OAuth issuer={_oauth_issuer()} client={_MODEL_PROVIDER.oauth_client_id()}"
        )
        logger.debug(f"Token输出 dir={_token_base_dir()}/{_model_provider_name()}")

    success_count = 0
    fail_count = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        futures = {}
        for idx in range(1, total_accounts + 1):
            future = executor.submit(_register_one, idx, total_accounts, proxy)
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            try:
                ok, email, err = future.result()
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
                    logger.warning(f"[账号 {idx}] 失败: {err}")
            except Exception as e:
                fail_count += 1
                logger.error(f"[FAIL] 账号 {idx} 线程异常: {e}")
                logger.opt(exception=True).debug(f"账号 {idx} 线程异常堆栈")

    elapsed = time.time() - start_time
    avg = elapsed / total_accounts if total_accounts else 0
    logger.info(
        f"注册完成: 耗时 {elapsed:.1f}s | 总数 {total_accounts} | 成功 {success_count} | 失败 {fail_count}"
    )
    logger.info(f"平均速度: {avg:.1f}s/个")
