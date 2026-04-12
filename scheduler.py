"""
APScheduler configuration.

Two recurring jobs:
  • event_job   — checks for upcoming events and sends reminders
  • finance_job — checks for new reimbursement form submissions
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
from agents.event_agent import check_and_send_reminders
from agents.finance_agent import check_new_reimbursements

logger = logging.getLogger(__name__)


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        check_and_send_reminders,
        trigger=IntervalTrigger(minutes=config.EVENT_CHECK_INTERVAL_MINUTES),
        id="event_job",
        name="Event Reminder Agent",
        replace_existing=True,
        max_instances=1,        # prevent overlapping runs
        misfire_grace_time=300, # allow up to 5-min late start
    )
    logger.info(
        "Scheduled Event Reminder Agent every %d minute(s).",
        config.EVENT_CHECK_INTERVAL_MINUTES,
    )

    scheduler.add_job(
        check_new_reimbursements,
        trigger=IntervalTrigger(minutes=config.FINANCE_CHECK_INTERVAL_MINUTES),
        id="finance_job",
        name="Finance Agent",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )
    logger.info(
        "Scheduled Finance Agent every %d minute(s).",
        config.FINANCE_CHECK_INTERVAL_MINUTES,
    )

    return scheduler
