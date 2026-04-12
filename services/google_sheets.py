"""
Google Sheets service wrapper — used for two purposes:

1. Reading a "social calendar" sheet that acts as an event database
   (when EVENT_SOURCE=sheets).

2. Reading Google Form reimbursement responses (always active for the
   Finance Agent).

Requires a service account JSON key with at least Viewer access on the
target spreadsheets.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config
from services.google_calendar import ClubEvent

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


@dataclass
class ReimbursementRequest:
    row_number: int           # 1-based sheet row (for state tracking)
    timestamp: str
    submitter_name: str
    submitter_email: str
    amount: float
    category: str
    event_name: str
    description: str
    receipt_attached: bool


def _build_service():
    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _safe_get(row: list, index: int, default: str = "") -> str:
    try:
        return str(row[index]).strip()
    except IndexError:
        return default


# ── Social Calendar Sheet ─────────────────────────────────────

def get_events_from_sheet() -> list[ClubEvent]:
    """
    Read events from a Google Sheet acting as a social calendar.

    Expected columns (configured via GOOGLE_SOCIAL_SHEET_RANGE):
      A: Event Name
      B: Date       (YYYY-MM-DD)
      C: Time       (HH:MM, 24h)
      D: Location
      E: Description
      F: Type       (social / professional / meeting)
    """
    try:
        service = _build_service()
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=config.GOOGLE_SOCIAL_SHEET_ID,
                range=config.GOOGLE_SOCIAL_SHEET_RANGE,
            )
            .execute()
        )
        rows = result.get("values", [])
        events: list[ClubEvent] = []
        now = datetime.now(timezone.utc)

        for i, row in enumerate(rows):
            name = _safe_get(row, 0)
            date_str = _safe_get(row, 1)
            time_str = _safe_get(row, 2, "00:00")
            location = _safe_get(row, 3, "TBD")
            description = _safe_get(row, 4)
            event_type = _safe_get(row, 5, "event").lower()

            if not name or not date_str:
                continue

            try:
                dt_str = f"{date_str} {time_str}"
                start_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                logger.warning("Row %d: could not parse date '%s %s'", i + 2, date_str, time_str)
                continue

            if start_dt < now:
                continue  # skip past events

            # Use a stable row-based ID since sheets don't have UUIDs
            event_id = f"sheet-row-{i + 2}-{date_str}"
            events.append(
                ClubEvent(
                    id=event_id,
                    name=name,
                    description=description,
                    location=location,
                    event_type=event_type,
                    start=start_dt,
                    end=start_dt,  # end time not tracked in simple sheet format
                )
            )

        logger.info("Fetched %d upcoming events from Google Sheet.", len(events))
        return events

    except Exception as exc:
        logger.error("Failed to fetch events from Google Sheet: %s", exc)
        return []


# ── Reimbursement Form Responses ──────────────────────────────

def get_new_reimbursements(last_processed_row: int) -> list[ReimbursementRequest]:
    """
    Fetch reimbursement form responses newer than *last_processed_row*.

    Row numbering is 1-based (row 1 = header).  We start reading from
    row (last_processed_row + 1) onward.
    """
    try:
        service = _build_service()
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=config.GOOGLE_REIMBURSEMENT_SHEET_ID,
                range=config.GOOGLE_REIMBURSEMENT_SHEET_RANGE,
            )
            .execute()
        )
        all_rows: list[list] = result.get("values", [])

        # Row 0 in the list corresponds to sheet row 1 (header).
        # We want rows after last_processed_row, so slice from that index.
        new_rows = all_rows[last_processed_row:]  # last_processed_row == list index of first new row

        requests: list[ReimbursementRequest] = []
        for i, row in enumerate(new_rows):
            sheet_row = last_processed_row + 1 + i  # 1-based sheet row number

            amount_str = _safe_get(row, config.REIMBURSEMENT_COL_AMOUNT, "0")
            try:
                amount = float(amount_str.replace("$", "").replace(",", ""))
            except ValueError:
                amount = 0.0

            receipt_raw = _safe_get(row, config.REIMBURSEMENT_COL_RECEIPT, "").lower()
            receipt_attached = receipt_raw in ("yes", "true", "1", "attached")

            requests.append(
                ReimbursementRequest(
                    row_number=sheet_row,
                    timestamp=_safe_get(row, config.REIMBURSEMENT_COL_TIMESTAMP),
                    submitter_name=_safe_get(row, config.REIMBURSEMENT_COL_NAME),
                    submitter_email=_safe_get(row, config.REIMBURSEMENT_COL_EMAIL),
                    amount=amount,
                    category=_safe_get(row, config.REIMBURSEMENT_COL_CATEGORY),
                    event_name=_safe_get(row, config.REIMBURSEMENT_COL_EVENT),
                    description=_safe_get(row, config.REIMBURSEMENT_COL_DESCRIPTION),
                    receipt_attached=receipt_attached,
                )
            )

        logger.info(
            "Found %d new reimbursement request(s) since row %d.",
            len(requests),
            last_processed_row,
        )
        return requests

    except Exception as exc:
        logger.error("Failed to fetch reimbursement data: %s", exc)
        return []
