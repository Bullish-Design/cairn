"""External function factory for Cairn agent execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import re
from typing import Any

from fsdantic import FileNotFoundError, ViewQuery, Workspace

from cairn.external_models import (
    FileExistsRequest,
    ListDirRequest,
    LogRequest,
    ReadFileRequest,
    ReadFileResponse,
    SearchContentMatch,
    SearchContentRequest,
    SearchFilesRequest,
    SubmissionPayload,
    SubmitResultRequest,
    WriteFileRequest,
)
from cairn.lifecycle import SUBMISSION_KEY, SubmissionRecord

ExternalFunction = Callable[..., Awaitable[Any]]


class CairnExternalFunctions:
    """Implementation of external functions exposed to agent scripts."""

    def __init__(self, agent_id: str, agent_fs: Workspace, stable_fs: Workspace) -> None:
        self.agent_id = agent_id
        self.agent_fs = agent_fs
        self.stable_fs = stable_fs

    async def read_file(self, path: str) -> str:
        request = ReadFileRequest(path=path)
        try:
            content = await self.agent_fs.files.read(request.path)
        except FileNotFoundError:
            content = await self.stable_fs.files.read(request.path)
        return ReadFileResponse(content=content).content

    async def write_file(self, path: str, content: str) -> bool:
        request = WriteFileRequest(path=path, content=content)
        await self.agent_fs.files.write(request.path, request.content)
        return True

    async def list_dir(self, path: str) -> list[str]:
        request = ListDirRequest(path=path)
        return await self.agent_fs.files.list_dir(request.path, output="name")

    async def file_exists(self, path: str) -> bool:
        request = FileExistsRequest(path=path)
        if await self.agent_fs.files.exists(request.path):
            return True
        return await self.stable_fs.files.exists(request.path)

    async def search_files(self, pattern: str) -> list[str]:
        request = SearchFilesRequest(pattern=pattern)
        files = await self.agent_fs.files.search(request.pattern)
        return [file_path.lstrip("/") for file_path in files]

    async def search_content(self, pattern: str, path: str = ".") -> list[dict[str, Any]]:
        request = SearchContentRequest(pattern=pattern, path=path)
        path_pattern = self._search_content_path_pattern(request.path)
        regex = re.compile(request.pattern)

        query = ViewQuery(
            path_pattern=path_pattern,
            recursive=True,
            include_stats=False,
            include_content=True,
        )

        agent_entries = await self.agent_fs.files.query(query)
        stable_entries = await self.stable_fs.files.query(query)
        agent_paths = {entry.path for entry in agent_entries}

        def build_matches(entries: list[Any]) -> list[dict[str, Any]]:
            matches: list[dict[str, Any]] = []
            for entry in entries:
                content = entry.content
                if content is None:
                    continue
                if isinstance(content, bytes):
                    content = content.decode("utf-8", errors="ignore")
                for line_number, line in enumerate(str(content).splitlines(), start=1):
                    if regex.search(line):
                        matches.append(
                            SearchContentMatch(
                                file=entry.path.lstrip("/"),
                                line=line_number,
                                text=line,
                            ).model_dump()
                        )
            return matches

        all_matches = build_matches(agent_entries)
        overlay_matches = build_matches([entry for entry in stable_entries if entry.path not in agent_paths])
        all_matches.extend(overlay_matches)

        return all_matches

    @staticmethod
    def _search_content_path_pattern(path: str) -> str:
        normalized = path.rstrip("/")
        if normalized in {"", ".", "/"}:
            return "**/*"

        if any(token in normalized for token in "*?[]"):
            return normalized

        return f"{normalized}/**/*"

    async def submit_result(self, summary: str, changed_files: list[str]) -> bool:
        request = SubmitResultRequest(summary=summary, changed_files=changed_files)
        submission = SubmissionPayload(summary=request.summary, changed_files=request.changed_files)
        submission_record = SubmissionRecord(agent_id=self.agent_id, submission=submission.model_dump())
        submission_repo = self.agent_fs.kv.repository(prefix="", model_type=SubmissionRecord)
        await submission_repo.save(SUBMISSION_KEY, submission_record)
        return True

    async def log(self, message: str) -> bool:
        request = LogRequest(message=message)
        print(f"[{self.agent_id}] {request.message}")
        return True


def create_external_functions(agent_id: str, agent_fs: Workspace, stable_fs: Workspace) -> dict[str, ExternalFunction]:
    """Create the external function map for Grail execution."""
    ext = CairnExternalFunctions(agent_id=agent_id, agent_fs=agent_fs, stable_fs=stable_fs)

    async def read_file(path: str) -> str:
        return await ext.read_file(path)

    async def write_file(path: str, content: str) -> bool:
        return await ext.write_file(path, content)

    async def list_dir(path: str = ".") -> list[str]:
        return await ext.list_dir(path)

    async def file_exists(path: str) -> bool:
        return await ext.file_exists(path)

    async def search_files(pattern: str) -> list[str]:
        return await ext.search_files(pattern)

    async def search_content(pattern: str, path: str = ".") -> list[dict[str, Any]]:
        return await ext.search_content(pattern, path)

    async def submit_result(summary: str, changed_files: list[str]) -> bool:
        return await ext.submit_result(summary, changed_files)

    async def log(message: str) -> bool:
        return await ext.log(message)

    return {
        "read_file": read_file,
        "write_file": write_file,
        "list_dir": list_dir,
        "file_exists": file_exists,
        "search_files": search_files,
        "search_content": search_content,
        "submit_result": submit_result,
        "log": log,
    }
