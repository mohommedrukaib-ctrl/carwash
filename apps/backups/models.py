"""
AquaFlow — Backup Models
Powered by Quantum Axis
"""

from django.db import models
from django.contrib.auth.models import User


class BackupRecord(models.Model):

    TYPE_SQL  = 'sql'
    TYPE_JSON = 'json'
    TYPE_FULL = 'full'

    TYPE_CHOICES = [
        (TYPE_SQL,  'SQL Dump'),
        (TYPE_JSON, 'JSON Export'),
        (TYPE_FULL, 'Full Backup (SQL + JSON)'),
    ]

    TRIGGER_MANUAL      = 'manual'
    TRIGGER_SCHEDULED   = 'scheduled'
    TRIGGER_PRE_RESTORE = 'pre_restore'
    TRIGGER_PRE_UPGRADE = 'pre_upgrade'

    TRIGGER_CHOICES = [
        (TRIGGER_MANUAL,      'Manual'),
        (TRIGGER_SCHEDULED,   'Scheduled'),
        (TRIGGER_PRE_RESTORE, 'Pre-Restore Safety'),
        (TRIGGER_PRE_UPGRADE, 'Pre-Upgrade Safety'),
    ]

    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_SUCCESS     = 'success'
    STATUS_FAILED      = 'failed'
    STATUS_VERIFIED    = 'verified'

    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_SUCCESS,     'Success'),
        (STATUS_FAILED,      'Failed'),
        (STATUS_VERIFIED,    'Verified'),
    ]

    backup_type   = models.CharField(max_length=20, choices=TYPE_CHOICES)
    trigger_type  = models.CharField(max_length=20, choices=TRIGGER_CHOICES,
                                       default=TRIGGER_MANUAL)
    file_path     = models.CharField(max_length=500)
    file_name     = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField(default=0)

    status        = models.CharField(max_length=20, choices=STATUS_CHOICES,
                                       default=STATUS_IN_PROGRESS)
    verified      = models.BooleanField(default=False)
    verification_notes = models.TextField(blank=True)
    error_message = models.TextField(blank=True)

    db_host       = models.CharField(max_length=255, blank=True)
    db_name       = models.CharField(max_length=255, blank=True)
    app_version   = models.CharField(max_length=50, blank=True)

    started_at    = models.DateTimeField(auto_now_add=True)
    completed_at  = models.DateTimeField(null=True, blank=True)

    created_by    = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='backups_created',
    )

    class Meta:
        db_table = 'backup_records'
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.file_name} ({self.status})'

    @property
    def file_size_display(self):
        """Human-readable size."""
        size = self.file_size_bytes
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f'{size:.2f} {unit}'
            size /= 1024
        return f'{size:.2f} TB'

    @property
    def duration(self):
        if not self.completed_at:
            return None
        return (self.completed_at - self.started_at).total_seconds()


class BackupSchedule(models.Model):
    """
    Auto-backup scheduling configuration.
    Single-row config table.
    """

    FREQUENCY_DISABLED = 'disabled'
    FREQUENCY_HOURLY   = 'hourly'
    FREQUENCY_DAILY    = 'daily'
    FREQUENCY_WEEKLY   = 'weekly'

    FREQUENCY_CHOICES = [
        (FREQUENCY_DISABLED, 'Disabled'),
        (FREQUENCY_HOURLY,   'Every Hour'),
        (FREQUENCY_DAILY,    'Daily'),
        (FREQUENCY_WEEKLY,   'Weekly'),
    ]

    TYPE_SQL  = 'sql'
    TYPE_JSON = 'json'
    TYPE_FULL = 'full'

    TYPE_CHOICES = [
        (TYPE_SQL,  'SQL Only'),
        (TYPE_JSON, 'JSON Only'),
        (TYPE_FULL, 'Full (SQL + JSON)'),
    ]

    enabled       = models.BooleanField(default=False)
    frequency     = models.CharField(
        max_length=20, choices=FREQUENCY_CHOICES,
        default=FREQUENCY_DAILY,
    )
    backup_type   = models.CharField(
        max_length=20, choices=TYPE_CHOICES,
        default=TYPE_FULL,
    )
    scheduled_time = models.TimeField(
        default='02:00',
        help_text='Time of day for daily/weekly backups.',
    )
    scheduled_day = models.PositiveIntegerField(
        default=0,
        help_text='Day of week for weekly (0=Mon, 6=Sun).',
    )
    retention_days = models.PositiveIntegerField(
        default=30,
        help_text='Keep backups for this many days.',
    )
    auto_cleanup = models.BooleanField(
        default=True,
        help_text='Automatically delete old backups.',
    )

    last_run_at    = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(max_length=20, blank=True)
    next_run_at    = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'backup_schedule'

    def __str__(self):
        return f'{"Enabled" if self.enabled else "Disabled"} — {self.get_frequency_display()}'

    @classmethod
    def get_config(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def calculate_next_run(self):
        """Calculate next scheduled run time."""
        from django.utils import timezone
        from datetime import timedelta

        if not self.enabled or self.frequency == self.FREQUENCY_DISABLED:
            return None

        now = timezone.now()

        if self.frequency == self.FREQUENCY_HOURLY:
            # Next hour
            next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

        elif self.frequency == self.FREQUENCY_DAILY:
            # Today at scheduled_time, or tomorrow if passed
            local_now = timezone.localtime(now)
            next_run = local_now.replace(
                hour=self.scheduled_time.hour,
                minute=self.scheduled_time.minute,
                second=0, microsecond=0,
            )
            if next_run <= local_now:
                next_run += timedelta(days=1)

        elif self.frequency == self.FREQUENCY_WEEKLY:
            local_now = timezone.localtime(now)
            next_run = local_now.replace(
                hour=self.scheduled_time.hour,
                minute=self.scheduled_time.minute,
                second=0, microsecond=0,
            )
            days_ahead = (self.scheduled_day - local_now.weekday()) % 7
            if days_ahead == 0 and next_run <= local_now:
                days_ahead = 7
            next_run += timedelta(days=days_ahead)
        else:
            return None

        return next_run

    def is_due(self):
        """Check if backup should run now."""
        from django.utils import timezone
        if not self.enabled:
            return False
        if not self.next_run_at:
            return True  # Never ran
        return timezone.now() >= self.next_run_at