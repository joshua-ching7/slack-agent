"""
APScheduler configuration.

Two recurring jobs:
  • event_job   — checks for upcoming events and sends (or drafts) reminders
                  every hour.
  • finance_job — checks for new reimbursement form submissions once a week
                  (default: every Monday at 09:00 UTC) to save compute.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config
from agents.event_agent import check_and_send_reminders
from agents.finance_agent import check_new_reimbursements

logger = logging.getLogger(__name__)


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")

    # ── Event Reminder Agent (hourly) ────────────────────────
    scheduler.add_job(
        check_and_send_reminders,
        trigger=IntervalTrigger(minutes=config.EVENT_CHECK_INTERVAL_MINUTES),
        id="event_job",
        name="Event Reminder Agent",
        replace_existing=True,
        max_instances=1,         # prevent overlapping runs
        misfire_grace_time=300,  # allow up to 5-min late start
    )
    logger.info(
        "Scheduled Event Reminder Agent every %d minute(s).",
        config.EVENT_CHECK_INTERVAL_MINUTES,
    )

    # ── Finance Agent (weekly) ───────────────────────────────
    # Runs once a week — enough to catch submissions without burning compute.
    # Override day/hour/minute via FINANCE_WEEKLY_* env vars.
    finance_trigger = CronTrigger(
        day_of_week=config.FINANCE_WEEKLY_DAY,
        hour=config.FINANCE_WEEKLY_HOUR,
        minute=config.FINANCE_WEEKLY_MINUTE,
        timezone="UTC",
    )
    scheduler.add_job(
        check_new_reimbursements,
        trigger=finance_trigger,
        id="finance_job",
        name="Finance Agent (weekly)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,  # up to 1-hour grace for a weekly job
    )
    logger.info(
        "Scheduled Finance Agent weekly on %s at %02d:%02d UTC.",
        config.FINANCE_WEEKLY_DAY.upper(),
        config.FINANCE_WEEKLY_HOUR,
        config.FINANCE_WEEKLY_MINUTE,
    )

    return scheduler
