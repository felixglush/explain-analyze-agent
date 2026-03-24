import httpx
import pytest


@pytest.fixture
def paginated():
    def _make(first_page: list):
        responses = iter(
            [
                httpx.Response(200, json=first_page),
                httpx.Response(200, json=[]),
            ]
        )
        return lambda req: next(responses)

    return _make
