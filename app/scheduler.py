"""APScheduler with MSK timezone."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.services.channel import post_one
from app.services.db_rotation import maybe_rotate
from app.services.funnel import (
    reach_out_to_churned,
    remind_expiring_subs,
    remind_unpaid_invoices,
)
from app.utils.time import MSK

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


def setup_scheduler(bot: "Bot") -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=MSK)

    if settings.channel_id:
        # Auto-posts: 3x per day at 10:30, 14:00, 19:30 MSK + a random review on Tue/Fri.
        sched.add_job(
            post_one,
            trigger=CronTrigger(hour=10, minute=30),
            args=[bot, "post"],
            id="post_morning",
            replace_existing=True,
        )
        sched.add_job(
            post_one,
            trigger=CronTrigger(hour=14, minute=0),
            args=[bot, "trigger"],
            id="post_afternoon",
            replace_existing=True,
        )
        sched.add_job(
            post_one,
            trigger=CronTrigger(hour=19, minute=30),
            args=[bot, "case"],
            id="post_evening",
            replace_existing=True,
        )
        sched.add_job(
            post_one,
            trigger=CronTrigger(day_of_week="tue,fri", hour=12, minute=15),
            args=[bot, "review"],
            id="post_review_biweekly",
            replace_existing=True,
        )
        sched.add_job(
            post_one,
            trigger=CronTrigger(day_of_week="thu", hour=16, minute=0),
            args=[bot, "update"],
            id="post_update_weekly",
            replace_existing=True,
        )

    # Funnel reminders.
    sched.add_job(
        remind_expiring_subs,
        trigger=IntervalTrigger(hours=6),
        args=[bot],
        id="remind_expiring",
        replace_existing=True,
    )
    sched.add_job(
        reach_out_to_churned,
        trigger=IntervalTrigger(hours=24),
        args=[bot],
        id="reach_churned",
        replace_existing=True,
    )
    sched.add_job(
        remind_unpaid_invoices,
        trigger=IntervalTrigger(hours=2),
        args=[bot],
        id="remind_unpaid",
        replace_existing=True,
    )

    # Postgres auto-rotation (Render free PG dies after 30 days).
    sched.add_job(
        maybe_rotate,
        trigger=IntervalTrigger(hours=6),
        args=[bot],
        id="db_rotate",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    return sched
