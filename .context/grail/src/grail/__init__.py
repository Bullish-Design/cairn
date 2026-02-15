"""Grail: a Pydantic-native wrapper around Monty for secure code execution."""

from .context import MontyContext
from .decorators import secure
from .errors import (
    GrailExecutionError,
    GrailLimitError,
    GrailOutputValidationError,
    GrailValidationError,
)
from .observability import MetricsCollector, RetryPolicy, StructuredLogger
from .filesystem import (
    FilePermission,
    GrailFilesystem,
    callback_filesystem,
    hooked_filesystem,
    memory_filesystem,
)
from .policies import (
    AI_AGENT_POLICY,
    NAMED_POLICIES,
    PERMISSIVE_POLICY,
    STRICT_POLICY,
    PolicyValidationError,
    ResourcePolicy,
    resolve_effective_limits,
    resolve_policy,
)
from .resource_guard import ResourceGuard, ResourceUsageMetrics
from .snapshots import (
    MontySnapshot,
    deserialize_snapshot_payload,
    serialize_snapshot_payload,
    snapshot_payload_from_base64,
    snapshot_payload_to_base64,
)
from .stubs import StubGenerator
from .tools import ToolRegistry
from .types import ResourceLimits, merge_resource_limits

__all__ = [
    "GrailExecutionError",
    "GrailLimitError",
    "GrailOutputValidationError",
    "MetricsCollector",
    "RetryPolicy",
    "StructuredLogger",
    "GrailValidationError",
    "FilePermission",
    "GrailFilesystem",
    "memory_filesystem",
    "callback_filesystem",
    "hooked_filesystem",
    "MontyContext",
    "ResourceGuard",
    "ResourceUsageMetrics",
    "ResourcePolicy",
    "PolicyValidationError",
    "STRICT_POLICY",
    "PERMISSIVE_POLICY",
    "AI_AGENT_POLICY",
    "NAMED_POLICIES",
    "resolve_policy",
    "resolve_effective_limits",
    "ResourceLimits",
    "snapshot_payload_to_base64",
    "snapshot_payload_from_base64",
    "serialize_snapshot_payload",
    "deserialize_snapshot_payload",
    "MontySnapshot",
    "StubGenerator",
    "ToolRegistry",
    "merge_resource_limits",
    "secure",
]

__version__ = "0.0.0"
