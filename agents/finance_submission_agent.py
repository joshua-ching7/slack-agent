"""
Finance Submission Agent — UC Berkeley Reimbursement System

Automates filling out and saving a draft reimbursement report on Berkeley's
online Reimbursement System (reimbursement.berkeley.edu) using a Claude
tool-use agentic loop backed by Playwright.

The agent always saves reports as DRAFTS — it never clicks "Submit these
expenses for Review".  A finance officer reviews each draft in the portal
and submits it themselves, keeping a human in the loop for final approval.

Berkeley Reimbursement System — 4-step workflow
────────────────────────────────────────────────
  1. Payee Info   — Home Department dropdown → Save and Continue
  2. Expenses     — Business Purpose, Date, Type, Amount, Remarks → Add Expense
                    → Save and Continue
  3. Totals       — Leave chartstring blank → Save and Continue
                    → dismiss "Uncharged Expenses" popup with OK
  4. Confirm &    — Upload receipts and backup (if receipt available)
     Submit         → Save this report, return to Main Menu  ← DRAFT, not submit

Report type mapping (from form category → portal section):
  travel / airfare / transportation  →  Travel
  food / catering / entertainment    →  Entertainment
  everything else                    →  Other Expenses

Receipt rules (per UC Berkeley policy):
  Travel        : required for airfare, car rental, lodging, conference reg
                  (any amount), or any single expense ≥ $75
  Entertainment : required if amount ≥ $75
  Other Expenses: ALWAYS required regardless of amount

Architecture
────────────
Claude acts as the "pilot": it receives reimbursement details and a
screenshot of the current page, then decides which browser action to take
next.  Playwright executes the action and returns the result — including a
fresh screenshot — which feeds back to Claude.  The loop continues until
Claude emits a final text summary or hits the iteration cap (30).

Extended thinking is intentionally NOT used in the tool-use loop; the API
does not support thinking alongside tool calls.
"""

import asyncio
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

# ── Report-type helpers ───────────────────────────────────────

_TRAVEL_KEYWORDS = {"travel", "airfare", "flight", "transportation", "lodging", "hotel", "car rental", "conference"}
_ENTERTAINMENT_KEYWORDS = {"food", "catering", "entertainment", "dining", "meal", "restaurant", "drinks"}


def _report_type(category: str) -> str:
    """Map a form submission category to Travel / Entertainment / Other Expenses."""
    cat = category.lower()
    if any(k in cat for k in _TRAVEL_KEYWORDS):
        return "Travel"
    if any(k in cat for k in _ENTERTAINMENT_KEYWORDS):
        return "Entertainment"
    return "Other Expenses"


def _receipt_required(report_type: str, amount: float) -> bool:
    """Return True if Berkeley policy requires a receipt for this submission."""
    if report_type == "Other Expenses":
        return True  # always required
    # Travel and Entertainment: required at $75+
    return amount >= 75.0


# ── Tool definitions (passed to Claude's tool-use API) ────────

_TOOLS: list[dict] = [
    {
        "name": "screenshot",
        "description": (
            "Take a screenshot of the current browser page. Returns a base64 PNG image. "
            "Use this whenever you are unsure what is on screen."
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
            "Log in to Berkeley CalNet SSO. Call this after navigating to the portal "
            "and being redirected to login.berkeley.edu. Handles Duo 2FA automatically "
            "(waits up to 60 s for push approval)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "click",
        "description": (
            "Click an element. Provide either 'selector' (CSS) or 'text' (visible label), "
            "not both."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the element",
                },
                "text": {
                    "type": "string",
                    "description": "Visible text of the element to click",
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
                "value": {"type": "string", "description": "Text to type"},
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
            "Use this on the Upload Receipts page after clicking 'Upload receipts and backup'. "
            "The file must already exist on disk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the <input type='file'>",
                },
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
            "Use this to read form labels, error messages, dropdown options, or "
            "confirmation text."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "wait_for_text",
        "description": "Wait until specific text appears on the page (e.g. a success message).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to wait for"},
                "timeout_ms": {
                    "type": "integer",
                    "description": "Max wait in milliseconds (default: 15000)",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "submit_form",
        "description": "Click the form's primary submit / save button.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the button (default: button[type='submit'])",
                },
            },
            "required": [],
        },
    },
]


# ── Receipt file resolution ───────────────────────────────────

def _find_receipt(submitter_name: str, row_number: int) -> str | None:
    """
    Look in ASUC_RECEIPTS_DIR for a receipt matching this request.

    Naming convention (finance officers must follow this when saving files):
      <row_number>_<lastname>.<ext>   e.g.  42_smith.pdf
      <row_number>.<ext>              e.g.  42.pdf        (fallback)

    Accepted extensions: pdf, png, jpg, jpeg.
    """
    receipts_dir = Path(config.ASUC_RECEIPTS_DIR)
    if not receipts_dir.exists():
        return None

    last_name = submitter_name.split()[-1].lower() if submitter_name else ""
    for ext in ("pdf", "PDF", "png", "PNG", "jpg", "JPG", "jpeg", "JPEG"):
        for name in (f"{row_number}_{last_name}.{ext}", f"{row_number}.{ext}"):
            candidate = receipts_dir / name
            if candidate.exists():
                return str(candidate.resolve())
    return None


# ── Claude tool dispatch ──────────────────────────────────────

async def _execute_tool(
    sub: ASUCSubmitter, tool_name: str, tool_input: dict
) -> dict[str, Any]:
    """Route a Claude tool call to the appropriate ASUCSubmitter method."""
    dispatch: dict[str, Any] = {
        "screenshot":    lambda: sub.screenshot(),
        "navigate":      lambda: sub.navigate(tool_input["url"]),
        "login_calnet":  lambda: sub.login_calnet(),
        "click":         lambda: sub.click(
                             selector=tool_input.get("selector"),
                             text=tool_input.get("text"),
                         ),
        "fill":          lambda: sub.fill(
                             selector=tool_input["selector"],
                             value=tool_input["value"],
                             clear_first=tool_input.get("clear_first", True),
                         ),
        "select_option": lambda: sub.select_option(
                             selector=tool_input["selector"],
                             value=tool_input["value"],
                         ),
        "upload_file":   lambda: sub.upload_file(
                             selector=tool_input["selector"],
                             file_path=tool_input["file_path"],
                         ),
        "get_page_text": lambda: sub.get_page_text(),
        "wait_for_text": lambda: sub.wait_for_text(
                             text=tool_input["text"],
                             timeout_ms=tool_input.get("timeout_ms", 15_000),
                         ),
        "submit_form":   lambda: sub.submit_form(
                             selector=tool_input.get("selector", 'button[type="submit"]'),
                         ),
    }

    fn = dispatch.get(tool_name)
    if fn is None:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}
    return await fn()


# ── Message content builders ──────────────────────────────────

def _screenshot_content(b64: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }


def _tool_result_content(tool_use_id: str, result: dict) -> dict:
    """Build a tool_result content block, embedding any screenshot as an image."""
    screenshot_b64 = result.pop("screenshot_b64", None)
    content: list[dict] = [
        {"type": "text", "text": json.dumps(result)},
    ]
    if screenshot_b64:
        content.append(_screenshot_content(screenshot_b64))
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


# ── Main agentic loop ─────────────────────────────────────────

async def _run_submission_loop(
    req: ReimbursementRequest,
    receipt_path: str | None,
    report_type: str,
    receipt_required: bool,
) -> tuple[bool, str]:
    """
    Run the Claude + Playwright agentic loop to create a draft reimbursement
    report on the Berkeley Reimbursement System.

    Returns (success: bool, summary: str).
    """
    # Truncate fields to portal limits
    business_purpose = (
        f"{req.event_name or req.category}: {req.description}"
    )[:200]
    remarks = req.description[:75] if req.description else req.category[:75]

    receipt_note = (
        f"Receipt file on disk: {receipt_path}"
        if receipt_path
        else (
            "NO RECEIPT FILE FOUND on disk. "
            + ("Still create the draft; note the missing receipt in your summary."
               if not receipt_required
               else "Still create the draft; a finance officer must upload the receipt manually before submitting.")
        )
    )

    system_prompt = f"""You are an AI agent filling out a reimbursement report on UC Berkeley's
online Reimbursement System on behalf of {config.ASUC_ORG_NAME}.

IMPORTANT: You are creating a DRAFT only. On the final page you MUST click
"Save this report, return to Main Menu" — do NOT click "Submit these expenses
for Review". A finance officer will review and submit the draft themselves.

━━━ Reimbursement details ━━━
  Submitter name  : {req.submitter_name}
  Submitter email : {req.submitter_email}
  Amount          : ${req.amount:.2f}
  Category        : {req.category}
  Report type     : {report_type}
  Event / Purpose : {req.event_name or req.category}
  Description     : {req.description}
  Business Purpose (use exactly, max 200 chars): {business_purpose}
  Remarks (use exactly, max 75 chars)          : {remarks}
  Expense date    : {req.timestamp[:10]}
  {receipt_note}

━━━ Portal info ━━━
  URL             : {config.ASUC_PORTAL_URL}
  Home Department : {config.ASUC_HOME_DEPARTMENT}

━━━ Exact step-by-step instructions ━━━

STEP 1 — Log in
  1a. navigate() to the portal URL.
  1b. If the page redirects to login.berkeley.edu, call login_calnet().
  1c. You should land on the Reimbursement System home page, which shows
      three sections: Travel | Entertainment | Other Expenses.

STEP 2 — Create a new report
  2a. Under the "{report_type}" section, click the
      "Create New {report_type} Report" link (or equivalent label).
  2b. A preliminary information page will appear. Read it, then click
      "Let's Begin" at the bottom.

STEP 3 — Payee Info page
  3a. The Payee Information section (Vendor ID, Name, Payment Method, Email)
      is pre-filled and read-only — do NOT attempt to edit it.
  3b. In the Approver Info section, open the "Home Department" dropdown and
      select "{config.ASUC_HOME_DEPARTMENT}".
      If you cannot find that exact label, call get_page_text() to read the
      available options and pick the closest match.
  3c. Click "Save and Continue".

STEP 4 — Expenses page
  4a. Fill in the Business Purpose field (textarea) with exactly:
      "{business_purpose}"
  4b. Click the calendar/date button and select the expense date: {req.timestamp[:10]}.
  4c. Select the Expense Type from the dropdown (choose the option that best
      matches "{req.category}").
  4d. Enter the Amount: {req.amount:.2f}
  4e. Fill the Remarks field with exactly: "{remarks}"
  4f. Click "Add Expense".
  4g. Click "Save and Continue".

STEP 5 — Totals page
  5a. Leave ALL chartstring fields blank (BU/Business Unit, Account, Fund,
      Department, Function) — the finance officer will fill these.
  5b. Click "Save and Continue".
  5c. An "Uncharged Expenses" popup will appear. Click "OK" to continue.

STEP 6 — Confirm and Submit page  (SAVE AS DRAFT — do NOT submit)
  6a. If a receipt file is available ({receipt_path or 'none'}):
      - Click "Upload receipts and backup".
      - On the Upload Receipts page, click "Choose Files" and use
        upload_file() with the file input selector and path:
        {receipt_path or 'N/A'}
      - Click the "Upload" button.
      - Wait for the text "successfully uploaded" to appear.
      - Click the "Click here" link to return to the Confirm and Submit page.
  6b. Click "Save this report, return to Main Menu".
      *** DO NOT click "Submit these expenses for Review" ***
  6c. Confirm you are back on the main menu / home page.

━━━ General rules ━━━
- Call screenshot() whenever you are unsure what is on screen.
- Call get_page_text() to read labels, error messages, or dropdown options.
- If a required field shows a validation error, correct it and continue.
- If you hit an unrecoverable error (login failure, page not found, etc.),
  stop and report the exact error in your summary.
- When finished (draft saved or unrecoverable failure), output a concise
  plain-text summary of what happened.
"""

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Create a draft {report_type} reimbursement report for "
                f"{req.submitter_name} (${req.amount:.2f}, {req.category}). "
                f"Receipt available: {'yes — ' + receipt_path if receipt_path else 'no'}. "
                "Follow the step-by-step instructions exactly. Begin now."
            ),
        }
    ]

    async with ASUCSubmitter() as sub:
        max_iterations = 40
        for _ in range(max_iterations):
            response = _claude.messages.create(
                model="claude-opus-4-6",
                max_tokens=2048,
                # NOTE: extended thinking is incompatible with tool use —
                # do not add a thinking parameter here.
                system=system_prompt,
                tools=_TOOLS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        return True, block.text.strip()
                return True, "Draft saved (no text summary returned)."

            if response.stop_reason != "tool_use":
                return False, f"Unexpected stop_reason: {response.stop_reason}"

            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                logger.info("Tool call: %s(%s)", block.name, block.input)
                result = await _execute_tool(sub, block.name, dict(block.input))
                if not result.get("success", True):
                    logger.warning("Tool '%s' failed: %s", block.name, result.get("error"))
                tool_results.append(_tool_result_content(block.id, result))

            messages.append({"role": "user", "content": tool_results})

        return False, f"Hit max iterations ({max_iterations}) without completing."


# ── Public API ────────────────────────────────────────────────

def submit_reimbursement(req: ReimbursementRequest) -> None:
    """
    Create a draft reimbursement report on the Berkeley Reimbursement System
    for one request.  Posts progress and result updates to SLACK_FINANCE_CHANNEL.

    The report is always saved as a draft — never auto-submitted — so a
    finance officer can review it before it goes to BRS.

    Safe to call from a synchronous context (runs the async loop internally).
    """
    if not config.ASUC_PORTAL_URL:
        logger.error("ASUC_PORTAL_URL not configured — skipping.")
        return
    if not config.ASUC_CALNET_USERNAME or not config.ASUC_CALNET_PASSWORD:
        logger.error("ASUC CalNet credentials not configured — skipping.")
        return
    if not config.ASUC_HOME_DEPARTMENT:
        logger.error("ASUC_HOME_DEPARTMENT not configured — skipping.")
        return

    rtype = _report_type(req.category)
    rec_required = _receipt_required(rtype, req.amount)
    receipt_path = _find_receipt(req.submitter_name, req.row_number)

    # Post "starting" notice to Slack
    slack_client.send_message(
        channel=config.SLACK_FINANCE_CHANNEL,
        text=(
            f"[Finance Bot] Drafting {rtype} reimbursement for "
            f"{req.submitter_name} (${req.amount:.2f})…"
        ),
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":robot_face: *Creating draft reimbursement report…*\n"
                        f"*Requester:* {req.submitter_name}\n"
                        f"*Amount:* ${req.amount:.2f}\n"
                        f"*Report type:* {rtype}\n"
                        f"*Purpose:* {req.event_name or req.category}\n"
                        f"*Receipt:* {'✅ found' if receipt_path else '⚠️ not found'}"
                    ),
                },
            }
        ],
    )

    if rec_required and not receipt_path:
        logger.warning(
            "Receipt required (row %d, %s, $%.2f) but not found in %s. "
            "Draft will still be created; officer must upload receipt before submitting.",
            req.row_number, req.submitter_name, req.amount, config.ASUC_RECEIPTS_DIR,
        )

    try:
        success, summary = asyncio.run(
            _run_submission_loop(req, receipt_path, rtype, rec_required)
        )
    except Exception as exc:
        success = False
        summary = f"Unhandled exception: {exc}"
        logger.exception("finance_submission_agent crashed for row %d", req.row_number)

    # Post result to Slack
    icon = ":white_check_mark:" if success else ":x:"
    status = "Draft saved — ready for review" if success else "Draft creation FAILED"

    receipt_status = (
        "✅ uploaded"
        if receipt_path
        else ("⚠️ missing — upload before submitting" if rec_required else "not required")
    )

    slack_client.send_message(
        channel=config.SLACK_FINANCE_CHANNEL,
        text=f"[Finance Bot] {status} — {req.submitter_name} ${req.amount:.2f}",
        blocks=[
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{icon} {status}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Requester*\n{req.submitter_name}"},
                    {"type": "mrkdwn", "text": f"*Amount*\n${req.amount:.2f}"},
                    {"type": "mrkdwn", "text": f"*Report type*\n{rtype}"},
                    {"type": "mrkdwn", "text": f"*Receipt*\n{receipt_status}"},
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
                            f"Log in to {config.ASUC_PORTAL_URL} to review and submit."
                        ),
                    }
                ],
            },
        ],
    )
