"""Grail tool definitions exposed to sandboxed Cairn agents."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fsdantic import FileOperations, View, ViewQuery, Workspace
from pydantic import BaseModel, Field

from cairn.external_models import (
    AskLlmRequest,
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


class CairnAgentTools:
    """Implementation of agent-facing tool behavior."""

    def __init__(self, agent_id: str, agent_fs: Workspace, stable_fs: Workspace, llm_provider: Any = None):
        self.agent_id = agent_id
        self.agent_fs = agent_fs
        self.stable_fs = stable_fs
        self.llm_provider = llm_provider
        self.file_ops = FileOperations(agent_fs.raw, base_fs=stable_fs.raw)

    async def read_file(self, path: str) -> str:
        request = ReadFileRequest(path=path)
        content = await self.file_ops.read_file(request.path)
        return ReadFileResponse(content=content).content

    async def write_file(self, path: str, content: str) -> bool:
        request = WriteFileRequest(path=path, content=content)
        await self.file_ops.write_file(request.path, request.content)
        return True

    async def list_dir(self, path: str) -> list[str]:
        request = ListDirRequest(path=path)
        entries = await self.file_ops.list_dir(request.path)
        return [entry.path.split("/")[-1] for entry in entries]

    async def file_exists(self, path: str) -> bool:
        request = FileExistsRequest(path=path)
        return await self.file_ops.file_exists(request.path)

    async def search_files(self, pattern: str) -> list[str]:
        request = SearchFilesRequest(pattern=pattern)
        view = View(
            agent=self.agent_fs.raw,
            query=ViewQuery(path_pattern=request.pattern, recursive=True, include_stats=False, include_content=False),
        )
        files = await view.load()
        return [f.path for f in files]

    async def search_content(self, pattern: str, path: str = ".") -> list[dict[str, Any]]:
        request = SearchContentRequest(pattern=pattern, path=path)
        path_pattern = self._search_content_path_pattern(request.path)
        view = View(
            agent=self.agent_fs.raw,
            query=ViewQuery(
                path_pattern=path_pattern,
                content_regex=request.pattern,
                recursive=True,
                include_stats=False,
                include_content=True,
            ),
        )
        matches = await view.search_content()
        return [
            SearchContentMatch(file=match.path, line=match.line_number, text=match.line_text).model_dump()
            for match in matches
        ]

    @staticmethod
    def _search_content_path_pattern(path: str) -> str:
        normalized = path.rstrip("/")
        if normalized in {"", ".", "/"}:
            return "**/*"

        if any(token in normalized for token in "*?[]"):
            return normalized

        return f"{normalized}/**/*"

    async def ask_llm(self, prompt: str, context: str = "") -> str:
        request = AskLlmRequest(prompt=prompt, context=context)
        if self.llm_provider is None:
            raise RuntimeError("No LLM provider configured")
        full_prompt = f"{request.context}\n\n{request.prompt}" if request.context else request.prompt
        return await self.llm_provider.generate(full_prompt)

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


class ReadFileInput(BaseModel):
    path: str = Field(description="Relative file path to read")


class WriteFileInput(BaseModel):
    path: str
    content: str


class ListDirInput(BaseModel):
    path: str = "."


class FileExistsInput(BaseModel):
    path: str


class SearchFilesInput(BaseModel):
    pattern: str


class SearchContentInput(BaseModel):
    pattern: str
    path: str = "."


class AskLlmInput(BaseModel):
    prompt: str
    context: str = ""


class SubmitResultInput(BaseModel):
    summary: str
    changed_files: list[str]


class LogInput(BaseModel):
    message: str


def create_agent_tools(
    agent_id: str,
    agent_fs: Workspace,
    stable_fs: Workspace,
    llm_provider: Any = None,
) -> list[Callable[..., Any]]:
    """Create Grail-compatible tool callables for an agent sandbox."""
    ext = CairnAgentTools(agent_id=agent_id, agent_fs=agent_fs, stable_fs=stable_fs, llm_provider=llm_provider)

    async def read_file(path: str) -> str:
        return await ext.read_file(ReadFileInput(path=path).path)

    async def write_file(path: str, content: str) -> bool:
        payload = WriteFileInput(path=path, content=content)
        return await ext.write_file(payload.path, payload.content)

    async def list_dir(path: str = ".") -> list[str]:
        return await ext.list_dir(ListDirInput(path=path).path)

    async def file_exists(path: str) -> bool:
        return await ext.file_exists(FileExistsInput(path=path).path)

    async def search_files(pattern: str) -> list[str]:
        return await ext.search_files(SearchFilesInput(pattern=pattern).pattern)

    async def search_content(pattern: str, path: str = ".") -> list[dict[str, Any]]:
        payload = SearchContentInput(pattern=pattern, path=path)
        return await ext.search_content(payload.pattern, payload.path)

    async def ask_llm(prompt: str, context: str = "") -> str:
        payload = AskLlmInput(prompt=prompt, context=context)
        return await ext.ask_llm(payload.prompt, payload.context)

    async def submit_result(summary: str, changed_files: list[str]) -> bool:
        payload = SubmitResultInput(summary=summary, changed_files=changed_files)
        return await ext.submit_result(payload.summary, payload.changed_files)

    async def log(message: str) -> bool:
        return await ext.log(LogInput(message=message).message)

    return [read_file, write_file, list_dir, file_exists, search_files, search_content, ask_llm, submit_result, log]
