import sys
import datetime
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("finance-friend")

# Mock database state in memory
BALANCES = {
    "checking": 2450.75,
    "savings": 12800.50,
    "credit_card": -320.40
}

TRANSACTIONS = [
    {"date": "2026-06-23", "amount": -15.40, "category": "Food", "description": "Lunch at Deli", "account": "checking"},
    {"date": "2026-06-22", "amount": -120.00, "category": "Utilities", "description": "Electric Bill", "account": "checking"},
    {"date": "2026-06-20", "amount": 1500.00, "category": "Salary", "description": "Direct Deposit", "account": "checking"},
    {"date": "2026-06-19", "amount": -45.00, "category": "Shopping", "description": "Books", "account": "checking"},
]

SAVINGS_GOALS = {
    "Emergency Fund": {"target": 10000.0, "current": 8000.0},
    "New Laptop": {"target": 1500.0, "current": 1200.0},
    "Vacation": {"target": 3000.0, "current": 1500.0}
}

@mcp.tool()
def get_balance(account_type: str) -> str:
    """Get the current balance for checking, savings, or credit_card accounts.

    Args:
        account_type: One of 'checking', 'savings', or 'credit_card'.
    """
    acc = account_type.lower().strip()
    if acc not in BALANCES:
        return f"Account '{account_type}' not found. Available: checking, savings, credit_card."
    return f"Balance for {acc}: ${BALANCES[acc]:,.2f}"

@mcp.tool()
def get_transactions(account_type: str, limit: int = 5) -> str:
    """Retrieve the recent transactions for checking, savings, or credit_card accounts.

    Args:
        account_type: One of 'checking', 'savings', or 'credit_card'.
        limit: Max number of recent transactions to return.
    """
    acc = account_type.lower().strip()
    filtered = [tx for tx in TRANSACTIONS if tx["account"] == acc]
    if not filtered:
        return f"No recent transactions found for '{account_type}'."
    
    lines = [f"Recent transactions for {acc}:"]
    for tx in filtered[:limit]:
        lines.append(f"- {tx['date']} | {tx['category']} | {tx['description']} | ${tx['amount']:.2f}")
    return "\n".join(lines)

@mcp.tool()
def add_transaction(amount: float, category: str, description: str, account_type: str = "checking") -> str:
    """Log/add a new transaction to the expense database.

    Args:
        amount: The transaction amount (negative for expenses, positive for income).
        category: The spending category (e.g. Food, Utilities, Salary, Shopping).
        description: Description of the transaction.
        account_type: One of 'checking', 'savings', or 'credit_card'.
    """
    acc = account_type.lower().strip()
    if acc not in BALANCES:
        return f"Invalid account: {account_type}."
    
    date_str = datetime.date.today().isoformat()
    new_tx = {
        "date": date_str,
        "amount": amount,
        "category": category,
        "description": description,
        "account": acc
    }
    TRANSACTIONS.insert(0, new_tx)
    BALANCES[acc] = round(BALANCES[acc] + amount, 2)
    return f"Logged transaction successfully: ${amount:+.2f} for {category} ({description}) in {acc}. New balance: ${BALANCES[acc]:,.2f}"

@mcp.tool()
def get_savings_goals() -> str:
    """Retrieve all active savings goals and the current progress."""
    lines = ["Active Savings Goals:"]
    for goal, data in SAVINGS_GOALS.items():
        pct = (data["current"] / data["target"]) * 100
        lines.append(f"- {goal}: ${data['current']:,.2f} of ${data['target']:,.2f} ({pct:.1f}%)")
    return "\n".join(lines)

@mcp.tool()
def update_savings_goal(goal_name: str, target_amount: float) -> str:
    """Create or update a savings goal target.

    Args:
        goal_name: The name of the savings goal (e.g. Emergency Fund, Vacation).
        target_amount: The target amount for the goal.
    """
    if goal_name in SAVINGS_GOALS:
        old_target = SAVINGS_GOALS[goal_name]["target"]
        SAVINGS_GOALS[goal_name]["target"] = target_amount
        return f"Updated goal '{goal_name}' target from ${old_target:,.2f} to ${target_amount:,.2f}."
    else:
        SAVINGS_GOALS[goal_name] = {"target": target_amount, "current": 0.0}
        return f"Created new savings goal '{goal_name}' with target ${target_amount:,.2f}."

if __name__ == "__main__":
    mcp.run("stdio")
