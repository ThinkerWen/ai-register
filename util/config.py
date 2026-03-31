import os
import threading

import yaml


REGISTER_CONFIG_DEFAULTS = {
    "concurrency": 3,
    "total_accounts": 3,
    "model_provider": "openai",
    "model_providers": {
        "grok": {
            "browser_proxy": "",
        },
        "openai": {
            "enable_oauth": True,
            "oauth_required": True,
            "oauth_issuer": "https://auth.openai.com",
            "oauth_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
            "oauth_redirect_uri": "http://localhost:1455/auth/callback",
        },
    },
    "mail_provider": "duckmail",
    "mail_providers": {
        "duckmail": {
            "api_base": "https://api.duckmail.sbs",
            "bearer": "",
        }
    },
    "proxy": "",
    "token_dir": "token_dir",
    "cpa": {
        "enable": False,
        "api_url": "",
        "token": "",
        "use_proxy": False,
    },
    "g2a": {
        "enable": False,
        "api_url": "",
        "token": "",
        "append": True,
        "use_proxy": False,
    },
}


REGISTER_ENV_KEY_MAPPING = {
    "concurrency": "CONCURRENCY",
    "total_accounts": "TOTAL_ACCOUNTS",
    "model_provider": "MODEL_PROVIDER",
    "mail_provider": "MAIL_PROVIDER",
    "proxy": "PROXY",
    # 模型配置环境变量（映射到 model_providers.<model_provider>）
    "model_enable_oauth": "MODEL_ENABLE_OAUTH",
    "model_oauth_required": "MODEL_OAUTH_REQUIRED",
    "model_oauth_issuer": "MODEL_OAUTH_ISSUER",
    "model_oauth_client_id": "MODEL_OAUTH_CLIENT_ID",
    "model_oauth_redirect_uri": "MODEL_OAUTH_REDIRECT_URI",
    "token_dir": "TOKEN_DIR",
    "cpa_enable": "CPA_ENABLE",
    "cpa_api_url": "CPA_API_URL",
    "cpa_token": "CPA_TOKEN",
    "cpa_use_proxy": "CPA_USE_PROXY",
    "g2a_enable": "G2A_ENABLE",
    "g2a_api_url": "G2A_API_URL",
    "g2a_token": "G2A_TOKEN",
    "g2a_use_proxy": "G2A_USE_PROXY",
}


_REGISTER_CONFIG_CACHE = None
_REGISTER_CONFIG_LOCK = threading.Lock()


def load_yaml_config(config_path, defaults):
    """读取 YAML 配置，并与默认值合并。"""
    config = dict(defaults)

    if not os.path.exists(config_path):
        return config

    with open(config_path, "r", encoding="utf-8") as f:
        file_config = yaml.safe_load(f) or {}

    if not isinstance(file_config, dict):
        raise ValueError("配置文件格式错误，根节点必须是对象")

    config.update(file_config)
    return config


def apply_env_overrides(config, env_key_mapping):
    """按映射用环境变量覆盖配置。"""
    out = dict(config)
    for config_key, env_key in env_key_mapping.items():
        if env_key in os.environ:
            out[config_key] = os.environ[env_key]
    return out


def parse_int(value, default):
    """安全解析 int，失败时回退默认值。"""
    try:
        return int(value)
    except Exception:
        return int(default)


def parse_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_register_config(config_path, logger=None):
    config = load_yaml_config(config_path, defaults=REGISTER_CONFIG_DEFAULTS)
    config = apply_env_overrides(config, REGISTER_ENV_KEY_MAPPING)

    config["mail_provider"] = (
        str(config.get("mail_provider", "duckmail")).strip().lower()
    )
    if not isinstance(config.get("mail_providers"), dict):
        config["mail_providers"] = {}

    config["model_provider"] = (
        str(config.get("model_provider", "openai")).strip().lower()
    )
    if not isinstance(config.get("model_providers"), dict):
        config["model_providers"] = {}

    model_name = config["model_provider"]
    model_cfg = config["model_providers"].get(model_name)
    if not isinstance(model_cfg, dict):
        model_cfg = {}

    # 规范化 Model 配置: 结构为 model_providers.<provider>。
    if model_name == "grok":
        model_cfg["browser_proxy"] = str(
            model_cfg.get("browser_proxy") or config.get("proxy") or ""
        ).strip()
        model_cfg["api_endpoint"] = str(model_cfg.get("api_endpoint") or "").strip()
        model_cfg["api_token"] = str(model_cfg.get("api_token") or "").strip()
        model_cfg["api_append"] = parse_bool(model_cfg.get("api_append", True), True)
    else:
        model_enable_oauth_raw = (
            config.get("model_enable_oauth")
            if config.get("model_enable_oauth") is not None
            else model_cfg.get("enable_oauth", True)
        )
        model_oauth_required_raw = (
            config.get("model_oauth_required")
            if config.get("model_oauth_required") is not None
            else model_cfg.get("oauth_required", True)
        )
        model_oauth_issuer = (
            config.get("model_oauth_issuer")
            or model_cfg.get("oauth_issuer")
            or "https://auth.openai.com"
        )
        model_oauth_client_id = (
            config.get("model_oauth_client_id")
            or model_cfg.get("oauth_client_id")
            or "app_EMoamEEZ73f0CkXaXp7hrann"
        )
        model_oauth_redirect_uri = (
            config.get("model_oauth_redirect_uri")
            or model_cfg.get("oauth_redirect_uri")
            or "http://localhost:1455/auth/callback"
        )

        model_cfg["enable_oauth"] = parse_bool(model_enable_oauth_raw, True)
        model_cfg["oauth_required"] = parse_bool(model_oauth_required_raw, True)
        model_cfg["oauth_issuer"] = str(model_oauth_issuer).strip()
        model_cfg["oauth_client_id"] = str(model_oauth_client_id).strip()
        model_cfg["oauth_redirect_uri"] = str(model_oauth_redirect_uri).strip()
    config["model_providers"][model_name] = model_cfg

    # 规范化 CPA 配置: 结构为 cpa.enable / cpa.api_url / cpa.token。
    cpa_cfg = config.get("cpa")
    if not isinstance(cpa_cfg, dict):
        cpa_cfg = {}

    cpa_enable_raw = (
        config.get("cpa_enable")
        if config.get("cpa_enable") is not None
        else cpa_cfg.get("enable", False)
    )
    cpa_api_url = config.get("cpa_api_url") or cpa_cfg.get("api_url") or ""
    cpa_token = config.get("cpa_token") or cpa_cfg.get("token") or ""
    cpa_use_proxy_raw = (
        config.get("cpa_use_proxy")
        if config.get("cpa_use_proxy") is not None
        else cpa_cfg.get("use_proxy", False)
    )
    config["cpa"] = {
        "enable": parse_bool(cpa_enable_raw, False),
        "api_url": str(cpa_api_url).strip(),
        "token": str(cpa_token).strip(),
        "use_proxy": parse_bool(cpa_use_proxy_raw, False),
    }

    g2a_cfg = config.get("g2a")
    if not isinstance(g2a_cfg, dict):
        g2a_cfg = {}

    g2a_enable_raw = (
        config.get("g2a_enable")
        if config.get("g2a_enable") is not None
        else g2a_cfg.get("enable", False)
    )
    g2a_api_url = config.get("g2a_api_url") or g2a_cfg.get("api_url") or ""
    g2a_token = config.get("g2a_token") or g2a_cfg.get("token") or ""
    g2a_append_raw = g2a_cfg.get("append", True)
    g2a_use_proxy_raw = (
        config.get("g2a_use_proxy")
        if config.get("g2a_use_proxy") is not None
        else g2a_cfg.get("use_proxy", False)
    )
    config["g2a"] = {
        "enable": parse_bool(g2a_enable_raw, False),
        "api_url": str(g2a_api_url).strip(),
        "token": str(g2a_token).strip(),
        "append": parse_bool(g2a_append_raw, True),
        "use_proxy": parse_bool(g2a_use_proxy_raw, False),
    }

    # 规范化 Token 存储目录。
    token_dir = config.get("token_dir") or "token_dir"
    config["token_dir"] = str(token_dir).strip() or "token_dir"

    config["concurrency"] = parse_int(config.get("concurrency", 3), 3)
    config["total_accounts"] = parse_int(config.get("total_accounts", 3), 3)

    if logger:
        logger.debug("配置加载完成: {}", ", ".join(sorted(config.keys())))

    return config


def get_register_config(config_path=None, logger=None, force_reload=False):
    """返回注册配置单例，避免在各模块重复加载配置文件。"""
    global _REGISTER_CONFIG_CACHE

    if config_path is None:
        config_path = os.path.join(os.getcwd(), "config.yaml")

    if _REGISTER_CONFIG_CACHE is not None and not force_reload:
        return _REGISTER_CONFIG_CACHE

    with _REGISTER_CONFIG_LOCK:
        if _REGISTER_CONFIG_CACHE is not None and not force_reload:
            return _REGISTER_CONFIG_CACHE

        try:
            _REGISTER_CONFIG_CACHE = load_register_config(config_path, logger=logger)
        except Exception as e:
            if logger:
                logger.warning(f"加载 {config_path} 失败，使用默认配置: {e}")
            _REGISTER_CONFIG_CACHE = dict(REGISTER_CONFIG_DEFAULTS)

        return _REGISTER_CONFIG_CACHE


def clear_register_config_cache():
    """清空配置缓存，方便测试或需要重新加载配置时使用。"""
    global _REGISTER_CONFIG_CACHE
    with _REGISTER_CONFIG_LOCK:
        _REGISTER_CONFIG_CACHE = None
