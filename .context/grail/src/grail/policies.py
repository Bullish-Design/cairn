"""Reusable resource policy presets and deterministic policy composition."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .resource_guard import ResourceGuard
from .types import ResourceLimits

_LIMIT_FIELDS = (
    "max_allocations",
    "max_duration_secs",
    "max_memory",
    "gc_interval",
    "max_recursion_depth",
)


class PolicyValidationError(ValueError):
    """Raised when policy definitions or composition are invalid."""


class ResourcePolicy(BaseModel):
    """A named resource policy with optional inheritance and overrides."""

    model_config = ConfigDict(extra="forbid")

    name: str
    guard: ResourceGuard = Field(default_factory=ResourceGuard)
    inherits: tuple[str, ...] = ()


STRICT_POLICY = ResourcePolicy(
    name="strict",
    guard=ResourceGuard(
        max_duration_secs=0.5,
        max_memory=8 * 1024 * 1024,
        max_recursion_depth=120,
    ),
)
PERMISSIVE_POLICY = ResourcePolicy(
    name="permissive",
    guard=ResourceGuard(
        max_duration_secs=3.0,
        max_memory=64 * 1024 * 1024,
        max_recursion_depth=400,
    ),
)
AI_AGENT_POLICY = ResourcePolicy(
    name="ai_agent",
    inherits=("strict",),
    guard=ResourceGuard(
        max_duration_secs=1.5,
        max_memory=32 * 1024 * 1024,
        max_recursion_depth=250,
    ),
)

NAMED_POLICIES: dict[str, ResourcePolicy] = {
    STRICT_POLICY.name: STRICT_POLICY,
    PERMISSIVE_POLICY.name: PERMISSIVE_POLICY,
    AI_AGENT_POLICY.name: AI_AGENT_POLICY,
}

PolicySpec = str | ResourcePolicy


class _PolicyResolver:
    def __init__(self, policies: dict[str, ResourcePolicy]) -> None:
        self._policies = policies

    def resolve(self, spec: PolicySpec) -> ResourceGuard:
        ordered = self._expand(spec, trail=())
        return compose_guards([policy.guard for policy in ordered])

    def _expand(self, spec: PolicySpec, trail: tuple[str, ...]) -> list[ResourcePolicy]:
        policy = self._to_policy(spec)

        if policy.name in trail:
            cycle = " -> ".join((*trail, policy.name))
            raise PolicyValidationError(f"Policy inheritance cycle detected: {cycle}")

        expanded: list[ResourcePolicy] = []
        for parent_name in policy.inherits:
            if parent_name not in self._policies:
                raise PolicyValidationError(
                    f"Policy '{policy.name}' inherits unknown policy '{parent_name}'"
                )
            expanded.extend(self._expand(parent_name, (*trail, policy.name)))

        expanded.append(policy)

        deduped: OrderedDict[str, ResourcePolicy] = OrderedDict()
        for item in expanded:
            deduped[item.name] = item
        return list(deduped.values())

    def _to_policy(self, spec: PolicySpec) -> ResourcePolicy:
        if isinstance(spec, ResourcePolicy):
            known = self._policies.get(spec.name)
            if known is not None and known != spec:
                raise PolicyValidationError(
                    "Conflicting policy definitions for "
                    f"'{spec.name}': inline definition does not match registered preset"
                )
            return spec
        if isinstance(spec, str):
            if spec not in self._policies:
                raise PolicyValidationError(f"Unknown policy '{spec}'")
            return self._policies[spec]
        raise PolicyValidationError(f"Unsupported policy spec: {spec!r}")


def compose_guards(guards: list[ResourceGuard]) -> ResourceGuard:
    """Compose guards by choosing the strictest bound per configured field."""
    values: dict[str, Any] = {}
    for field in _LIMIT_FIELDS:
        candidates = [
            getattr(guard, field) for guard in guards if getattr(guard, field) is not None
        ]
        if candidates:
            values[field] = min(candidates)
    return ResourceGuard(**values)


def resolve_policy(
    policy: PolicySpec | list[PolicySpec] | tuple[PolicySpec, ...] | None,
    *,
    available_policies: dict[str, ResourcePolicy] | None = None,
) -> ResourceGuard | None:
    """Resolve named/custom policies into a concrete guard.

    Multiple policies are combined with deterministic strictest-wins semantics.
    """
    if policy is None:
        return None

    resolver = _PolicyResolver(available_policies or NAMED_POLICIES)
    specs = [policy] if not isinstance(policy, (list, tuple)) else list(policy)

    names = [item.name for item in specs if isinstance(item, ResourcePolicy)]
    conflicting_names = [
        name
        for name in names
        if name in (available_policies or NAMED_POLICIES)
        and (available_policies or NAMED_POLICIES)[name]
        != next(item for item in specs if isinstance(item, ResourcePolicy) and item.name == name)
    ]
    if conflicting_names:
        raise PolicyValidationError(
            "Conflicting inline/registered policy definitions for: "
            + ", ".join(sorted(set(conflicting_names)))
        )

    resolved = [resolver.resolve(spec) for spec in specs]
    return compose_guards(resolved)


def resolve_effective_limits(
    *,
    limits: ResourceLimits | None,
    guard: ResourceGuard | dict[str, Any] | None,
    policy: PolicySpec | list[PolicySpec] | tuple[PolicySpec, ...] | None,
) -> ResourceLimits:
    """Resolve concrete runtime limits.

    Precedence (highest last): defaults < policy < guard < explicit ``limits``.
    """
    from .types import merge_resource_limits

    policy_guard = resolve_policy(policy)
    base_guard = ResourceGuard.model_validate(guard) if guard is not None else None

    merged: ResourceLimits = {}
    if policy_guard is not None:
        merged.update(policy_guard.to_monty_limits())
    if base_guard is not None:
        merged.update(base_guard.to_monty_limits())
    if limits is not None:
        merged.update(limits)
    return merge_resource_limits(merged)
