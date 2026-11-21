import pytest

from smartcard.wallet import Wallet


@pytest.fixture
def wallet() -> Wallet:
    return Wallet.open(":memory:")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
