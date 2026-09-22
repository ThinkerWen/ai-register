import requests
from TempMail import TempMail

from util.providers.base import MailProvider, MailProviderError


class TempMailLolProvider(MailProvider):
    name = "tempmail"

    def __init__(self, api_key=None, domain=None, prefix=None, proxy=None, user_agent=None, **kwargs):
        _ = kwargs
        self.api_key = api_key or None
        self.domain = domain or None
        self.prefix = prefix or None
        self.proxy = proxy or None
        self.user_agent = user_agent or "TempMailPythonAPI/3.0"
        self.client = TempMail(self.api_key)

    def _http_request(self, endpoint, method="GET", payload=None):
        url = f"https://api.tempmail.lol/v2{endpoint}"
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        proxies = None
        if self.proxy:
            proxies = {"http": self.proxy, "https": self.proxy}

        if method == "POST":
            response = requests.post(url, headers=headers, json=payload or {}, timeout=30, proxies=proxies)
        else:
            response = requests.get(url, headers=headers, timeout=30, proxies=proxies)

        if response.status_code == 429:
            raise MailProviderError(f"TempMail Rate Limit: {response.text}")
        if 400 <= response.status_code < 500:
            raise MailProviderError(f"HTTP Error: {response.status_code} {response.text}")
        if 500 <= response.status_code < 600:
            raise MailProviderError(f"TempMail Server Error: {response.status_code} {response.text}")

        return response.json()

    def _create_inbox_proxy_mode(self):
        data = self._http_request(
            "/inbox/create",
            method="POST",
            payload={"domain": self.domain, "prefix": self.prefix},
        )
        address = data.get("address")
        token = data.get("token")
        if not address or not token:
            raise MailProviderError("TempMail 返回结果缺少 address/token")
        return address, token

    def _get_emails_proxy_mode(self, mail_token):
        data = self._http_request(f"/inbox?token={mail_token}")
        if data.get("expired") is True:
            raise MailProviderError("Token Expired")
        return data.get("emails") or []

    def create_temp_email(self):
        try:
            if self.proxy:
                address, token = self._create_inbox_proxy_mode()
            else:
                inbox = self.client.createInbox(domain=self.domain, prefix=self.prefix)
                address = getattr(inbox, "address", None)
                token = getattr(inbox, "token", None)
        except Exception as e:
            raise MailProviderError(f"TempMail 创建邮箱失败: {e}")

        if not address or not token:
            raise MailProviderError("TempMail 返回结果缺少 address/token")

        # tempmail-lol 没有邮箱密码概念，返回空字符串保持接口兼容。
        return address, "", token

    def fetch_emails(self, mail_token):
        try:
            if self.proxy:
                emails = self._get_emails_proxy_mode(mail_token)
            else:
                emails = self.client.getEmails(mail_token)
        except Exception:
            return []

        out = []
        for idx, email in enumerate(emails):
            if isinstance(email, dict):
                sender = email.get("from", "")
                recipient = email.get("to", "")
                subject = email.get("subject", "")
                body = email.get("body", "") or ""
                html = email.get("html", "") or ""
                date = email.get("date", "")
            else:
                sender = getattr(email, "sender", "")
                recipient = getattr(email, "recipient", "")
                subject = getattr(email, "subject", "")
                body = getattr(email, "body", "") or ""
                html = getattr(email, "html", "") or ""
                date = getattr(email, "date", "")

            out.append(
                {
                    "id": str(idx),
                    "from": sender,
                    "to": recipient,
                    "subject": subject,
                    "text": body,
                    "html": html,
                    "date": date,
                }
            )
        return out

    def fetch_email_detail(self, mail_token, msg_id):
        messages = self.fetch_emails(mail_token)
        if not messages:
            return None

        try:
            index = int(str(msg_id).split("/")[-1])
        except Exception:
            return None

        if index < 0 or index >= len(messages):
            return None

        msg = messages[index]
        return {
            "text": msg.get("text") or "",
            "html": msg.get("html") or "",
            "subject": msg.get("subject") or "",
            "from": msg.get("from") or "",
            "to": msg.get("to") or "",
            "date": msg.get("date") or "",
        }
