"""Prompt templates for LLM-driven code generation."""

DEFAULT_PROMPT = """from grail import Input, external

# Inputs
task_description: str = Input(\"task_description\")

# Externals
@external
async def submit_result(summary: str, changed_files: list[str]) -> bool:
    ...

summary = "Task: " + task_description + ". Request: {task}"
await submit_result(summary=summary, changed_files=[])

result = dict(summary=summary)
result
"""
