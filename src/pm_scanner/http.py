"""Shared HTTP fetch with backoff for anonymous, rate-limited APIs."""

import time
from typing import Any

import httpx

# 429 is the documented rate-limit response on both platforms; the 5xx set
# covers transient upstream failures that a scheduled scraper should ride out
# rather than fail the whole run.
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5
BASE_DELAY_SECONDS = 1.0


def get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    sleep=time.sleep,
) -> Any:
    """GET ``url`` and decode JSON, retrying transient failures.

    Honors ``Retry-After`` on 429s when present. ``sleep`` is injectable so
    tests can assert on chosen delays without actually waiting.
    """
    for attempt in range(MAX_RETRIES + 1):
        response = client.get(url, params=params)
        if response.status_code not in TRANSIENT_STATUSES:
            response.raise_for_status()
            return response.json()
        if attempt == MAX_RETRIES:
            response.raise_for_status()
        sleep(_retry_delay(response.headers.get("Retry-After"), attempt))
    raise AssertionError("unreachable")


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            # Retry-After can also be an HTTP-date; not worth parsing for a
            # scraper — exponential backoff below is a fine substitute.
            pass
    return BASE_DELAY_SECONDS * (2**attempt)
