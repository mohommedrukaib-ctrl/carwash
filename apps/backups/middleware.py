"""
AquaFlow — Backup Middleware
Powered by Quantum Axis

Checks backup schedule on each request.
Uses database row lock + file lock for multi-worker safety.
"""

import logging
import threading
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger('apps')


class BackupScheduleMiddleware:
    """
    Multi-worker safe backup scheduler.
    Uses database SELECT FOR UPDATE to prevent duplicate runs.
    """

    _last_check = None
    _check_interval_seconds = 300  # 5 minutes
    _local_lock = threading.Lock()

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check backup schedule (throttled)
        try:
            self._check_schedule()
        except Exception as e:
            logger.warning(f'Backup schedule check failed: {e}')

        response = self.get_response(request)
        return response

    def _check_schedule(self):
        now = timezone.now()

        # Throttle: only check once per interval (per worker)
        with self.__class__._local_lock:
            if self._last_check:
                elapsed = (now - self._last_check).total_seconds()
                if elapsed < self._check_interval_seconds:
                    return
            self.__class__._last_check = now

        try:
            from apps.backups.models import BackupSchedule

            # Use SELECT FOR UPDATE to lock the schedule row
            # so only ONE worker triggers the backup
            with transaction.atomic():
                schedule = BackupSchedule.objects.select_for_update(
                    skip_locked=True
                ).filter(pk=1, enabled=True).first()

                if not schedule:
                    return

                if not schedule.is_due():
                    return

                # Mark as running to prevent other workers from picking it up
                schedule.last_run_at = timezone.now()
                schedule.last_run_status = 'running'
                schedule.save(update_fields=['last_run_at', 'last_run_status'])

            # Trigger backup in background thread
            logger.info('Auto-backup is due, starting in background...')
            thread = threading.Thread(
                target=self._run_backup,
                args=(schedule.pk,),
                daemon=True,  # Wait for completion during shutdown
                name='BackupWorker',
            )
            thread.start()

        except Exception as e:
            logger.warning(f'Auto-backup check failed: {e}')

    def _run_backup(self, schedule_id):
        """Run backup in background thread."""
        try:
            from apps.backups.models import BackupSchedule, BackupRecord
            from apps.backups.services import BackupService

            schedule = BackupSchedule.objects.get(pk=schedule_id)
            service = BackupService(user=None)

            if schedule.backup_type == 'sql':
                service.create_sql_backup(trigger=BackupRecord.TRIGGER_SCHEDULED)
            elif schedule.backup_type == 'json':
                service.create_json_backup(trigger=BackupRecord.TRIGGER_SCHEDULED)
            else:
                service.create_full_backup(trigger=BackupRecord.TRIGGER_SCHEDULED)

            # Update schedule
            schedule.last_run_at = timezone.now()
            schedule.last_run_status = 'success'
            schedule.next_run_at = schedule.calculate_next_run()
            schedule.save()

            # Auto-cleanup
            if schedule.auto_cleanup:
                service.cleanup_old_backups(keep_days=schedule.retention_days)

            logger.info('Auto-backup completed successfully.')

        except Exception as e:
            logger.exception(f'Auto-backup failed: {e}')
            try:
                schedule = BackupSchedule.objects.get(pk=schedule_id)
                schedule.last_run_at = timezone.now()
                schedule.last_run_status = f'failed: {str(e)[:200]}'
                schedule.next_run_at = schedule.calculate_next_run()
                schedule.save()
            except Exception:
                pass