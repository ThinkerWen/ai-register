from register.base import ModelProviderError
from register.grok.grok import GrokModelProvider
from register.openai.openai import OpenAIModelProvider


_PROVIDER_REGISTRY = {
    "grok": GrokModelProvider,
    "openai": OpenAIModelProvider,
}


def register_model_provider(name, provider_cls):
    key = str(name or "").strip().lower()
    if not key:
        raise ModelProviderError("provider 名称不能为空")
    _PROVIDER_REGISTRY[key] = provider_cls


def _resolve_provider_name(config):
    return str((config or {}).get("model_provider") or "openai").strip().lower()


def _resolve_provider_settings(config, provider_name):
    provider_map = (config or {}).get("model_providers")
    if isinstance(provider_map, dict):
        cfg = provider_map.get(provider_name)
        if isinstance(cfg, dict):
            return dict(cfg)
    return {}


def create_model_provider(config):
    provider_name = _resolve_provider_name(config)
    provider_cls = _PROVIDER_REGISTRY.get(provider_name)
    if not provider_cls:
        available = ", ".join(sorted(_PROVIDER_REGISTRY.keys()))
        raise ModelProviderError(
            f"不支持的 model_provider: {provider_name} (可选: {available})"
        )

    provider_cfg = _resolve_provider_settings(config, provider_name)
    if provider_name == "openai":
        return provider_cls(
            enable_oauth=provider_cfg.get("enable_oauth", True),
            oauth_required=provider_cfg.get("oauth_required", True),
            oauth_issuer=provider_cfg.get("oauth_issuer"),
            oauth_client_id=provider_cfg.get("oauth_client_id"),
            oauth_redirect_uri=provider_cfg.get("oauth_redirect_uri"),
        )

    if provider_name == "grok":
        return provider_cls(
            source_path=provider_cfg.get("source_path"),
            browser_proxy=provider_cfg.get("browser_proxy"),
            api_endpoint=provider_cfg.get("api_endpoint"),
            api_token=provider_cfg.get("api_token"),
            api_append=provider_cfg.get("api_append", True),
        )

    raise ModelProviderError(f"provider 初始化未实现: {provider_name}")


def get_model_provider_info(config):
    provider_name = _resolve_provider_name(config)
    return {"name": provider_name}


def validate_model_provider_config(config):
    try:
        create_model_provider(config)
        return True, "ok"
    except Exception as e:
        return False, str(e)
