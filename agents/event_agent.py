"""
Event Reminder Agent

Polls Google Calendar (or a Google Sheet) for upcoming club events and either:

  • DMs the social chair with a Claude-drafted announcement for them to
    personalise and post (for social events — controlled by SOCIAL_CHAIR_NOTIFY
    and SOCIAL_CHAIR_EVENT_TYPES), or
  • Posts the reminder directly to the events Slack channel (all other events,
    or when the social chair cannot be found).

Claude generates naturally-worded, tone-matched messages for each event type.
"""

import logging
from datetime import datetime, timezone

import anthropic

import config
import state
from services.google_calendar import ClubEvent, get_upcoming_events
from services.google_sheets import get_events_from_sheet
from services import slack_client
from services.slack_social_chair import notify_social_chair

logger = logging.getLogger(__name__)

_claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


# ── Claude message generation ─────────────────────────────────

def _generate_reminder_message(event: ClubEvent, days_until: int) -> str:
    """
    Ask Claude to write an engaging, appropriately-toned reminder message
    for the given event.  Returns plain-text (Slack mrkdwn) suitable for
    embedding in a Block Kit section.
    """
    urgency_phrase = {
        0: "TODAY",
        1: "TOMORROW",
        3: "this week",
        7: "next week",
    }.get(days_until, f"in {days_until} days")

    prompt = f"""You are a friendly community manager for {config.CLUB_NAME}.
Write a short, engaging Slack reminder (2-4 sentences, Slack mrkdwn format) for the
following upcoming {event.event_type} event.

Event details:
- Name: {event.name}
- When: {event.friendly_date} ({urgency_phrase})
- Where: {event.location}
- Description: {event.description or "No additional details provided."}

Guidelines:
- Match the tone to the event type: casual/excited for social events,
  professional/informative for professional events, matter-of-fact for meetings.
- Mention the timing prominently (e.g. "TODAY", "tomorrow", etc.).
- Keep it under 60 words.
- Do NOT include the event name in the first word — start with a hook.
- Use 1-2 relevant emoji naturally (not at the start of every sentence).
- Do not add a sign-off or signature.
"""

    try:
        response = _claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            thinking={"type": "enabled", "budget_tokens": 512},
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract text from the response (skip thinking blocks)
        for block in response.content:
            if block.type == "text":
                return block.text.strip()
        return f"Don't miss *{event.name}* — {urgency_phrase}! 📅"
    except Exception as exc:
        logger.warning("Claude reminder generation failed: %s", exc)
        return f"Reminder: *{event.name}* is coming up {urgency_phrase}! 📅"


# ── Core agent logic ──────────────────────────────────────────

def _get_events() -> list[ClubEvent]:
    if config.EVENT_SOURCE == "sheets":
        return get_events_from_sheet()
    return get_upcoming_events(days_ahead=max(config.REMINDER_DAYS_BEFORE) + 1)


def check_and_send_reminders() -> None:
    """
    Main entry point called by the scheduler.

    For each upcoming event, checks whether any configured reminder intervals
    are due (based on days_until) and haven't been sent yet, then posts to
    the events Slack channel.
    """
    logger.info("Event Agent: checking for reminders to send...")
    events = _get_events()

    if not events:
        logger.info("No upcoming events found.")
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    reminders_sent = 0

    for event in events:
        days_until = event.days_until

        for threshold in config.REMINDER_DAYS_BEFORE:
            if days_until != threshold:
                continue

            if state.reminder_already_sent(event.id, threshold):
                logger.debug(
                    "Reminder already sent for '%s' at %d-day threshold.", event.name, threshold
                )
                continue

            # Generate a Claude-powered message
            message_text = _generate_reminder_message(event, days_until)

            # ── Route: social chair DM or direct post ──────────
            routed_to_chair = False
            should_notify_chair = (
                config.SOCIAL_CHAIR_NOTIFY
                and (
                    "all" in config.SOCIAL_CHAIR_EVENT_TYPES
                    or event.event_type in config.SOCIAL_CHAIR_EVENT_TYPES
                )
            )

            if should_notify_chair:
                routed_to_chair = notify_social_chair(
                    event_name=event.name,
                    event_date=event.friendly_date,
                    event_location=event.location,
                    event_type=event.event_type,
                    days_until=days_until,
                    claude_draft=message_text,
                )

            if not routed_to_chair:
                # Post directly to the events channel (non-social events, or
                # if the social chair could not be found in Slack).
                blocks = slack_client.event_reminder_blocks(
                    event_name=event.name,
                    friendly_date=event.friendly_date,
                    location=event.location,
                    description=event.description,
                    event_type=event.event_type,
                    days_until=days_until,
                    claude_message=message_text,
                )
                fallback = (
                    f"[{config.CLUB_NAME}] Reminder: {event.name} on {event.friendly_date}"
                )
                ts = slack_client.send_message(
                    channel=config.SLACK_EVENTS_CHANNEL,
                    text=fallback,
                    blocks=blocks,
                )
                sent_ok = ts is not None
            else:
                sent_ok = True  # DM counts as handled

            if sent_ok:
                state.mark_reminder_sent(event.id, threshold, now_iso)
                reminders_sent += 1
                route = "social chair DM" if routed_to_chair else "direct post"
                logger.info(
                    "Handled %d-day reminder for '%s' via %s.",
                    threshold, event.name, route,
                )

    logger.info("Event Agent: sent %d reminder(s) this run.", reminders_sent)
