import json
import os
import platform
import secrets
import shutil
import socket
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from glob import glob
from typing import Optional

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError

from register.base import ModelProvider
from util import config as config_utils
from util import get_logger, setup_logger
from util import magpie as magpie_utils
from util import mail as mail_utils
from util import twocaptcha as twocaptcha_utils
from util.browser_proxy import BrowserProxy

setup_logger()
logger = get_logger("agnes")

LOGIN_URL = "https://platform.agnes-ai.com/login"
API_KEYS_URL = "https://platform.agnes-ai.com/settings/apiKeys"
API_BASE = "https://platform-backend.agnes-ai.com"
_file_lock = threading.Lock()
CAPTCHA_METHOD_MANUAL = "manual"
CAPTCHA_METHOD_2CAPTCHA = "2captcha"
_CAPTCHA_METHODS = {CAPTCHA_METHOD_MANUAL, CAPTCHA_METHOD_2CAPTCHA}
_SEND_CODE_TIMEOUT = 60
_INVALID_EMAIL_DOMAIN_MARKERS = (
    "this email domain looks invalid or abusive",
    "email domain looks invalid",
    "not allowed for registration",
)


class AgnesModelProvider(ModelProvider):
    name = "agnes"

    def __init__(
        self,
        proxy: str = "",
        manual_captcha_timeout: int = 300,
        captcha_method: str = CAPTCHA_METHOD_MANUAL,
        captcha_key: str = "",
    ):
        self.proxy = str(proxy or "").strip()
        try:
            self.manual_captcha_timeout = int(manual_captcha_timeout)
        except (TypeError, ValueError):
            self.manual_captcha_timeout = 300
        self.captcha_method = _normalize_captcha_method(captcha_method)
        self.captcha_key = str(captcha_key or "").strip()
        _validate_captcha_settings(self.captcha_method, self.captcha_key)

    def oauth_enabled(self) -> bool:
        return False

    def oauth_required(self) -> bool:
        return False

    def oauth_issuer(self) -> str:
        return ""

    def oauth_client_id(self) -> str:
        return ""

    def oauth_redirect_uri(self) -> str:
        return ""

    def run_batch(
        self,
        total_accounts: Optional[int] = None,
        max_workers: Optional[int] = None,
        proxy: Optional[str] = None,
    ):
        config = config_utils.get_register_config(logger=logger)
        provider_cfg = (config.get("model_providers") or {}).get("agnes") or {}
        resolved_proxy = str(proxy if proxy is not None else self.proxy).strip()
        timeout = int(provider_cfg.get("manual_captcha_timeout") or self.manual_captcha_timeout)
        captcha_method = _normalize_captcha_method(provider_cfg.get("captcha_method") or self.captcha_method)
        captcha_key = str(provider_cfg.get("2captcha_key") or self.captcha_key).strip()
        _validate_captcha_settings(captcha_method, captcha_key)
        total = int(total_accounts if total_accounts is not None else config.get("total_accounts") or 1)
        workers = int(max_workers if max_workers is not None else config.get("concurrency") or 1)
        return run_batch(
            total_accounts=total,
            max_workers=workers,
            proxy=resolved_proxy,
            manual_captcha_timeout=timeout,
            captcha_method=captcha_method,
            captcha_key=captcha_key,
        )


def _normalize_captcha_method(value) -> str:
    method = str(value or "").strip().lower()
    if method not in _CAPTCHA_METHODS:
        options = " / ".join(sorted(_CAPTCHA_METHODS))
        raise RuntimeError(f"不支持的 Agnes 验证码方式: {value}（可选: {options}）")
    return method


def _validate_captcha_settings(captcha_method: str, captcha_key: str):
    if captcha_method == CAPTCHA_METHOD_2CAPTCHA and not str(captcha_key or "").strip():
        raise RuntimeError("Agnes 选择 2captcha 验证方式时需配置 model_providers.agnes.2captcha_key")


def _pick_local_debug_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _chrome_path():
    if platform.system() != "Linux":
        return None
    matches = glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
    for candidate in [*matches, "/usr/bin/chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome"]:
        if os.path.isfile(candidate):
            return candidate
    return None


class _BrowserSession:
    def __init__(self, proxy=""):
        self.proxy = BrowserProxy(proxy)
        self.profile = tempfile.mkdtemp(prefix="agnes_chrome_")
        options = ChromiumOptions()
        options.set_local_port(_pick_local_debug_port())
        options.set_user_data_path(self.profile)
        options.set_argument("--disable-gpu")
        options.set_argument("--disable-dev-shm-usage")
        options.set_argument("--disable-software-rasterizer")
        options.set_timeouts(base=2, page_load=60)
        if platform.system() == "Linux" and os.geteuid() == 0:
            options.set_argument("--no-sandbox")
        if self.proxy.server:
            options.set_proxy(self.proxy.server)
        else:
            options.set_argument("--no-proxy-server")
        browser_path = _chrome_path()
        if browser_path:
            options.set_browser_path(browser_path)
        try:
            self.browser = Chromium(options)
        except Exception:
            shutil.rmtree(self.profile, ignore_errors=True)
            raise
        tabs = self.browser.get_tabs()
        self.page = tabs[-1] if tabs else self.browser.new_tab()
        self.proxy.attach(self.page)

    def close(self):
        try:
            self.browser.quit()
        except Exception:
            pass
        shutil.rmtree(self.profile, ignore_errors=True)


def _click_any_by_text(page, texts, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        clicked = page.run_js(
            r"""
const expectedList = JSON.parse(arguments[0] || '[]').map((item) => String(item || '').replace(/\s+/g, '').toLowerCase());
const nodes = Array.from(document.querySelectorAll('a, button, [role="button"]'));
const node = nodes.find((item) => {
    const label = String(item.innerText || item.textContent || '').replace(/\s+/g, '').toLowerCase();
    return expectedList.includes(label);
});
if (!node || node.disabled || node.getAttribute('aria-disabled') === 'true') return false;
node.click();
return true;
            """,
            json.dumps(list(texts), ensure_ascii=False),
        )
        if clicked:
            return
        time.sleep(0.25)
    raise RuntimeError(f"未找到可点击元素: {' / '.join(str(item) for item in texts)}")


def _fill_input(page, selector, value):
    input_element = page.ele(selector, timeout=15)
    input_element.input(value)


def _click_send_code_icon(page, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        clicked = page.run_js(
            r"""
const icons = Array.from(document.querySelectorAll('img[src*="new_canSend.svg"], img[src*="new_resend.svg"]'));
const icon = icons.find((item) => item.getBoundingClientRect().width > 0);
if (!icon) return false;
icon.click();
return true;
            """
        )
        if clicked:
            return
        time.sleep(0.25)
    raise RuntimeError("未找到验证码发送按钮")


def _install_captcha_hook(page):
    """拦截腾讯验证码弹窗，保存 appId 和回调，供 2captcha 注入结果。"""
    page.run_js(
        r"""
if (window.__agnesCaptchaHookInstalled) return true;
window.__agnesCaptchaHookInstalled = true;
window.__agnesCaptchaState = { request: null, callback: null };
const WrappedTencentCaptcha = function (appId, callback, options) {
    window.__agnesCaptchaState.request = {
        appId: String(appId || ''),
        pageUrl: String(location.href || ''),
        captchaScript: (() => {
            const scripts = Array.from(document.querySelectorAll('script[src*="Captcha"]'));
            return scripts.length ? String(scripts[scripts.length - 1].src || '') : '';
        })(),
        options: options || {}
    };
    window.__agnesCaptchaState.callback = typeof callback === 'function' ? callback : null;
    this.show = function () {};
    this.hide = function () {};
    this.destroy = function () {};
};
Object.defineProperty(window, 'TencentCaptcha', {
    configurable: true,
    get: function () { return WrappedTencentCaptcha; },
    set: function (value) { window.__agnesRealTencentCaptcha = value; }
});
return true;
        """
    )


def _wait_for_captcha_request(page, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = page.run_js("return window.__agnesCaptchaState || null")
        except PageDisconnectedError:
            time.sleep(0.5)
            continue
        request = (state or {}).get("request")
        if isinstance(request, dict) and request.get("appId"):
            return request
        time.sleep(0.25)
    raise RuntimeError("未捕获 Agnes 腾讯验证码，无法使用 2captcha 自动验证")


def _solve_captcha_with_2captcha(page, captcha_key, timeout=180, log=None):
    request = _wait_for_captcha_request(page)
    app_id = str(request.get("appId") or twocaptcha_utils.DEFAULT_TENCENT_APP_ID).strip()
    page_url = str(request.get("pageUrl") or page.url).strip()
    result = twocaptcha_utils.solve_tencent_captcha(
        api_key=captcha_key,
        app_id=app_id,
        page_url=page_url,
        captcha_script=str(request.get("captchaScript") or ""),
        timeout=timeout,
        logger=log,
    )
    injected = page.run_js(
        r"""
const state = window.__agnesCaptchaState;
if (!state || typeof state.callback !== 'function') return false;
state.callback({
    ret: arguments[0],
    ticket: arguments[1],
    randstr: arguments[2]
});
return true;
        """,
        result.get("ret", 0),
        result.get("ticket"),
        result.get("randstr"),
    )
    if not injected:
        raise RuntimeError("Agnes 腾讯验证码回调不可用，2captcha 验证结果注入失败")


def _get_send_code_state(page):
    return page.run_js(
        r"""
const suffix = document.querySelector('#verification_code')
    ?.closest('.ant-input-affix-wrapper')
    ?.querySelector('.ant-input-suffix');
const countdownText = suffix ? String(suffix.innerText || '').trim() : '';
const errorNodes = Array.from(document.querySelectorAll(
    '.ant-form-item-explain-error, [class*="ant-message"], [class*="ant-notification"], [role="alert"]'
));
const errors = errorNodes.map((node) => String(node.innerText || '').trim()).filter(Boolean);
const captchaVisible = Array.from(document.querySelectorAll('iframe'))
    .some((frame) => /captcha/i.test(String(frame.src || frame.id || '')));
return {
    countdownText,
    errors,
    captchaVisible,
    bodyText: String(document.body.innerText || '').slice(0, 5000)
};
        """
    )


def _wait_for_send_code_result(page, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = _get_send_code_state(page)
        except PageDisconnectedError:
            time.sleep(0.5)
            continue
        texts = [str(item or "").lower() for item in state.get("errors", [])]
        texts.append(str(state.get("bodyText") or "").lower())
        if any(marker in text for marker in _INVALID_EMAIL_DOMAIN_MARKERS for text in texts):
            error_text = next(
                (item for item in state.get("errors", []) if any(marker in item.lower() for marker in _INVALID_EMAIL_DOMAIN_MARKERS)),
                "",
            )
            raise RuntimeError(f"Agnes 拒绝该邮箱域名: {error_text or 'invalid or abusive email domain'}")
        countdown = str(state.get("countdownText") or "").strip()
        if countdown.isdigit():
            return int(countdown)
        time.sleep(0.5)
    raise RuntimeError("等待 Agnes 验证码发送结果超时")


def _submit_register_form(page):
    clicked = page.run_js(
        r"""
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button'));
const button = buttons.find((item) => {
    const label = String(item.innerText || item.textContent || '').replace(/\s+/g, '').toLowerCase();
    return ['继续', 'continue', 'signup', 'signupnow'].includes(label);
});
if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') return false;
button.click();
return true;
        """
    )
    if not clicked:
        raise RuntimeError("未找到注册提交按钮")


def _read_login_token(page, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            token = page.run_js("return localStorage.getItem('token') || ''")
            if str(token or "").strip():
                return str(token).strip()
        except PageDisconnectedError:
            pass
        time.sleep(0.5)
    return ""


def _login_if_needed(page, email, password, timeout=20):
    token = _read_login_token(page, timeout=1)
    if token:
        return token
    result = page.run_js(
        r"""
return (async () => {
    const username = arguments[0];
    const password = arguments[1];
    const response = await fetch(`${arguments[2]}/api/user/login`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password})
    });
    const payload = await response.json();
    const token = payload?.data?.access_token || '';
    if (token) localStorage.setItem('token', token);
    return {code: payload.code, token, message: payload.message || ''};
})();
        """,
        email,
        password,
        API_BASE,
    )
    token = str(result.get("token") or "").strip()
    if not token:
        message = result.get("message") or "登录失败"
        raise RuntimeError(message)
    return token


def _create_api_key(page, token, name):
    result = page.run_js(
        r"""
return (async () => {
    const token = arguments[0];
    const name = arguments[1];
    const response = await fetch(`${arguments[2]}/api/token`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({name, api_key_profile: 'default'})
    });
    const payload = await response.json();
    return {
        code: payload.code,
        key: payload?.data?.key || '',
        message: payload.message || ''
    };
})();
        """,
        token,
        name,
        API_BASE,
    )
    key = str(result.get("key") or "").strip()
    if not key:
        message = result.get("message") or "创建 API Key 失败"
        raise RuntimeError(message)
    return key


def _save_api_key(path, key):
    with _file_lock:
        existing = set()
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as file:
                existing = {line.strip() for line in file if line.strip()}
        if key in existing:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as file:
            file.write(key + "\n")
        return True


def _mask_key(key):
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "***"


def _default_key_file(config):
    root = os.path.expanduser(str(config.get("token_dir") or "token_dir"))
    if not os.path.isabs(root):
        root = os.path.join(os.getcwd(), root)
    return os.path.join(root, "agnes", f"api_keys_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt")


def _register_one(
    index,
    output_path,
    proxy,
    manual_captcha_timeout=300,
    captcha_method=CAPTCHA_METHOD_MANUAL,
    captcha_key="",
):
    config = config_utils.get_register_config(logger=logger)
    logger.info("task={} 获取临时邮箱", index)
    mail_provider = mail_utils.create_mail_provider(config, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    email, _password, mail_token = mail_provider.create_temp_email()
    if not email or not mail_token:
        raise RuntimeError("获取临时邮箱失败")
    password = "A" + secrets.token_hex(8) + "!a7#" + secrets.token_urlsafe(8)
    logger.info("task={} 邮箱就绪，启动浏览器", index)
    session = _BrowserSession(proxy)
    try:
        page = session.page
        logger.info("task={} 打开注册页", index)
        page.get(LOGIN_URL, timeout=45)
        time.sleep(2)
        _click_any_by_text(page, ["注册", "Sign up"])
        _fill_input(page, "css:#email", email)
        if captcha_method == CAPTCHA_METHOD_2CAPTCHA:
            logger.info("task={} 点击发送验证码，使用 2captcha 自动完成腾讯点选验证码", index)
            _install_captcha_hook(page)
        else:
            logger.info("task={} 点击发送验证码，请在浏览器中手动完成腾讯点选验证码", index)
        _click_send_code_icon(page)
        if captcha_method == CAPTCHA_METHOD_2CAPTCHA:
            _solve_captcha_with_2captcha(
                page,
                captcha_key,
                log=lambda message: logger.info("task={} {}", index, message),
            )
            logger.info("task={} 2captcha 验证通过，等待验证码发送结果", index)
            _wait_for_send_code_result(page, timeout=_SEND_CODE_TIMEOUT)
        else:
            _wait_for_send_code_result(page, timeout=manual_captcha_timeout)
        logger.info("task={} 验证码发送成功，等待邮件", index)
        code = mail_utils.wait_for_verification_email(
            mail_token=mail_token,
            timeout=manual_captcha_timeout + 120,
            provider=mail_provider,
            logger=logger.info,
        )
        if not code:
            raise RuntimeError("未收到 Agnes 验证码")
        _fill_input(page, "css:#verification_code", code)
        _fill_input(page, "css:#password", password)
        _fill_input(page, "css:#confirmPassword", password)
        _submit_register_form(page)
        token = _read_login_token(page, timeout=30)
        if not token:
            token = _login_if_needed(page, email, password)
        page.get(API_KEYS_URL, timeout=45)
        key_name = f"ai-register-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        key = _create_api_key(page, token, key_name)
        added = _save_api_key(output_path, key)
        uploaded = False
        if magpie_utils.is_enabled(config):
            try:
                uploaded = magpie_utils.upload_api_key(config, key, logger=lambda message: logger.info("task={} {}", index, message))
            except Exception as exc:
                logger.error("task={} Magpie 上传失败 | {}", index, exc)
        logger.info(
            "注册成功 | task={} | email={} | key={} | {} | Magpie={}",
            index,
            email,
            _mask_key(key),
            "已写入" if added else "已存在",
            "已上传" if uploaded else ("失败" if magpie_utils.is_enabled(config) else "未启用"),
        )
        return {"email": email, "key": key, "added": added, "magpie_uploaded": uploaded}
    finally:
        session.close()


def run_batch(
    total_accounts=None,
    max_workers=None,
    proxy=None,
    manual_captcha_timeout=300,
    captcha_method=CAPTCHA_METHOD_MANUAL,
    captcha_key="",
):
    config = config_utils.get_register_config(logger=logger)
    captcha_method = _normalize_captcha_method(captcha_method)
    _validate_captcha_settings(captcha_method, captcha_key)
    mail_ok, mail_err = mail_utils.validate_mail_provider_config(config)
    if not mail_ok:
        raise RuntimeError(f"邮箱 provider 配置无效: {mail_err}")
    magpie_ok, magpie_state = magpie_utils.validate_config(config)
    if not magpie_ok:
        raise RuntimeError(magpie_state)
    total = int(total_accounts if total_accounts is not None else config.get("total_accounts") or 1)
    workers = max(1, min(int(max_workers if max_workers is not None else config.get("concurrency") or 1), max(1, total)))
    output_path = _default_key_file(config)
    logger.info(
        "Agnes 批量注册开始 | total={} | workers={} | captcha={} | output={}",
        total,
        workers,
        captcha_method,
        output_path,
    )
    logger.info("Magpie 上传: {}", "启用" if magpie_state == "ok" else "未启用")
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _register_one,
                index,
                output_path,
                proxy,
                manual_captcha_timeout,
                captcha_method,
                captcha_key,
            ): index
            for index in range(1, total + 1)
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.error("注册失败 | task={} | {}", futures[future], exc)
    logger.info("Agnes 批量结束 | success={}/{} | output={}", len(results), total, output_path)
    return results
