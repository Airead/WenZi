"""Shared fixtures for wenzi.enhance tests."""

import pytest


@pytest.fixture
def rate_limit_error():
    """Create a mock 429 RateLimitError for testing."""
    from wenzi.llm_http import RateLimitError

    return RateLimitError(
        "rate limited",
        status_code=429,
        body='{"error": {"message": "rate limited"}}',
    )
