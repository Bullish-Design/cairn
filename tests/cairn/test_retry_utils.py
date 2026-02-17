from __future__ import annotations

import asyncio
import logging

import pytest

import cairn.utils.retry as retry_module
from cairn.utils.retry_utils import with_retry


@pytest.mark.asyncio
async def test_with_retry_retries_with_expected_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(retry_module.asyncio, "sleep", fake_sleep)

    @with_retry(max_attempts=3, initial_delay=0.01, max_delay=0.02, backoff_factor=2.0)
    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("temporary")
        return "ok"

    result = await flaky()

    assert result == "ok"
    assert attempts == 3
    assert sleep_calls == [0.01, 0.02]


@pytest.mark.asyncio
async def test_with_retry_stops_on_non_retryable_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(retry_module.asyncio, "sleep", fake_sleep)

    @with_retry(
        max_attempts=5,
        initial_delay=0.01,
        max_delay=0.01,
        retry_exceptions=(ValueError,),
    )
    async def fails_fast() -> None:
        nonlocal attempts
        attempts += 1
        raise TypeError("do not retry")

    with pytest.raises(TypeError, match="do not retry"):
        await fails_fast()

    assert attempts == 1
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_with_retry_exhaustion_raises_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(retry_module.asyncio, "sleep", fake_sleep)

    @with_retry(max_attempts=3, initial_delay=0.001, max_delay=0.001, retry_exceptions=(ConnectionError,))
    async def always_fails() -> None:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("still down")

    with pytest.raises(ConnectionError, match="still down"):
        await always_fails()

    assert attempts == 3
    assert sleep_calls == [0.001, 0.001]


@pytest.mark.asyncio
async def test_with_retry_logs_retry_attempts(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    logger = logging.getLogger("tests.retry-utils")
    attempts = 0

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    @with_retry(
        max_attempts=3,
        initial_delay=0.001,
        max_delay=0.001,
        logger=logger,
        retry_exceptions=(RuntimeError,),
    )
    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary")
        return "done"

    with caplog.at_level(logging.WARNING, logger=logger.name):
        result = await flaky()

    assert result == "done"
    assert attempts == 3
    assert len(caplog.records) == 2
    assert all(record.levelname == "WARNING" for record in caplog.records)
    assert all("Retryable operation 'flaky' failed" in record.message for record in caplog.records)
