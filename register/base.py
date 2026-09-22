import random
from abc import ABC, abstractmethod
from typing import Optional


class ModelProviderError(Exception):
    """模型 provider 统一异常类型。"""


class ModelProvider(ABC):
    """模型 provider 抽象接口。"""

    name = "base"

    @abstractmethod
    def oauth_enabled(self) -> bool:
        """是否启用 OAuth 流程。"""

    @abstractmethod
    def oauth_required(self) -> bool:
        """OAuth 失败时是否中断流程。"""

    @abstractmethod
    def oauth_issuer(self) -> str:
        """OAuth issuer 地址。"""

    @abstractmethod
    def oauth_client_id(self) -> str:
        """OAuth client_id。"""

    @abstractmethod
    def oauth_redirect_uri(self) -> str:
        """OAuth redirect_uri。"""

    @abstractmethod
    def run_batch(
        self,
        total_accounts: Optional[int] = None,
        max_workers: Optional[int] = None,
        proxy: Optional[str] = None,
    ):
        """执行 provider 对应的注册流程。"""


def random_name() -> str:
    first = random.choice(
        [
            "James",
            "Emma",
            "Liam",
            "Olivia",
            "Noah",
            "Ava",
            "Ethan",
            "Sophia",
            "Lucas",
            "Mia",
            "Mason",
            "Isabella",
            "Logan",
            "Charlotte",
            "Alexander",
            "Amelia",
            "Benjamin",
            "Harper",
            "William",
            "Evelyn",
            "Henry",
            "Abigail",
            "Sebastian",
            "Emily",
            "Jack",
            "Elizabeth",
        ]
    )
    last = random.choice(
        [
            "Smith",
            "Johnson",
            "Brown",
            "Davis",
            "Wilson",
            "Moore",
            "Taylor",
            "Clark",
            "Hall",
            "Young",
            "Anderson",
            "Thomas",
            "Jackson",
            "White",
            "Harris",
            "Martin",
            "Thompson",
            "Garcia",
            "Robinson",
            "Lewis",
            "Walker",
            "Allen",
            "King",
            "Wright",
            "Scott",
            "Green",
        ]
    )
    return f"{first} {last}"
