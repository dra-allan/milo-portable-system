import time
import logging
from datetime import datetime
from typing import List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from .utils import setup_logger
from .config import config

logger = setup_logger(__name__)

class PipelineScheduler:
    def __init__(self):
        """Initialize the pipeline scheduler"""
        self.scheduler = BackgroundScheduler()
        self.is_running = False
        logger.info("Pipeline scheduler initialized")

    def add_daily_job(self, job_func, cron_expression: str, job_id: str = "daily_pipeline"):
        """
        Add a job to run at specific times using cron expression

        Args:
            job_func: Function to execute
            cron_expression: Cron format string (e.g., "0 9 * * *" for 9 AM daily)
            job_id: Unique identifier for the job
        """
        try:
            # Parse cron expression (simplified - expecting 5-field cron)
            parts = cron_expression.strip().split()
            if len(parts) != 5:
                raise ValueError(f"Invalid cron expression: {cron_expression}. Expected 5 parts")

            minute, hour, day, month, day_of_week = parts

            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week
            )

            self.scheduler.add_job(
                func=job_func,
                trigger=trigger,
                id=job_id,
                name=f'Pipeline job: {job_id}',
                replace_existing=True
            )

            logger.info(f"Added scheduled job '{job_id}' with cron expression: {cron_expression}")
        except Exception as e:
            logger.error(f"Failed to add scheduled job: {str(e)}")
            raise

    def add_interval_job(self, job_func, interval_seconds: int, job_id: str = "interval_pipeline"):
        """
        Add a job to run at fixed intervals

        Args:
            job_func: Function to execute
            interval_seconds: Interval in seconds
            job_id: Unique identifier for the job
        """
        try:
            self.scheduler.add_job(
                func=job_func,
                trigger='interval',
                seconds=interval_seconds,
                id=job_id,
                name=f'Pipeline job: {job_id}',
                replace_existing=True
            )

            logger.info(f"Added interval job '{job_id}' every {interval_seconds} seconds")
        except Exception as e:
            logger.error(f"Failed to add interval job: {str(e)}")
            raise

    def start(self):
        """Start the scheduler"""
        if not self.is_running:
            self.scheduler.start()
            self.is_running = True
            logger.info("Pipeline scheduler started")
        else:
            logger.warning("Scheduler is already running")

    def shutdown(self, wait: bool = True):
        """Shutdown the scheduler"""
        if self.is_running:
            self.scheduler.shutdown(wait=wait)
            self.is_running = False
            logger.info("Pipeline scheduler shutdown")
        else:
            logger.warning("Scheduler is not running")

    def get_jobs(self) -> List[dict]:
        """Get list of scheduled jobs"""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name,
                'next_run_time': job.next_run_time,
                'trigger': str(job.trigger)
            })
        return jobs