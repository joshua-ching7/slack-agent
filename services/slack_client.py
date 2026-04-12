"""
Slack messaging utilities using Block Kit for rich formatting.

All public functions accept plain-text or Block Kit payloads and handle
Slack API error logging so callers don't need to think about it.
"""

import logging
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config

logger = logging.getLogger(__name__)

_client = WebClient(token=config.SLACK_BOT_TOKEN)


def send_message(
    channel: str,
    text: str,
    blocks: list[dict] | None = None,
) -> str | None:
    """
    Post a message to *channel*.  Returns the message timestamp on success,
    None on failure.
    """
    try:
        resp = _client.chat_postMessage(
            channel=channel,
            text=text,          # fallback for notifications / screen-readers
            blocks=blocks or [],
            unfurl_links=False,
        )
        return resp["ts"]
    except SlackApiError as exc:
        logger.error("Slack API error posting to %s: %s", channel, exc.response["error"])
        return None


# ── Block Kit builders ────────────────────────────────────────

def event_reminder_blocks(
    event_name: str,
    friendly_date: str,
    location: str,
    description: str,
    event_type: str,
    days_until: int,
    claude_message: str,
) -> list[dict[str, Any]]:
    """Build a rich Slack Block Kit message for an event reminder."""
    icon = {
        "social": "🎉",
        "professional": "💼",
        "meeting": "📋",
    }.get(event_type, "📅")

    if days_until == 0:
        urgency = "Today!"
    elif days_until == 1:
        urgency = "Tomorrow!"
    else:
        urgency = f"In {days_until} days"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{icon} {urgency} — {event_name}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": claude_message},
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*When*\n{friendly_date}"},
                {"type": "mrkdwn", "text": f"*Where*\n{location or 'TBD'}"},
            ],
        },
    ]

    if description:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*About*\n{description}"},
            }
        )

    # RSVP poll (emoji reactions act as the voting mechanism)
    blocks += [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Will you be there?*\nReact with :white_check_mark: for YES · :x: for NO",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Reminder sent by {config.CLUB_NAME} bot · {friendly_date}",
                }
            ],
        },
    ]

    return blocks


def reimbursement_alert_blocks(
    submitter_name: str,
    submitter_email: str,
    amount: float,
    category: str,
    event_name: str,
    description: str,
    receipt_attached: bool,
    row_number: int,
    timestamp: str,
    claude_summary: str,
) -> list[dict[str, Any]]:
    """Build a rich Slack Block Kit message for a new reimbursement request."""
    receipt_badge = ":white_check_mark: Attached" if receipt_attached else ":warning: Missing"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"💰 New Reimbursement Request — ${amount:.2f}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*AI Summary*\n{claude_summary}"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Submitted by*\n{submitter_name}"},
                {"type": "mrkdwn", "text": f"*Email*\n{submitter_email}"},
                {"type": "mrkdwn", "text": f"*Amount*\n${amount:.2f}"},
                {"type": "mrkdwn", "text": f"*Category*\n{category or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Event / Purpose*\n{event_name or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Receipt*\n{receipt_badge}"},
            ],
        },
    ]

    if description:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Details*\n{description}",
                },
            }
        )

    blocks += [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Next steps for officers:*\n"
                    "• React :white_check_mark: once submitted to ASUC\n"
                    "• React :hourglass_flowing_sand: if more info is needed\n"
                    "• React :x: if denied"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Sheet row #{row_number} · Submitted {timestamp} · "
                        f"Tracked by {config.CLUB_NAME} Finance Bot"
                    ),
                }
            ],
        },
    ]

    return blocks
