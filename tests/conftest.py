import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def fetched_at() -> datetime:
    return datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
