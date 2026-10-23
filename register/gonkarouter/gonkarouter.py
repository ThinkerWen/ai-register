import os
import platform
import shutil
import socket
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from glob import glob
from typing import Optional

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError

from register.base import ModelProvider
from util import config as config_utils
from util import get_logger, setup_logger
from util import magpie as magpie_utils
from util import mail as mail_utils
from util.browser_proxy import BrowserProxy

setup_logger()
logger = get_logger("gonkarouter")

SITE_URL = "https://gonkarouter.io/"
DASHBOARD_URL = "https://gonkarouter.io/dashboard"
API_BASE = "https://api.gonkarouter.io"
_file_lock = threading.Lock()


class GonkaRouterModelProvider(ModelProvider):
    name = "gonkarouter"

    def __init__(self, proxy=""):
        self.proxy = str(proxy or "").strip()

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

    def run_batch(self, total_accounts: Optional[int] = None, max_workers: Optional[int] = None, proxy: Optional[str] = None):
        config = config_utils.get_register_config(logger=logger)
        total = int(total_accounts if total_accounts is not None else config.get("total_accounts") or 1)
        workers = int(max_workers if max_workers is not None else config.get("concurrency") or 1)
        return run_batch(total_accounts=total, max_workers=workers, proxy=self.proxy)


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
    def __init__(self, proxy):
        self.proxy = BrowserProxy(proxy)
        self.profile = tempfile.mkdtemp(prefix="gonkarouter_chrome_")
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


def _click_button(page, text, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for button in page.eles("tag:button"):
            if button.text.strip() == text:
                try:
                    button.click()
                    return
                except Exception:
                    break
        time.sleep(0.25)
    raise RuntimeError(f"未找到按钮: {text}")


def _page_state(page):
    return page.run_js(r"""
const dialog = document.querySelector('[role="dialog"]');
const otp = Array.from(document.querySelectorAll('input[inputmode="numeric"][maxlength="1"]'))
    .filter((node) => node.getBoundingClientRect().width > 0);
const text = String((dialog || document.body).innerText || '').replace(/\s+/g, ' ').trim();
return {otp: otp.length, text: text.slice(-700), url: location.href};
    """)


def _wait_for_otp_page(page, timeout=90):
    deadline = time.monotonic() + timeout
    challenge_clicked = False
    while time.monotonic() < deadline:
        try:
            state = _page_state(page)
        except PageDisconnectedError:
            time.sleep(0.5)
            continue
        if state.get("otp", 0) >= 6:
            return
        if not challenge_clicked:
            challenge_clicked = _click_turnstile_if_present(page)
        lowered = state.get("text", "").lower()
        if "human verification failed" in lowered or "verification failed" in lowered:
            raise RuntimeError("人机验证失败")
        time.sleep(0.5)
    raise RuntimeError("提交邮箱后未进入验证码页面")


def _click_turnstile_if_present(page):
    try:
        hidden = page.ele("@name=cf-turnstile-response", timeout=0)
        if hidden and str(hidden.value or "").strip():
            return True
        frames = page.run_js("return Array.from(document.querySelectorAll('iframe')).map((frame) => frame.src)")
        if not any("challenges.cloudflare.com" in str(src) for src in frames or []):
            return False
        frame = page.get_frame("@src:challenges.cloudflare.com")
        checkbox = frame.ele("tag:input", timeout=0)
        if checkbox:
            checkbox.click()
            return True
    except Exception:
        pass
    return False


def _fill_otp(page, code):
    result = page.run_js(
        r"""
const code = String(arguments[0] || '').trim();
const inputs = Array.from(document.querySelectorAll('input[inputmode="numeric"][maxlength="1"]'))
    .filter((node) => node.getBoundingClientRect().width > 0);
if (inputs.length < code.length) return false;
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
for (let i = 0; i < code.length; i += 1) {
    const input = inputs[i];
    input.focus();
    if (setter) setter.call(input, code[i]); else input.value = code[i];
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: code[i], inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: code[i], inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: code[i] }));
    input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: code[i] }));
}
return inputs.map((node) => node.value).join('') === code;
        """,
        code,
    )
    if not result:
        raise RuntimeError("填写验证码失败")


def _wait_auth_token(page, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for item in page.cookies(all_domains=True, all_info=True) or []:
                name = item.get("name") if isinstance(item, dict) else getattr(item, "name", "")
                value = item.get("value") if isinstance(item, dict) else getattr(item, "value", "")
                if str(name) == "auth_token" and str(value or "").strip():
                    return str(value).strip()
            state = _page_state(page)
            if "verification failed" in state.get("text", "").lower():
                raise RuntimeError("邮箱验证码验证失败")
        except PageDisconnectedError:
            pass
        time.sleep(0.5)
    raise RuntimeError("邮箱验证后未获取登录凭证")


def _valid_api_key(key):
    return isinstance(key, str) and key.startswith("sk-") and len(key) >= 20


def _reveal_api_key_from_ui(page, timeout=15):
    try:
        for button in page.eles("tag:button"):
            label = str(button.attr("title") or button.attr("aria-label") or "").strip().lower()
            if "show full key" in label or "显示完整" in label or "完整 key" in label:
                button.click()
                break
    except Exception:
        return ""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for code in page.eles("tag:code"):
                value = code.text.strip()
                if _valid_api_key(value):
                    return value
        except Exception:
            pass
        time.sleep(0.5)
    return ""


def _fetch_api_key(page, auth_token, timeout=60):
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        result = page.run_js(
            """
return (async () => {
    try {
        const token = arguments[0];
        const headers = {Authorization: `Bearer ${token}`};
        const response = await fetch(`${arguments[1]}/api/keys`, {headers, credentials: 'include'});
        if (!response.ok) return {error: `keys HTTP ${response.status}`};
        const data = await response.json();
        const item = (data.items || [])[0];
        if (!item) return {error: 'no API key'};
        const reveal = await fetch(`${arguments[1]}/api/keys/${encodeURIComponent(item.id)}/reveal`, {
            method: 'POST', headers, credentials: 'include'
        });
        if (!reveal.ok) return {error: `reveal HTTP ${reveal.status}`};
        const payload = await reveal.json();
        return {key: payload.key || '', preview: item.key_preview || ''};
    } catch (error) {
        return {error: `network ${error.message || error}`};
    }
})();
            """,
            auth_token,
            API_BASE,
        )
        if _valid_api_key(result.get("key")):
            return result
        last_error = result.get("error", "unknown error")
        retryable = (
            last_error == "no API key"
            or last_error.startswith("network")
            or any(f"HTTP {code}" in last_error for code in (401, 403, 429, 500, 502, 503, 504))
        )
        if not retryable:
            break
        if time.monotonic() + 2 >= deadline:
            break
        time.sleep(2)
    try:
        if "/dashboard" not in page.url:
            page.get(DASHBOARD_URL, timeout=45)
        key = _reveal_api_key_from_ui(page)
        if key:
            return {"key": key, "preview": ""}
    except Exception as exc:
        last_error = f"{last_error}; dashboard fallback: {exc}"
    if last_error:
        raise RuntimeError(f"读取 API Key 失败: {last_error}")
    else:
        raise RuntimeError("读取 API Key 失败: 页面没有返回完整 Key")


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


def _register_one(index, output_path, proxy):
    config = config_utils.get_register_config(logger=logger)
    logger.info("task={} 获取临时邮箱", index)
    mail_provider = mail_utils.create_mail_provider(config, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    email, _password, mail_token = mail_provider.create_temp_email()
    if not email or not mail_token:
        raise RuntimeError("获取临时邮箱失败")
    logger.info("task={} 邮箱就绪，启动浏览器", index)
    session = _BrowserSession(proxy)
    try:
        page = session.page
        logger.info("task={} 打开注册页", index)
        page.get(SITE_URL, timeout=30)
        time.sleep(2)
        _click_button(page, "Sign In")
        email_input = page.ele('css:input[type="email"]', timeout=15)
        email_input.input(email)
        _click_button(page, "Continue")
        _wait_for_otp_page(page)
        logger.info("task={} 已进入验证码页面，等待邮件", index)
        code = mail_utils.wait_for_verification_email(mail_token=mail_token, timeout=120, provider=mail_provider, logger=logger.info)
        if not code:
            raise RuntimeError("未收到 GonkaRouter 验证码")
        _fill_otp(page, code)
        auth_token = _wait_auth_token(page)
        logger.info("task={} 登录成功，读取 API Key", index)
        key_info = _fetch_api_key(page, auth_token)
        key = key_info["key"]
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


def _default_key_file(config):
    root = os.path.expanduser(str(config.get("token_dir") or "token_dir"))
    if not os.path.isabs(root):
        root = os.path.join(os.getcwd(), root)
    return os.path.join(root, "gonkarouter", "api_keys.txt")


def run_batch(total_accounts=None, max_workers=None, proxy=None):
    config = config_utils.get_register_config(logger=logger)
    mail_ok, mail_err = mail_utils.validate_mail_provider_config(config)
    if not mail_ok:
        raise RuntimeError(f"邮箱 provider 配置无效: {mail_err}")
    magpie_ok, magpie_state = magpie_utils.validate_config(config)
    if not magpie_ok:
        raise RuntimeError(magpie_state)
    total = int(total_accounts if total_accounts is not None else config.get("total_accounts") or 1)
    workers = max(1, min(int(max_workers if max_workers is not None else config.get("concurrency") or 1), max(1, total)))
    output_path = _default_key_file(config)
    logger.info("GonkaRouter 批量注册开始 | total={} | workers={} | output={}", total, workers, output_path)
    logger.info("Magpie 上传: {}", "启用" if magpie_state == "ok" else "未启用")
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_register_one, index, output_path, proxy): index for index in range(1, total + 1)}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.error("注册失败 | task={} | {}", futures[future], exc)
    logger.info("GonkaRouter 批量结束 | success={}/{} | output={}", len(results), total, output_path)
    return results
