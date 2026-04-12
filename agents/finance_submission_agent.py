"""
Finance Submission Agent — ASUC Reimbursement Portal

Automates the end-to-end submission of a club reimbursement request to the
ASUC finance portal using a Claude tool-use agentic loop backed by Playwright.

Architecture
────────────
Claude acts as the "pilot": it receives the reimbursement details plus a
screenshot of the current browser state, then decides which browser action
(click / fill / upload / navigate …) to take next.  The Playwright
ASUCSubmitter executes the action and returns the result — including a fresh
screenshot — which is fed back to Claude for the next step.  The loop runs
until Claude emits a final text response declaring success or failure.

Tools exposed to Claude
────────────────────────
  screenshot       — see the current browser state
  navigate         — go to a URL
  login_calnet     — complete CalNet SSO + Duo 2FA
  click            — click an element by CSS selector or visible text
  fill             — type into an input / textarea
  select_option    — pick from a <select> dropdown
  upload_file      — set a file input to a local receipt path
  get_page_text    — read all visible text (for confirmation screens)
  wait_for_text    — wait until a string appears on the page
  submit_form      — click the form submit button

Triggering a submission
────────────────────────
Call `submit_reimbursement(req)` with a `ReimbursementRequest` dataclass.
This is typically done:
  • From a Slack slash command / reaction handler (when an officer marks a
    request "ready to submit"), or
  • Automatically by the Finance Agent for requests that pass all checks
    (receipt attached, amount within auto-approval threshold, etc.).

The agent posts progress updates and a final confirmation message to the
finance officers' Slack channel throughout the process.
"""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

import anthropic

import config
from services.google_sheets import ReimbursementRequest
from services.asuc_submitter import ASUCSubmitter
from services import slack_client

logger = logging.getLogger(__name__)

_claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

# ── Tool definitions (passed to Claude's tool-use API) ────────

_TOOLS: list[dict] = [
    {
        "name": "screenshot",
        "description": (
            "Take a screenshot of the current browser page. Returns a base64 PNG image. "
            "Use this to understand the current state of the ASUC portal before taking action."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "navigate",
        "description": "Navigate the browser to a specific URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to navigate to"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "login_calnet",
        "description": (
            "Log in to Berkeley CalNet SSO. Call this after navigating to the ASUC portal "
            "and being redirected to login.berkeley.edu. Handles Duo 2FA automatically."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "click",
        "description": "Click an element. Provide either a CSS selector or visible text (not both).",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the element (e.g. '#submit-btn', '.nav-link')",
                },
                "text": {
                    "type": "string",
                    "description": "Visible text of the element to click (e.g. 'Submit Request')",
                },
            },
            "required": [],
        },
    },
    {
        "name": "fill",
        "description": "Type a value into an input field or textarea identified by CSS selector.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the field"},
                "value": {"type": "string", "description": "Text to enter"},
                "clear_first": {
                    "type": "boolean",
                    "description": "Clear the field before typing (default: true)",
                },
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "select_option",
        "description": "Choose an option from a <select> dropdown by its value or visible label.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the <select>"},
                "value": {"type": "string", "description": "Option value or label to select"},
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "upload_file",
        "description": (
            "Attach a local file to a file-input element. "
            "The file must already exist in the receipts directory. "
            "Use this to upload receipts to the reimbursement form."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the <input type='file'>"},
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the receipt file on disk",
                },
            },
            "required": ["selector", "file_path"],
        },
    },
    {
        "name": "get_page_text",
        "description": (
            "Read all visible text on the current page (up to 8,000 characters). "
            "Use this to check for confirmation messages, error messages, or to understand "
            "the form fields that need to be filled."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wait_for_text",
        "description": (
            "Wait until specific text appears on the page (e.g. a success confirmation). "
            "Times out after 15 seconds by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to wait for"},
                "timeout_ms": {
                    "type": "integer",
                    "description": "Max wait time in milliseconds (default: 15000)",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "submit_form",
        "description": "Click the form's submit button to submit the current form.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the submit button (default: button[type='submit'])",
                },
            },
            "required": [],
        },
    },
]


# ── Receipt file resolution ───────────────────────────────────

def _find_receipt(submitter_name: str, row_number: int) -> str | None:
    """
    Look in ASUC_RECEIPTS_DIR for a receipt file matching this request.

    Convention: the file should be named  <row_number>_<LastName>.<ext>
    or simply <row_number>.<ext>.  Accepted extensions: pdf, png, jpg, jpeg.

    Finance officers should download receipts from the form responses
    (Google Drive link in the sheet) and place them here before triggering
    auto-submission.
    """
    receipts_dir = Path(config.ASUC_RECEIPTS_DIR)
    if not receipts_dir.exists():
        return None

    last_name = submitter_name.split()[-1].lower() if submitter_name else ""
    for ext in ("pdf", "PDF", "png", "PNG", "jpg", "JPG", "jpeg", "JPEG"):
        # Try <row>_<lastname>.<ext>
        candidate = receipts_dir / f"{row_number}_{last_name}.{ext}"
        if candidate.exists():
            return str(candidate.resolve())
        # Try <row>.<ext>
        candidate = receipts_dir / f"{row_number}.{ext}"
        if candidate.exists():
            return str(candidate.resolve())

    return None


# ── Claude tool dispatch ──────────────────────────────────────

async def _execute_tool(
    sub: ASUCSubmitter, tool_name: str, tool_input: dict
) -> dict[str, Any]:
    """Route a Claude tool call to the appropriate ASUCSubmitter method."""
    dispatch = {
        "screenshot": lambda: sub.screenshot(),
        "navigate": lambda: sub.navigate(tool_input["url"]),
        "login_calnet": lambda: sub.login_calnet(),
        "click": lambda: sub.click(
            selector=tool_input.get("selector"),
            text=tool_input.get("text"),
        ),
        "fill": lambda: sub.fill(
            selector=tool_input["selector"],
            value=tool_input["value"],
            clear_first=tool_input.get("clear_first", True),
        ),
        "select_option": lambda: sub.select_option(
            selector=tool_input["selector"],
            value=tool_input["value"],
        ),
        "upload_file": lambda: sub.upload_file(
            selector=tool_input["selector"],
            file_path=tool_input["file_path"],
        ),
        "get_page_text": lambda: sub.get_page_text(),
        "wait_for_text": lambda: sub.wait_for_text(
            text=tool_input["text"],
            timeout_ms=tool_input.get("timeout_ms", 15_000),
        ),
        "submit_form": lambda: sub.submit_form(
            selector=tool_input.get("selector", 'button[type="submit"]'),
        ),
    }

    fn = dispatch.get(tool_name)
    if fn is None:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}
    return await fn()


# ── Message content builders ──────────────────────────────────

def _screenshot_content(b64: str) -> dict:
    """Build a Claude image content block from a base64 PNG."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": b64,
        },
    }


def _tool_result_content(tool_use_id: str, result: dict) -> dict:
    """Build a tool_result content block for the Claude messages list."""
    # If the result includes a screenshot, embed it as an image
    content: list[dict] = []
    screenshot_b64 = result.pop("screenshot_b64", None)

    # Always include the JSON summary
    content.append({
        "type": "text",
        "text": json.dumps({k: v for k, v in result.items() if k != "screenshot_b64"}),
    })
    if screenshot_b64:
        content.append(_screenshot_content(screenshot_b64))

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }


# ── Main agentic loop ─────────────────────────────────────────

async def _run_submission_loop(
    req: ReimbursementRequest,
    receipt_path: str | None,
) -> tuple[bool, str]:
    """
    Run the Claude + Playwright agentic loop to submit the reimbursement.

    Returns (success: bool, summary: str).
    """
    system_prompt = f"""You are an AI agent submitting a club reimbursement request to the ASUC
finance portal on behalf of {config.ASUC_ORG_NAME} at UC Berkeley.

You have access to a real web browser controlled via Playwright tools.  Use them
to navigate the portal, fill out the form, upload the receipt, and submit the request.

Reimbursement details to submit:
  Submitter name   : {req.submitter_name}
  Submitter email  : {req.submitter_email}
  Amount           : ${req.amount:.2f}
  Category         : {req.category}
  Event / Purpose  : {req.event_name}
  Description      : {req.description}
  Receipt file     : {receipt_path or "NOT AVAILABLE — note this in the form description"}
  Submitted on     : {req.timestamp}

Portal URL: {config.ASUC_PORTAL_URL}
Organisation: {config.ASUC_ORG_NAME}

Step-by-step plan:
1. navigate() to the ASUC portal URL.
2. If redirected to login.berkeley.edu, call login_calnet().
3. Find and navigate to the reimbursement / expense request section.
4. Create a new reimbursement request and fill in all the fields.
5. upload_file() the receipt if one is available.
6. Review the form, then submit_form().
7. wait_for_text() for a success/confirmation message.
8. Take a final screenshot() and report success.

Rules:
- Call screenshot() whenever you are unsure what is on screen.
- Call get_page_text() to read form labels, error messages, or confirmation text.
- If you hit an error, try to recover; if you cannot, stop and report the issue clearly.
- Do NOT submit if the receipt is missing and the amount is over $50 — leave a note instead.
- When done (success or unrecoverable failure), output a concise plain-text summary.
"""

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Please submit this reimbursement request to the ASUC portal. "
                f"Submitter: {req.submitter_name}, Amount: ${req.amount:.2f}, "
                f"Purpose: {req.event_name or req.category}. "
                f"Receipt path: {receipt_path or 'none'}. Start now."
            ),
        }
    ]

    async with ASUCSubmitter() as sub:
        max_iterations = 30  # safety cap
        for iteration in range(max_iterations):
            response = _claude.messages.create(
                model="claude-opus-4-6",
                max_tokens=2048,
                thinking={"type": "adaptive"},
                system=system_prompt,
                tools=_TOOLS,
                messages=messages,
            )

            # Append assistant message
            messages.append({"role": "assistant", "content": response.content})

            # Check stop reason
            if response.stop_reason == "end_turn":
                # Claude is done — extract the final text summary
                for block in response.content:
                    if block.type == "text":
                        return True, block.text.strip()
                return True, "Submission completed (no text summary)."

            if response.stop_reason != "tool_use":
                return False, f"Unexpected stop_reason: {response.stop_reason}"

            # Execute all tool calls and collect results
            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                logger.info("Claude calling tool: %s(%s)", block.name, block.input)
                result = await _execute_tool(sub, block.name, dict(block.input))

                if not result.get("success", True):
                    logger.warning("Tool '%s' failed: %s", block.name, result.get("error"))

                tool_results.append(_tool_result_content(block.id, result))

            messages.append({"role": "user", "content": tool_results})

        return False, f"Submission loop hit max iterations ({max_iterations}) without completing."


# ── Public API ────────────────────────────────────────────────

def submit_reimbursement(req: ReimbursementRequest) -> None:
    """
    Orchestrate the full ASUC submission for one reimbursement request.

    Posts progress updates and a final result to SLACK_FINANCE_CHANNEL.
    Safe to call from a synchronous context (runs the async loop internally).
    """
    if not config.ASUC_PORTAL_URL:
        logger.error("ASUC_PORTAL_URL not configured — skipping submission.")
        return
    if not config.ASUC_CALNET_USERNAME or not config.ASUC_CALNET_PASSWORD:
        logger.error("ASUC CalNet credentials not configured — skipping submission.")
        return

    # Notify the channel that submission is starting
    slack_client.send_message(
        channel=config.SLACK_FINANCE_CHANNEL,
        text=(
            f"[Finance Bot] Starting ASUC portal submission for "
            f"{req.submitter_name} (${req.amount:.2f})…"
        ),
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":robot_face: *Submitting to ASUC portal…*\n"
                        f"*Requester:* {req.submitter_name}\n"
                        f"*Amount:* ${req.amount:.2f}\n"
                        f"*Purpose:* {req.event_name or req.category}"
                    ),
                },
            }
        ],
    )

    receipt_path = _find_receipt(req.submitter_name, req.row_number)
    if not receipt_path:
        logger.warning(
            "No receipt file found for row %d (%s). Continuing without upload.",
            req.row_number,
            req.submitter_name,
        )

    try:
        success, summary = asyncio.run(_run_submission_loop(req, receipt_path))
    except Exception as exc:
        success = False
        summary = f"Unhandled exception during submission: {exc}"
        logger.exception("finance_submission_agent crashed for row %d", req.row_number)

    # Report result to Slack
    icon = ":white_check_mark:" if success else ":x:"
    status = "Submission successful" if success else "Submission FAILED"

    slack_client.send_message(
        channel=config.SLACK_FINANCE_CHANNEL,
        text=f"[Finance Bot] {status} — {req.submitter_name} ${req.amount:.2f}",
        blocks=[
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{icon} ASUC Submission — {status}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Requester*\n{req.submitter_name}"},
                    {"type": "mrkdwn", "text": f"*Amount*\n${req.amount:.2f}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Agent summary:*\n{summary}"},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Sheet row #{req.row_number} · "
                            f"Receipt: {'✅ uploaded' if receipt_path else '⚠️ not found'}"
                        ),
                    }
                ],
            },
        ],
    )
