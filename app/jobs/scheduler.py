import logging
from typing import Optional

logger = logging.getLogger("dealflow360.jobs")
_scheduler = None


def get_scheduler():
    global _scheduler
    return _scheduler


def start_scheduler():
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        if _scheduler is None:
            _scheduler = BackgroundScheduler(timezone="UTC")
            logger.info("APScheduler initialized for DealFlow360 background jobs.")
            _scheduler.start()
    except ImportError:
        logger.info("APScheduler not installed in environment; background jobs available on-demand via API.")
    except Exception as e:
        logger.warning(f"Could not start background scheduler: {e}")


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("APScheduler stopped.")
        except Exception as e:
            logger.warning(f"Error shutting down scheduler: {e}")
        finally:
            _scheduler = None
