"""External functions interface for Cairn agents.

This module defines the external functions that agent code can call from
within the Monty sandbox. These are the ONLY ways agents can interact with
the host system.
"""

from typing import Any, Protocol

from agentfs_sdk import AgentFS

from cairn.kv_store import KVRepository
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
        agent_fs: AgentFS,
        stable_fs: AgentFS,
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

    async def read_file(self, path: str) -> str:
        """Read file from agent overlay (falls through to stable)."""
        request = ReadFileRequest(path=path)

        try:
            # Try to read from agent overlay first
            content = await self.agent_fs.fs.read_file(request.path)
        except FileNotFoundError:
            # Fall through to stable
            content = await self.stable_fs.fs.read_file(request.path)

        response = ReadFileResponse(content=content.decode("utf-8"))
        return response.content

    async def write_file(self, path: str, content: str) -> bool:
        """Write file to agent overlay only."""
        request = WriteFileRequest(path=path, content=content)

        # Write to agent overlay only
        await self.agent_fs.fs.write_file(request.path, request.content.encode("utf-8"))
        return True

    async def list_dir(self, path: str) -> list[str]:
        """List directory contents."""
        request = ListDirRequest(path=path)

        # List from agent overlay (which includes stable via fallthrough)
        entries = await self.agent_fs.fs.readdir(request.path)
        return [entry.name for entry in entries]

    async def file_exists(self, path: str) -> bool:
        """Check if file exists."""
        request = FileExistsRequest(path=path)

        try:
            await self.agent_fs.fs.stat(request.path)
            return True
        except FileNotFoundError:
            return False

    async def search_files(self, pattern: str) -> list[str]:
        """Find files matching glob pattern.

        This uses a temporary materialized workspace to run glob search.
        """
        request = SearchFilesRequest(pattern=pattern)

        # For now, we'll walk the directory tree in AgentFS
        # In production, this should use materialized workspace
        results = []

        async def walk_dir(dir_path: str = "/") -> None:
            try:
                entries = await self.agent_fs.fs.readdir(dir_path)
                for entry in entries:
                    full_path = f"{dir_path}/{entry.name}".lstrip("/")

                    if entry.type == "directory":
                        await walk_dir(full_path)
                    else:
                        # Simple glob matching (just * for now)
                        if self._match_pattern(full_path, request.pattern):
                            results.append(full_path)
            except FileNotFoundError:
                pass

        await walk_dir()
        return results

    def _match_pattern(self, path: str, pattern: str) -> bool:
        """Simple glob pattern matching.

        Args:
            path: File path to match
            pattern: Glob pattern (supports * and **)

        Returns:
            True if path matches pattern
        """
        import fnmatch

        return fnmatch.fnmatch(path, pattern)

    async def search_content(self, pattern: str, path: str = ".") -> list[dict[str, Any]]:
        """Search file contents using ripgrep (if available) or basic search."""
        request = SearchContentRequest(pattern=pattern, path=path)

        # For MVP, we'll do basic search within AgentFS
        # In production, this should use ripgrep on materialized workspace
        results = []

        async def search_in_file(file_path: str) -> None:
            try:
                content = await self.read_file(file_path)
                lines = content.split("\n")

                import re

                regex = re.compile(request.pattern)

                for line_num, line in enumerate(lines, start=1):
                    if regex.search(line):
                        match = SearchContentMatch(
                            file=file_path,
                            line=line_num,
                            text=line.strip(),
                        )
                        results.append(match.model_dump())
            except Exception:
                # Ignore errors (binary files, etc.)
                pass

        # Get all files
        files = await self.search_files("*")
        for file_path in files:
            await search_in_file(file_path)

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
        submission_repo = KVRepository(self.agent_fs)
        await submission_repo.save_submission(self.agent_id, submission.model_dump())
        return True

    async def log(self, message: str) -> bool:
        """Log debug message."""
        request = LogRequest(message=message)
        print(f"[{self.agent_id}] {request.message}")
        return True


def create_external_functions(
    agent_id: str,
    agent_fs: AgentFS,
    stable_fs: AgentFS,
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
