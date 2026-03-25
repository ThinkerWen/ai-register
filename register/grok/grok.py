import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional

from register.base import ModelProvider, ModelProviderError
from util import config as config_utils
from util import get_logger, setup_logger
from util import mail as mail_utils

setup_logger()
logger = get_logger("grok")


class GrokModelProvider(ModelProvider):
    name = "grok"

    def __init__(
        self,
        source_path="",
        browser_proxy="",
        api_endpoint="",
        api_token="",
        api_append=True,
    ):
        self._source_path = str(source_path or "").strip()
        self._browser_proxy = str(browser_proxy or "").strip()
        self._api_endpoint = str(api_endpoint or "").strip()
        self._api_token = str(api_token or "").strip()
        self._api_append = bool(api_append)

        if not self._source_path:
            raise ModelProviderError("providers.grok.source_path 未配置")
        if not os.path.isdir(self._source_path):
            raise ModelProviderError(
                f"providers.grok.source_path 不存在: {self._source_path}"
            )

    def oauth_enabled(self) -> bool:
        return False

    def oauth_required(self) -> bool:
        return False

    def oauth_issuer(self) -> str:
        return ""

    def oauth_client_id(self) -> str:
        return ""

    def oauth_redirect_uri(self) -> str:
        return ""

    def source_path(self):
        return self._source_path

    def browser_proxy(self):
        return self._browser_proxy

    def api_endpoint(self):
        return self._api_endpoint

    def api_token(self):
        return self._api_token

    def api_append(self):
        return self._api_append

    @staticmethod
    def _coerce_total_accounts(value) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def _resolve_output_path(self, token_dir):
        model_name = self.name
        root_dir = (
            token_dir
            if os.path.isabs(token_dir)
            else os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), token_dir
            )
        )
        output_dir = os.path.join(root_dir, model_name)
        os.makedirs(output_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        return os.path.join(output_dir, f"sso_{ts}.txt")

    def _build_runtime_config(self, config, total_accounts, proxy):
        mail_cfg = ((config or {}).get("mail_providers") or {}).get("duckmail") or {}
        return {
            "run": {"count": int(total_accounts)},
            "duckmail_api_base": str(
                mail_cfg.get("api_base") or "https://api.duckmail.sbs"
            ).strip(),
            "duckmail_bearer": str(mail_cfg.get("bearer") or "").strip(),
            "proxy": str(proxy or "").strip(),
            "browser_proxy": self.browser_proxy() or str(proxy or "").strip(),
            "api": {
                "endpoint": self.api_endpoint(),
                "token": self.api_token(),
                "append": self.api_append(),
            },
        }

    def _prepare_runtime_tree(self):
        runtime_dir = tempfile.mkdtemp(prefix="grok_provider_")
        files_to_copy = ["DrissionPage_example.py", "email_register.py"]
        dirs_to_copy = ["turnstilePatch"]

        for name in files_to_copy:
            src = os.path.join(self.source_path(), name)
            if not os.path.isfile(src):
                raise FileNotFoundError(f"缺少 Grok 流程文件: {src}")
            shutil.copy2(src, os.path.join(runtime_dir, name))

        for name in dirs_to_copy:
            src = os.path.join(self.source_path(), name)
            dst = os.path.join(runtime_dir, name)
            if not os.path.isdir(src):
                raise FileNotFoundError(f"缺少 Grok 流程目录: {src}")
            shutil.copytree(src, dst, dirs_exist_ok=True)

        return runtime_dir

    def run_batch(
        self,
        total_accounts: Optional[int] = None,
        max_workers: Optional[int] = None,
        proxy: Optional[str] = None,
    ):
        _ = max_workers
        config = config_utils.get_register_config(logger=logger)

        if total_accounts is None:
            total_accounts = self._coerce_total_accounts(config.get("total_accounts"))
        else:
            total_accounts = self._coerce_total_accounts(total_accounts)
        if proxy is None:
            proxy = str(config.get("proxy") or "")

        mail_provider = str((config or {}).get("mail_provider") or "").strip().lower()
        if mail_provider != "duckmail":
            raise RuntimeError("Grok 流程当前仅支持 duckmail 邮箱 provider")

        mail_ok, mail_err = mail_utils.validate_mail_provider_config(config)
        if not mail_ok:
            raise RuntimeError(f"邮箱 provider 配置无效: {mail_err}")

        output_path = self._resolve_output_path(
            str(config.get("token_dir") or "token_dir")
        )
        runtime_config = self._build_runtime_config(config, total_accounts, proxy)

        logger.info(
            "开始执行 Grok 注册流程: total={} source={}",
            total_accounts,
            self.source_path(),
        )
        logger.info("Mail provider: duckmail {}", runtime_config["duckmail_api_base"])
        logger.info("SSO 输出文件: {}", output_path)

        runtime_dir = self._prepare_runtime_tree()
        try:
            config_path = os.path.join(runtime_dir, "config.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(runtime_config, f, ensure_ascii=False, indent=4)

            command = [
                sys.executable,
                os.path.join(runtime_dir, "DrissionPage_example.py"),
                "--count",
                str(total_accounts),
                "--output",
                output_path,
            ]
            result = subprocess.run(command, cwd=runtime_dir)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Grok 注册流程执行失败，退出码: {result.returncode}"
                )
        finally:
            shutil.rmtree(runtime_dir, ignore_errors=True)


def run_batch(total_accounts=None, max_workers=None, proxy=None):
    config = config_utils.get_register_config(logger=logger)
    provider_cfg = ((config or {}).get("model_providers") or {}).get("grok") or {}
    provider = GrokModelProvider(
        source_path=provider_cfg.get("source_path"),
        browser_proxy=provider_cfg.get("browser_proxy"),
        api_endpoint=provider_cfg.get("api_endpoint"),
        api_token=provider_cfg.get("api_token"),
        api_append=provider_cfg.get("api_append", True),
    )
    return provider.run_batch(
        total_accounts=total_accounts,
        max_workers=max_workers,
        proxy=proxy,
    )


if __name__ == "__main__":
    run_batch()
