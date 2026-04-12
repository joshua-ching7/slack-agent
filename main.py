"""
Slack Agent — Entry Point

Runs two background agents on a schedule:
  1. Event Reminder Agent  — reads Google Calendar / Sheets, sends Slack
     reminders 7/3/1/0 days before each club event.
  2. Finance Agent         — monitors a Google Form response sheet and posts
     AI-summarised reimbursement alerts to the finance officers channel.

Usage:
    python main.py

Both agents also execute once immediately at startup so you get feedback
right away without waiting for the first scheduled interval.
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


def main() -> None:
    # Import here so any config validation errors surface immediately
    import config  # noqa: F401 — validates required env vars on import
    from agents.event_agent import check_and_send_reminders
    from agents.finance_agent import check_new_reimbursements
    from scheduler import build_scheduler

    logger.info("=" * 60)
    logger.info("  %s Slack Agent starting up", config.CLUB_NAME)
    logger.info("  Event source  : %s", config.EVENT_SOURCE)
    logger.info("  Events channel: %s", config.SLACK_EVENTS_CHANNEL)
    logger.info("  Finance channel: %s", config.SLACK_FINANCE_CHANNEL)
    logger.info("=" * 60)

    # Run both agents immediately so we don't have to wait for the first tick
    logger.info("Running initial checks...")
    try:
        check_and_send_reminders()
    except Exception as exc:
        logger.error("Event Agent initial run failed: %s", exc)

    try:
        check_new_reimbursements()
    except Exception as exc:
        logger.error("Finance Agent initial run failed: %s", exc)

    # Start the scheduler (blocks until Ctrl-C / SIGTERM)
    scheduler = build_scheduler()
    logger.info("Scheduler started. Press Ctrl-C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down gracefully.")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
