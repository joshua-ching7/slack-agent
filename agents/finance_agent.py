"""
Finance / Reimbursement Agent

Polls the Google Sheet that collects your club's reimbursement form responses.
For each new submission, Claude generates a concise summary and action checklist
that is posted to the finance officers' Slack channel.

Officers can react to the Slack message with emoji to mark status:
  ✅  = submitted to ASUC
  ⏳  = more information needed
  ❌  = denied

State is tracked so submissions are never double-posted.
"""

import logging

import anthropic

import config
import state
from services.google_sheets import ReimbursementRequest, get_new_reimbursements
from services import slack_client

logger = logging.getLogger(__name__)

_claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


# ── Claude summary generation ─────────────────────────────────

def _generate_reimbursement_summary(req: ReimbursementRequest) -> str:
    """
    Ask Claude to produce a short, actionable summary of the reimbursement
    request that helps officers quickly understand what to do.
    """
    receipt_status = "is attached" if req.receipt_attached else "has NOT been attached"

    prompt = f"""You are the treasurer's assistant for {config.CLUB_NAME}.
A new ASUC reimbursement request has been submitted. Write a 2-3 sentence
plain-text summary (no markdown) for the finance officers that:
1. Describes what the money was spent on and why.
2. Flags any missing information or concerns (e.g. missing receipt).
3. States clearly whether this appears ready to submit to ASUC.

Request details:
- Submitted by: {req.submitter_name} ({req.submitter_email})
- Amount: ${req.amount:.2f}
- Category: {req.category or "Not specified"}
- Event / Purpose: {req.event_name or "Not specified"}
- Description: {req.description or "No description provided."}
- Receipt: {receipt_status}

Be direct and professional. Do not include a greeting or sign-off.
"""

    try:
        response = _claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=256,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "text":
                return block.text.strip()
        return _fallback_summary(req)
    except Exception as exc:
        logger.warning("Claude summary generation failed: %s", exc)
        return _fallback_summary(req)


def _fallback_summary(req: ReimbursementRequest) -> str:
    receipt = "Receipt attached." if req.receipt_attached else "Receipt NOT attached — follow up required."
    return (
        f"{req.submitter_name} is requesting ${req.amount:.2f} for "
        f"{req.event_name or req.category or 'club expenses'}. "
        f"{receipt}"
    )


# ── Core agent logic ──────────────────────────────────────────

def check_new_reimbursements() -> None:
    """
    Main entry point called by the scheduler.

    Reads any new rows from the reimbursement Google Sheet, asks Claude for
    a summary of each one, and posts to the finance Slack channel.
    Updates state so each submission is only ever posted once.
    """
    logger.info("Finance Agent: checking for new reimbursement submissions...")

    last_row = state.get_last_reimbursement_row()
    new_requests = get_new_reimbursements(last_row)

    if not new_requests:
        logger.info("Finance Agent: no new submissions.")
        return

    posted = 0
    for req in new_requests:
        logger.info(
            "Processing reimbursement row %d: %s $%.2f",
            req.row_number,
            req.submitter_name,
            req.amount,
        )

        summary = _generate_reimbursement_summary(req)

        blocks = slack_client.reimbursement_alert_blocks(
            submitter_name=req.submitter_name,
            submitter_email=req.submitter_email,
            amount=req.amount,
            category=req.category,
            event_name=req.event_name,
            description=req.description,
            receipt_attached=req.receipt_attached,
            row_number=req.row_number,
            timestamp=req.timestamp,
            claude_summary=summary,
        )

        fallback = (
            f"[Finance] New reimbursement: {req.submitter_name} — "
            f"${req.amount:.2f} for {req.event_name or req.category}"
        )

        ts = slack_client.send_message(
            channel=config.SLACK_FINANCE_CHANNEL,
            text=fallback,
            blocks=blocks,
        )

        if ts:
            posted += 1
            logger.info(
                "Posted reimbursement alert for row %d (ts=%s).", req.row_number, ts
            )

        # Always advance the pointer even if Slack post failed (avoids infinite
        # re-posting of a broken message — the sheet row still exists)
        state.set_last_reimbursement_row(req.row_number)

    logger.info("Finance Agent: posted %d alert(s) this run.", posted)
