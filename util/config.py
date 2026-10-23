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
        "gonkarouter": {
            "proxy": "",
        },
        "openai": {
            "proxy": "",
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
            "proxy": "",
            "api_base": "https://api.duckmail.sbs",
            "bearer": "",
        }
    },
    "token_dir": "token_dir",
    "cpa": {
        "enable": False,
        "api_url": "",
        "token": "",
        "proxy": "",
    },
    "g2a": {
        "enable": False,
        "api_url": "",
        "admin_username": "",
        "admin_password": "",
        "proxy": "",
    },
    "magpie": {
        "enable": False,
        "api_url": "",
        "magpie_auth_token": "",
        "id": "",
        "name": "",
        "proxy": "",
    },
}


REGISTER_ENV_KEY_MAPPING = {
    "concurrency": "CONCURRENCY",
    "total_accounts": "TOTAL_ACCOUNTS",
    "model_provider": "MODEL_PROVIDER",
    "mail_provider": "MAIL_PROVIDER",
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
    "cpa_proxy": "CPA_PROXY",
    "g2a_enable": "G2A_ENABLE",
    "g2a_api_url": "G2A_API_URL",
    "g2a_admin_username": "G2A_ADMIN_USERNAME",
    "g2a_admin_password": "G2A_ADMIN_PASSWORD",
    "g2a_proxy": "G2A_PROXY",
    "magpie_api_url": "MAGPIE_API_URL",
    "magpie_enable": "MAGPIE_ENABLE",
    "magpie_auth_token": "MAGPIE_AUTH_TOKEN",
    "magpie_id": "MAGPIE_ID",
    "magpie_name": "MAGPIE_NAME",
    "magpie_proxy": "MAGPIE_PROXY",
}

PROVIDER_PROXY_ENV_MAPPING = {
    "OPENAI_PROXY": ("model_providers", "openai", "proxy"),
    "GROK_BROWSER_PROXY": ("model_providers", "grok", "browser_proxy"),
    "DUCKMAIL_PROXY": ("mail_providers", "duckmail", "proxy"),
    "TEMPMAIL_PROXY": ("mail_providers", "tempmail", "proxy"),
    "TEMPMAIL_HUB_PROXY": ("mail_providers", "tempmail_hub", "proxy"),
    "GONKAROUTER_PROXY": ("model_providers", "gonkarouter", "proxy"),
}


_REGISTER_CONFIG_CACHE = None
_REGISTER_CONFIG_LOCK = threading.Lock()


def load_yaml_config(config_path, defaults):
    """读取 YAML 配置，并与默认值合并。"""
    config = dict(defaults)

    if not os.path.exists(config_path):
        return config

    with open(config_path, encoding="utf-8") as f:
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

    config["mail_provider"] = str(config.get("mail_provider", "duckmail")).strip().lower()
    if not isinstance(config.get("mail_providers"), dict):
        config["mail_providers"] = {}

    config["model_provider"] = str(config.get("model_provider", "openai")).strip().lower()
    if not isinstance(config.get("model_providers"), dict):
        config["model_providers"] = {}

    for env_key, (section, name, field) in PROVIDER_PROXY_ENV_MAPPING.items():
        providers = config[section] = dict(config[section])
        settings = providers.get(name)
        settings = dict(settings) if isinstance(settings, dict) else {}
        settings[field] = str(os.environ.get(env_key, settings.get(field)) or "").strip()
        providers[name] = settings

    model_name = config["model_provider"]
    model_cfg = config["model_providers"].get(model_name)
    if not isinstance(model_cfg, dict):
        model_cfg = {}

    # 规范化 Model 配置: 结构为 model_providers.<provider>。
    if model_name == "grok":
        model_cfg["browser_proxy"] = str(model_cfg.get("browser_proxy") or "").strip()
        model_cfg["api_endpoint"] = str(model_cfg.get("api_endpoint") or "").strip()
        model_cfg["api_token"] = str(model_cfg.get("api_token") or "").strip()
        model_cfg["api_append"] = parse_bool(model_cfg.get("api_append", True), True)
    elif model_name == "gonkarouter":
        model_cfg["proxy"] = str(model_cfg.get("proxy") or "").strip()
    else:
        model_enable_oauth_raw = (
            config.get("model_enable_oauth") if config.get("model_enable_oauth") is not None else model_cfg.get("enable_oauth", True)
        )
        model_oauth_required_raw = (
            config.get("model_oauth_required") if config.get("model_oauth_required") is not None else model_cfg.get("oauth_required", True)
        )
        model_oauth_issuer = config.get("model_oauth_issuer") or model_cfg.get("oauth_issuer") or "https://auth.openai.com"
        model_oauth_client_id = config.get("model_oauth_client_id") or model_cfg.get("oauth_client_id") or "app_EMoamEEZ73f0CkXaXp7hrann"
        model_oauth_redirect_uri = (
            config.get("model_oauth_redirect_uri") or model_cfg.get("oauth_redirect_uri") or "http://localhost:1455/auth/callback"
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

    cpa_enable_raw = config.get("cpa_enable") if config.get("cpa_enable") is not None else cpa_cfg.get("enable", False)
    cpa_api_url = config.get("cpa_api_url") or cpa_cfg.get("api_url") or ""
    cpa_token = config.get("cpa_token") or cpa_cfg.get("token") or ""
    cpa_proxy = config.get("cpa_proxy") if config.get("cpa_proxy") is not None else cpa_cfg.get("proxy", "")
    config["cpa"] = {
        "enable": parse_bool(cpa_enable_raw, False),
        "api_url": str(cpa_api_url).strip(),
        "token": str(cpa_token).strip(),
        "proxy": str(cpa_proxy or "").strip(),
    }

    g2a_cfg = config.get("g2a")
    if not isinstance(g2a_cfg, dict):
        g2a_cfg = {}

    g2a_enable_raw = config.get("g2a_enable") if config.get("g2a_enable") is not None else g2a_cfg.get("enable", False)
    g2a_api_url = config.get("g2a_api_url") or g2a_cfg.get("api_url") or ""
    g2a_admin_username = config.get("g2a_admin_username") or g2a_cfg.get("admin_username") or ""
    g2a_admin_password = config.get("g2a_admin_password") or g2a_cfg.get("admin_password") or ""
    g2a_proxy = config.get("g2a_proxy") if config.get("g2a_proxy") is not None else g2a_cfg.get("proxy", "")
    config["g2a"] = {
        "enable": parse_bool(g2a_enable_raw, False),
        "api_url": str(g2a_api_url).strip(),
        "admin_username": str(g2a_admin_username).strip(),
        "admin_password": str(g2a_admin_password),
        "proxy": str(g2a_proxy or "").strip(),
    }

    magpie_cfg = config.get("magpie")
    if not isinstance(magpie_cfg, dict):
        magpie_cfg = {}
    magpie_api_url = config.get("magpie_api_url") or magpie_cfg.get("api_url") or ""
    magpie_auth_token = config.get("magpie_auth_token") or magpie_cfg.get("magpie_auth_token") or ""
    magpie_enable_raw = config.get("magpie_enable") if config.get("magpie_enable") is not None else magpie_cfg.get("enable")
    config["magpie"] = {
        "enable": parse_bool(magpie_enable_raw, bool(magpie_api_url or magpie_auth_token)),
        "api_url": str(magpie_api_url).strip().rstrip("/"),
        "magpie_auth_token": str(magpie_auth_token).strip(),
        "id": str(config.get("magpie_id") or magpie_cfg.get("id") or "").strip(),
        "name": str(config.get("magpie_name") or magpie_cfg.get("name") or "").strip(),
        "proxy": str(config.get("magpie_proxy") or magpie_cfg.get("proxy") or "").strip(),
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
