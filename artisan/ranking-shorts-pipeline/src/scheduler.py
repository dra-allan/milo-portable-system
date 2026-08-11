"""APScheduler wrapper mirroring the youtube-shorts-pipeline scheduler.

Self-contained copy: the ranking pipeline imports nothing from the shorts
pipeline, so this file is a vendored twin of that one. It exposes the same
add_daily_job / add_interval_job / start / shutdown surface the shorts
pipeline's automatic posting relies on.
"""
import logging
from typing import List

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .utils import setup_logger

logger = setup_logger(__name__)


class PipelineScheduler:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.is_running = False
        logger.info("Pipeline scheduler initialized")

    def add_daily_job(self, job_func, cron_expression: str,
                      job_id: str = "daily_pipeline"):
        """Schedule a job with a 5-field cron expression (e.g. '0 9 * * *')."""
        try:
            parts = cron_expression.strip().split()
            if len(parts) != 5:
                raise ValueError(
                    f"Invalid cron expression: {cron_expression}. "
                    'Expected 5 parts')
            minute, hour, day, month, day_of_week = parts
            trigger = CronTrigger(minute=minute, hour=hour, day=day,
                                  month=month, day_of_week=day_of_week)
            self.scheduler.add_job(
                func=job_func, trigger=trigger, id=job_id,
                name=f'Pipeline job: {job_id}', replace_existing=True)
            logger.info(
                "Added scheduled job '%s' with cron expression: %s",
                job_id, cron_expression)
        except Exception as exc:
            logger.error("Failed to add scheduled job: %s", str(exc))
            raise

    def add_interval_job(self, job_func, interval_seconds: int,
                         job_id: str = "interval_pipeline"):
        try:
            self.scheduler.add_job(
                func=job_func, trigger='interval', seconds=interval_seconds,
                id=job_id, name=f'Pipeline job: {job_id}',
                replace_existing=True)
            logger.info("Added interval job '%s' every %d seconds",
                        job_id, interval_seconds)
        except Exception as exc:
            logger.error("Failed to add interval job: %s", str(exc))
            raise

    def start(self):
        if not self.is_running:
            self.scheduler.start()
            self.is_running = True
            logger.info("Pipeline scheduler started")
        else:
            logger.warning("Scheduler is already running")

    def shutdown(self, wait: bool = True):
        if self.is_running:
            self.scheduler.shutdown(wait=wait)
            self.is_running = False
            logger.info("Pipeline scheduler shutdown")
        else:
            logger.warning("Scheduler is not running")

    def get_jobs(self) -> List[dict]:
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name,
                'next_run_time': getattr(job, 'next_run_time', None),
                'trigger': str(job.trigger),
            })
        return jobs