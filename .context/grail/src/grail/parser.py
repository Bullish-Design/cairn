"""Parser for .pym files - extracts externals and inputs from AST."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from grail._types import ExternalSpec, InputSpec, ParamSpec, ParseResult
from grail.errors import CheckError, ParseError


def get_type_annotation_str(node: ast.expr | None) -> str:
    """Convert AST type annotation node to string.

    Args:
        node: AST annotation node.

    Returns:
        String representation of type (e.g., "int", "dict[str, Any]").

    Raises:
        CheckError: If annotation is missing or invalid.
    """
    if node is None:
        raise CheckError("Missing type annotation")

    return ast.unparse(node)


def extract_function_params(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ParamSpec]:
    """Extract parameter specifications from function definition.

    Args:
        func_node: Function definition AST node.

    Returns:
        List of parameter specifications.

    Raises:
        CheckError: If parameters lack type annotations.
    """
    params: list[ParamSpec] = []
    args = func_node.args.args

    for index, arg in enumerate(args):
        if arg.arg == "self":
            continue

        if arg.annotation is None:
            raise CheckError(
                f"Parameter '{arg.arg}' in function '{func_node.name}' missing type annotation",
                lineno=func_node.lineno,
            )

        default = None
        num_defaults = len(func_node.args.defaults)
        num_args = len(args)

        if index >= num_args - num_defaults:
            default_index = index - (num_args - num_defaults)
            default_node = func_node.args.defaults[default_index]
            try:
                default = ast.literal_eval(default_node)
            except (ValueError, TypeError):
                default = ast.unparse(default_node)

        params.append(
            ParamSpec(
                name=arg.arg,
                type_annotation=get_type_annotation_str(arg.annotation),
                default=default,
            )
        )

    return params


def validate_external_function(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> None:
    """Validate that external function meets requirements.

    Requirements:
        - Complete type annotations on all parameters
        - Return type annotation
        - Body is single Ellipsis statement (optionally preceded by docstring)

    Args:
        func_node: Function definition to validate.

    Raises:
        CheckError: If validation fails.
    """
    if func_node.returns is None:
        raise CheckError(
            f"Function '{func_node.name}' missing return type annotation",
            lineno=func_node.lineno,
        )

    # Skip optional docstring (first statement if it's a string constant)
    body_start_idx = 0
    if (
        len(func_node.body) > 0
        and isinstance(func_node.body[0], ast.Expr)
        and isinstance(func_node.body[0].value, ast.Constant)
        and isinstance(func_node.body[0].value.value, str)
    ):
        body_start_idx = 1

    remaining_body = func_node.body[body_start_idx:]

    if len(remaining_body) != 1:
        raise CheckError(
            f"External function '{func_node.name}' body must be single '...' (Ellipsis)",
            lineno=func_node.lineno,
        )

    body_stmt = remaining_body[0]

    if not isinstance(body_stmt, ast.Expr):
        raise CheckError(
            f"External function '{func_node.name}' body must be '...' (Ellipsis)",
            lineno=func_node.lineno,
        )

    if not isinstance(body_stmt.value, ast.Constant) or body_stmt.value.value is not Ellipsis:
        raise CheckError(
            f"External function '{func_node.name}' body must be '...' (Ellipsis), not actual code",
            lineno=func_node.lineno,
        )


def extract_externals(module: ast.Module) -> dict[str, ExternalSpec]:
    """Extract external function specifications from AST.

    Looks for functions decorated with @external.

    Args:
        module: Parsed AST module.

    Returns:
        Dictionary mapping function names to ExternalSpec.

    Raises:
        CheckError: If external declarations are malformed.
    """
    externals: dict[str, ExternalSpec] = {}

    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        has_external = False
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "external":
                has_external = True
                break
            if isinstance(decorator, ast.Attribute) and decorator.attr == "external":
                has_external = True
                break

        if not has_external:
            continue

        validate_external_function(node)
        params = extract_function_params(node)
        docstring = ast.get_docstring(node)

        externals[node.name] = ExternalSpec(
            name=node.name,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            parameters=params,
            return_type=get_type_annotation_str(node.returns),
            docstring=docstring,
            lineno=node.lineno,
            col_offset=node.col_offset,
        )

    return externals


def extract_inputs(module: ast.Module) -> dict[str, InputSpec]:
    """Extract input specifications from AST.

    Looks for assignments like: x: int = Input("x").

    Args:
        module: Parsed AST module.

    Returns:
        Dictionary mapping input names to InputSpec.

    Raises:
        CheckError: If input declarations are malformed.
    """
    inputs: dict[str, InputSpec] = {}

    for node in module.body:
        # Check annotated assignments (x: int = Input("x"))
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.value, ast.Call):
                continue

            is_input_call = False
            if isinstance(node.value.func, ast.Name) and node.value.func.id == "Input":
                is_input_call = True
            elif isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "Input":
                is_input_call = True

            if not is_input_call:
                continue

            if node.annotation is None:
                raise CheckError("Input() call must have type annotation", lineno=node.lineno)

            if not isinstance(node.target, ast.Name):
                raise CheckError(
                    "Input() must be assigned to a simple variable name",
                    lineno=node.lineno,
                )

            var_name = node.target.id

            if not node.value.args:
                raise CheckError(
                    f"Input() call for '{var_name}' missing name argument",
                    lineno=node.lineno,
                )

            default = None
            for keyword in node.value.keywords:
                if keyword.arg == "default":
                    try:
                        default = ast.literal_eval(keyword.value)
                    except (ValueError, TypeError):
                        default = ast.unparse(keyword.value)
                    break

            inputs[var_name] = InputSpec(
                name=var_name,
                type_annotation=get_type_annotation_str(node.annotation),
                default=default,
                required=default is None,
                lineno=node.lineno,
                col_offset=node.col_offset,
            )

        # Check non-annotated assignments (x = Input("x")) and raise error
        elif isinstance(node, ast.Assign):
            if not isinstance(node.value, ast.Call):
                continue

            is_input_call = False
            if isinstance(node.value.func, ast.Name) and node.value.func.id == "Input":
                is_input_call = True
            elif isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "Input":
                is_input_call = True

            if is_input_call:
                raise CheckError(
                    "Input() call must have type annotation",
                    lineno=node.lineno,
                )

    return inputs


def parse_pym_file(path: Path) -> ParseResult:
    """Parse a .pym file and extract metadata.

    Args:
        path: Path to .pym file.

    Returns:
        ParseResult with externals, inputs, AST, and source lines.

    Raises:
        FileNotFoundError: If file doesn't exist.
        ParseError: If file has syntax errors.
        CheckError: If declarations are malformed.
    """
    if not path.exists():
        raise FileNotFoundError(f".pym file not found: {path}")

    source = path.read_text()
    source_lines = source.splitlines()

    try:
        module = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ParseError(exc.msg, lineno=exc.lineno, col_offset=exc.offset) from exc

    externals = extract_externals(module)
    inputs = extract_inputs(module)

    return ParseResult(
        externals=externals,
        inputs=inputs,
        ast_module=module,
        source_lines=source_lines,
    )


def parse_pym_content(content: str, filename: str = "<string>") -> ParseResult:
    """Parse .pym content from string (useful for testing).

    Args:
        content: .pym file content.
        filename: Optional filename for error messages.

    Returns:
        ParseResult.

    Raises:
        ParseError: If content has syntax errors.
        CheckError: If declarations are malformed.
    """
    source_lines = content.splitlines()

    try:
        module = ast.parse(content, filename=filename)
    except SyntaxError as exc:
        raise ParseError(exc.msg, lineno=exc.lineno, col_offset=exc.offset) from exc

    externals = extract_externals(module)
    inputs = extract_inputs(module)

    return ParseResult(
        externals=externals,
        inputs=inputs,
        ast_module=module,
        source_lines=source_lines,
    )
