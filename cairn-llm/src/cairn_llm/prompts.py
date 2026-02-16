"""Prompt templates for LLM-driven code generation."""

DEFAULT_PROMPT = """from grail import Input, external

# Inputs
task_description: str = Input(\"task_description\")

# Externals
@external
async def submit_summary(summary: str) -> None:
    ...

summary = \"Task: \" + task_description + \". Request: {task}\"

await submit_summary(summary=summary)

result = dict(summary=summary)
result
"""
