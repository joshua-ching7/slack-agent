"""
Google Calendar service wrapper.

Returns ClubEvent objects for all events starting within the next N days.
Requires a service account JSON key whose email has been given at least
"See all event details" (Viewer) access to the target calendar.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


@dataclass
class ClubEvent:
    id: str
    name: str
    description: str
    location: str
    event_type: str          # e.g. "social", "professional", "meeting"
    start: datetime
    end: datetime

    @property
    def days_until(self) -> int:
        now = datetime.now(timezone.utc)
        delta = self.start.replace(tzinfo=timezone.utc) - now
        return max(0, delta.days)

    @property
    def friendly_date(self) -> str:
        return self.start.strftime("%A, %B %-d at %-I:%M %p")


def _build_service():
    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def get_upcoming_events(days_ahead: int = 14) -> list[ClubEvent]:
    """Return all calendar events starting within the next *days_ahead* days."""
    try:
        service = _build_service()
        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        result = (
            service.events()
            .list(
                calendarId=config.GOOGLE_CALENDAR_ID,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events: list[ClubEvent] = []
        for item in result.get("items", []):
            start_raw = item["start"].get("dateTime") or item["start"].get("date")
            end_raw = item["end"].get("dateTime") or item["end"].get("date")

            # Parse ISO strings; fall back gracefully for all-day events
            try:
                start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue

            # Derive event type from the calendar category/colorId or description keywords
            description = item.get("description", "")
            event_type = _infer_type(item.get("summary", ""), description)

            events.append(
                ClubEvent(
                    id=item["id"],
                    name=item.get("summary", "Untitled Event"),
                    description=description,
                    location=item.get("location", "TBD"),
                    event_type=event_type,
                    start=start_dt,
                    end=end_dt,
                )
            )
        logger.info("Fetched %d upcoming events from Google Calendar.", len(events))
        return events

    except Exception as exc:
        logger.error("Failed to fetch Google Calendar events: %s", exc)
        return []


def _infer_type(name: str, description: str) -> str:
    """Heuristically classify an event based on name/description keywords."""
    text = (name + " " + description).lower()
    if any(w in text for w in ("professional", "career", "networking", "workshop", "speaker")):
        return "professional"
    if any(w in text for w in ("social", "party", "game", "fun", "hangout", "bbq", "picnic")):
        return "social"
    if any(w in text for w in ("meeting", "general", "board", "officer", "gbm")):
        return "meeting"
    return "event"
