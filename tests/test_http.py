import httpx
import pytest

from pm_scanner.http import MAX_RETRIES, get_json


def _client_returning(responses: list[httpx.Response]) -> httpx.Client:
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        # Serve the scripted responses in order; repeat the last one forever.
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://example.test"
    )


def test_429_honors_retry_after_header():
    client = _client_returning(
        [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sleeps: list[float] = []
    assert get_json(client, "/x", sleep=sleeps.append) == {"ok": True}
    assert sleeps == [7.0]


def test_malformed_retry_after_falls_back_to_exponential_backoff():
    client = _client_returning(
        [
            httpx.Response(429, headers={"Retry-After": "Fri, 31 Dec 2027 23:59:59 GMT"}),
            httpx.Response(429),
            httpx.Response(200, json=[]),
        ]
    )
    sleeps: list[float] = []
    assert get_json(client, "/x", sleep=sleeps.append) == []
    assert sleeps == [1.0, 2.0]  # 1 * 2**attempt


def test_gives_up_after_max_retries():
    client = _client_returning([httpx.Response(429)])
    sleeps: list[float] = []
    with pytest.raises(httpx.HTTPStatusError):
        get_json(client, "/x", sleep=sleeps.append)
    assert len(sleeps) == MAX_RETRIES


def test_non_transient_error_raises_immediately():
    client = _client_returning([httpx.Response(404)])
    sleeps: list[float] = []
    with pytest.raises(httpx.HTTPStatusError):
        get_json(client, "/x", sleep=sleeps.append)
    assert sleeps == []
