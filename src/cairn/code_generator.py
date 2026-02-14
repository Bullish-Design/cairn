"""LLM-based code generation for Cairn agents.

This module generates Python code for agent tasks using LLM,
with prompt templates that constrain agents to use only external functions.
"""

import re
from typing import Optional

import llm


class CodeGenerator:
    """Generates agent code using LLM."""

    PROMPT_TEMPLATE = """Write a short Python script to accomplish this task:
{task}

Available functions (the ONLY things you can call):
- read_file(path: str) -> str
- write_file(path: str, content: str) -> bool
- list_dir(path: str) -> list[str]
- file_exists(path: str) -> bool
- search_files(pattern: str) -> list[str]
- search_content(pattern: str, path: str = ".") -> list[dict]
- ask_llm(prompt: str, context: str = "") -> str
- submit_result(summary: str, changed_files: list[str]) -> bool
- log(message: str) -> bool

Constraints:
- You CANNOT: import anything, define classes, use open(), use print()
- Write simple procedural Python: variables, functions, loops, conditionals only
- Always call submit_result() at the end with summary and list of changed files
- Use log() to debug

Respond with ONLY the Python code. No markdown, no explanation."""

    def __init__(self, model: Optional[str] = None):
        """Initialize code generator.

        Args:
            model: LLM model name (uses default if None)
        """
        if model:
            self.model = llm.get_model(model)
        else:
            self.model = llm.get_default_model()

    async def generate(self, task: str) -> str:
        """Generate Python code for agent task.

        Args:
            task: Task description

        Returns:
            Python code as string

        Raises:
            RuntimeError: If code generation fails
        """
        prompt = self.PROMPT_TEMPLATE.format(task=task)

        try:
            response = self.model.prompt(prompt)
            code = response.text()
            return self.extract_code(code)
        except Exception as e:
            raise RuntimeError(f"Code generation failed: {str(e)}")

    def extract_code(self, response: str) -> str:
        """Extract code from LLM response.

        Removes markdown fences and other formatting.

        Args:
            response: Raw LLM response

        Returns:
            Cleaned Python code
        """
        lines = response.strip().split("\n")

        # Remove markdown code fences
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]

        code = "\n".join(lines)

        # Remove leading/trailing whitespace
        code = code.strip()

        return code

    def validate_code(self, code: str) -> tuple[bool, Optional[str]]:
        """Validate generated code.

        Checks for:
        - Valid Python syntax
        - No import statements
        - No forbidden builtins (open, eval, exec)

        Args:
            code: Python code to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check syntax
        try:
            compile(code, "<string>", "exec")
        except SyntaxError as e:
            return False, f"Syntax error: {str(e)}"

        # Check for forbidden patterns
        forbidden_patterns = [
            (r"\bimport\b", "Import statements not allowed"),
            (r"\bfrom\b.*\bimport\b", "Import statements not allowed"),
            (r"\bopen\s*\(", "open() not allowed - use read_file/write_file"),
            (r"\beval\s*\(", "eval() not allowed"),
            (r"\bexec\s*\(", "exec() not allowed"),
            (r"\b__import__\s*\(", "__import__() not allowed"),
        ]

        for pattern, message in forbidden_patterns:
            if re.search(pattern, code):
                return False, message

        # Check for required submit_result call
        if "submit_result(" not in code:
            return False, "Code must call submit_result() at the end"

        return True, None
