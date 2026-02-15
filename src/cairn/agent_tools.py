"""Grail tool definitions exposed to sandboxed Cairn agents."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fsdantic import Workspace
from pydantic import BaseModel, Field

from cairn.external_functions import CairnExternalFunctions


class ReadFileInput(BaseModel):
    path: str = Field(description="Relative file path to read")


class ReadFileOutput(BaseModel):
    content: str


class WriteFileInput(BaseModel):
    path: str
    content: str


class WriteFileOutput(BaseModel):
    success: bool = True


class ListDirInput(BaseModel):
    path: str = "."


class ListDirOutput(BaseModel):
    entries: list[str]


class FileExistsInput(BaseModel):
    path: str


class FileExistsOutput(BaseModel):
    exists: bool


class SearchFilesInput(BaseModel):
    pattern: str


class SearchFilesOutput(BaseModel):
    files: list[str]


class SearchContentInput(BaseModel):
    pattern: str
    path: str = "."


class SearchContentOutput(BaseModel):
    matches: list[dict[str, Any]]


class AskLlmInput(BaseModel):
    prompt: str
    context: str = ""


class AskLlmOutput(BaseModel):
    response: str


class SubmitResultInput(BaseModel):
    summary: str
    changed_files: list[str]


class SubmitResultOutput(BaseModel):
    success: bool = True


class LogInput(BaseModel):
    message: str


class LogOutput(BaseModel):
    success: bool = True


def create_agent_tools(
    agent_id: str,
    agent_fs: Workspace,
    stable_fs: Workspace,
    llm_provider: Any = None,
) -> list[Callable[..., Any]]:
    """Create Grail-compatible tool callables for an agent sandbox."""
    ext = CairnExternalFunctions(
        agent_id=agent_id,
        agent_fs=agent_fs,
        stable_fs=stable_fs,
        llm_provider=llm_provider,
    )

    async def read_file(path: str) -> str:
        ReadFileInput(path=path)
        result = await ext.read_file(path)
        return ReadFileOutput(content=result).content

    async def write_file(path: str, content: str) -> bool:
        WriteFileInput(path=path, content=content)
        success = await ext.write_file(path, content)
        return WriteFileOutput(success=success).success

    async def list_dir(path: str = ".") -> list[str]:
        payload = ListDirInput(path=path)
        entries = await ext.list_dir(payload.path)
        return ListDirOutput(entries=entries).entries

    async def file_exists(path: str) -> bool:
        payload = FileExistsInput(path=path)
        exists = await ext.file_exists(payload.path)
        return FileExistsOutput(exists=exists).exists

    async def search_files(pattern: str) -> list[str]:
        payload = SearchFilesInput(pattern=pattern)
        files = await ext.search_files(payload.pattern)
        return SearchFilesOutput(files=files).files

    async def search_content(pattern: str, path: str = ".") -> list[dict[str, Any]]:
        payload = SearchContentInput(pattern=pattern, path=path)
        matches = await ext.search_content(payload.pattern, payload.path)
        return SearchContentOutput(matches=matches).matches

    async def ask_llm(prompt: str, context: str = "") -> str:
        payload = AskLlmInput(prompt=prompt, context=context)
        response = await ext.ask_llm(payload.prompt, payload.context)
        return AskLlmOutput(response=response).response

    async def submit_result(summary: str, changed_files: list[str]) -> bool:
        payload = SubmitResultInput(summary=summary, changed_files=changed_files)
        success = await ext.submit_result(payload.summary, payload.changed_files)
        return SubmitResultOutput(success=success).success

    async def log(message: str) -> bool:
        payload = LogInput(message=message)
        success = await ext.log(payload.message)
        return LogOutput(success=success).success

    return [
        read_file,
        write_file,
        list_dir,
        file_exists,
        search_files,
        search_content,
        ask_llm,
        submit_result,
        log,
    ]
