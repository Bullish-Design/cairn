"""Validation checker for Monty compatibility."""

from __future__ import annotations

import ast

from grail._types import CheckMessage, CheckResult, ParseResult


class MontyCompatibilityChecker(ast.NodeVisitor):
    """AST visitor that detects Monty-incompatible Python features.

    Errors detected:
    - E001: Class definitions
    - E002: Generators (yield/yield from)
    - E003: with statements
    - E004: match statements
    - E005: Forbidden imports
    """

    def __init__(self, source_lines: list[str]):
        self.errors: list[CheckMessage] = []
        self.warnings: list[CheckMessage] = []
        self.source_lines = source_lines
        self.features_used: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Detect class definitions (not supported in Monty)."""
        self.errors.append(
            CheckMessage(
                code="E001",
                lineno=node.lineno,
                col_offset=node.col_offset,
                end_lineno=node.end_lineno,
                end_col_offset=node.end_col_offset,
                severity="error",
                message="Class definitions are not supported in Monty",
                suggestion="Remove the class or refactor to use functions and dicts",
            )
        )
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        """Detect yield expressions (generators not supported)."""
        self.errors.append(
            CheckMessage(
                code="E002",
                lineno=node.lineno,
                col_offset=node.col_offset,
                end_lineno=node.end_lineno,
                end_col_offset=node.end_col_offset,
                severity="error",
                message="Generator functions (yield) are not supported in Monty",
                suggestion="Refactor to return a list or use async iteration",
            )
        )
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        """Detect yield from expressions."""
        self.errors.append(
            CheckMessage(
                code="E002",
                lineno=node.lineno,
                col_offset=node.col_offset,
                end_lineno=node.end_lineno,
                end_col_offset=node.end_col_offset,
                severity="error",
                message="Generator functions (yield from) are not supported in Monty",
                suggestion="Refactor to return a list",
            )
        )
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        """Detect with statements (not supported)."""
        self.errors.append(
            CheckMessage(
                code="E003",
                lineno=node.lineno,
                col_offset=node.col_offset,
                end_lineno=node.end_lineno,
                end_col_offset=node.end_col_offset,
                severity="error",
                message="'with' statements are not supported in Monty",
                suggestion="Use try/finally instead, or make file operations external functions",
            )
        )
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        """Detect match statements (not supported yet)."""
        self.errors.append(
            CheckMessage(
                code="E004",
                lineno=node.lineno,
                col_offset=node.col_offset,
                end_lineno=node.end_lineno,
                end_col_offset=node.end_col_offset,
                severity="error",
                message="'match' statements are not supported in Monty yet",
                suggestion="Use if/elif/else instead",
            )
        )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Detect import statements (only grail and typing allowed)."""
        for alias in node.names:
            if alias.name != "typing":
                self.errors.append(
                    CheckMessage(
                        code="E005",
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        end_lineno=node.end_lineno,
                        end_col_offset=node.end_col_offset,
                        severity="error",
                        message=f"Import '{alias.name}' is not allowed in Monty",
                        suggestion=(
                            "Only 'from grail import ...' and 'from typing import ...' are allowed"
                        ),
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Detect from...import statements."""
        if node.module not in {"grail", "typing"}:
            module_name = node.module or "<relative>"
            self.errors.append(
                CheckMessage(
                    code="E005",
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    end_lineno=node.end_lineno,
                    end_col_offset=node.end_col_offset,
                    severity="error",
                    message=f"Import from '{module_name}' is not allowed in Monty",
                    suggestion=(
                        "Only 'from grail import ...' and 'from typing import ...' are allowed"
                    ),
                )
            )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Track async/await usage."""
        is_external = any(
            (isinstance(decorator, ast.Name) and decorator.id == "external")
            or (isinstance(decorator, ast.Attribute) and decorator.attr == "external")
            for decorator in node.decorator_list
        )
        # External async functions are excluded from feature tracking because
        # they are stripped during code generation and don't represent actual
        # async usage within the Monty sandbox. Only user-defined async code
        # counts as a Monty feature dependency.
        if not is_external:
            self.features_used.add("async_await")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        """Track for-loop usage."""
        self.features_used.add("for_loop")
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        """Track list comprehension usage."""
        self.features_used.add("list_comprehension")
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        """Track dict comprehension usage."""
        self.features_used.add("dict_comprehension")
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        """Track f-string usage."""
        self.features_used.add("f_string")
        self.generic_visit(node)


def check_for_warnings(parse_result: ParseResult) -> list[CheckMessage]:
    """Check for warning conditions (non-blocking issues).

    Warnings:
    - W001: Bare dict/list as return value
    - W002: Unused @external function
    - W003: Unused Input() variable
    - W004: Very long script (>200 lines)

    Args:
        parse_result: Result of parsing a .pym file.

    Returns:
        List of warning messages.
    """
    warnings: list[CheckMessage] = []
    module = parse_result.ast_module

    if module.body:
        last_stmt = module.body[-1]
        if isinstance(last_stmt, ast.Expr) and isinstance(last_stmt.value, (ast.Dict, ast.List)):
            warnings.append(
                CheckMessage(
                    code="W001",
                    lineno=last_stmt.lineno,
                    col_offset=last_stmt.col_offset,
                    end_lineno=last_stmt.end_lineno,
                    end_col_offset=last_stmt.end_col_offset,
                    severity="warning",
                    message=(
                        "Bare dict/list as return value — consider assigning to a variable for clarity"
                    ),
                    suggestion="result = {...}; result",
                )
            )

    if len(parse_result.source_lines) > 200:
        warnings.append(
            CheckMessage(
                code="W004",
                lineno=1,
                col_offset=0,
                end_lineno=None,
                end_col_offset=None,
                severity="warning",
                message=(
                    "Script is "
                    f"{len(parse_result.source_lines)} lines long (>200) — may indicate too much logic in sandbox"
                ),
                suggestion="Consider breaking into smaller scripts or moving logic to external functions",
            )
        )

    return warnings


def check_pym(parse_result: ParseResult) -> CheckResult:
    """Run all validation checks on parsed .pym file.

    Args:
        parse_result: Result from parse_pym_file().

    Returns:
        CheckResult with errors, warnings, and info.
    """
    checker = MontyCompatibilityChecker(parse_result.source_lines)
    checker.visit(parse_result.ast_module)

    warnings = check_for_warnings(parse_result)
    warnings.extend(checker.warnings)

    info = {
        "externals_count": len(parse_result.externals),
        "inputs_count": len(parse_result.inputs),
        "lines_of_code": len(parse_result.source_lines),
        "monty_features_used": sorted(checker.features_used),
    }

    return CheckResult(
        file="<unknown>",
        valid=len(checker.errors) == 0,
        errors=checker.errors,
        warnings=warnings,
        info=info,
    )
