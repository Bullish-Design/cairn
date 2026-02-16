"""Mock data and external function implementations."""

from typing import Any


async def get_team_members(department: str) -> dict[str, Any]:
    """Mock implementation of get_team_members."""
    return {
        "members": [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 3, "name": "Charlie"},
        ]
    }


async def get_expenses(user_id: int) -> dict[str, Any]:
    """Mock implementation of get_expenses."""
    expenses = {
        1: [{"amount": 3000}, {"amount": 2500}],
        2: [{"amount": 1000}],
        3: [{"amount": 4000}, {"amount": 2000}],
    }

    return {"items": expenses.get(user_id, [])}


async def get_custom_budget(user_id: int) -> dict[str, Any] | None:
    """Mock implementation of get_custom_budget."""
    # Alice has a custom budget.
    if user_id == 1:
        return {"limit": 6000.0}
    return None
