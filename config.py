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

# Social chair notification
# When True, Claude-drafted announcements are DM'd to the social chair
# for personalisation before posting rather than auto-posted.
SOCIAL_CHAIR_NOTIFY: bool = os.getenv("SOCIAL_CHAIR_NOTIFY", "true").lower() == "true"
# Keyword matched against Slack profile "title" field (case-insensitive)
SOCIAL_CHAIR_TITLE_KEYWORD: str = os.getenv("SOCIAL_CHAIR_TITLE_KEYWORD", "social chair")
# Which event types route through the social chair (comma-separated).
# Use "all" to include every event type.
SOCIAL_CHAIR_EVENT_TYPES: list[str] = [
    t.strip().lower()
    for t in os.getenv("SOCIAL_CHAIR_EVENT_TYPES", "social").split(",")
]

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

# Finance agent runs once a week by default (saves compute vs. polling every 15 min).
# FINANCE_WEEKLY_DAY  : 0=Monday … 6=Sunday, or names like "mon", "fri"
# FINANCE_WEEKLY_HOUR : hour of day in UTC (0-23)
# FINANCE_WEEKLY_MINUTE: minute (0-59)
FINANCE_WEEKLY_DAY: str = os.getenv("FINANCE_WEEKLY_DAY", "mon")
FINANCE_WEEKLY_HOUR: int = int(os.getenv("FINANCE_WEEKLY_HOUR", "9"))
FINANCE_WEEKLY_MINUTE: int = int(os.getenv("FINANCE_WEEKLY_MINUTE", "0"))

# ── ASUC Reimbursement Submission ─────────────────────────────
# URL of the ASUC / Berkeley finance portal for submitting reimbursements
ASUC_PORTAL_URL: str = os.getenv("ASUC_PORTAL_URL", "")
# CalNet credentials used for SSO login to the portal
ASUC_CALNET_USERNAME: str = os.getenv("ASUC_CALNET_USERNAME", "")
ASUC_CALNET_PASSWORD: str = os.getenv("ASUC_CALNET_PASSWORD", "")
# Name of your student org as it appears in the ASUC portal
ASUC_ORG_NAME: str = os.getenv("ASUC_ORG_NAME", config_club_name := os.getenv("CLUB_NAME", ""))
# Home Department value to select in the Payee Info dropdown (Step 1 of the form).
# Must match the label exactly as it appears in the portal's Home Department list.
ASUC_HOME_DEPARTMENT: str = os.getenv("ASUC_HOME_DEPARTMENT", "")
# Local directory where downloaded receipt files are stored
ASUC_RECEIPTS_DIR: str = os.getenv("ASUC_RECEIPTS_DIR", "receipts")
# Run the browser in headless mode (True) or show the window (False for debugging)
ASUC_HEADLESS: bool = os.getenv("ASUC_HEADLESS", "true").lower() == "true"

# ── General ───────────────────────────────────────────────────
CLUB_NAME: str = os.getenv("CLUB_NAME", "our club")
STATE_FILE: str = os.getenv("STATE_FILE", "state.json")

# Ensure ASUC_ORG_NAME falls back to CLUB_NAME when not set explicitly
if not ASUC_ORG_NAME:
    ASUC_ORG_NAME = CLUB_NAME
