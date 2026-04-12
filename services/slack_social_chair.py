"""
Social Chair notification service.

Looks up the social chair (or any officer) by their Slack profile title and
sends them a DM with a Claude-drafted event announcement for personalisation
before it goes out to the full #events channel.

How it works
─────────────
1. The event agent calls `notify_social_chair()` instead of posting directly.
2. We page through the Slack Users API looking for anyone whose profile "title"
   contains the configured keyword (default: "social chair").
3. We open a DM and send a rich Block Kit message with:
     • The raw Claude draft they can copy-paste or edit
     • A one-click "Post to #events" reminder (they do this manually — no
       interactive webhook needed)
4. State is saved so the social chair is only notified once per event/threshold.

Why no interactive button?
───────────────────────────
Slack interactive components (approve/reject buttons that do something) require
an HTTPS endpoint to receive the callback.  Adding a full web server just for
this one feature is overkill for a club bot.  The DM message is clear enough:
the social chair edits the text and posts it themselves.  A future enhancement
could add a Flask/FastAPI endpoint and use `actions` blocks.
"""

import logging
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config

logger = logging.getLogger(__name__)

_client = WebClient(token=config.SLACK_BOT_TOKEN)


# ── User lookup ───────────────────────────────────────────────

def find_officer_by_title(title_keyword: str) -> Optional[dict]:
    """
    Page through the workspace's user list and return the first non-bot,
    non-deleted member whose profile 'title' contains *title_keyword*
    (case-insensitive).  Returns None if no match is found.
    """
    try:
        cursor = None
        while True:
            kwargs: dict = {"limit": 200}
            if cursor:
                kwargs["cursor"] = cursor

            resp = _client.users_list(**kwargs)

            for member in resp.get("members", []):
                if member.get("deleted") or member.get("is_bot") or member.get("is_app_user"):
                    continue
                title: str = member.get("profile", {}).get("title", "")
                if title_keyword.lower() in title.lower():
                    logger.info(
                        "Found officer '%s' (id=%s) with title '%s'.",
                        member.get("real_name", "?"),
                        member["id"],
                        title,
                    )
                    return member

            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        logger.warning(
            "No Slack user found with title containing '%s'.", title_keyword
        )
        return None

    except SlackApiError as exc:
        logger.error("Slack users.list error: %s", exc.response["error"])
        return None


def find_social_chair() -> Optional[dict]:
    """Return the social chair Slack user dict, or None."""
    return find_officer_by_title(config.SOCIAL_CHAIR_TITLE_KEYWORD)


# ── DM helpers ────────────────────────────────────────────────

def _open_dm(user_id: str) -> Optional[str]:
    """Open (or retrieve) a DM channel with user_id.  Returns channel ID."""
    try:
        resp = _client.conversations_open(users=[user_id])
        return resp["channel"]["id"]
    except SlackApiError as exc:
        logger.error("Could not open DM with %s: %s", user_id, exc.response["error"])
        return None


def notify_social_chair(
    event_name: str,
    event_date: str,
    event_location: str,
    event_type: str,
    days_until: int,
    claude_draft: str,
) -> bool:
    """
    DM the social chair with a Claude-generated draft announcement for the
    given event, asking them to personalise and post it.

    Returns True if the DM was sent successfully.
    """
    chair = find_social_chair()
    if not chair:
        logger.warning(
            "Social chair not found; falling back to direct post for '%s'.", event_name
        )
        return False

    dm_channel = _open_dm(chair["id"])
    if not dm_channel:
        return False

    chair_name = chair.get("profile", {}).get("first_name") or chair.get("real_name", "hey")

    urgency = {0: "TODAY", 1: "tomorrow"}.get(days_until, f"in {days_until} days")

    icon = {"social": "🎉", "professional": "💼", "meeting": "📋"}.get(event_type, "📅")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{icon} Draft announcement ready — {event_name}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Hey {chair_name}! :wave:\n\n"
                    f"I've drafted an announcement for *{event_name}* happening *{urgency}*. "
                    f"Feel free to personalise it, then post it to *{config.SLACK_EVENTS_CHANNEL}* "
                    f"when you're happy with it."
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":pencil: *Draft message (copy, edit, and post):*",
            },
        },
        {
            # Show the draft in a code block so it's easy to copy
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"```{claude_draft}```",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Event*\n{event_name}"},
                {"type": "mrkdwn", "text": f"*When*\n{event_date}"},
                {"type": "mrkdwn", "text": f"*Where*\n{event_location or 'TBD'}"},
                {"type": "mrkdwn", "text": f"*Post to*\n{config.SLACK_EVENTS_CHANNEL}"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Generated by {config.CLUB_NAME} Bot · "
                        "Edit freely before posting!"
                    ),
                }
            ],
        },
    ]

    try:
        _client.chat_postMessage(
            channel=dm_channel,
            text=(
                f"[{config.CLUB_NAME}] Draft announcement for {event_name} — "
                "review and post when ready!"
            ),
            blocks=blocks,
        )
        logger.info(
            "Notified social chair %s (id=%s) for event '%s'.",
            chair.get("real_name"),
            chair["id"],
            event_name,
        )
        return True

    except SlackApiError as exc:
        logger.error(
            "Failed to DM social chair for '%s': %s", event_name, exc.response["error"]
        )
        return False
