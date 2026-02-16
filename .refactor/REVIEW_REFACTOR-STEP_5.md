# Refactoring Step 5: Security Hardening

## Overview
This step implements critical security improvements including ReDoS protection, resource limit enforcement, and secrets detection. These changes address security vulnerabilities that could lead to denial of service, resource exhaustion, or accidental exposure of sensitive information.

**Priority:** 🔴 CRITICAL (Security)
**Estimated Effort:** 6-8 hours
**Dependencies:**
- Step 1 (uses SecurityError, TimeoutError exceptions)
- Step 2 (uses improved type hints)

---

## Issues Addressed

### Issue #6: ReDoS Vulnerability
**Location:** `external_functions.py:68`
**Severity:** MEDIUM

**Problem:**
```python
regex = re.compile(request.pattern)  # User-provided regex
```
No timeout or complexity limits on regex execution - vulnerable to Regular Expression Denial of Service.

### Issue #2: Resource Limits Not Enforced
**Location:** `orchestrator.py:391`
**Severity:** HIGH

**Problem:**
```python
await script.run(inputs={"task_description": ctx.task}, externals=tools)
```
Settings exist for `max_execution_time` and `max_memory_bytes` but are never applied.

### Security Recommendation: Secrets Detection
**Severity:** HIGH

**Problem:**
- No detection of secrets in agent submissions
- Could accidentally commit API keys, passwords, tokens
- Need to scan for common secret patterns before accepting changes

---

## Detailed Implementation Steps

### 1. Implement ReDoS Protection

**File:** `cairn/regex_utils.py` (NEW FILE)

```python
"""Safe regex utilities with ReDoS protection.

This module provides regex compilation and matching with timeout protection
to prevent Regular Expression Denial of Service (ReDoS) attacks.
"""

import re
import signal
import asyncio
from typing import Pattern
from contextlib import contextmanager

from cairn.exceptions import SecurityError, TimeoutError as CairnTimeoutError
from cairn.constants import REGEX_TIMEOUT_SECONDS


class RegexTimeoutError(CairnTimeoutError):
    """Regex execution exceeded timeout - possible ReDoS attack."""
    pass


def compile_safe_regex(
    pattern: str,
    *,
    flags: int = 0,
    timeout: float = REGEX_TIMEOUT_SECONDS,
) -> Pattern[str]:
    """Compile regex pattern with validation.

    Args:
        pattern: Regex pattern to compile
        flags: Regex flags (re.IGNORECASE, etc.)
        timeout: Timeout for pattern compilation

    Returns:
        Compiled regex pattern

    Raises:
        SecurityError: If pattern is potentially dangerous
        ValueError: If pattern is invalid

    Example:
        pattern = compile_safe_regex(r"\\d+", timeout=1.0)
        matches = pattern.findall(text)
    """
    # Basic validation - check for obviously dangerous patterns
    if len(pattern) > 1000:
        raise SecurityError(
            "Regex pattern too long - possible DoS attempt",
            error_code="REGEX_TOO_LONG",
            context={"pattern_length": len(pattern)}
        )

    # Check for nested quantifiers (common ReDoS pattern)
    dangerous_patterns = [
        r"\(\.\*\)\+",  # (.*)+
        r"\(\.\+\)\*",  # (.+)*
        r"\(\.\*\)\*",  # (.*)*
        r"\(\.\+\)\+",  # (.+)+
    ]

    for danger in dangerous_patterns:
        if re.search(danger, pattern):
            raise SecurityError(
                "Regex pattern contains dangerous nested quantifiers",
                error_code="REGEX_DANGEROUS_PATTERN",
                context={"pattern": pattern[:100]}
            )

    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc


async def search_with_timeout(
    pattern: Pattern[str],
    text: str,
    *,
    timeout: float = REGEX_TIMEOUT_SECONDS,
) -> re.Match[str] | None:
    """Search text with regex pattern and timeout.

    Args:
        pattern: Compiled regex pattern
        text: Text to search
        timeout: Maximum time in seconds

    Returns:
        Match object or None

    Raises:
        RegexTimeoutError: If search exceeds timeout
    """
    loop = asyncio.get_event_loop()

    try:
        # Run regex search in executor with timeout
        result = await asyncio.wait_for(
            loop.run_in_executor(None, pattern.search, text),
            timeout=timeout
        )
        return result
    except asyncio.TimeoutError:
        raise RegexTimeoutError(
            f"Regex search exceeded timeout of {timeout}s - possible ReDoS",
            error_code="REGEX_TIMEOUT",
            context={
                "timeout": timeout,
                "text_length": len(text),
                "pattern": pattern.pattern[:100]
            }
        )


async def findall_with_timeout(
    pattern: Pattern[str],
    text: str,
    *,
    timeout: float = REGEX_TIMEOUT_SECONDS,
    max_matches: int = 10000,
) -> list[str]:
    """Find all matches with timeout and limit.

    Args:
        pattern: Compiled regex pattern
        text: Text to search
        timeout: Maximum time in seconds
        max_matches: Maximum number of matches to return

    Returns:
        List of matches (limited to max_matches)

    Raises:
        RegexTimeoutError: If search exceeds timeout
    """
    loop = asyncio.get_event_loop()

    try:
        # Run regex findall in executor with timeout
        result = await asyncio.wait_for(
            loop.run_in_executor(None, pattern.findall, text),
            timeout=timeout
        )

        # Limit number of matches
        if len(result) > max_matches:
            import logging
            logging.warning(
                f"Regex found {len(result)} matches, truncating to {max_matches}",
                extra={"total_matches": len(result), "max_matches": max_matches}
            )
            result = result[:max_matches]

        return result

    except asyncio.TimeoutError:
        raise RegexTimeoutError(
            f"Regex findall exceeded timeout of {timeout}s - possible ReDoS",
            error_code="REGEX_TIMEOUT",
            context={
                "timeout": timeout,
                "text_length": len(text),
                "pattern": pattern.pattern[:100]
            }
        )
```

### 2. Update External Functions to Use Safe Regex

**File:** `cairn/external_functions.py`

```python
from cairn.regex_utils import compile_safe_regex, findall_with_timeout
from cairn.exceptions import SecurityError, RegexTimeoutError


async def search_content_impl(
    request: SearchContentRequest,
    agent_ws: Workspace,
    stable_ws: Workspace,
) -> SearchResponse:
    """Search file contents using regex pattern with ReDoS protection.

    Args:
        request: Search request with pattern
        agent_ws: Agent workspace
        stable_ws: Stable workspace

    Returns:
        Search results

    Raises:
        SecurityError: If pattern is dangerous
        RegexTimeoutError: If search times out
    """
    # Compile pattern with safety checks
    try:
        pattern = compile_safe_regex(request.pattern)
    except SecurityError as exc:
        logger.warning(
            "Blocked dangerous regex pattern",
            extra={"pattern": request.pattern[:100]}
        )
        raise

    results: list[SearchResult] = []
    files = await agent_ws.list_files(request.file_pattern or "*")

    for file_path in files:
        try:
            content = await agent_ws.read_file(file_path)

            # Search each line with timeout protection
            for line_num, line in enumerate(content.splitlines(), 1):
                try:
                    # Use async search with timeout
                    match = await search_with_timeout(pattern, line, timeout=0.5)
                    if match:
                        results.append(
                            SearchResult(
                                file=file_path,
                                line_number=line_num,
                                line_content=line.strip()[:200],  # Limit length
                                match=match.group(0)[:100],  # Limit match length
                            )
                        )
                except RegexTimeoutError:
                    logger.warning(
                        "Regex search timed out on line",
                        extra={
                            "file": file_path,
                            "line_number": line_num,
                            "pattern": request.pattern[:100]
                        }
                    )
                    # Continue with next line instead of failing entirely
                    continue

        except Exception as exc:
            logger.warning(
                f"Error searching file {file_path}: {exc}",
                extra={"file": file_path}
            )
            continue

    return SearchResponse(results=results, total_matches=len(results))
```

### 3. Implement Resource Limit Enforcement

**File:** `cairn/resource_limits.py` (NEW FILE)

```python
"""Resource limit enforcement for agent execution.

This module provides utilities for enforcing CPU time, memory, and wall-clock
time limits on agent code execution.
"""

import asyncio
import psutil
import resource
from contextlib import asynccontextmanager
from typing import AsyncIterator

from cairn.exceptions import ResourceLimitError, TimeoutError as CairnTimeoutError
from cairn.constants import DEFAULT_EXECUTION_TIMEOUT_SECONDS, DEFAULT_MAX_MEMORY_BYTES


class ResourceLimiter:
    """Enforce resource limits on code execution."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES,
        max_cpu_percent: float = 80.0,
    ):
        """Initialize resource limiter.

        Args:
            timeout_seconds: Maximum wall-clock time
            max_memory_bytes: Maximum memory usage
            max_cpu_percent: Maximum CPU usage percentage
        """
        self.timeout_seconds = timeout_seconds
        self.max_memory_bytes = max_memory_bytes
        self.max_cpu_percent = max_cpu_percent

    @asynccontextmanager
    async def limit(self) -> AsyncIterator[None]:
        """Context manager to enforce resource limits.

        Example:
            async with ResourceLimiter(timeout_seconds=30).limit():
                await run_untrusted_code()

        Raises:
            TimeoutError: If execution exceeds time limit
            ResourceLimitError: If execution exceeds memory or CPU limits
        """
        process = psutil.Process()
        start_memory = process.memory_info().rss

        # Create task with timeout
        async def monitor_resources():
            """Monitor resource usage during execution."""
            while True:
                await asyncio.sleep(0.5)

                # Check memory
                current_memory = process.memory_info().rss - start_memory
                if current_memory > self.max_memory_bytes:
                    raise ResourceLimitError(
                        f"Memory limit exceeded: {current_memory / (1024**2):.1f}MB > "
                        f"{self.max_memory_bytes / (1024**2):.1f}MB",
                        error_code="MEMORY_LIMIT_EXCEEDED",
                        context={
                            "current_bytes": current_memory,
                            "limit_bytes": self.max_memory_bytes,
                        }
                    )

                # Check CPU
                cpu_percent = process.cpu_percent(interval=0.1)
                if cpu_percent > self.max_cpu_percent:
                    import logging
                    logging.warning(
                        f"High CPU usage: {cpu_percent:.1f}%",
                        extra={"cpu_percent": cpu_percent}
                    )

        monitor_task = asyncio.create_task(monitor_resources())

        try:
            # Set resource limits using resource module (Unix only)
            try:
                # Limit CPU time
                resource.setrlimit(
                    resource.RLIMIT_CPU,
                    (int(self.timeout_seconds), int(self.timeout_seconds) + 5)
                )

                # Limit memory
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (self.max_memory_bytes, self.max_memory_bytes)
                )
            except (ValueError, OSError) as exc:
                # Resource limits may not be available on all platforms
                import logging
                logging.warning(f"Could not set resource limits: {exc}")

            yield

        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass


async def run_with_timeout(
    coro,
    *,
    timeout_seconds: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
):
    """Run coroutine with timeout.

    Args:
        coro: Coroutine to run
        timeout_seconds: Maximum execution time

    Returns:
        Result of coroutine

    Raises:
        TimeoutError: If execution exceeds timeout
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        raise CairnTimeoutError(
            f"Operation exceeded timeout of {timeout_seconds}s",
            error_code="EXECUTION_TIMEOUT",
            context={"timeout_seconds": timeout_seconds}
        )
```

### 4. Update Orchestrator to Enforce Resource Limits

**File:** `cairn/orchestrator.py`

```python
from cairn.resource_limits import ResourceLimiter, run_with_timeout
from cairn.exceptions import ResourceLimitError, TimeoutError as CairnTimeoutError


async def _execute_script(self, ctx: AgentContext) -> None:
    """Execute agent script with resource limits.

    Args:
        ctx: Agent execution context

    Raises:
        ResourceLimitError: If resource limits exceeded
        TimeoutError: If execution times out
        AgentExecutionError: If execution fails
    """
    ctx.state = AgentState.EXECUTING
    await self._save_lifecycle_record(ctx)

    try:
        # Load and validate script
        script = await self._load_grail_script_with_retry(ctx.script_path)

        # Create tools for agent
        tools = self.tools_factory(
            ctx.agent_id,
            ctx.agent_fs,
            ctx.stable_fs
        )

        # Execute with resource limits
        limiter = ResourceLimiter(
            timeout_seconds=self.executor_settings.max_execution_time,
            max_memory_bytes=self.executor_settings.max_memory_bytes,
        )

        async with limiter.limit():
            # Run with timeout
            result = await run_with_timeout(
                script.run(
                    inputs={"task_description": ctx.task},
                    externals=tools
                ),
                timeout_seconds=self.executor_settings.max_execution_time
            )

        ctx.result = result

    except (ResourceLimitError, CairnTimeoutError) as exc:
        ctx.error = str(exc)
        ctx.state = AgentState.ERRORED
        await self._save_lifecycle_record(ctx)
        raise

    except Exception as exc:
        ctx.error = str(exc)
        ctx.state = AgentState.ERRORED
        await self._save_lifecycle_record(ctx)
        raise AgentExecutionError(
            format_agent_error(
                "Script execution failed",
                agent_id=ctx.agent_id,
                state=ctx.state.value,
            ),
            error_code="SCRIPT_EXECUTION_FAILED",
            context={"agent_id": ctx.agent_id}
        ) from exc
```

### 5. Implement Secrets Detection

**File:** `cairn/secrets_detection.py` (NEW FILE)

```python
"""Secrets detection for preventing accidental exposure.

This module provides utilities for detecting common secret patterns in code
and preventing them from being committed.
"""

import re
from pathlib import Path
from typing import NamedTuple

from cairn.exceptions import SecretsDetectedError


class SecretMatch(NamedTuple):
    """A detected secret match."""
    file: str
    line_number: int
    secret_type: str
    match: str  # Redacted
    context: str  # Line context


# Common secret patterns
SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "aws_secret_key": re.compile(r"aws_secret_access_key\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
    "github_token": re.compile(r"gh[ps]_[a-zA-Z0-9]{36,}"),
    "generic_api_key": re.compile(r"api[_-]?key\s*[=:]\s*['\"]([a-zA-Z0-9_\-]{20,})['\"]", re.IGNORECASE),
    "generic_secret": re.compile(r"secret\s*[=:]\s*['\"]([a-zA-Z0-9_\-]{20,})['\"]", re.IGNORECASE),
    "generic_password": re.compile(r"password\s*[=:]\s*['\"]([^'\"]{8,})['\"]", re.IGNORECASE),
    "private_key": re.compile(r"-----BEGIN (RSA |DSA |EC )?PRIVATE KEY-----"),
    "jwt_token": re.compile(r"eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*"),
    "slack_token": re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,}"),
    "stripe_key": re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
}


# Patterns to ignore (test data, examples, etc.)
IGNORE_PATTERNS = [
    re.compile(r"example\.com"),
    re.compile(r"test[_-]?data"),
    re.compile(r"dummy[_-]?secret"),
    re.compile(r"fake[_-]?key"),
    re.compile(r"placeholder"),
    re.compile(r"xxx+", re.IGNORECASE),
]


def scan_file_for_secrets(file_path: Path, content: str) -> list[SecretMatch]:
    """Scan file content for potential secrets.

    Args:
        file_path: Path to file being scanned
        content: File content

    Returns:
        List of detected secret matches
    """
    matches: list[SecretMatch] = []

    for line_num, line in enumerate(content.splitlines(), 1):
        # Skip if line contains ignore patterns
        if any(pattern.search(line) for pattern in IGNORE_PATTERNS):
            continue

        # Check each secret pattern
        for secret_type, pattern in SECRET_PATTERNS.items():
            if match := pattern.search(line):
                # Redact the actual secret
                redacted = match.group(0)[:4] + "***REDACTED***"

                matches.append(
                    SecretMatch(
                        file=str(file_path),
                        line_number=line_num,
                        secret_type=secret_type,
                        match=redacted,
                        context=line.strip()[:100]  # Limited context
                    )
                )

    return matches


async def scan_workspace_for_secrets(workspace, *, exclude_patterns: list[str] | None = None) -> list[SecretMatch]:
    """Scan entire workspace for secrets.

    Args:
        workspace: Workspace to scan
        exclude_patterns: File patterns to exclude (e.g., ["*.md", "test_*"])

    Returns:
        List of all detected secrets

    Raises:
        SecretsDetectedError: If secrets are found (with details)
    """
    all_matches: list[SecretMatch] = []
    exclude_patterns = exclude_patterns or []

    # Get all files
    files = await workspace.list_files("**/*")

    for file_path in files:
        # Skip excluded patterns
        if any(Path(file_path).match(pattern) for pattern in exclude_patterns):
            continue

        # Skip binary files, large files
        if Path(file_path).suffix in [".pyc", ".so", ".dll", ".exe", ".bin"]:
            continue

        try:
            content = await workspace.read_file(file_path)

            # Skip very large files
            if len(content) > 1_000_000:  # 1MB
                continue

            matches = scan_file_for_secrets(Path(file_path), content)
            all_matches.extend(matches)

        except Exception:
            # Skip files that can't be read
            continue

    return all_matches


def validate_no_secrets(matches: list[SecretMatch]) -> None:
    """Validate that no secrets were detected.

    Args:
        matches: List of detected secrets

    Raises:
        SecretsDetectedError: If any secrets found
    """
    if matches:
        error_msg = f"Detected {len(matches)} potential secret(s) in submission:\n"
        for match in matches[:5]:  # Show first 5
            error_msg += f"  - {match.file}:{match.line_number} ({match.secret_type})\n"

        if len(matches) > 5:
            error_msg += f"  ... and {len(matches) - 5} more\n"

        raise SecretsDetectedError(
            error_msg,
            error_code="SECRETS_DETECTED",
            context={
                "secret_count": len(matches),
                "files": list(set(m.file for m in matches)),
                "secret_types": list(set(m.secret_type for m in matches)),
            }
        )
```

### 6. Integrate Secrets Detection into Submission Flow

**File:** `cairn/orchestrator.py`

```python
from cairn.secrets_detection import scan_workspace_for_secrets, validate_no_secrets


async def _submit_results(self, ctx: AgentContext) -> None:
    """Submit agent results with secrets detection.

    Args:
        ctx: Agent execution context

    Raises:
        SecretsDetectedError: If secrets detected in changes
    """
    ctx.state = AgentState.SUBMITTING
    await self._save_lifecycle_record(ctx)

    try:
        # Scan for secrets before submitting
        secrets = await scan_workspace_for_secrets(
            ctx.agent_fs,
            exclude_patterns=["*.md", "test_*", "*_test.py"]
        )

        # Validate no secrets found
        validate_no_secrets(secrets)

        # Continue with normal submission
        # ... existing submission logic

    except SecretsDetectedError as exc:
        ctx.error = str(exc)
        ctx.state = AgentState.ERRORED
        await self._save_lifecycle_record(ctx)

        logger.error(
            "Agent submission blocked - secrets detected",
            extra={
                "agent_id": ctx.agent_id,
                "secret_count": len(secrets),
                "files": [s.file for s in secrets],
            }
        )
        raise
```

---

## Testing Requirements

### Unit Tests

**File:** `tests/test_regex_utils.py`

```python
"""Tests for safe regex utilities."""

import pytest
from cairn.regex_utils import compile_safe_regex, search_with_timeout
from cairn.exceptions import SecurityError, RegexTimeoutError


def test_compile_safe_regex_success():
    """Test compiling safe regex pattern."""
    pattern = compile_safe_regex(r"\d+")
    assert pattern is not None


def test_compile_safe_regex_too_long():
    """Test rejecting too-long patterns."""
    long_pattern = "a" * 2000
    with pytest.raises(SecurityError, match="too long"):
        compile_safe_regex(long_pattern)


def test_compile_safe_regex_dangerous_pattern():
    """Test rejecting dangerous nested quantifiers."""
    with pytest.raises(SecurityError, match="dangerous"):
        compile_safe_regex(r"(.*)+")


@pytest.mark.asyncio
async def test_search_with_timeout_success():
    """Test regex search succeeds within timeout."""
    pattern = compile_safe_regex(r"\d+")
    result = await search_with_timeout(pattern, "test 123 data")
    assert result is not None
    assert result.group(0) == "123"


@pytest.mark.asyncio
async def test_search_with_timeout_exceeds():
    """Test regex search timeout on pathological pattern."""
    # This pattern can be slow on certain inputs
    pattern = compile_safe_regex(r"(a+)+b")
    text = "a" * 100 + "c"  # No 'b', causes backtracking

    with pytest.raises(RegexTimeoutError):
        await search_with_timeout(pattern, text, timeout=0.1)
```

**File:** `tests/test_secrets_detection.py`

```python
"""Tests for secrets detection."""

import pytest
from cairn.secrets_detection import scan_file_for_secrets, validate_no_secrets
from cairn.exceptions import SecretsDetectedError
from pathlib import Path


def test_detect_aws_access_key():
    """Test detecting AWS access key."""
    content = 'AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"'
    matches = scan_file_for_secrets(Path("test.py"), content)
    assert len(matches) > 0
    assert any(m.secret_type == "aws_access_key" for m in matches)


def test_detect_generic_api_key():
    """Test detecting generic API key."""
    content = 'api_key = "sk_1234567890abcdefghij"'
    matches = scan_file_for_secrets(Path("config.py"), content)
    assert len(matches) > 0


def test_ignore_test_data():
    """Test ignoring test data patterns."""
    content = 'api_key = "test_data_fake_key_xxxxx"'
    matches = scan_file_for_secrets(Path("test.py"), content)
    assert len(matches) == 0  # Should be ignored


def test_validate_no_secrets_raises():
    """Test validate raises when secrets found."""
    matches = [
        SecretMatch(
            file="test.py",
            line_number=10,
            secret_type="api_key",
            match="sk_***REDACTED***",
            context="api_key = 'secret'"
        )
    ]

    with pytest.raises(SecretsDetectedError):
        validate_no_secrets(matches)
```

---

## Files to Create

1. `cairn/regex_utils.py` - Safe regex utilities
2. `cairn/resource_limits.py` - Resource limit enforcement
3. `cairn/secrets_detection.py` - Secrets detection
4. `tests/test_regex_utils.py` - Regex utility tests
5. `tests/test_resource_limits.py` - Resource limit tests
6. `tests/test_secrets_detection.py` - Secrets detection tests

---

## Files to Modify

1. `cairn/external_functions.py` - Use safe regex
2. `cairn/orchestrator.py` - Enforce resource limits and scan for secrets
3. `cairn/constants.py` - Add REGEX_TIMEOUT_SECONDS constant

---

## Validation Criteria

### Success Criteria
- ✅ ReDoS protection prevents dangerous patterns
- ✅ Resource limits enforced on all agent execution
- ✅ Secrets detection blocks suspicious patterns
- ✅ All security tests pass
- ✅ No performance degradation from safety checks

---

## Notes for Implementer

### Time Estimates
- regex_utils.py: 2 hours
- resource_limits.py: 2 hours
- secrets_detection.py: 2 hours
- Update existing files: 1.5 hours
- Tests: 2.5 hours
- **Total: 10 hours**

---

## References

- CODE_REVIEW.md - Issue #6 (ReDoS Vulnerability)
- CODE_REVIEW.md - Issue #2 (Resource Limits Not Enforced)
- CODE_REVIEW.md - Section 3.3 (Secrets Management)
