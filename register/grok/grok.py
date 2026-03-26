import argparse
import datetime
import os
import platform
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from glob import glob
from typing import Optional

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError

from register.base import ModelProvider, random_name
from util import config as config_utils
from util import g2a as g2a_utils
from util import get_logger, setup_logger
from util import mail as mail_utils

setup_logger()
logger = get_logger("grok")

_virtual_display = None
_thread_state = threading.local()
_file_lock = threading.Lock()


class _ThreadLocalObjectProxy:
    def __init__(self, attr_name: str):
        self.attr_name = attr_name

    def _get_target(self):
        target = getattr(_thread_state, self.attr_name, None)
        if target is None:
            raise RuntimeError(f"当前线程未初始化 {self.attr_name}")
        return target

    def __getattr__(self, item):
        return getattr(self._get_target(), item)


browser = _ThreadLocalObjectProxy("browser")
page = _ThreadLocalObjectProxy("page")


def _get_browser():
    return getattr(_thread_state, "browser", None)


def _set_browser(value):
    _thread_state.browser = value


def _get_page():
    return getattr(_thread_state, "page", None)


def _set_page(value):
    _thread_state.page = value


def _get_chrome_temp_dir() -> str:
    return str(getattr(_thread_state, "chrome_temp_dir", "") or "")


def _set_chrome_temp_dir(value: str):
    _thread_state.chrome_temp_dir = value


SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"
EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)


class RegistrationStageError(Exception):
    def __init__(self, stage: str, detail: str):
        self.stage = stage
        self.detail = detail
        super().__init__(f"[{stage}] {detail}")


class GrokModelProvider(ModelProvider):
    name = "grok"

    def __init__(
        self,
        browser_proxy="",
    ):
        self._browser_proxy = str(browser_proxy or "").strip()

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

    def browser_proxy(self):
        return self._browser_proxy

    def run_batch(
        self,
        total_accounts: Optional[int] = None,
        max_workers: Optional[int] = None,
        proxy: Optional[str] = None,
    ):
        config = config_utils.get_register_config(logger=logger)
        mail_ok, mail_err = mail_utils.validate_mail_provider_config(config)
        if not mail_ok:
            raise RuntimeError(f"邮箱 provider 配置无效: {mail_err}")

        if total_accounts is None:
            total_accounts = int(config.get("total_accounts") or 0)
        else:
            total_accounts = int(total_accounts)
        if max_workers is None:
            max_workers = int(config.get("concurrency") or 1)
        else:
            max_workers = int(max_workers)
        if proxy is None:
            proxy = str(config.get("proxy") or "")

        output_path = _default_sso_file(config)
        logger.info(
            "开始执行内置 Grok 注册流程: total={} | workers={}",
            total_accounts,
            max_workers,
        )
        logger.info("SSO 输出文件: {}", output_path)
        g2a_ok, g2a_msg = g2a_utils.validate_g2a_config(config)
        if not g2a_ok:
            logger.warning("G2A 配置不完整: {}", g2a_msg)
        _run_loop(
            total_accounts=total_accounts,
            output_path=output_path,
            max_workers=max_workers,
        )


def _get_config():
    return config_utils.get_register_config(logger=logger)


def _get_provider_cfg():
    config = _get_config()
    return ((config or {}).get("model_providers") or {}).get("grok") or {}


def _default_sso_file(config):
    token_dir = os.path.expanduser(str(config.get("token_dir") or "token_dir"))
    root = (
        token_dir if os.path.isabs(token_dir) else os.path.join(os.getcwd(), token_dir)
    )
    sso_dir = os.path.join(root, "grok")
    os.makedirs(sso_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(sso_dir, f"sso_{ts}.txt")


def get_page_diagnostics() -> str:
    try:
        info = page.run_js(
            r"""
const title = String(document.title || '').trim();
const text = String(document.body ? (document.body.innerText || document.body.textContent || '') : '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 300);
const buttons = Array.from(document.querySelectorAll('button, [role="button"], a'))
    .map((node) => String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .slice(0, 8);
return { url: location.href, title, text, buttons };
            """
        )
    except Exception as exc:
        return f"page_state_unavailable: {exc}"
    if not isinstance(info, dict):
        return str(info)
    return (
        f"url={info.get('url', '')} | title={info.get('title', '')} | "
        f"text={info.get('text', '')} | buttons={info.get('buttons', [])}"
    )


def run_stage(stage: str, func, *args, **kwargs):
    try:
        result = func(*args, **kwargs)
        logger.info("阶段完成: {}", stage)
        return result
    except RegistrationStageError:
        raise
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        diagnostics = get_page_diagnostics()
        message = f"{detail} | page: {diagnostics}"
        logger.error("阶段失败 | stage={} | detail={}", stage, message)
        raise RegistrationStageError(stage, message) from exc


def ensure_stable_python_runtime():
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]
    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return
        logger.warning(
            "检测到 Python {}，自动切换到更稳定的解释器: {}",
            sys.version.split()[0],
            candidate,
        )
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        logger.warning(
            "当前 Python 为 3.14+；若出现 Mail TLS 异常，建议改用 Python 3.12 或 3.13。"
        )


def _create_chromium_options():
    config = _get_config()
    provider_cfg = _get_provider_cfg()
    browser_proxy = str(
        provider_cfg.get("browser_proxy") or config.get("proxy") or ""
    ).strip()

    options = ChromiumOptions()
    options.set_local_port(_pick_local_debug_port())
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-gpu")
    options.set_argument("--disable-dev-shm-usage")
    options.set_argument("--disable-software-rasterizer")
    if browser_proxy:
        options.set_proxy(browser_proxy)
        logger.info("浏览器代理: {}", browser_proxy)

    if platform.system() == "Linux":
        playwright_chromes = glob(
            os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")
        )
        if playwright_chromes:
            options.set_browser_path(playwright_chromes[0])
        else:
            for candidate in [
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
                "/usr/bin/google-chrome",
            ]:
                if os.path.isfile(candidate):
                    options.set_browser_path(candidate)
                    break

    options.set_timeouts(base=1)
    options.add_extension(EXTENSION_PATH)
    return options


def _pick_local_debug_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def _ensure_virtual_display():
    global _virtual_display
    if _virtual_display is not None:
        return
    if os.environ.get("DISPLAY") and os.environ.get("USE_XVFB") != "1":
        return
    try:
        from pyvirtualdisplay import Display

        _virtual_display = Display(visible=0, size=(1920, 1080))
        _virtual_display.start()
        logger.info("Xvfb 虚拟显示器已启动: {}", os.environ.get("DISPLAY"))
    except Exception as exc:
        logger.warning("Xvfb 启动失败: {}，将尝试直接运行", exc)


def start_browser():
    _ensure_virtual_display()
    co = _create_chromium_options()
    chrome_temp_dir = tempfile.mkdtemp(prefix="chrome_run_")
    co.set_user_data_path(chrome_temp_dir)
    browser_obj = Chromium(co)
    tabs = browser_obj.get_tabs()
    page_obj = tabs[-1] if tabs else browser_obj.new_tab()
    _set_chrome_temp_dir(chrome_temp_dir)
    _set_browser(browser_obj)
    _set_page(page_obj)
    return browser_obj, page_obj


def stop_browser():
    browser_obj = _get_browser()
    chrome_temp_dir = _get_chrome_temp_dir()
    if browser_obj is not None:
        try:
            browser_obj.quit()
        except Exception:
            pass
    _set_browser(None)
    _set_page(None)
    if chrome_temp_dir and os.path.isdir(chrome_temp_dir):
        shutil.rmtree(chrome_temp_dir, ignore_errors=True)
    _set_chrome_temp_dir("")


def restart_browser():
    browser_obj = _get_browser()
    if browser_obj is None:
        start_browser()
        return
    try:
        tabs = browser_obj.get_tabs()
        page_obj = tabs[-1] if tabs else browser_obj.new_tab()
        _set_page(page_obj)
        page_obj.run_js("window.localStorage.clear(); window.sessionStorage.clear();")
        page_obj.clear_cache(session_storage=True, cookies=True)
    except Exception:
        stop_browser()
        start_browser()


def refresh_active_page():
    browser_obj = _get_browser()
    if browser_obj is None:
        start_browser()
        browser_obj = _get_browser()
    try:
        tabs = browser_obj.get_tabs()
        page_obj = tabs[-1] if tabs else browser_obj.new_tab()
        _set_page(page_obj)
    except Exception:
        restart_browser()
        page_obj = _get_page()
    return page_obj


def open_signup_page():
    refresh_active_page()
    try:
        page.get(SIGNUP_URL)
    except Exception:
        refresh_active_page()
        page_obj = _get_browser().new_tab(SIGNUP_URL)
        _set_page(page_obj)
    click_email_signup_button()


def close_current_page():
    restart_browser()


def has_profile_form():
    refresh_active_page()
    try:
        return bool(
            page.run_js(
                """
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]');
return !!(givenInput && familyInput && passwordInput);
                """
            )
        )
    except Exception:
        return False


def click_email_signup_button(timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        clicked = page.run_js(r"""
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = candidates.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
    return text.includes('使用邮箱注册') || text.includes('signupwithemail') || text.includes('signupemail') || text.includes('continuewithemail') || text.includes('email');
});
if (!target) return false;
target.click();
return true;
        """)
        if clicked:
            return True
        time.sleep(0.5)
    raise Exception("未找到使用邮箱注册按钮")


def fill_email_and_submit(timeout=15):
    config = _get_config()
    mail_provider = mail_utils.create_mail_provider(
        config,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        proxy=str(config.get("proxy") or ""),
        impersonate="chrome131",
    )
    email, _password, dev_token = mail_provider.create_temp_email()
    if not email or not dev_token:
        raise Exception("获取邮箱失败")
    deadline = time.time() + timeout
    while time.time() < deadline:
        filled = page.run_js(
            """
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const input = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]')).find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) return 'not-ready';
input.focus();
input.click();
const valueSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
if ((input.value || '').trim() !== email || !input.checkValidity()) return false;
input.blur();
return 'filled';
            """,
            email,
        )
        if filled == "not-ready":
            time.sleep(0.5)
            continue
        if filled != "filled":
            time.sleep(0.5)
            continue
        time.sleep(0.8)
        clicked = page.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const input = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"]')).find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input || !input.checkValidity() || !(input.value || '').trim()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '');
    const t = text.toLowerCase();
    return text === '注册' || text.includes('注册') || t === 'signup' || t === 'sign up' || t.includes('sign up');
});
if (!submitButton || submitButton.disabled) return false;
submitButton.click();
return true;
        """)
        if clicked:
            logger.info("邮箱提交成功: {}", email)
            return email, dev_token
        time.sleep(0.5)
    raise Exception("未找到邮箱输入框或注册按钮")


def fill_code_and_submit(email, dev_token, timeout=120):
    _ = email
    config = _get_config()
    mail_provider = mail_utils.create_mail_provider(
        config,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        proxy=str(config.get("proxy") or ""),
        impersonate="chrome131",
    )
    code = wait_for_verification_code(mail_provider, dev_token, timeout=timeout)
    if not code:
        raise Exception("获取验证码失败")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            filled = page.run_js(
                """
const code = String(arguments[0] || '').trim();
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function setNativeValue(input, value) {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) {
        nativeSetter.call(input, '');
        nativeSetter.call(input, value);
    } else {
        input.value = '';
        input.value = value;
    }
}
function dispatchInputEvents(input, value) {
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
}
const input = Array.from(document.querySelectorAll('input[data-input-otp="true"], input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"]')).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || code.length || 6) > 1) || null;
const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    const autocomplete = String(node.autocomplete || '').toLowerCase();
    return maxLength === 1 || autocomplete === 'one-time-code';
});
if (!input && otpBoxes.length < code.length) return 'not-ready';
if (input) {
    input.focus();
    input.click();
    setNativeValue(input, code);
    dispatchInputEvents(input, code);
    const normalizedValue = String(input.value || '').trim();
    const expectedLength = Number(input.maxLength || code.length || 6);
    const slots = Array.from(document.querySelectorAll('[data-input-otp-slot="true"]'));
    const filledSlots = slots.filter((slot) => (slot.textContent || '').trim()).length;
    if (normalizedValue !== code) return 'aggregate-mismatch';
    if (expectedLength > 0 && normalizedValue.length !== expectedLength) return 'aggregate-length-mismatch';
    if (slots.length && filledSlots && filledSlots !== normalizedValue.length) return 'aggregate-slot-mismatch';
    input.blur();
    return 'filled';
}
const orderedBoxes = otpBoxes.slice(0, code.length);
for (let i = 0; i < orderedBoxes.length; i += 1) {
    const box = orderedBoxes[i];
    const char = code[i] || '';
    box.focus();
    box.click();
    setNativeValue(box, char);
    dispatchInputEvents(box, char);
    box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: char }));
    box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: char }));
    box.blur();
}
const merged = orderedBoxes.map((node) => String(node.value || '').trim()).join('');
return merged === code ? 'filled' : 'box-mismatch';
                """,
                code,
            )
        except PageDisconnectedError:
            refresh_active_page()
            if has_profile_form():
                logger.info("验证码提交后已跳转到最终注册页。")
                return code
            time.sleep(1)
            continue

        if filled == "not-ready":
            if has_profile_form():
                logger.info("已直接进入最终注册页，跳过验证码按钮确认。")
                return code
            time.sleep(0.5)
            continue
        if filled != "filled":
            time.sleep(0.5)
            continue

        time.sleep(1.2)
        try:
            clicked = page.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const aggregateInput = Array.from(document.querySelectorAll('input[data-input-otp="true"], input[name="code"], input[autocomplete="one-time-code"], input[inputmode="numeric"], input[inputmode="text"]')).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 0) > 1) || null;
let value = '';
if (aggregateInput) {
    value = String(aggregateInput.value || '').trim();
    const expectedLength = Number(aggregateInput.maxLength || value.length || 6);
    if (!value || (expectedLength > 0 && value.length !== expectedLength)) return false;
    const slots = Array.from(document.querySelectorAll('[data-input-otp-slot="true"]'));
    if (slots.length) {
        const filledSlots = slots.filter((slot) => (slot.textContent || '').trim()).length;
        if (filledSlots && filledSlots !== value.length) return false;
    }
} else {
    const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
        if (!isVisible(node) || node.disabled || node.readOnly) return false;
        const maxLength = Number(node.maxLength || 0);
        const autocomplete = String(node.autocomplete || '').toLowerCase();
        return maxLength === 1 || autocomplete === 'one-time-code';
    });
    value = otpBoxes.map((node) => String(node.value || '').trim()).join('');
    if (!value || value.length < 6) return false;
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button')).filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const confirmButton = buttons.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '');
    const t = text.toLowerCase();
    return text === '确认邮箱' || text.includes('确认邮箱') || text === '继续' || text.includes('继续') || text === '下一步' || text.includes('下一步') || t.includes('confirm') || t.includes('continue') || t.includes('next') || t.includes('verify');
});
if (!confirmButton) return 'no-button';
confirmButton.focus();
confirmButton.click();
return 'clicked';
                    """)
        except PageDisconnectedError:
            refresh_active_page()
            if has_profile_form():
                logger.info("确认邮箱后页面跳转成功，已进入最终注册页。")
                return code
            clicked = "disconnected"

        if clicked == "clicked":
            logger.info("已填写验证码并点击确认邮箱: {}", code)
            time.sleep(2)
            refresh_active_page()
            if has_profile_form():
                logger.info("验证码确认完成，最终注册页已就绪。")
            return code
        if clicked == "no-button":
            current_url = page.url
            if "sign-up" in current_url or "signup" in current_url:
                return code
        if clicked == "disconnected":
            time.sleep(1)
            continue
        time.sleep(0.5)
    raise Exception("未找到验证码输入框或确认邮箱按钮")


def getTurnstileToken():
    page.run_js("try { turnstile.reset() } catch(e) { }")
    for _ in range(15):
        try:
            turnstileResponse = page.run_js(
                "try { return turnstile.getResponse() } catch(e) { return null }"
            )
            if turnstileResponse:
                return turnstileResponse
            challengeSolution = page.ele("@name=cf-turnstile-response")
            challengeWrapper = challengeSolution.parent()
            challengeIframe = challengeWrapper.shadow_root.ele("tag:iframe")
            challengeIframe.run_js(
                """
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
let screenX = getRandomInt(800, 1200);
let screenY = getRandomInt(400, 600);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: screenX });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: screenY });
                """
            )
            challengeIframeBody = challengeIframe.ele("tag:body").shadow_root
            challengeButton = challengeIframeBody.ele("tag:input")
            challengeButton.click()
        except Exception:
            pass
        time.sleep(1)
    raise Exception("failed to solve turnstile")


def extract_verification_code(content: str) -> Optional[str]:
    import re

    if not content:
        return None
    m = re.search(r"(?<![A-Z0-9-])([A-Z0-9]{3}-[A-Z0-9]{3})(?![A-Z0-9-])", content)
    if m:
        return m.group(1)
    m = re.search(
        r"(?:verification code|验证码|your code)[:\s]*[<>\s]*([A-Z0-9]{3}-[A-Z0-9]{3})\b",
        content,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?([A-Z0-9]{3}-[A-Z0-9]{3})[\s\S]*?</p>",
        content,
    )
    if m:
        return m.group(1)
    m = re.search(r"Subject:.*?(\d{6})", content)
    if m and m.group(1) != "177010":
        return m.group(1)
    for code in re.findall(r">\s*(\d{6})\s*<", content):
        if code != "177010":
            return code
    for code in re.findall(r"(?<![&#\d])(\d{6})(?![&#\d])", content):
        if code != "177010":
            return code
    return None


def wait_for_verification_code(
    mail_provider, mail_token: str, timeout: int = 120
) -> Optional[str]:
    start = time.time()
    seen_ids = set()
    while time.time() - start < timeout:
        messages = mail_provider.fetch_emails(mail_token) or []
        if messages:
            logger.info("收到 {} 封候选邮件，开始扫描验证码。", len(messages))
        for msg in messages:
            if not isinstance(msg, dict):
                continue

            direct_content = "\n".join(
                [
                    str(msg.get("subject") or ""),
                    str(msg.get("text") or msg.get("body") or ""),
                    str(msg.get("html") or ""),
                ]
            ).strip()
            if direct_content:
                code = extract_verification_code(direct_content)
                if code:
                    logger.info("从邮件列表提取到验证码: {}", code)
                    return code.replace("-", "")

            msg_id = msg.get("id") or msg.get("@id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            detail = mail_provider.fetch_email_detail(mail_token, str(msg_id))
            if not isinstance(detail, dict):
                continue
            content = "\n".join(
                [
                    str(detail.get("subject") or ""),
                    str(detail.get("text") or detail.get("body") or ""),
                    str(detail.get("html") or ""),
                ]
            ).strip()
            code = extract_verification_code(content)
            if code:
                logger.info("从邮箱提取到验证码: {}", code)
                return code.replace("-", "")
        time.sleep(3)
    return None


def build_profile():
    name = random_name().split(" ", 1)
    given_name = name[0]
    family_name = name[1] if len(name) > 1 else "Smith"
    password = "N" + secrets.token_hex(4) + "!a7#" + secrets.token_urlsafe(6)
    return given_name, family_name, password


def fill_profile_and_submit(timeout=30):
    given_name, family_name, password = build_profile()
    deadline = time.time() + timeout
    turnstile_token = ""
    while time.time() < deadline:
        filled = page.run_js(
            """
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
}
function setInputValue(input, value) {
    if (!input) return false;
    input.focus();
    input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) {
        nativeSetter.call(input, '');
        nativeSetter.call(input, value);
    } else {
        input.value = '';
        input.value = value;
    }
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, cancelable: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new Event('blur', { bubbles: true }));
    return String(input.value || '') === String(value || '');
}
const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"]');
if (!givenInput || !familyInput || !passwordInput) return 'not-ready';
const givenOk = setInputValue(givenInput, givenName);
const familyOk = setInputValue(familyInput, familyName);
const passwordOk = setInputValue(passwordInput, password);
if (!givenOk || !familyOk || !passwordOk) return 'filled-failed';
return [
    String(givenInput.value || '').trim() === String(givenName || '').trim(),
    String(familyInput.value || '').trim() === String(familyName || '').trim(),
    String(passwordInput.value || '') === String(password || ''),
].every(Boolean) ? 'filled' : 'verify-failed';
            """,
            given_name,
            family_name,
            password,
        )
        if filled == "not-ready":
            time.sleep(0.5)
            continue
        if filled != "filled":
            time.sleep(0.5)
            continue
        turnstile_state = page.run_js(
            """
const challengeInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!challengeInput) return 'not-found';
const value = String(challengeInput.value || '').trim();
return value ? 'ready' : 'pending';
            """
        )
        if turnstile_state == "pending" and not turnstile_token:
            turnstile_token = getTurnstileToken()
            if turnstile_token:
                page.run_js(
                    """
const token = arguments[0];
const challengeInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!challengeInput) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(challengeInput, token); else challengeInput.value = token;
challengeInput.dispatchEvent(new Event('input', { bubbles: true }));
challengeInput.dispatchEvent(new Event('change', { bubbles: true }));
return true;
                    """,
                    turnstile_token,
                )
        time.sleep(1.2)
        try:
            submit_button = (
                page.ele("tag:button@@text()=完成注册")
                or page.ele("tag:button@@text():Create Account")
                or page.ele("tag:button@@text():Sign up")
            )
        except Exception:
            submit_button = None
        if not submit_button:
            clicked = page.run_js(r"""
const challengeInput = document.querySelector('input[name="cf-turnstile-response"]');
if (challengeInput && !String(challengeInput.value || '').trim()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button'));
const submitButton = buttons.find((node) => {
    const text = (node.innerText || node.textContent || '').replace(/\s+/g, '');
    const t = text.toLowerCase();
    return text === '完成注册' || text.includes('完成注册') || t.includes('create account') || t.includes('sign up') || t.includes('complete');
});
if (!submitButton || submitButton.disabled || submitButton.getAttribute('aria-disabled') === 'true') return false;
submitButton.focus();
submitButton.click();
return true;
            """)
        else:
            challenge_value = page.run_js("""
const challengeInput = document.querySelector('input[name="cf-turnstile-response"]');
return challengeInput ? String(challengeInput.value || '').trim() : 'not-found';
            """)
            if challenge_value not in ("not-found", ""):
                submit_button.click()
                clicked = True
            else:
                clicked = False
        if clicked:
            logger.info(
                "注册资料提交成功: {} {} / {}",
                given_name,
                family_name,
                password,
            )
            return {
                "given_name": given_name,
                "family_name": family_name,
                "password": password,
            }
        time.sleep(0.5)
    raise Exception("未找到最终注册表单或完成注册按钮")


def extract_visible_numbers(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = page.run_js(r"""
function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const selector = ['h1','h2','h3','h4','h5','h6','div','span','p','strong','b','small','[data-testid]','[class]','[role="heading"]'].join(',');
const seen = new Set();
const matches = [];
for (const node of document.querySelectorAll(selector)) {
    if (!isVisible(node)) continue;
    const text = String(node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const found = text.match(/\d+(?:\.\d+)?/g);
    if (!found) continue;
    for (const value of found) {
        const key = `${value}@@${text}`;
        if (seen.has(key)) continue;
        seen.add(key);
        matches.push({ value, text });
    }
}
return matches.slice(0, 30);
        """)
        if result:
            return result
        time.sleep(1)
    raise Exception("登录后未提取到可见数字文本")


def wait_for_sso_cookie(timeout=30):
    deadline = time.time() + timeout
    last_seen_names = set()
    while time.time() < deadline:
        try:
            refresh_active_page()
            if page is None:
                time.sleep(1)
                continue
            cookies = page.cookies(all_domains=True, all_info=True) or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()
                if name:
                    last_seen_names.add(name)
                if name == "sso" and value:
                    logger.info("注册完成后已获取到 sso cookie。")
                    return value
        except PageDisconnectedError:
            refresh_active_page()
        except Exception:
            pass
        time.sleep(1)
    raise Exception(
        f"注册完成后未获取到 sso cookie，当前已见 cookie: {sorted(last_seen_names)}"
    )


def append_sso_to_txt(sso_value, output_path):
    normalized = str(sso_value or "").strip()
    if not normalized:
        raise Exception("待写入的 sso 为空")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with _file_lock:
        with open(output_path, "a", encoding="utf-8") as file:
            file.write(normalized + "\n")
    logger.info("已追加写入 sso 到文件: {}", output_path)


def run_single_registration(output_path, extract_numbers=False):
    run_stage("打开注册页", open_signup_page)
    email, dev_token = run_stage("提交邮箱", fill_email_and_submit)
    run_stage("提交验证码", fill_code_and_submit, email, dev_token)
    profile = run_stage("提交注册资料", fill_profile_and_submit)
    sso_value = run_stage("获取 sso", wait_for_sso_cookie)
    append_sso_to_txt(sso_value, output_path)
    if extract_numbers:
        extract_visible_numbers()
    result = {"email": email, "sso": sso_value, **profile}
    logger.info(
        "注册成功 | email={} | password={} | given={} | family={}",
        email,
        profile.get("password", ""),
        profile.get("given_name", ""),
        profile.get("family_name", ""),
    )
    logger.info("注册完成: {}", email)
    return result


def load_run_count():
    return int(_get_config().get("total_accounts") or 1)


def _run_single_account(task_index, output_path, extract_numbers=False):
    logger.info("开始执行注册流程 | task={}", task_index)
    start_browser()
    try:
        return run_single_registration(output_path, extract_numbers=extract_numbers)
    finally:
        stop_browser()


def _run_loop(total_accounts, output_path, extract_numbers=False, max_workers=None):
    ensure_stable_python_runtime()
    warn_runtime_compatibility()
    collected_sso = []
    resolved_total_accounts = int(total_accounts or 0)
    if resolved_total_accounts <= 0:
        raise ValueError("Grok provider 目前要求 total_accounts 大于 0")
    resolved_max_workers = int(max_workers or _get_config().get("concurrency") or 1)
    actual_workers = max(1, min(resolved_max_workers, resolved_total_accounts))
    logger.info(
        "Grok 并发执行开始 | total_accounts={} | workers={}",
        resolved_total_accounts,
        actual_workers,
    )
    try:
        with ThreadPoolExecutor(max_workers=actual_workers) as executor:
            futures = [
                executor.submit(
                    _run_single_account,
                    task_index,
                    output_path,
                    extract_numbers,
                )
                for task_index in range(1, resolved_total_accounts + 1)
            ]
            for future in as_completed(futures):
                try:
                    result = future.result()
                    collected_sso.append(result["sso"])
                except KeyboardInterrupt:
                    logger.info("收到中断信号，停止执行。")
                    raise
                except Exception as error:
                    logger.error("注册失败: {}", error)
    finally:
        if collected_sso and g2a_utils.should_upload(_get_config()):
            logger.info("准备推送 {} 个 token 到 API...", len(collected_sso))
            g2a_utils.upload_sso_tokens(
                collected_sso,
                _get_config(),
                proxy=str(_get_config().get("proxy") or ""),
                logger=logger.info,
            )
        stop_browser()


def run_batch(total_accounts=None, max_workers=None, proxy=None):
    config = _get_config()
    provider_cfg = ((config or {}).get("model_providers") or {}).get("grok") or {}
    provider = GrokModelProvider(
        browser_proxy=provider_cfg.get("browser_proxy"),
    )
    return provider.run_batch(
        total_accounts=total_accounts, max_workers=max_workers, proxy=proxy
    )


def main():
    config_count = load_run_count()
    parser = argparse.ArgumentParser(description="xAI 自动注册并采集 sso")
    parser.add_argument("--count", type=int, default=config_count)
    parser.add_argument("--output", default=_default_sso_file(_get_config()))
    parser.add_argument("--extract-numbers", action="store_true")
    args = parser.parse_args()
    _run_loop(
        total_accounts=args.count,
        output_path=args.output,
        extract_numbers=args.extract_numbers,
        max_workers=_get_config().get("concurrency"),
    )


if __name__ == "__main__":
    main()
