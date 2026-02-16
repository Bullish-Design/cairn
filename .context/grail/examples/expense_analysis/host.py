"""Host file for expense analysis example."""

import asyncio
from grail import load
from data import get_team_members, get_expenses, get_custom_budget


async def main():
    # Load the script
    script = load("analysis.pym")

    # Check for errors
    check_result = script.check()
    if not check_result.valid:
        print("Script has errors:")
        for error in check_result.errors:
            print(f"  Line {error.lineno}: {error.message}")
        return

    # Run the analysis
    result = await script.run(
        inputs={"budget_limit": 5000.0, "department": "Engineering"},
        externals={
            "get_team_members": get_team_members,
            "get_expenses": get_expenses,
            "get_custom_budget": get_custom_budget,
        },
    )

    print(f"Analyzed {result['analyzed']} team members")
    print(f"Found {result['over_budget_count']} over budget")

    if result["details"]:
        print("\nDetails:")
        for item in result["details"]:
            print(f"  {item['name']}: ${item['total']:.2f} (over by ${item['over_by']:.2f})")


if __name__ == "__main__":
    asyncio.run(main())
