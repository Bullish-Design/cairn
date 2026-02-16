"""GrailScript - Main API for loading and executing .pym files."""

import asyncio
from pathlib import Path
from typing import Any, Callable
import time
import re

try:
    import pydantic_monty
except ImportError:
    pydantic_monty = None

from grail._types import ExternalSpec, InputSpec, CheckResult, SourceMap
from grail.parser import parse_pym_file
from grail.checker import check_pym
from grail.stubs import generate_stubs
from grail.codegen import generate_monty_code
from grail.artifacts import ArtifactsManager
from grail.limits import merge_limits, parse_limits
from grail.errors import InputError, ExternalError, ExecutionError, LimitError, OutputError


class GrailScript:
    """
    Main interface for loading and executing .pym files.

    This class encapsulates:
    - Parsed .pym file metadata
    - Generated Monty code and stubs
    - Validation results
    - Execution interface
    """

    def __init__(
        self,
        path: Path,
        externals: dict[str, ExternalSpec],
        inputs: dict[str, InputSpec],
        monty_code: str,
        stubs: str,
        source_map: SourceMap,
        source_lines: list[str],
        limits: dict[str, Any] | None = None,
        files: dict[str, str | bytes] | None = None,
        grail_dir: Path | None = None,
    ):
        """
        Initialize GrailScript.

        Args:
            path: Path to original .pym file
            externals: External function specifications
            inputs: Input specifications
            monty_code: Generated Monty code
            stubs: Generated type stubs
            source_map: Line number mapping
            source_lines: .pym source lines
            limits: Resource limits
            files: Virtual filesystem files
            grail_dir: Directory for artifacts (None disables)
        """
        self.path = path
        self.name = path.stem
        self.externals = externals
        self.inputs = inputs
        self.monty_code = monty_code
        self.stubs = stubs
        self.source_map = source_map
        self.source_lines = source_lines
        self.limits = limits
        self.files = files
        self.grail_dir = grail_dir

        # Initialize artifacts manager if grail_dir is set
        self._artifacts = ArtifactsManager(grail_dir) if grail_dir else None

    def check(self) -> CheckResult:
        """
        Run validation checks on the script.

        Returns:
            CheckResult with errors, warnings, and info
        """
        # Re-parse and check
        parse_result = parse_pym_file(self.path)
        check_result = check_pym(parse_result)
        check_result.file = str(self.path)

        # Write check results to artifacts if enabled
        if self._artifacts:
            self._artifacts.write_script_artifacts(
                self.name, self.stubs, self.monty_code, check_result, self.externals, self.inputs
            )

        return check_result

    def _validate_inputs(self, inputs: dict[str, Any]) -> None:
        """
        Validate that provided inputs match declarations.

        Args:
            inputs: Runtime input values

        Raises:
            InputError: If validation fails
        """
        # Check for missing required inputs
        for name, spec in self.inputs.items():
            if spec.required and name not in inputs:
                raise InputError(
                    f"Missing required input: '{name}' (type: {spec.type_annotation})",
                    input_name=name,
                )

        # Check for extra inputs (warn but don't fail)
        for name in inputs:
            if name not in self.inputs:
                print(f"Warning: Extra input '{name}' not declared in script")

    def _validate_externals(self, externals: dict[str, Callable]) -> None:
        """
        Validate that provided externals match declarations.

        Args:
            externals: Runtime external function implementations

        Raises:
            ExternalError: If validation fails
        """
        # Check for missing externals
        for name in self.externals:
            if name not in externals:
                raise ExternalError(f"Missing external function: '{name}'", function_name=name)

        # Check for extra externals (warn but don't fail)
        for name in externals:
            if name not in self.externals:
                print(f"Warning: Extra external '{name}' not declared in script")

    def _prepare_monty_limits(self, override_limits: dict[str, Any] | None) -> dict[str, Any]:
        """
        Merge and parse limits for Monty.

        Args:
            override_limits: Runtime limit overrides

        Returns:
            Parsed limits dict ready for Monty
        """
        return merge_limits(self.limits, override_limits)

    def _prepare_monty_files(self, override_files: dict[str, str | bytes] | None):
        """
        Prepare files for Monty's OSAccess.

        Args:
            override_files: Runtime file overrides

        Returns:
            OSAccess object or None
        """
        if pydantic_monty is None:
            return None

        files = override_files if override_files is not None else self.files
        if not files:
            return None

        # Convert dict to Monty's MemoryFile + OSAccess
        memory_files = []
        for path, content in files.items():
            memory_files.append(pydantic_monty.MemoryFile(path, content=content))

        return pydantic_monty.OSAccess(memory_files)

    def _map_error_to_pym(self, error: Exception) -> ExecutionError:
        """
        Map Monty error to .pym file line numbers.

        Args:
            error: Original error from Monty

        Returns:
            ExecutionError with mapped line numbers
        """
        # Extract error message
        error_msg = str(error)
        error_msg_lower = error_msg.lower()

        # Try to extract line number from Monty error
        match = re.search(r"line (\d+)", error_msg, re.IGNORECASE)
        lineno = None
        if match:
            monty_line = int(match.group(1))
            lineno = self.source_map.monty_to_pym.get(monty_line, monty_line)

        # Heuristic: infer limit type from Monty error message keywords.
        limit_type = None
        if "memory" in error_msg_lower:
            limit_type = "memory"
        elif "duration" in error_msg_lower:
            limit_type = "duration"
        elif "recursion" in error_msg_lower:
            limit_type = "recursion"

        # Check if it's a limit error
        if "limit" in error_msg_lower or limit_type is not None:
            return LimitError(error_msg, limit_type=limit_type)

        source_context = "\n".join(self.source_lines) if self.source_lines else None
        return ExecutionError(
            error_msg, lineno=lineno, source_context=source_context, suggestion=None
        )

    async def run(
        self,
        inputs: dict[str, Any] | None = None,
        externals: dict[str, Callable] | None = None,
        output_model: type | None = None,
        files: dict[str, str | bytes] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> Any:
        """
        Execute the script in Monty.

        Args:
            inputs: Input values
            externals: External function implementations
            output_model: Optional Pydantic model for output validation
            files: Override files from load()
            limits: Override limits from load()

        Returns:
            Result of script execution

        Raises:
            InputError: Missing or invalid inputs
            ExternalError: Missing external functions
            ExecutionError: Monty runtime error
            OutputError: Output validation failed
        """
        if pydantic_monty is None:
            raise RuntimeError("pydantic-monty not installed")

        inputs = inputs or {}
        externals = externals or {}

        # Validate inputs and externals
        self._validate_inputs(inputs)
        self._validate_externals(externals)

        # Prepare Monty configuration
        parsed_limits = self._prepare_monty_limits(limits)
        os_access = self._prepare_monty_files(files)

        # Create Monty instance - catch type errors during construction
        try:
            monty = pydantic_monty.Monty(
                self.monty_code,
                type_check=True,
                type_check_stubs=self.stubs,
                inputs=list(self.inputs.keys()),
                external_functions=list(self.externals.keys()),
            )
        except pydantic_monty.MontyTypingError as e:
            # Convert type errors to ExecutionError
            raise ExecutionError(
                f"Type checking failed: {str(e)}",
                lineno=None,
                source_context=None,
                suggestion="Fix type errors in your code",
            ) from e

        # Execute
        start_time = time.time()
        try:
            result = await pydantic_monty.run_monty_async(
                monty,
                inputs=inputs,
                external_functions=externals,
                os=os_access,
                limits=parsed_limits,
            )
            success = True
            error_msg = None
        except Exception as e:
            success = False
            error_msg = str(e)
            mapped_error = self._map_error_to_pym(e)

            # Write error log
            if self._artifacts:
                duration_ms = (time.time() - start_time) * 1000
                self._artifacts.write_run_log(
                    self.name,
                    stdout="",
                    stderr=str(mapped_error),
                    duration_ms=duration_ms,
                    success=False,
                )

            raise mapped_error

        duration_ms = (time.time() - start_time) * 1000

        # Write success log
        if self._artifacts:
            self._artifacts.write_run_log(
                self.name,
                stdout=f"Result: {result}",
                stderr="",
                duration_ms=duration_ms,
                success=True,
            )

        # Validate output if model provided
        if output_model is not None:
            try:
                result = (
                    output_model(**result) if isinstance(result, dict) else output_model(result)
                )
            except Exception as e:
                raise OutputError(f"Output validation failed: {e}", validation_errors=e)

        return result

    def run_sync(
        self,
        inputs: dict[str, Any] | None = None,
        externals: dict[str, Callable] | None = None,
        **kwargs,
    ) -> Any:
        """
        Synchronous wrapper around run().

        Args:
            inputs: Input values
            externals: External function implementations
            **kwargs: Additional arguments for run()

        Returns:
            Result of script execution
        """
        return asyncio.run(self.run(inputs, externals, **kwargs))

    def start(
        self,
        inputs: dict[str, Any] | None = None,
        externals: dict[str, Callable] | None = None,
    ):
        """
        Begin resumable execution (pause/resume pattern).

        Args:
            inputs: Input values
            externals: External function implementations

        Returns:
            Snapshot object
        """
        # Import here to avoid circular dependency
        from grail.snapshot import Snapshot

        if pydantic_monty is None:
            raise RuntimeError("pydantic-monty not installed")

        inputs = inputs or {}
        externals = externals or {}

        # Validate inputs and externals
        self._validate_inputs(inputs)
        self._validate_externals(externals)

        # Create Monty instance
        monty = pydantic_monty.Monty(
            self.monty_code,
            type_check=True,
            type_check_stubs=self.stubs,
            inputs=list(self.inputs.keys()),  # Pass list of input names
            external_functions=list(self.externals.keys()),  # Pass list of external function names
        )

        # Start execution (this will pause on first external call)
        monty_snapshot = monty.start(inputs=inputs)

        return Snapshot(monty_snapshot, self.source_map, externals)


def load(
    path: str | Path,
    limits: dict[str, Any] | None = None,
    files: dict[str, str | bytes] | None = None,
    grail_dir: str | Path | None = ".grail",
) -> GrailScript:
    """
    Load and parse a .pym file.

    Args:
        path: Path to .pym file
        limits: Resource limits
        files: Virtual filesystem files
        grail_dir: Directory for artifacts (None disables)

    Returns:
        GrailScript instance

    Raises:
        FileNotFoundError: If file doesn't exist
        ParseError: If file has syntax errors
        CheckError: If declarations are malformed
    """
    path = Path(path)

    # Parse the file
    parse_result = parse_pym_file(path)

    # Run validation checks
    check_result = check_pym(parse_result)
    check_result.file = str(path)

    # Generate stubs
    stubs = generate_stubs(parse_result.externals, parse_result.inputs)

    # Generate Monty code
    monty_code, source_map = generate_monty_code(parse_result)

    # Setup grail_dir
    grail_dir_path = Path(grail_dir) if grail_dir else None

    # Write artifacts
    if grail_dir_path:
        artifacts = ArtifactsManager(grail_dir_path)
        artifacts.write_script_artifacts(
            path.stem, stubs, monty_code, check_result, parse_result.externals, parse_result.inputs
        )

    return GrailScript(
        path=path,
        externals=parse_result.externals,
        inputs=parse_result.inputs,
        monty_code=monty_code,
        stubs=stubs,
        source_map=source_map,
        source_lines=parse_result.source_lines,
        limits=limits,
        files=files,
        grail_dir=grail_dir_path,
    )


async def run(code: str, inputs: dict[str, Any] | None = None) -> Any:
    """
    Execute inline Monty code (escape hatch for simple cases).

    Args:
        code: Monty code to execute
        inputs: Input values

    Returns:
        Result of code execution
    """
    if pydantic_monty is None:
        raise RuntimeError("pydantic-monty not installed")

    inputs = inputs or {}

    monty = pydantic_monty.Monty(code, inputs=list(inputs.keys()))
    result = await pydantic_monty.run_monty_async(monty, inputs=inputs)
    return result
