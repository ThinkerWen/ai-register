import math
import time
from urllib.parse import quote, urlsplit
from uuid import uuid4

import requests

from util.providers.base import MailProvider, MailProviderError


class TempMailHubProvider(MailProvider):
    name = "tempmail_hub"

    def __init__(
        self, api_base, api_token, provider="auto", ttl_seconds=3600, create_timeout=120, poll_interval=1, proxy=None, user_agent=None
    ):
        self.api_base = (api_base or "").strip().rstrip("/")
        self.api_token = (api_token or "").strip()
        self.provider = provider or "auto"
        self.ttl_seconds = ttl_seconds
        self.proxy = proxy or None
        self.user_agent = user_agent or "TempMailHubProvider/1.0"
        if not self.api_base:
            raise MailProviderError("mail_providers.tempmail_hub.api_base 未配置")
        if urlsplit(self.api_base).scheme not in {"http", "https"} or not urlsplit(self.api_base).netloc:
            raise MailProviderError("mail_providers.tempmail_hub.api_base 必须是 HTTP(S) 服务地址")
        if not self.api_token:
            raise MailProviderError("mail_providers.tempmail_hub.api_token 未配置")
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 31_536_000:
            raise MailProviderError("mail_providers.tempmail_hub.ttl_seconds 必须是 60–31536000 之间的整数")
        for key, value in (("create_timeout", create_timeout), ("poll_interval", poll_interval)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise MailProviderError(f"mail_providers.tempmail_hub.{key} 必须是有限正数")
        self.create_timeout = create_timeout
        self.poll_interval = poll_interval

    def _request(self, method, endpoint, *, timeout=30, **kwargs):
        try:
            with requests.Session() as session:
                # 自部署服务默认直连，避免注册代理或环境代理影响本地收件。
                session.trust_env = False
                session.headers.update(
                    {"Authorization": f"Bearer {self.api_token}", "Accept": "application/json", "User-Agent": self.user_agent}
                )
                if self.proxy:
                    session.proxies.update({"http": self.proxy, "https": self.proxy})
                response = session.request(method, f"{self.api_base}{endpoint}", timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            raise MailProviderError(f"Temp Mail Hub 请求失败: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise MailProviderError(f"Temp Mail Hub 返回非 JSON 响应 (HTTP {response.status_code})") from exc
        if not 200 <= response.status_code < 300:
            error = data.get("error") if isinstance(data, dict) else None
            code = error.get("code", "") if isinstance(error, dict) else ""
            raise MailProviderError(f"Temp Mail Hub HTTP {response.status_code}: {code}")
        if not isinstance(data, dict):
            raise MailProviderError("Temp Mail Hub 返回结果必须是对象")
        return data

    def create_temp_email(self):
        deadline = time.monotonic() + self.create_timeout
        operation = self._request(
            "POST",
            "/v1/mailboxes",
            timeout=min(30, self.create_timeout),
            headers={"Idempotency-Key": uuid4().hex},
            json={"provider": self.provider, "required_capabilities": ["receive"], "ttl_seconds": self.ttl_seconds},
        )
        operation_id = operation.get("id")
        if not operation_id:
            raise MailProviderError("Temp Mail Hub 创建响应缺少操作 id")
        endpoint = f"/v1/operations/{quote(str(operation_id), safe='')}"
        while True:
            status = operation.get("status")
            if status == "succeeded":
                result = operation.get("result") or {}
                if not isinstance(result, dict) or not result.get("email") or not result.get("mailbox_id"):
                    raise MailProviderError("Temp Mail Hub 创建结果缺少 email/mailbox_id")
                # mailbox_id 可跨 provider 实例复用；服务令牌仅用于 HTTP 鉴权。
                return result["email"], "", result["mailbox_id"]
            if status in {"failed", "unknown"}:
                raise MailProviderError(
                    f"Temp Mail Hub 创建邮箱 {status}: {operation.get('error_code') or 'UNKNOWN'} (操作 {operation_id})"
                )
            if status not in {"pending", "running"}:
                raise MailProviderError(f"Temp Mail Hub 未知操作状态: {status}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MailProviderError(f"Temp Mail Hub 创建邮箱超时 (操作 {operation_id})，请检查 Worker 是否运行")
            time.sleep(min(self.poll_interval, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MailProviderError(f"Temp Mail Hub 创建邮箱超时 (操作 {operation_id})，请检查 Worker 是否运行")
            operation = self._request("GET", endpoint, timeout=min(30, remaining))

    @staticmethod
    def _normalize_message(message):
        return {
            "id": message["id"],
            "from": message.get("sender") or "",
            "to": message.get("recipients") or [],
            "subject": message.get("subject") or "",
            "text": message.get("text") or "",
            "html": "",
            "date": message.get("received_at") or "",
        }

    def fetch_emails(self, mail_token):
        try:
            data = self._request("GET", f"/v1/mailboxes/{quote(str(mail_token), safe='')}/messages", params={"limit": 100})
            items = data.get("items") or []
            if not isinstance(items, list):
                raise MailProviderError("Temp Mail Hub 邮件列表必须是数组")
            return [self._normalize_message(item) for item in items if isinstance(item, dict) and item.get("id")]
        except MailProviderError:
            # 与其他 provider 一致，临时收件错误交给验证码轮询重试。
            return []

    def fetch_email_detail(self, mail_token, msg_id):
        try:
            endpoint = f"/v1/mailboxes/{quote(str(mail_token), safe='')}/messages/{quote(str(msg_id), safe='')}"
            data = self._request("GET", endpoint)
            return self._normalize_message(data) if data.get("id") else None
        except MailProviderError:
            return None
