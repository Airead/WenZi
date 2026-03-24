"""Shared fixtures for wenzi.enhance tests."""

import pytest


@pytest.fixture(autouse=True)
def _fast_common_words(monkeypatch):
    """Skip loading 8MB word list files — not needed for enhance test logic."""
    monkeypatch.setattr(
        "wenzi.enhance.vocabulary_builder._load_common_words",
        lambda: set(),
    )


@pytest.fixture
def rate_limit_error():
    """Create a mock 429 RateLimitError for testing."""
    from httpx import Request, Response
    from openai import RateLimitError

    response = Response(
        status_code=429,
        request=Request("POST", "https://example.com/v1/chat/completions"),
        json={"error": {"message": "rate limited", "code": "429"}},
    )
    return RateLimitError(
        message="rate limited",
        response=response,
        body={"error": {"message": "rate limited"}},
    )
