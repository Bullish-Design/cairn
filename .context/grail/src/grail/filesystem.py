"""Filesystem adapters for integrating pydantic_monty OSAccess with Grail contexts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, TypeVar

from pydantic_monty import AbstractOS, CallbackFile, MemoryFile, OSAccess


class FilePermission(str, Enum):
    READ = "read"
    WRITE = "write"
    DENY = "deny"


PERMISSION_TO_MODE: dict[FilePermission, int] = {
    FilePermission.READ: 0o444,
    FilePermission.WRITE: 0o222,
    FilePermission.DENY: 0o000,
}

ReadHook = Callable[[PurePosixPath], str | bytes]
WriteHook = Callable[[PurePosixPath, str | bytes], None]


T = TypeVar("T")


def _normalized_paths(mapping: Mapping[str | PurePosixPath, T] | None) -> dict[PurePosixPath, T]:
    return {PurePosixPath(path): value for path, value in (mapping or {}).items()}


def _permission_mode(
    path: str | PurePosixPath,
    permission_map: Mapping[str | PurePosixPath, FilePermission],
) -> int:
    permission = permission_map.get(path, FilePermission.READ)
    return PERMISSION_TO_MODE.get(permission, 0o444)


def _build_os_access_from_memory_files(
    files: Mapping[str | PurePosixPath, str | bytes],
    *,
    root_dir: str | PurePosixPath,
    permission_map: Mapping[str | PurePosixPath, FilePermission] | None = None,
) -> OSAccess:
    memory_files = []
    for path, content in files.items():
        memory_file_kwargs: dict[str, int] = {}
        if permission_map is not None:
            memory_file_kwargs["permissions"] = _permission_mode(path, permission_map)
        memory_files.append(MemoryFile(path, content, **memory_file_kwargs))
    return OSAccess(memory_files, root_dir=root_dir)


def _build_grail_filesystem(
    os_access: AbstractOS,
    *,
    root_dir: str | PurePosixPath,
    permissions: Mapping[str | PurePosixPath, FilePermission] | None,
    default_permission: FilePermission,
    read_hooks: Mapping[str | PurePosixPath, ReadHook] | None = None,
    write_hooks: Mapping[str | PurePosixPath, WriteHook] | None = None,
) -> GrailFilesystem:
    return GrailFilesystem(
        os_access,
        root_dir=root_dir,
        permissions=permissions,
        default_permission=default_permission,
        read_hooks=read_hooks,
        write_hooks=write_hooks,
    )


class GrailFilesystem(AbstractOS):
    """Guarded OS adapter with explicit path permissions."""

    def __init__(
        self,
        os_access: AbstractOS,
        *,
        root_dir: str | PurePosixPath = "/",
        permissions: Mapping[str | PurePosixPath, FilePermission] | None = None,
        default_permission: FilePermission = FilePermission.DENY,
        read_hooks: Mapping[str | PurePosixPath, ReadHook] | None = None,
        write_hooks: Mapping[str | PurePosixPath, WriteHook] | None = None,
    ) -> None:
        self._os = os_access
        self._root = PurePosixPath(root_dir)
        self._default_permission = default_permission
        self._permissions = _normalized_paths(permissions)
        self._read_hooks = _normalized_paths(read_hooks)
        self._write_hooks = _normalized_paths(write_hooks)

    def _normalize(self, path: PurePosixPath) -> PurePosixPath:
        normalized = PurePosixPath(self._os.path_absolute(path))
        if ".." in normalized.parts:
            raise PermissionError(f"Path escapes filesystem root: {normalized}")
        try:
            normalized.relative_to(self._root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes filesystem root: {normalized}") from exc
        resolved = PurePosixPath(self._os.path_resolve(normalized))
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes filesystem root: {resolved}") from exc
        return normalized

    def _permission_for(self, path: PurePosixPath) -> FilePermission:
        current = path
        while True:
            if current in self._permissions:
                return self._permissions[current]
            if current == current.parent:
                break
            current = current.parent
        return self._default_permission

    def _assert_read_allowed(self, path: PurePosixPath) -> None:
        if self._permission_for(path) not in (FilePermission.READ, FilePermission.WRITE):
            raise PermissionError(f"Read denied for path: {path}")

    def _assert_write_allowed(self, path: PurePosixPath) -> None:
        if self._permission_for(path) is not FilePermission.WRITE:
            raise PermissionError(f"Write denied for path: {path}")

    def _iter_prefix_hooks(
        self, mapping: dict[PurePosixPath, Any], path: PurePosixPath
    ) -> Any | None:
        for candidate in sorted(mapping, key=lambda item: len(item.parts), reverse=True):
            if path == candidate or path.is_relative_to(candidate):
                return mapping[candidate]
        return None

    def path_exists(self, path: PurePosixPath) -> bool:
        normalized = self._normalize(path)
        return self._os.path_exists(normalized)

    def path_is_file(self, path: PurePosixPath) -> bool:
        normalized = self._normalize(path)
        return self._os.path_is_file(normalized)

    def path_is_dir(self, path: PurePosixPath) -> bool:
        normalized = self._normalize(path)
        return self._os.path_is_dir(normalized)

    def path_is_symlink(self, path: PurePosixPath) -> bool:
        normalized = self._normalize(path)
        return self._os.path_is_symlink(normalized)

    def path_read_text(self, path: PurePosixPath) -> str:
        normalized = self._normalize(path)
        self._assert_read_allowed(normalized)
        hook = self._iter_prefix_hooks(self._read_hooks, normalized)
        if hook is not None:
            value = hook(normalized)
            return value if isinstance(value, str) else value.decode()
        return self._os.path_read_text(normalized)

    def path_read_bytes(self, path: PurePosixPath) -> bytes:
        normalized = self._normalize(path)
        self._assert_read_allowed(normalized)
        hook = self._iter_prefix_hooks(self._read_hooks, normalized)
        if hook is not None:
            value = hook(normalized)
            return value if isinstance(value, bytes) else value.encode()
        return self._os.path_read_bytes(normalized)

    def path_write_text(self, path: PurePosixPath, data: str) -> int:
        normalized = self._normalize(path)
        self._assert_write_allowed(normalized)
        hook = self._iter_prefix_hooks(self._write_hooks, normalized)
        if hook is not None:
            hook(normalized, data)
            return len(data)
        return self._os.path_write_text(normalized, data)

    def path_write_bytes(self, path: PurePosixPath, data: bytes) -> int:
        normalized = self._normalize(path)
        self._assert_write_allowed(normalized)
        hook = self._iter_prefix_hooks(self._write_hooks, normalized)
        if hook is not None:
            hook(normalized, data)
            return len(data)
        return self._os.path_write_bytes(normalized, data)

    def path_mkdir(self, path: PurePosixPath, parents: bool, exist_ok: bool) -> None:
        normalized = self._normalize(path)
        self._assert_write_allowed(normalized)
        return self._os.path_mkdir(normalized, parents=parents, exist_ok=exist_ok)

    def path_unlink(self, path: PurePosixPath) -> None:
        normalized = self._normalize(path)
        self._assert_write_allowed(normalized)
        return self._os.path_unlink(normalized)

    def path_rmdir(self, path: PurePosixPath) -> None:
        normalized = self._normalize(path)
        self._assert_write_allowed(normalized)
        return self._os.path_rmdir(normalized)

    def path_iterdir(self, path: PurePosixPath) -> list[PurePosixPath]:
        normalized = self._normalize(path)
        self._assert_read_allowed(normalized)
        return self._os.path_iterdir(normalized)

    def path_stat(self, path: PurePosixPath) -> Any:
        normalized = self._normalize(path)
        self._assert_read_allowed(normalized)
        return self._os.path_stat(normalized)

    def path_rename(self, path: PurePosixPath, target: PurePosixPath) -> None:
        normalized_path = self._normalize(path)
        normalized_target = self._normalize(target)
        self._assert_write_allowed(normalized_path)
        self._assert_write_allowed(normalized_target)
        return self._os.path_rename(normalized_path, normalized_target)

    def path_resolve(self, path: PurePosixPath) -> str:
        return self._os.path_resolve(self._normalize(path))

    def path_absolute(self, path: PurePosixPath) -> str:
        return self._os.path_absolute(path)

    def getenv(self, key: str, default: str | None = None) -> str | None:
        return self._os.getenv(key, default)

    def get_environ(self) -> dict[str, str]:
        return self._os.get_environ()


def memory_filesystem(
    files: Mapping[str | PurePosixPath, str | bytes],
    *,
    root_dir: str | PurePosixPath = "/",
    permissions: Mapping[str | PurePosixPath, FilePermission] | None = None,
    default_permission: FilePermission = FilePermission.DENY,
) -> GrailFilesystem:
    return _build_grail_filesystem(
        _build_os_access_from_memory_files(
            files,
            root_dir=root_dir,
            permission_map=permissions or {},
        ),
        root_dir=root_dir,
        permissions=permissions,
        default_permission=default_permission,
    )


def hooked_filesystem(
    *,
    read_hooks: Mapping[str | PurePosixPath, ReadHook] | None = None,
    write_hooks: Mapping[str | PurePosixPath, WriteHook] | None = None,
    files: Mapping[str | PurePosixPath, str | bytes] | None = None,
    root_dir: str | PurePosixPath = "/",
    permissions: Mapping[str | PurePosixPath, FilePermission] | None = None,
    default_permission: FilePermission = FilePermission.DENY,
) -> GrailFilesystem:
    """Build a filesystem with optional seed files and prefix path hooks."""
    return _build_grail_filesystem(
        _build_os_access_from_memory_files(
            files or {},
            root_dir=root_dir,
        ),
        root_dir=root_dir,
        permissions=permissions,
        default_permission=default_permission,
        read_hooks=read_hooks,
        write_hooks=write_hooks,
    )


def callback_filesystem(
    files: Mapping[str | PurePosixPath, tuple[ReadHook, WriteHook]],
    *,
    root_dir: str | PurePosixPath = "/",
    permissions: Mapping[str | PurePosixPath, FilePermission] | None = None,
    default_permission: FilePermission = FilePermission.DENY,
    read_hooks: Mapping[str | PurePosixPath, ReadHook] | None = None,
    write_hooks: Mapping[str | PurePosixPath, WriteHook] | None = None,
) -> GrailFilesystem:
    callback_files = [
        CallbackFile(
            path,
            read=read,
            write=write,
            permissions=_permission_mode(path, permissions or {}),
        )
        for path, (read, write) in files.items()
    ]
    return _build_grail_filesystem(
        OSAccess(callback_files, root_dir=root_dir),
        root_dir=root_dir,
        permissions=permissions,
        default_permission=default_permission,
        read_hooks=read_hooks,
        write_hooks=write_hooks,
    )
