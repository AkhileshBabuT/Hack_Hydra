import pytest

from hydramem import client


@pytest.fixture(scope="session")
def driver():
    d = client.connect()
    yield d
    d.close()


@pytest.fixture
def instance_id(request):
    """A unique tenant partition per test, so tests never see each other."""
    return f"test-{request.node.name}"
