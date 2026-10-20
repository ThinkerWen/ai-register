from util.providers.base import MailProvider, MailProviderError
from util.providers.duckmail import DuckMailProvider
from util.providers.tempmail import TempMailLolProvider
from util.providers.tempmail_hub import TempMailHubProvider

__all__ = ["MailProvider", "MailProviderError", "DuckMailProvider", "TempMailLolProvider", "TempMailHubProvider"]
