from __future__ import annotations

import logging

import pytest

from cairn.retry_utils import with_retry


@pytest.mark.asyncio
async def test_with_retry_retries_and_succeeds() -> None:
    attempts = 0

    @with_retry(max_attempts=3, initial_delay=0.0, max_delay=0.0)
    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary")
        return "ok"

    result = await flaky()

    assert result == "ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_with_retry_filters_non_retryable_exceptions() -> None:
    attempts = 0

    @with_retry(
        max_attempts=5,
        initial_delay=0.0,
        max_delay=0.0,
        retry_exceptions=(ValueError,),
    )
    async def fails_fast() -> None:
        nonlocal attempts
        attempts += 1
        raise TypeError("do not retry")

    with pytest.raises(TypeError):
        await fails_fast()

    assert attempts == 1


@pytest.mark.asyncio
async def test_with_retry_logs_retry_errors(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("tests.retry-utils")
    attempts = 0

    @with_retry(
        max_attempts=3,
        initial_delay=0.0,
        max_delay=0.0,
        logger=logger,
    )
    async def flaky() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        await flaky()

    assert len(caplog.records) == 2
    assert all(record.levelname == "WARNING" for record in caplog.records)
    assert all("Retryable operation 'flaky' failed" in record.message for record in caplog.records)
