"""Central configuration — loaded once at startup from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Anthropic ─────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]

# ── Slack ─────────────────────────────────────────────────────
SLACK_BOT_TOKEN: str = os.environ["SLACK_BOT_TOKEN"]
SLACK_EVENTS_CHANNEL: str = os.getenv("SLACK_EVENTS_CHANNEL", "#events")
SLACK_FINANCE_CHANNEL: str = os.getenv("SLACK_FINANCE_CHANNEL", "#finance-officers")

# ── Google ────────────────────────────────────────────────────
GOOGLE_SERVICE_ACCOUNT_FILE: str = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"
)

# Event source: "calendar" or "sheets"
EVENT_SOURCE: str = os.getenv("EVENT_SOURCE", "calendar")

GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")

GOOGLE_SOCIAL_SHEET_ID: str = os.getenv("GOOGLE_SOCIAL_SHEET_ID", "")
GOOGLE_SOCIAL_SHEET_RANGE: str = os.getenv("GOOGLE_SOCIAL_SHEET_RANGE", "Sheet1!A2:F")

GOOGLE_REIMBURSEMENT_SHEET_ID: str = os.environ["GOOGLE_REIMBURSEMENT_SHEET_ID"]
GOOGLE_REIMBURSEMENT_SHEET_RANGE: str = os.getenv(
    "GOOGLE_REIMBURSEMENT_SHEET_RANGE", "Form Responses 1!A:I"
)

# ── Reimbursement form column mapping (0-based) ───────────────
REIMBURSEMENT_COL_TIMESTAMP: int = int(os.getenv("REIMBURSEMENT_COL_TIMESTAMP", "0"))
REIMBURSEMENT_COL_NAME: int = int(os.getenv("REIMBURSEMENT_COL_NAME", "1"))
REIMBURSEMENT_COL_EMAIL: int = int(os.getenv("REIMBURSEMENT_COL_EMAIL", "2"))
REIMBURSEMENT_COL_AMOUNT: int = int(os.getenv("REIMBURSEMENT_COL_AMOUNT", "3"))
REIMBURSEMENT_COL_CATEGORY: int = int(os.getenv("REIMBURSEMENT_COL_CATEGORY", "4"))
REIMBURSEMENT_COL_EVENT: int = int(os.getenv("REIMBURSEMENT_COL_EVENT", "5"))
REIMBURSEMENT_COL_DESCRIPTION: int = int(os.getenv("REIMBURSEMENT_COL_DESCRIPTION", "6"))
REIMBURSEMENT_COL_RECEIPT: int = int(os.getenv("REIMBURSEMENT_COL_RECEIPT", "7"))

# ── Agent schedule settings ───────────────────────────────────
REMINDER_DAYS_BEFORE: list[int] = [
    int(d)
    for d in os.getenv("REMINDER_DAYS_BEFORE", "7,3,1,0").split(",")
]
EVENT_CHECK_INTERVAL_MINUTES: int = int(
    os.getenv("EVENT_CHECK_INTERVAL_MINUTES", "60")
)
FINANCE_CHECK_INTERVAL_MINUTES: int = int(
    os.getenv("FINANCE_CHECK_INTERVAL_MINUTES", "15")
)

# ── General ───────────────────────────────────────────────────
CLUB_NAME: str = os.getenv("CLUB_NAME", "our club")
STATE_FILE: str = os.getenv("STATE_FILE", "state.json")
