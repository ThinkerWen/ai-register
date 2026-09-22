import sys
from threading import Lock

from loguru import logger as _logger

_config_lock = Lock()
_configured = False


def setup_logger(level="INFO"):
    """配置全局 loguru 日志，启用彩色控制台输出。"""
    global _configured

    with _config_lock:
        if _configured:
            return _logger

        _logger.remove()
        _logger.configure(extra={"component": "main"})
        _logger.add(
            sys.stdout,
            colorize=True,
            backtrace=False,
            diagnose=False,
            level=level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
                "| <level>{level: <8}</level> "
                "| <cyan>{extra[component]}</cyan> "
                "- <level>{message}</level>"
            ),
        )

        _configured = True
        return _logger


def get_logger(name=None, level="INFO"):
    """返回绑定模块名的 logger。"""
    base = setup_logger(level=level)
    if name:
        return base.bind(component=name)
    return base
