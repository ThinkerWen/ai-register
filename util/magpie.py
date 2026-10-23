from urllib.parse import quote, urlsplit

import requests


class MagpieError(Exception):
    """Magpie 上传异常。"""


def _settings(config):
    magpie = (config or {}).get("magpie")
    if not isinstance(magpie, dict):
        magpie = {}
    return {
        "enable": magpie.get("enable"),
        "api_url": str(magpie.get("api_url") or "").strip().rstrip("/"),
        "auth_token": str(magpie.get("magpie_auth_token") or "").strip(),
        "id": str(magpie.get("id") or "").strip(),
        "name": str(magpie.get("name") or "").strip(),
        "proxy": str(magpie.get("proxy") or "").strip(),
    }


def validate_config(config):
    settings = _settings(config)
    configured = bool(settings["api_url"] or settings["auth_token"])
    enabled = settings["enable"]
    if enabled is None:
        enabled = configured
    elif not isinstance(enabled, bool):
        enabled = str(enabled).strip().lower() in {"1", "true", "yes", "y", "on"}
    if not enabled:
        return True, "disabled"
    if not configured:
        return False, "magpie 已启用但 api_url/magpie_auth_token 未配置"
    missing = [key for key in ("api_url", "auth_token", "id", "name") if not settings[key]]
    if missing:
        return False, f"magpie 配置不完整: {', '.join(missing)}"
    return True, "ok"


def is_enabled(config):
    ok, state = validate_config(config)
    return ok and state == "ok"


def upload_api_key(config, key, logger=None):
    ok, state = validate_config(config)
    if not ok:
        raise MagpieError(state)
    if state == "disabled":
        return False
    settings = _settings(config)
    parsed = urlsplit(settings["api_url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MagpieError("magpie.api_url 必须是 HTTP(S) 地址")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    token = settings["auth_token"] if "=" in settings["auth_token"] else f"magpie_web_3430={settings['auth_token']}"
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Cookie": token,
        "Origin": origin,
        "Referer": f"{origin}/?view=providers&edit={quote(settings['id'], safe='')}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/155.0.0.0 Safari/537.36",
    }
    payload = {"id": settings["id"], "name": settings["name"], "key": key, "protocol": ""}
    try:
        with requests.Session() as session:
            session.trust_env = False
            if settings["proxy"]:
                session.proxies = {"http": settings["proxy"], "https": settings["proxy"]}
            response = session.post(f"{settings['api_url']}/api/keys/add", headers=headers, json=payload, timeout=30)
    except requests.RequestException as exc:
        raise MagpieError(f"Magpie 请求失败: {exc}") from exc
    body = response.text.replace(key, "***")[:300]
    if response.status_code in {400, 409} and any(
        marker in body.lower() for marker in ("already has this key", "already exists", "duplicate")
    ):
        if logger:
            logger("Magpie 已存在该 Key，跳过重复上传")
        return True
    if not 200 <= response.status_code < 300:
        raise MagpieError(f"Magpie HTTP {response.status_code}: {body}")
    if logger:
        logger("Magpie 上传成功")
    return True
