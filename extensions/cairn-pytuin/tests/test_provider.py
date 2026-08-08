from __future__ import annotations

import pytest

from cairn.core.exceptions import CodeProviderError, ProviderError
from cairn_pytuin.provider import PytuinCodeProvider
from cairn_pytuin.reference import parse_kv_reference


class FakeRecord:
    def __init__(self, value: str) -> None:
        self.value = value


class FakeStatus:
    def __init__(self, reachable: bool) -> None:
        self.reachable = reachable


class FakeClient:
    def __init__(self, values=None, *, reachable=True, raises=None):
        self.values = values or {}
        self._reachable = reachable
        self._raises = raises
        self.calls: list[tuple[str, str]] = []

    def kv_get(self, namespace, key):
        self.calls.append((namespace, key))
        if self._raises is not None:
            raise self._raises
        value = self.values.get((namespace, key))
        return FakeRecord(value) if value is not None else None

    def daemon_status(self):
        return FakeStatus(self._reachable)


# -- reference parsing ------------------------------------------------------


@pytest.mark.parametrize(
    ("reference", "namespace", "key"),
    [("tasks/deploy", "tasks", "deploy"), ("deploy", "cairn", "deploy"), ("  tasks/deploy  ", "tasks", "deploy")],
)
def test_reference_parsing(reference, namespace, key):
    ref = parse_kv_reference(reference, default_namespace="cairn")
    assert (ref.namespace, ref.key) == (namespace, key)


@pytest.mark.parametrize("reference", ["", "   ", "a/b/c", "/deploy", "tasks/", "a//b"])
def test_malformed_references_are_rejected(reference):
    with pytest.raises(CodeProviderError):
        parse_kv_reference(reference, default_namespace="cairn")


@pytest.mark.parametrize(
    "reference",
    [
        "--help",
        "-h",
        "tasks/-h",
        "--namespace/cairn",
        "tasks/--namespace",
    ],
)
def test_flag_shaped_references_are_rejected(reference):
    """A reference starting with '-' would be parsed as a flag by the atuin
    CLI (e.g. '--help' returns atuin's help text as task code). Reject it at
    parse time; the blast radius must not depend on atuin's CLI surface."""
    with pytest.raises(CodeProviderError, match="must not start with '-'"):
        parse_kv_reference(reference, default_namespace="cairn")


# -- get_code ---------------------------------------------------------------


async def test_get_code_returns_stored_value():
    client = FakeClient({("tasks", "deploy"): "write_file('a', 'b')\n"})
    provider = PytuinCodeProvider(client=client)
    assert await provider.get_code("tasks/deploy", {}) == "write_file('a', 'b')\n"
    assert client.calls == [("tasks", "deploy")]


async def test_default_namespace_applied():
    client = FakeClient({("cairn", "deploy"): "pass"})
    provider = PytuinCodeProvider(client=client)
    await provider.get_code("deploy", {})
    assert client.calls == [("cairn", "deploy")]


async def test_missing_key_with_healthy_daemon_says_not_found():
    provider = PytuinCodeProvider(client=FakeClient({}, reachable=True))
    with pytest.raises(CodeProviderError, match="No task stored at"):
        await provider.get_code("tasks/nope", {})


async def test_missing_key_with_dead_daemon_says_unreachable():
    """The distinction that justifies this provider's shape (guide §4.2).

    pytuin returns None for a missing key AND for timeouts/nonzero exits, so
    without the probe an outage reads as a typo.
    """
    provider = PytuinCodeProvider(client=FakeClient({}, reachable=False))
    with pytest.raises(CodeProviderError, match="unreachable"):
        await provider.get_code("tasks/deploy", {})


async def test_empty_value_is_an_error():
    provider = PytuinCodeProvider(client=FakeClient({("tasks", "deploy"): "   "}))
    with pytest.raises(CodeProviderError, match="empty"):
        await provider.get_code("tasks/deploy", {})


async def test_backend_exception_becomes_a_provider_error():
    """Provider errors must be ProviderError so orchestrator.py:1049 catches
    them and the agent transitions to ERRORED cleanly."""
    provider = PytuinCodeProvider(client=FakeClient(raises=RuntimeError("boom")))
    with pytest.raises(ProviderError):
        await provider.get_code("tasks/deploy", {})


# -- entry point ------------------------------------------------------------


def test_entry_point_resolves():
    """The `cairn.providers` entry point is what makes this a plugin rather
    than a library; every other test would pass even if the entry-point name
    or module path were wrong. Requires the package to be installed (editable
    is fine), which the dev venv is."""
    from cairn.providers.providers import resolve_code_provider

    provider = resolve_code_provider("pytuin", project_root=None, base_path=None)
    assert type(provider).__name__ == "PytuinCodeProvider"


# -- validate_code ----------------------------------------------------------


async def test_validate_accepts_valid_python():
    provider = PytuinCodeProvider(client=FakeClient())
    assert await provider.validate_code("x = 1\n") == (True, None)


@pytest.mark.parametrize("code", ["", "   ", "def broken(:\n"])
async def test_validate_rejects_bad_code(code):
    provider = PytuinCodeProvider(client=FakeClient())
    ok, error = await provider.validate_code(code)
    assert ok is False and error
