from urllib.parse import urlparse

import requests


def _parse_g2a_config(config):
    g2a_cfg = (config or {}).get("g2a")
    if not isinstance(g2a_cfg, dict):
        g2a_cfg = {}

    enabled = bool(g2a_cfg.get("enable", False))
    api_url = str(g2a_cfg.get("api_url") or "").strip()
    token = str(g2a_cfg.get("token") or "").strip()
    append = bool(g2a_cfg.get("append", True))
    use_proxy = bool(g2a_cfg.get("use_proxy", False))
    return enabled, api_url, token, append, use_proxy


def should_upload(config):
    enabled, api_url, token, _, _ = _parse_g2a_config(config)
    return enabled and bool(api_url) and bool(token)


def validate_g2a_config(config):
    enabled, api_url, token, _, _ = _parse_g2a_config(config)
    if not enabled:
        return True, "g2a disabled"
    if not api_url:
        return False, "g2a.enable=true 但 g2a.api_url 未配置"
    if not token:
        return False, "g2a.enable=true 但 g2a.token 未配置"
    return True, "ok"


def upload_sso_tokens(tokens, config, proxy=None, logger=None):
    enabled, api_url, token, append_mode, use_proxy_pref = _parse_g2a_config(config)
    if not enabled:
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    tokens_to_push = [str(item).strip() for item in (tokens or []) if str(item).strip()]
    if not tokens_to_push:
        return False

    session = requests.Session()
    host = (urlparse(api_url).hostname or "").lower()
    final_use_proxy = bool(proxy) and (bool(use_proxy_pref) or host not in {"localhost", "127.0.0.1", "::1"})
    if final_use_proxy:
        resolved_proxy = str(proxy)
        session.proxies = {"http": resolved_proxy, "https": resolved_proxy}
    elif proxy and logger:
        logger(f"[G2A] 检测到本地地址 {host}，上传请求已绕过代理")

    if append_mode:
        try:
            get_resp = session.get(api_url, headers=headers, timeout=15, verify=False)
            if get_resp.status_code == 200:
                data = get_resp.json()
                if isinstance(data, dict) and isinstance(data.get("tokens"), dict):
                    existing = data["tokens"].get("ssoBasic", [])
                else:
                    existing = (
                        data.get("ssoBasic", []) if isinstance(data, dict) else []
                    )
                existing_tokens = [
                    item["token"] if isinstance(item, dict) else str(item)
                    for item in existing
                    if item
                ]
                seen = set()
                deduped = []
                for item in existing_tokens + tokens_to_push:
                    if item not in seen:
                        seen.add(item)
                        deduped.append(item)
                tokens_to_push = deduped
            else:
                if logger:
                    logger(
                        "[G2A] 查询线上 token 失败: HTTP {}，放弃推送以保护存量数据".format(
                            get_resp.status_code
                        )
                    )
                return False
        except Exception as exc:
            if logger:
                logger(f"[G2A] 查询线上 token 异常: {exc}，放弃推送以保护存量数据")
            return False

    try:
        resp = session.post(
            api_url,
            json={"ssoBasic": tokens_to_push},
            headers=headers,
            timeout=60,
            verify=False,
        )
        if resp.status_code == 200:
            if logger:
                logger(
                    f"[G2A] SSO token 已推送到 API（共 {len(tokens_to_push)} 个）: {api_url}"
                )
            return True
        if logger:
            logger(
                f"[G2A] 推送 API 返回异常: HTTP {resp.status_code} {resp.text[:200]}"
            )
        return False
    except Exception as exc:
        if logger:
            logger(f"[G2A] 推送 API 失败: {exc}")
        return False
