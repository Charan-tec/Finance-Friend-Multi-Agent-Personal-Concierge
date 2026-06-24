import re
import json
import sys
import os
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"  # Force Gemini API key mode
from typing import AsyncGenerator

from google.adk.workflow import Workflow, START, node
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.genai import types
from mcp import StdioServerParameters

from .config import config

# Create MCP Toolset to connect to our local MCP Server
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
        ),
    ),
)

# 1. Specialized LlmAgent sub-agents with MCP tools wired in
expense_tracker = LlmAgent(
    name="expense_tracker",
    model=config.model,
    instruction="""You are a Personal Expense Tracker.
    You help users log, categorize, and track expenses.
    Use the wired-in MCP tools to get account balances, check transactions, or add new transactions.
    Analyze transaction details and output clear, categorized expense summaries.
    For example: 'Logged $15 for Lunch under Food.'""",
    tools=[mcp_toolset],
    description="Logs and tracks user expenses."
)

savings_advisor = LlmAgent(
    name="savings_advisor",
    model=config.model,
    instruction="""You are a Savings Advisor.
    You suggest personalized saving strategies and track savings goals.
    Use the wired-in MCP tools to check savings goals or update savings goal targets.
    Provide realistic suggestions on how to save money based on budget targets.
    Always give specific suggestions based on the user's situation.""",
    tools=[mcp_toolset],
    description="Advises on savings strategies and goals."
)

# 2. Orchestrator LlmAgent
orchestrator = LlmAgent(
    name="orchestrator",
    model=config.model,
    instruction="""You are the Personal Finance Orchestrator.
    You route queries to specialized sub-agents:
    - Use the expense_tracker to track or log expenses.
    - Use the savings_advisor to get saving tips or modify savings goals.
    Synthesize the final response from the sub-agents and present it to the user.
    If the user's intent is to log an expense or set/modify a savings goal, include keywords like 'set savings goal' or 'log expense' in your response to trigger the approval gate.""",
    tools=[AgentTool(expense_tracker), AgentTool(savings_advisor)],
    description="Orchestrates personal finance operations."
)

# 3. Security Checkpoint Function Node (Phase 4 requirements)
def security_checkpoint(ctx: Context, node_input: types.Content):
    text = ""
    if node_input and node_input.parts:
        text = "".join(part.text for part in node_input.parts if part.text)
    
    # PII Scrubbing
    scrubbed_text = text
    if config.pii_redaction_enabled:
        # Credit Card
        scrubbed_text = re.sub(r'\b(?:\d[ -]??){13,16}\b', '[REDACTED CARD]', scrubbed_text)
        # Account Number / IBAN
        scrubbed_text = re.sub(r'\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b', '[REDACTED IBAN]', scrubbed_text)
        scrubbed_text = re.sub(r'\b\d{9,18}\b', '[REDACTED ACCOUNT]', scrubbed_text)

    # Prompt Injection Detection
    injection_keywords = ["ignore previous instructions", "override config", "system prompt", "bypass security"]
    detected_injection = False
    if config.injection_detection_enabled:
        for kw in injection_keywords:
            if kw in text.lower():
                detected_injection = True
                break

    # Domain-specific rule: Prevent transaction request > $10,000
    large_transaction = False
    amounts = re.findall(r'\$\s*(\d+(?:,\d{3})*(?:\.\d{2})?)', text)
    for amt_str in amounts:
        try:
            val = float(amt_str.replace(',', ''))
            if val > 10000.0:
                large_transaction = True
        except ValueError:
            pass

    # Audit Log
    audit_log = {
        "session_id": ctx.session.id,
        "timestamp": str(ctx.run_id),
        "severity": "INFO",
        "pii_scrubbed": scrubbed_text != text,
        "injection_detected": detected_injection,
        "large_transaction_blocked": large_transaction
    }

    if detected_injection:
        audit_log["severity"] = "CRITICAL"
        print(json.dumps(audit_log))
        return Event(
            output="Security Alert: Potential prompt injection detected. Request blocked.",
            route="SECURITY_EVENT"
        )
    
    if large_transaction:
        audit_log["severity"] = "WARNING"
        print(json.dumps(audit_log))
        return Event(
            output="Policy Alert: Transactions exceeding $10,000 require manual review. Request blocked.",
            route="SECURITY_EVENT"
        )

    print(json.dumps(audit_log))
    # Pass clean input
    return Event(
        output=types.Content(role='user', parts=[types.Part.from_text(text=scrubbed_text)]),
        route="safe"
    )

def security_violation_handler(node_input: str):
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=node_input)]))
    yield Event(output=node_input)

# 4. Human-in-the-Loop Approval Gate Function Node
async def hitl_approval_gate(ctx: Context, node_input: str) -> AsyncGenerator[Event, None]:
    text = node_input

    # Detect if user requested to log an expense or modify a goal
    needs_confirm = "set savings goal" in text.lower() or "new savings goal" in text.lower() or "log expense" in text.lower() or "proposed savings goal" in text.lower()
    
    if needs_confirm:
        if not ctx.resume_inputs or "confirm_action" not in ctx.resume_inputs:
            # Render message to user and yield RequestInput
            yield RequestInput(
                interrupt_id="confirm_action",
                message="✋ Action Required: Please confirm to apply this change (yes/no):"
            )
            return
        
        user_response = ctx.resume_inputs["confirm_action"].strip().lower()
        if user_response in ["yes", "y", "confirm", "approve"]:
            yield Event(
                content=types.Content(role='model', parts=[types.Part.from_text(text=f"✅ Approved! The following change was applied:\n\n{text}")]),
                state={"needs_approval": False}
            )
            yield Event(output=f"Approved: {text}")
        else:
            yield Event(
                content=types.Content(role='model', parts=[types.Part.from_text(text="❌ Cancelled: The change was not applied.")]),
                state={"needs_approval": False}
            )
            yield Event(output="Action cancelled by user.")
    else:
        # Just return the text response
        yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=text)]))
        yield Event(output=text)

# 5. Graph Definition
root_agent = Workflow(
    name="finance_friend_workflow",
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"safe": orchestrator, "SECURITY_EVENT": security_violation_handler}),
        (orchestrator, hitl_approval_gate),
    ]
)

# App wrapping with resumability enabled
app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True)
)
