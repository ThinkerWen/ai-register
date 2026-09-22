import re
import time

from util.providers.base import MailProviderError
from util.providers.duckmail import DuckMailProvider
from util.providers.tempmail import TempMailLolProvider

_PROVIDER_REGISTRY = {
    "duckmail": DuckMailProvider,
    "tempmail": TempMailLolProvider,
}


def register_mail_provider(name, provider_cls):
    """注册新邮箱 provider。"""
    key = str(name or "").strip().lower()
    if not key:
        raise MailProviderError("provider 名称不能为空")
    _PROVIDER_REGISTRY[key] = provider_cls


def _resolve_provider_name(config):
    return str((config or {}).get("mail_provider") or "duckmail").strip().lower()


def _resolve_provider_settings(config, provider_name):
    provider_map = (config or {}).get("mail_providers")
    if isinstance(provider_map, dict):
        cfg = provider_map.get(provider_name)
        if isinstance(cfg, dict):
            return dict(cfg)
    return {}


def create_mail_provider(config, *, user_agent=None, proxy=None, impersonate="chrome131", password_generator=None):
    provider_name = _resolve_provider_name(config)
    provider_cls = _PROVIDER_REGISTRY.get(provider_name)
    if not provider_cls:
        available = ", ".join(sorted(_PROVIDER_REGISTRY.keys()))
        raise MailProviderError(f"不支持的 mail_provider: {provider_name} (可选: {available})")

    provider_cfg = _resolve_provider_settings(config, provider_name)
    if provider_name == "duckmail":
        api_base = provider_cfg.get("api_base")
        bearer = provider_cfg.get("bearer")
        if not api_base:
            raise MailProviderError("mail_providers.duckmail.api_base 未配置")
        if not bearer:
            raise MailProviderError("mail_providers.duckmail.bearer 未配置")
        return provider_cls(
            api_base=api_base,
            bearer=bearer,
            user_agent=user_agent,
            proxy=proxy,
            impersonate=impersonate,
            password_generator=password_generator,
        )

    if provider_name == "tempmail":
        tempmail_proxy = provider_cfg.get("proxy") or proxy
        return provider_cls(
            api_key=provider_cfg.get("api_key"),
            domain=provider_cfg.get("domain"),
            prefix=provider_cfg.get("prefix"),
            proxy=tempmail_proxy,
            user_agent=user_agent,
            impersonate=impersonate,
        )

    raise MailProviderError(f"provider 初始化未实现: {provider_name}")


def get_mail_provider_info(config):
    provider_name = _resolve_provider_name(config)
    provider_cfg = _resolve_provider_settings(config, provider_name)

    if provider_name == "duckmail":
        api_base = provider_cfg.get("api_base")
        return {"name": provider_name, "api_base": api_base or ""}

    if provider_name == "tempmail":
        return {
            "name": provider_name,
            "api_base": "https://api.tempmail.lol/v2",
        }

    return {"name": provider_name, "api_base": ""}


def validate_mail_provider_config(config):
    try:
        create_mail_provider(config, user_agent="Mozilla/5.0", proxy=config.get("proxy"))
        return True, "ok"
    except Exception as e:
        return False, str(e)


def create_temp_email(
    duckmail_api_base=None,
    duckmail_bearer=None,
    user_agent=None,
    proxy=None,
    impersonate="chrome131",
    password_generator=None,
    provider=None,
    config=None,
):
    """创建临时邮箱，返回 (email, password, mail_token)。"""
    resolved = provider
    if resolved is None:
        if config is not None:
            resolved = create_mail_provider(
                config,
                user_agent=user_agent,
                proxy=proxy,
                impersonate=impersonate,
                password_generator=password_generator,
            )
        else:
            resolved = DuckMailProvider(
                api_base=duckmail_api_base,
                bearer=duckmail_bearer,
                user_agent=user_agent,
                proxy=proxy,
                impersonate=impersonate,
                password_generator=password_generator,
            )
    return resolved.create_temp_email()


def fetch_emails(mail_token, provider=None, config=None, user_agent=None, proxy=None, impersonate="chrome131"):
    resolved = provider
    if resolved is None:
        if config is None:
            raise MailProviderError("fetch_emails 需要 provider 或 config")
        resolved = create_mail_provider(config, user_agent=user_agent, proxy=proxy, impersonate=impersonate)
    return resolved.fetch_emails(mail_token)


def fetch_email_detail(msg_id, mail_token, provider=None, config=None, user_agent=None, proxy=None, impersonate="chrome131"):
    resolved = provider
    if resolved is None:
        if config is None:
            raise MailProviderError("fetch_email_detail 需要 provider 或 config")
        resolved = create_mail_provider(config, user_agent=user_agent, proxy=proxy, impersonate=impersonate)
    return resolved.fetch_email_detail(mail_token, msg_id)


def fetch_emails_duckmail(
    duckmail_api_base,
    mail_token,
    user_agent=None,
    proxy=None,
    impersonate="chrome131",
):
    """兼容旧接口：从 DuckMail 获取邮件列表。"""
    provider = DuckMailProvider(
        api_base=duckmail_api_base,
        bearer="",
        user_agent=user_agent,
        proxy=proxy,
        impersonate=impersonate,
    )
    return provider.fetch_emails(mail_token)


def fetch_email_detail_duckmail(
    duckmail_api_base,
    mail_token,
    msg_id,
    user_agent=None,
    proxy=None,
    impersonate="chrome131",
):
    """兼容旧接口：获取 DuckMail 单封邮件详情。"""
    provider = DuckMailProvider(
        api_base=duckmail_api_base,
        bearer="",
        user_agent=user_agent,
        proxy=proxy,
        impersonate=impersonate,
    )
    return provider.fetch_email_detail(mail_token, msg_id)


def extract_verification_code(email_content):
    """从邮件内容提取 6 位验证码"""
    if not email_content:
        return None

    patterns = [
        r"Verification code:?\s*(\d{6})",
        r"code is\s*(\d{6})",
        r"代码为[:：]?\s*(\d{6})",
        r"验证码[:：]?\s*(\d{6})",
        r">\s*(\d{6})\s*<",
        r"(?<![#&])\b(\d{6})\b",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, email_content, re.IGNORECASE)
        for code in matches:
            if code == "177010":
                continue
            return code
    return None


def wait_for_verification_email(
    duckmail_api_base=None,
    mail_token=None,
    timeout=120,
    user_agent=None,
    proxy=None,
    impersonate="chrome131",
    logger=None,
    provider=None,
    config=None,
):
    """等待并提取 OpenAI 验证码。"""
    if not mail_token:
        return None

    resolved = provider
    if resolved is None:
        if config is not None:
            resolved = create_mail_provider(config, user_agent=user_agent, proxy=proxy, impersonate=impersonate)
        else:
            resolved = DuckMailProvider(
                api_base=duckmail_api_base,
                bearer="",
                user_agent=user_agent,
                proxy=proxy,
                impersonate=impersonate,
            )

    start_time = time.time()

    while time.time() - start_time < timeout:
        messages = resolved.fetch_emails(mail_token)
        if messages:
            # 扫描多封邮件，避免只检查首封导致漏掉验证码。
            for msg in messages[:12]:
                if not isinstance(msg, dict):
                    continue

                # 优先直接从列表项可见字段提取，减少一次详情请求。
                content_parts = [
                    msg.get("subject") or "",
                    msg.get("text") or msg.get("body") or "",
                    msg.get("html") or "",
                ]
                direct_content = "\n".join(content_parts).strip()
                if direct_content:
                    code = extract_verification_code(direct_content)
                    if code:
                        return code

                msg_id = msg.get("id") or msg.get("@id")
                if not msg_id:
                    continue

                detail = resolved.fetch_email_detail(mail_token, msg_id)
                if not detail:
                    continue

                detail_content = "\n".join(
                    [
                        detail.get("subject") or "",
                        detail.get("text") or detail.get("body") or "",
                        detail.get("html") or "",
                    ]
                ).strip()
                if not detail_content:
                    continue

                code = extract_verification_code(detail_content)
                if code:
                    return code

        elapsed = int(time.time() - start_time)
        if logger:
            logger(f"等待中... ({elapsed}s/{timeout}s)")
        time.sleep(3)

    return None
