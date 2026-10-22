import os

import requests


def _parse_cpa_config(config):
    cpa = (config or {}).get("cpa")
    if not isinstance(cpa, dict):
        cpa = {}

    enabled = bool(cpa.get("enable", False))
    api_url = str(cpa.get("api_url") or "").strip()
    token = str(cpa.get("token") or "").strip()
    proxy = str(cpa.get("proxy") or "").strip()
    return enabled, api_url, token, proxy


def should_upload(config):
    (
        enabled,
        api_url,
        _,
        _,
    ) = _parse_cpa_config(config)
    return enabled and bool(api_url)


def validate_cpa_config(config):
    (
        enabled,
        api_url,
        _,
        _,
    ) = _parse_cpa_config(config)
    if not enabled:
        return True, "cpa disabled"
    if not api_url:
        return False, "cpa.enable=true 但 cpa.api_url 未配置"
    return True, "ok"


def upload_token_json(filepath, upload_api_url, upload_api_token="", proxy=None, logger=None):
    """上传 Token JSON 文件到 CPA 管理平台"""
    if not upload_api_url:
        return False

    try:
        filename = os.path.basename(filepath)
        session = requests.Session()
        session.trust_env = False
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}

        with open(filepath, "rb") as f:
            files = {"file": (filename, f, "application/json")}
            resp = session.post(
                upload_api_url,
                files=files,
                headers={"Authorization": f"Bearer {upload_api_token}"},
                verify=False,
                timeout=30,
            )

        if resp.status_code == 200:
            if logger:
                logger("[CPA] Token JSON 已上传到 CPA 管理平台")
            return True

        if logger:
            logger(f"[CPA] 上传失败: {resp.status_code} - {resp.text[:200]}")
        return False

    except Exception as e:
        if logger:
            logger(f"[CPA] 上传异常: {e}")
        return False


def upload_token_json_from_config(filepath, config, proxy=None, logger=None):
    enabled, api_url, token, proxy = _parse_cpa_config(config)
    if not enabled:
        return False
    if not api_url:
        if logger:
            logger("[CPA] cpa.enable=true 但 cpa.api_url 未配置，跳过上传")
        return False
    return upload_token_json(
        filepath=filepath,
        upload_api_url=api_url,
        upload_api_token=token,
        proxy=proxy,
        logger=logger,
    )
