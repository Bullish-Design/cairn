"""External functions interface for Cairn agents.

This module defines the external functions that agent code can call from
within the Monty sandbox. These are the ONLY ways agents can interact with
the host system.
"""

from typing import Any, Protocol

from fsdantic import FileOperations, View, ViewQuery, Workspace
from cairn.kv_models import SUBMISSION_KEY, SubmissionRecord
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


class ExternalFunctions(Protocol):
    """Protocol defining external functions available to agents."""

    async def read_file(self, path: str) -> str:
        """Read file from agent overlay (falls through to stable).

        Args:
            path: Relative path to file

        Returns:
            File content as string

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If path is invalid (contains ..)
        """
        ...

    async def write_file(self, path: str, content: str) -> bool:
        """Write file to agent overlay only.

        Args:
            path: Relative path to file
            content: File content to write

        Returns:
            True if successful

        Raises:
            ValueError: If path is invalid or content too large
        """
        ...

    async def list_dir(self, path: str) -> list[str]:
        """List directory contents.

        Args:
            path: Directory path to list

        Returns:
            List of file/directory names

        Raises:
            FileNotFoundError: If directory doesn't exist
        """
        ...

    async def file_exists(self, path: str) -> bool:
        """Check if file exists.

        Args:
            path: Path to check

        Returns:
            True if file exists
        """
        ...

    async def search_files(self, pattern: str) -> list[str]:
        """Find files matching glob pattern.

        Args:
            pattern: Glob pattern (e.g., "*.py", "src/**/*.ts")

        Returns:
            List of matching file paths
        """
        ...

    async def search_content(self, pattern: str, path: str = ".") -> list[dict[str, Any]]:
        """Search file contents using regex pattern.

        Args:
            pattern: Regex pattern to search for
            path: Root path to search in

        Returns:
            List of matches with structure:
            [{"file": "path.py", "line": 42, "text": "matching line"}]
        """
        ...

    async def ask_llm(self, prompt: str, context: str = "") -> str:
        """Query LLM for assistance.

        Args:
            prompt: Question or instruction for LLM
            context: Optional context to provide

        Returns:
            LLM response text
        """
        ...

    async def submit_result(self, summary: str, changed_files: list[str]) -> bool:
        """Submit agent results for review.

        Args:
            summary: Brief description of changes made
            changed_files: List of files modified

        Returns:
            True if submission successful
        """
        ...

    async def log(self, message: str) -> bool:
        """Log debug message.

        Args:
            message: Debug message to log

        Returns:
            True if logged successfully
        """
        ...


class CairnExternalFunctions:
    """Implementation of external functions for Cairn agents."""

    def __init__(
        self,
        agent_id: str,
        agent_fs: Workspace,
        stable_fs: Workspace,
        llm_provider: Any = None,
    ):
        """Initialize external functions.

        Args:
            agent_id: Agent identifier
            agent_fs: Agent's AgentFS instance (overlay)
            stable_fs: Stable AgentFS instance (base layer)
            llm_provider: LLM provider for ask_llm (optional)
        """
        self.agent_id = agent_id
        self.agent_fs = agent_fs
        self.stable_fs = stable_fs
        self.llm_provider = llm_provider

        # Use fsdantic FileOperations for automatic overlay fallthrough
        self.file_ops = FileOperations(agent_fs.raw, base_fs=stable_fs.raw)

    async def read_file(self, path: str) -> str:
        """Read file from agent overlay (falls through to stable)."""
        request = ReadFileRequest(path=path)

        # Use FileOperations which automatically handles overlay fallthrough
        content = await self.file_ops.read_file(request.path)
        response = ReadFileResponse(content=content)
        return response.content

    async def write_file(self, path: str, content: str) -> bool:
        """Write file to agent overlay only."""
        request = WriteFileRequest(path=path, content=content)

        # Use FileOperations for automatic encoding handling
        await self.file_ops.write_file(request.path, request.content)
        return True

    async def list_dir(self, path: str) -> list[str]:
        """List directory contents."""
        request = ListDirRequest(path=path)

        # Use FileOperations for consistent API
        entries = await self.file_ops.list_dir(request.path)
        return [entry.path.split("/")[-1] for entry in entries]

    async def file_exists(self, path: str) -> bool:
        """Check if file exists."""
        request = FileExistsRequest(path=path)

        # Use FileOperations which checks both overlay and base
        return await self.file_ops.file_exists(request.path)

    async def search_files(self, pattern: str) -> list[str]:
        """Find files matching glob pattern.

        Uses fsdantic's View for powerful glob pattern matching.
        """
        request = SearchFilesRequest(pattern=pattern)

        # Use View for glob search with proper ** support
        view = View(
            agent=self.agent_fs.raw,
            query=ViewQuery(
                path_pattern=request.pattern,
                recursive=True,
                include_stats=False,
                include_content=False,
            ),
        )

        files = await view.load()
        return [f.path for f in files]

    async def search_content(self, pattern: str, path: str = ".") -> list[dict[str, Any]]:
        """Search file contents using regex pattern.

        Uses fsdantic's View for efficient content search.
        """
        request = SearchContentRequest(pattern=pattern, path=path)

        # Use View for regex content search
        view = View(
            agent=self.agent_fs.raw,
            query=ViewQuery(
                path_pattern="**/*",  # Search all files
                content_regex=request.pattern,
                recursive=True,
                include_stats=False,
                include_content=True,
            ),
        )

        # Use search_content method which returns SearchMatch objects
        matches = await view.search_content()

        # Convert to the expected format
        results = []
        for match in matches:
            search_match = SearchContentMatch(
                file=match.path,
                line=match.line_number,
                text=match.line_text,
            )
            results.append(search_match.model_dump())

        return results

    async def ask_llm(self, prompt: str, context: str = "") -> str:
        """Query LLM for assistance."""
        request = AskLlmRequest(prompt=prompt, context=context)

        if self.llm_provider is None:
            raise RuntimeError("No LLM provider configured")

        full_prompt = f"{request.context}\n\n{request.prompt}" if request.context else request.prompt
        response = await self.llm_provider.generate(full_prompt)
        return response

    async def submit_result(self, summary: str, changed_files: list[str]) -> bool:
        """Submit agent results for review."""
        request = SubmitResultRequest(summary=summary, changed_files=changed_files)
        submission = SubmissionPayload(
            summary=request.summary,
            changed_files=request.changed_files,
        )

        # Store in agent's KV store using typed adapter format.
        submission_record = SubmissionRecord(agent_id=self.agent_id, submission=submission.model_dump())
        submission_repo = self.agent_fs.kv.repository(prefix="", model_type=SubmissionRecord)
        await submission_repo.save(SUBMISSION_KEY, submission_record)
        return True

    async def log(self, message: str) -> bool:
        """Log debug message."""
        request = LogRequest(message=message)
        print(f"[{self.agent_id}] {request.message}")
        return True


def create_external_functions(
    agent_id: str,
    agent_fs: Workspace,
    stable_fs: Workspace,
    llm_provider: Any = None,
) -> dict[str, Any]:
    """Create external functions dictionary for Monty.

    The canonical argument/return schemas for each function are defined in
    ``cairn.external_models.EXTERNAL_FUNCTION_SCHEMAS``.

    Args:
        agent_id: Agent identifier
        agent_fs: Agent's AgentFS instance
        stable_fs: Stable AgentFS instance
        llm_provider: LLM provider for ask_llm

    Returns:
        Dictionary mapping function names to callables.
    """
    ext_funcs = CairnExternalFunctions(agent_id, agent_fs, stable_fs, llm_provider)

    return {
        "read_file": ext_funcs.read_file,
        "write_file": ext_funcs.write_file,
        "list_dir": ext_funcs.list_dir,
        "file_exists": ext_funcs.file_exists,
        "search_files": ext_funcs.search_files,
        "search_content": ext_funcs.search_content,
        "ask_llm": ext_funcs.ask_llm,
        "submit_result": ext_funcs.submit_result,
        "log": ext_funcs.log,
    }
