"""Code generator - transforms .pym to Monty-compatible code."""

import ast
from grail._types import ParseResult, SourceMap
from grail.errors import GrailError


class GrailDeclarationStripper(ast.NodeTransformer):
    """
    AST transformer that removes grail-specific declarations.

    Removes:
    - from grail import ... statements
    - @external decorated function definitions
    - Input() assignment statements

    Preserves:
    - All executable code
    - from typing import ... statements
    """

    def __init__(self, externals: set[str], inputs: set[str]):
        self.externals = externals  # Set of external function names
        self.inputs = inputs  # Set of input variable names

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom | None:
        """Remove 'from grail import ...' statements."""
        if node.module == "grail":
            return None  # Remove this node
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef | None:
        """Remove @external function definitions."""
        if node.name in self.externals:
            return None  # Remove this node
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef | None:
        """Remove @external async function definitions."""
        if node.name in self.externals:
            return None
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AnnAssign | None:
        """Remove Input() assignment statements."""
        if isinstance(node.target, ast.Name) and node.target.id in self.inputs:
            return None
        return node


def build_source_map(transformed_ast: ast.Module, generated_code: str) -> SourceMap:
    """
    Build line number mapping between .pym and generated code.

    Args:
        transformed_ast: AST after stripping declarations
        generated_code: Generated Monty code

    Returns:
        SourceMap with line mappings
    """
    source_map = SourceMap()

    generated_ast = ast.parse(generated_code)

    for transformed_node, generated_node in zip(
        ast.walk(transformed_ast),
        ast.walk(generated_ast),
    ):
        transformed_lineno = getattr(transformed_node, "lineno", None)
        generated_lineno = getattr(generated_node, "lineno", None)
        if transformed_lineno is not None and generated_lineno is not None:
            source_map.add_mapping(
                pym_line=transformed_lineno,
                monty_line=generated_lineno,
            )

    return source_map


def generate_monty_code(parse_result: ParseResult) -> tuple[str, SourceMap]:
    """
    Generate Monty-compatible code from parsed .pym file.

    Args:
        parse_result: Result from parse_pym_file()

    Returns:
        Tuple of (monty_code, source_map)
    """
    # Get sets of names to remove
    external_names = set(parse_result.externals.keys())
    input_names = set(parse_result.inputs.keys())

    # Transform AST
    stripper = GrailDeclarationStripper(external_names, input_names)
    transformed = stripper.visit(parse_result.ast_module)

    # Fix missing locations after transformation
    ast.fix_missing_locations(transformed)

    # Generate code from transformed AST
    monty_code = ast.unparse(transformed)

    # Validate generated code is syntactically valid
    try:
        ast.parse(monty_code)
    except SyntaxError as exc:
        raise GrailError(
            f"Code generation produced invalid Python: {exc}. "
            "This is a bug in grail — please report it."
        )

    # Build source map
    source_map = build_source_map(transformed, monty_code)

    return monty_code, source_map
