from __future__ import annotations

import json

from pydantic import BaseModel

from grail import MontyContext


class InputModel(BaseModel):
    a: int
    b: int


class OutputModel(BaseModel):
    total: int


def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    ctx = MontyContext(InputModel, output_model=OutputModel, tools=[add])
    result = ctx.execute("{'total': add(inputs['a'], inputs['b'])}", {"a": 20, "b": 22})
    print(json.dumps(result.model_dump(mode="python"), sort_keys=True))
