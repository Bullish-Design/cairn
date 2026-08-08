"""Atuin KV-backed code provider for Cairn."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict

from cairn.core.exceptions import CodeProviderError
from cairn.providers.providers import CodeProvider
from cairn_pytuin.reference import parse_kv_reference


class PytuinSettings(BaseSettings):
    """Provider configuration.

    Read from the environment, because cairn's plugin loader passes only
    ``project_root``/``base_path`` to a provider constructor — there is no
    channel for plugin-specific CLI flags (guide §3.2).
    """

    model_config = SettingsConfigDict(env_prefix="CAIRN_PYTUIN_", extra="ignore")

    namespace: str = "cairn"
    executable: str = "atuin"


class KvClient(Protocol):
    """The slice of ``pytuin.Pytuin`` this provider uses.

    Narrow on purpose: tests substitute a fake, and the provider depends on
    two methods rather than the whole client.
    """

    def kv_get(self, namespace: str, key: str) -> Any: ...
    def daemon_status(self) -> Any: ...


class PytuinCodeProvider(CodeProvider):
    """Load task code from the Atuin KV store.

    ``cairn queue tasks/deploy`` fetches the value stored at
    ``atuin kv get --namespace tasks deploy``.

    Atuin KV is synced state, so task code may originate on any synced host.
    That is accepted by design: bwrap is the security boundary and task code
    is untrusted regardless of provenance.
    """

    def __init__(self, client: KvClient | None = None) -> None:
        self._settings = PytuinSettings()
        self._client = client or self._build_client()

    def _build_client(self) -> KvClient:
        try:
            from pytuin import Pytuin
        except ImportError as exc:  # 3.13 hits this: pytuin needs 3.14 (guide §2.1)
            raise CodeProviderError(
                "pytuin is not importable (it requires Python >= 3.14); install cairn-pytuin on a 3.14+ interpreter"
            ) from exc
        return Pytuin(cli_executable=self._settings.executable)

    async def get_code(self, reference: str, context: dict[str, Any]) -> str:
        _ = context
        ref = parse_kv_reference(reference, default_namespace=self._settings.namespace)

        # pytuin is fully synchronous and shells out to the atuin binary;
        # calling it inline would block the orchestrator's event loop.
        try:
            record = await asyncio.to_thread(self._client.kv_get, ref.namespace, ref.key)
        except CodeProviderError:
            raise
        except Exception as exc:  # every pytuin error becomes a provider error
            raise CodeProviderError(f"Atuin KV lookup failed for {ref.namespace}/{ref.key}: {exc}") from exc

        if record is None:
            raise await self._explain_missing(ref)

        value = getattr(record, "value", None)
        if not isinstance(value, str) or not value.strip():
            raise CodeProviderError(f"Atuin KV entry {ref.namespace}/{ref.key} is empty")
        return value

    async def _explain_missing(self, ref: Any) -> CodeProviderError:
        """Distinguish an absent key from an unreachable backend (guide §4.2).

        ``CliKvBackend.get`` returns ``None`` for a missing key *and* for
        timeouts and nonzero exits, so a dead daemon would otherwise be
        reported as "task not found".
        """
        try:
            status = await asyncio.to_thread(self._client.daemon_status)
            reachable = bool(getattr(status, "reachable", False))
        except Exception:  # noqa: BLE001 - probe is best-effort
            reachable = False

        if not reachable:
            return CodeProviderError(
                "Atuin daemon is unreachable; cannot fetch task "
                f"{ref.namespace}/{ref.key}. Run `pytuin doctor` to diagnose."
            )
        return CodeProviderError(f"No task stored at {ref.namespace}/{ref.key}")

    async def validate_code(self, code: str) -> tuple[bool, str | None]:
        """Reject empty or syntactically invalid Python.

        A KV value is free-form text; catching a typo here beats an opaque
        traceback from inside the sandbox.  Nothing about *what* the code does
        is judged — that is the sandbox's job.
        """
        if not code.strip():
            return False, "Task code is empty"
        try:
            compile(code, "<pytuin-task>", "exec")
        except SyntaxError as exc:
            return False, f"Task code is not valid Python: {exc.msg} (line {exc.lineno})"
        return True, None
