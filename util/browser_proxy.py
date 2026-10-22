import re
from urllib.parse import unquote, urlsplit

from util.logger import get_logger


def redact_proxy(proxy):
    return re.sub(r"(://)[^/]*@", r"\1***@", str(proxy or ""))


class BrowserProxy:
    """Keep proxy credentials out of Chromium arguments and answer proxy-only challenges."""

    def __init__(self, proxy):
        self.server = str(proxy or "").strip()
        self.username = None
        self.password = ""
        self._drivers = set()
        if not self.server:
            return
        parsed = urlsplit(self.server if "://" in self.server else f"http://{self.server}")
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("浏览器代理端口无效") from None
        if not parsed.hostname or parsed.scheme not in {"http", "https", "socks4", "socks5"}:
            raise ValueError("浏览器代理需使用 http、https、socks4 或 socks5 地址")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("浏览器代理地址不能包含路径、查询参数或片段；账号密码中的特殊字符请使用 URL 编码")
        self.host = parsed.hostname
        self.port = port or {"http": 80, "https": 443, "socks4": 1080, "socks5": 1080}[parsed.scheme]
        host = f"[{self.host}]" if ":" in self.host else self.host
        self.server = f"{parsed.scheme}://{host}:{self.port}"
        if parsed.username is not None:
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("浏览器认证代理仅支持 HTTP/HTTPS；SOCKS 代理请使用无认证地址")
            self.username = unquote(parsed.username)
            self.password = unquote(parsed.password or "")

    def attach(self, page):
        if self.username is None or page.driver in self._drivers:
            return
        driver = page.driver
        attempted = set()

        def on_request(requestId, **kwargs):
            driver.run("Fetch.continueRequest", requestId=requestId)

        def on_auth(requestId, authChallenge, **kwargs):
            origin = urlsplit(authChallenge.get("origin", ""))
            port = origin.port or (443 if origin.scheme == "https" else 80)
            is_proxy = authChallenge.get("source") == "Proxy" and origin.hostname == self.host and port == self.port
            response = {"response": "Default"}
            if is_proxy:
                if requestId in attempted:
                    response = {"response": "CancelAuth"}
                    get_logger("grok").warning("代理认证被拒绝，请检查浏览器代理账号密码")
                else:
                    attempted.add(requestId)
                    response = {"response": "ProvideCredentials", "username": self.username, "password": self.password}
            driver.run("Fetch.continueWithAuth", requestId=requestId, authChallengeResponse=response)

        driver.set_callback("Fetch.requestPaused", on_request)
        driver.set_callback("Fetch.authRequired", on_auth)
        page.run_cdp("Fetch.enable", handleAuthRequests=True)
        self._drivers.add(driver)
