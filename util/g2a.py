import hashlib
import ipaddress
import json
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone

from curl_cffi import requests as curl_requests

_ADMIN_LOCK = threading.Lock()
_admin_token = ""
_admin_expires_at = None
_admin_session_key = ""


class RemoteTokenRequestError(RuntimeError):
    """远端请求失败（网络、HTTP 状态码等）。"""


class RemoteTokenCompatibilityError(RuntimeError):
    """远端响应格式不兼容（接口版本不匹配等）。"""


def _parse_g2a_config(config):
    g2a_cfg = (config or {}).get("g2a")
    if not isinstance(g2a_cfg, dict):
        g2a_cfg = {}

    enabled = bool(g2a_cfg.get("enable", False))
    api_url = str(g2a_cfg.get("api_url") or "").strip()
    admin_username = str(g2a_cfg.get("admin_username") or "").strip()
    admin_password = str(g2a_cfg.get("admin_password") or "")
    use_proxy = bool(g2a_cfg.get("use_proxy", False))
    return {
        "enabled": enabled,
        "api_url": api_url,
        "admin_username": admin_username,
        "admin_password": admin_password,
        "use_proxy": use_proxy,
    }


def should_upload(config):
    cfg = _parse_g2a_config(config)
    if not cfg["enabled"] or not cfg["api_url"]:
        return False
    return bool(cfg["admin_username"]) and bool(cfg["admin_password"])


def validate_g2a_config(config):
    cfg = _parse_g2a_config(config)
    if not cfg["enabled"]:
        return True, "g2a disabled"
    if not cfg["api_url"]:
        return False, "g2a.enable=true 但 g2a.api_url 未配置"
    if not cfg["admin_username"] or not cfg["admin_password"]:
        return False, "g2a.enable=true 但未配置 admin_username/admin_password"
    return True, "ok"


def _normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def _build_proxies(proxy, use_proxy_pref):
    if proxy and use_proxy_pref:
        return {"http": str(proxy), "https": str(proxy)}
    return {}


def _log(logger, message):
    """无论调用方是否提供 logger，都保证日志被打印出来。"""
    if logger:
        try:
            logger(message)
            return
        except Exception:
            pass
    print(message)


def _go_api_base(base):
    """将用户配置的远端地址归一化为 go 版管理 API 根路径。"""
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return ""
    for suffix in ("/api/admin/v1", "/admin/api", "/admin"):
        if normalized.lower().endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            break
    parsed = urllib.parse.urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and not _is_local_or_private_host(host):
        raise RemoteTokenRequestError("新版 grok2api 远端管理接口必须使用 HTTPS")
    return normalized + "/api/admin/v1"


def _is_local_or_private_host(host):
    """判断主机是否为本地或私有网段（允许走 http，无需 HTTPS）。"""
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _clear_admin_cache():
    global _admin_token, _admin_expires_at, _admin_session_key
    with _ADMIN_LOCK:
        _admin_token = ""
        _admin_expires_at = None
        _admin_session_key = ""


def _get_admin_token(api_base, username, password, proxies, force=False):
    global _admin_token, _admin_expires_at, _admin_session_key
    session_key = "%s\n%s\n%s" % (
        api_base,
        username,
        hashlib.sha256(password.encode("utf-8")).hexdigest(),
    )
    with _ADMIN_LOCK:
        now = datetime.now(timezone.utc)
        if (
            not force
            and _admin_session_key == session_key
            and _admin_token
            and isinstance(_admin_expires_at, datetime)
            and _admin_expires_at > now + timedelta(seconds=30)
        ):
            return _admin_token
        endpoint = api_base + "/auth/login"
        try:
            response = curl_requests.post(
                endpoint,
                headers={"Content-Type": "application/json"},
                json={"username": username, "password": password},
                timeout=60,
                proxies=proxies,
                verify=True,
            )
        except Exception as exc:
            raise RemoteTokenRequestError(
                f"新版 grok2api 管理员登录请求失败: {endpoint}: {exc}"
            ) from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status < 300:
            raise RemoteTokenRequestError(
                f"新版 grok2api 管理员登录失败: {endpoint}: HTTP {status}"
            )
        try:
            tokens = response.json().get("data", {}).get("tokens", {})
            token = str(tokens.get("accessToken") or "").strip()
            expiry = str(tokens.get("accessTokenExpiresAt") or "").strip()
        except Exception as exc:
            raise RemoteTokenCompatibilityError(
                "新版 grok2api 登录响应格式不兼容"
            ) from exc
        if not token:
            raise RemoteTokenCompatibilityError("新版 grok2api 登录响应缺少 accessToken")
        try:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            expires_at = now + timedelta(minutes=5)
        _admin_token = token
        _admin_expires_at = expires_at.astimezone(timezone.utc)
        _admin_session_key = session_key
        return token


def _parse_go_import(response, secrets=()):
    current_event = ""
    completed = None
    for raw_line in str(getattr(response, "text", "") or "").splitlines():
        line = raw_line.strip()
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except Exception:
            data = {"message": line[5:].strip()}
        if current_event == "error":
            message = (
                str(data.get("message") or data.get("error") or "")
                if isinstance(data, dict)
                else ""
            )
            for secret in secrets:
                if secret:
                    message = message.replace(str(secret), "[REDACTED]")
            raise RemoteTokenRequestError(
                "新版 grok2api 导入失败" + (f": {message[:200]}" if message else "")
            )
        if current_event == "complete":
            completed = data if isinstance(data, dict) else {}
    if completed is None:
        raise RemoteTokenCompatibilityError("新版 grok2api 导入响应缺少 complete 事件")
    return completed


def _upload_go_remote(tokens, cfg, proxies, logger=None):
    api_base = _go_api_base(cfg["api_url"])
    endpoint = api_base + "/accounts/web/import"
    payload = "\n".join(tokens) + "\n"
    try:
        from curl_cffi import CurlMime
    except Exception as exc:
        raise RemoteTokenRequestError(f"无法创建新版 grok2api 导入表单: {exc}") from exc
    for attempt in range(2):
        access_token = _get_admin_token(
            api_base,
            cfg["admin_username"],
            cfg["admin_password"],
            proxies,
            force=attempt > 0,
        )
        multipart = CurlMime()
        multipart.addpart(
            name="file",
            filename="grok-web-sso.txt",
            content_type="text/plain; charset=utf-8",
            data=payload.encode("utf-8"),
        )
        try:
            response = curl_requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "text/event-stream",
                },
                multipart=multipart,
                timeout=60,
                proxies=proxies,
                verify=True,
            )
        except Exception as exc:
            raise RemoteTokenRequestError(
                f"新版 grok2api SSO 导入请求失败: {endpoint}: {exc}"
            ) from exc
        finally:
            multipart.close()
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401 and attempt == 0:
            _clear_admin_cache()
            continue
        if not 200 <= status < 300:
            raise RemoteTokenRequestError(
                f"新版 grok2api SSO 导入失败: {endpoint}: HTTP {status}"
            )
        result = _parse_go_import(response, (access_token, *tokens))
        summary = ", ".join(
            f"{key}={result.get(key)}"
            for key in ("created", "updated", "skipped", "synced", "syncFailed")
            if result.get(key) is not None
        )
        if int(result.get("syncFailed") or 0):
            _log(
                logger,
                "[G2A] 上传成功: SSO 已导入，但初始同步失败"
                + (f": {summary}" if summary else ""),
            )
            return True
        _log(
            logger,
            f"[G2A] 上传成功：已导入新版 grok2api Grok Web（共 {len(tokens)} 个）"
            + (f": {summary}" if summary else ""),
        )
        return True
    raise RemoteTokenRequestError("新版 grok2api 管理员认证已失效")


def upload_sso_tokens(tokens, config, proxy=None, logger=None):
    cfg = _parse_g2a_config(config)
    if not cfg["enabled"]:
        return False

    tokens_to_push = []
    seen = set()
    for item in tokens or []:
        normalized = _normalize_sso_token(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            tokens_to_push.append(normalized)
    if not tokens_to_push:
        return False

    proxies = _build_proxies(proxy, cfg["use_proxy"])
    if proxy and not cfg["use_proxy"]:
        _log(logger, "[G2A] use_proxy=false，上传请求已绕过全局代理")

    _log(logger, f"[G2A] 开始上传 {len(tokens_to_push)} 个 token 到 grok2api...")
    try:
        result = _upload_go_remote(tokens_to_push, cfg, proxies, logger=logger)
        if not result:
            _log(logger, "[G2A] 上传结束：未写入任何 token")
        return result
    except (RemoteTokenRequestError, RemoteTokenCompatibilityError) as exc:
        _log(logger, f"[G2A] 上传失败: {exc}")
        return False
    except Exception as exc:
        _log(logger, f"[G2A] 上传异常: {exc}")
        return False