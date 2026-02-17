"""Pydantic models for Cairn external function schemas.

These models are the canonical contract for external function inputs and outputs.
"""

from __future__ import annotations

import time
from pathlib import PurePosixPath
from typing import Annotated, Any

from pydantic import BaseModel, Field
from pydantic.functional_validators import AfterValidator

from cairn.core.constants import MAX_FILE_SIZE_BYTES
from cairn.core.exceptions import PathValidationError


def _validate_path(value: str, *, allow_root: bool = False) -> str:
    """Validate a path for sandbox-safe use."""
    if allow_root and value == "/":
        return value

    path = PurePosixPath(value)
    if path.is_absolute():
        raise PathValidationError(
            f"Absolute paths not allowed in sandbox: {value}",
            error_code="PATH_ABSOLUTE",
            context={"path": value},
        )
    if ".." in path.parts:
        raise PathValidationError(
            f"Path traversal not allowed: {value}",
            error_code="PATH_TRAVERSAL",
            context={"path": value},
        )
    return value


def validate_relative_path(value: str) -> str:
    """Validator for relative file paths."""
    return _validate_path(value)


def validate_relative_or_root_path(value: str) -> str:
    """Validator for relative paths plus root directory '/'."""
    return _validate_path(value, allow_root=True)


def validate_max_file_size_text(value: str) -> str:
    """Validate UTF-8 text size against MAX_FILE_SIZE."""
    size = len(value.encode("utf-8"))
    if size > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"Content too large: {size} bytes")
    return value


RelativePath = Annotated[str, AfterValidator(validate_relative_path)]
RelativeOrRootPath = Annotated[str, AfterValidator(validate_relative_or_root_path)]
MaxFileSizeText = Annotated[str, AfterValidator(validate_max_file_size_text)]


class ReadFileRequest(BaseModel):
    path: RelativePath


class ReadFileResponse(BaseModel):
    content: MaxFileSizeText


class WriteFileRequest(BaseModel):
    path: RelativePath
    content: MaxFileSizeText


class WriteFileResponse(BaseModel):
    success: bool


class ListDirRequest(BaseModel):
    path: RelativeOrRootPath


class ListDirResponse(BaseModel):
    entries: list[str]


class FileExistsRequest(BaseModel):
    path: RelativePath


class FileExistsResponse(BaseModel):
    exists: bool


class SearchFilesRequest(BaseModel):
    pattern: str


class SearchFilesResponse(BaseModel):
    files: list[str]


class SearchContentRequest(BaseModel):
    pattern: str
    path: RelativeOrRootPath = "."


class SearchContentMatch(BaseModel):
    file: RelativePath
    line: int = Field(ge=1)
    text: str


class SearchContentResponse(BaseModel):
    matches: list[SearchContentMatch]


class SubmitResultRequest(BaseModel):
    summary: str
    changed_files: list[RelativePath]


class SubmissionPayload(BaseModel):
    summary: str
    changed_files: list[RelativePath]
    submitted_at: float = Field(default_factory=time.time)


class SubmitResultResponse(BaseModel):
    success: bool


class LogRequest(BaseModel):
    message: str


class LogResponse(BaseModel):
    success: bool


ExternalFunctionSchemaMap = dict[str, dict[str, type[BaseModel] | type[Any]]]


EXTERNAL_FUNCTION_SCHEMAS: ExternalFunctionSchemaMap = {
    "read_file": {"request": ReadFileRequest, "response": ReadFileResponse},
    "write_file": {"request": WriteFileRequest, "response": WriteFileResponse},
    "list_dir": {"request": ListDirRequest, "response": ListDirResponse},
    "file_exists": {"request": FileExistsRequest, "response": FileExistsResponse},
    "search_files": {"request": SearchFilesRequest, "response": SearchFilesResponse},
    "search_content": {"request": SearchContentRequest, "response": SearchContentResponse},
    "submit_result": {"request": SubmitResultRequest, "response": SubmitResultResponse},
    "log": {"request": LogRequest, "response": LogResponse},
}
