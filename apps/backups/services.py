"""
AquaFlow — Backup Service
Powered by Quantum Axis

Cross-platform pg_dump auto-detection.
Works on Windows, Linux, and Mac without PATH configuration.
"""

import os
import subprocess
import logging
import json
import platform
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from .models import BackupRecord

logger = logging.getLogger('apps')


# ─── PostgreSQL Executable Detection ─────────────────────────

def find_postgres_executable(name):
    """
    Find pg_dump, psql, etc. across Windows, Linux, Mac.
    Returns full path or None.
    """
    # 1. Try system PATH first
    found = shutil.which(name)
    if found:
        return found

    # 2. Windows: check common install locations
    if platform.system() == 'Windows':
        # Common paths for PostgreSQL 12-17 on Windows
        common_paths = []
        for version in ['17', '16', '15', '14', '13', '12']:
            common_paths.extend([
                fr'C:\Program Files\PostgreSQL\{version}\bin\{name}.exe',
                fr'C:\Program Files (x86)\PostgreSQL\{version}\bin\{name}.exe',
            ])

        for path in common_paths:
            if os.path.exists(path):
                logger.info(f'Found {name} at: {path}')
                return path

        # 3. Search Program Files for any PostgreSQL install
        for root_dir in [r'C:\Program Files\PostgreSQL',
                         r'C:\Program Files (x86)\PostgreSQL']:
            if os.path.exists(root_dir):
                for entry in os.listdir(root_dir):
                    exe_path = os.path.join(root_dir, entry, 'bin', f'{name}.exe')
                    if os.path.exists(exe_path):
                        logger.info(f'Found {name} at: {exe_path}')
                        return exe_path

    # 4. Linux/Mac: check common install locations
    else:
        common_paths = [
            f'/usr/bin/{name}',
            f'/usr/local/bin/{name}',
            f'/opt/postgresql/bin/{name}',
            f'/usr/lib/postgresql/17/bin/{name}',
            f'/usr/lib/postgresql/16/bin/{name}',
            f'/usr/lib/postgresql/15/bin/{name}',
            f'/usr/lib/postgresql/14/bin/{name}',
            f'/usr/pgsql-17/bin/{name}',
            f'/usr/pgsql-16/bin/{name}',
            f'/usr/pgsql-15/bin/{name}',
        ]
        for path in common_paths:
            if os.path.exists(path):
                logger.info(f'Found {name} at: {path}')
                return path

    return None


# ─── BackupService ───────────────────────────────────────────

class BackupService:

    def __init__(self, user=None):
        self.user = user
        self.backup_dir = self._ensure_backup_dir()
        self.db_config = settings.DATABASES['default']

        # Cache executable paths
        self._pg_dump = None
        self._psql = None

    @property
    def pg_dump_path(self):
        if self._pg_dump is None:
            self._pg_dump = find_postgres_executable('pg_dump')
        return self._pg_dump

    @property
    def psql_path(self):
        if self._psql is None:
            self._psql = find_postgres_executable('psql')
        return self._psql

    def _ensure_backup_dir(self):
        """
        Create date-organized backup directory.
        Falls back to project directory if configured path fails.
        """
        configured = getattr(settings, 'BACKUP_PATH', '')

        if configured:
            try:
                base = Path(configured)
                if not base.is_absolute():
                    base = Path(settings.BASE_DIR) / base
                base.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError, FileNotFoundError) as e:
                logger.warning(
                    f'Cannot use configured backup path "{configured}": {e}. '
                    f'Falling back to project directory.'
                )
                base = Path(settings.BASE_DIR) / 'backups'
                base.mkdir(parents=True, exist_ok=True)
        else:
            base = Path(settings.BASE_DIR) / 'backups'
            base.mkdir(parents=True, exist_ok=True)

        today = timezone.now().strftime('%Y-%m-%d')
        backup_dir = base / today

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            logger.error(f'Could not create backup directory: {e}')
            raise Exception(
                f'Cannot create backup directory. Check permissions on {base}'
            )

        return backup_dir

    # ─── SQL Backup ──────────────────────────────────────────

    def create_sql_backup(self, trigger=BackupRecord.TRIGGER_MANUAL):
        """Create PostgreSQL dump backup."""

        # Check pg_dump exists first
        pg_dump = self.pg_dump_path
        if not pg_dump:
            raise Exception(
                'pg_dump not found. Please install PostgreSQL client tools.\n'
                'Windows: Install PostgreSQL from postgresql.org\n'
                'Linux: sudo apt install postgresql-client\n'
                'Mac: brew install postgresql'
            )

        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        file_name = f'aquaflow_sql_{timestamp}.sql'
        file_path = self.backup_dir / file_name

        record = BackupRecord.objects.create(
            backup_type=BackupRecord.TYPE_SQL,
            trigger_type=trigger,
            file_path=str(file_path),
            file_name=file_name,
            db_host=self.db_config.get('HOST', ''),
            db_name=self.db_config.get('NAME', ''),
            app_version=getattr(settings, 'AQUAFLOW_VERSION', '1.0.0'),
            created_by=self.user,
        )

        try:
            env = os.environ.copy()
            env['PGPASSWORD'] = self.db_config.get('PASSWORD', '')

            cmd = [
                pg_dump,   # Full path to executable
                '-h', self.db_config.get('HOST', 'localhost'),
                '-p', str(self.db_config.get('PORT', 5432)),
                '-U', self.db_config.get('USER', ''),
                '-d', self.db_config.get('NAME', ''),
                '-F', 'p',
                '--no-owner',
                '--no-acl',
                '--clean',
                '--if-exists',
                '-f', str(file_path),
            ]

            result = subprocess.run(
                cmd, env=env,
                capture_output=True,
                text=True,
                timeout=1800,
            )

            if result.returncode != 0:
                raise Exception(f'pg_dump failed: {result.stderr}')

            if not file_path.exists():
                raise Exception('Backup file was not created.')

            file_size = file_path.stat().st_size
            if file_size < 100:
                raise Exception(f'Backup file too small ({file_size} bytes).')

            record.file_size_bytes = file_size
            record.status = BackupRecord.STATUS_SUCCESS
            record.completed_at = timezone.now()
            record.verified = True
            record.verification_notes = f'File exists, {file_size} bytes.'
            record.save()

            logger.info(f'SQL backup successful: {file_name} ({file_size} bytes)')
            return record

        except subprocess.TimeoutExpired:
            record.status = BackupRecord.STATUS_FAILED
            record.error_message = 'Backup timed out after 30 minutes.'
            record.completed_at = timezone.now()
            record.save()
            logger.exception('SQL backup timeout')
            raise

        except Exception as e:
            record.status = BackupRecord.STATUS_FAILED
            record.error_message = str(e)
            record.completed_at = timezone.now()
            record.save()
            logger.exception('SQL backup failed')
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass
            raise

    # ─── JSON Backup ─────────────────────────────────────────

    def create_json_backup(self, trigger=BackupRecord.TRIGGER_MANUAL):
        """Create Django JSON export."""
        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        file_name = f'aquaflow_json_{timestamp}.json'
        file_path = self.backup_dir / file_name

        record = BackupRecord.objects.create(
            backup_type=BackupRecord.TYPE_JSON,
            trigger_type=trigger,
            file_path=str(file_path),
            file_name=file_name,
            db_host=self.db_config.get('HOST', ''),
            db_name=self.db_config.get('NAME', ''),
            app_version=getattr(settings, 'AQUAFLOW_VERSION', '1.0.0'),
            created_by=self.user,
        )

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                call_command(
                    'dumpdata',
                    '--natural-foreign',
                    '--natural-primary',
                    '--indent', '2',
                    '--exclude', 'contenttypes',
                    '--exclude', 'auth.permission',
                    '--exclude', 'sessions',
                    '--exclude', 'admin.logentry',
                    '--exclude', 'backups.backuprecord',
                    stdout=f,
                )

            if not file_path.exists():
                raise Exception('JSON file was not created.')

            file_size = file_path.stat().st_size
            if file_size < 100:
                raise Exception(f'JSON file too small ({file_size} bytes).')

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    raise Exception('JSON structure invalid.')
                record_count = len(data)

            record.file_size_bytes = file_size
            record.status = BackupRecord.STATUS_SUCCESS
            record.completed_at = timezone.now()
            record.verified = True
            record.verification_notes = f'{record_count} records, {file_size} bytes.'
            record.save()

            logger.info(f'JSON backup successful: {file_name} ({record_count} records)')
            return record

        except Exception as e:
            record.status = BackupRecord.STATUS_FAILED
            record.error_message = str(e)
            record.completed_at = timezone.now()
            record.save()
            logger.exception('JSON backup failed')
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass
            raise

    # ─── Full Backup ─────────────────────────────────────────

    def create_full_backup(self, trigger=BackupRecord.TRIGGER_MANUAL):
        """Create both SQL and JSON backups."""
        results = {}
        try:
            results['sql'] = self.create_sql_backup(trigger=trigger)
        except Exception as e:
            results['sql_error'] = str(e)

        try:
            results['json'] = self.create_json_backup(trigger=trigger)
        except Exception as e:
            results['json_error'] = str(e)

        return results

    # ─── Restore SQL Backup ──────────────────────────────────

    def restore_sql_backup(self, backup_id):
        """Restore from SQL backup. Auto-creates safety backup first."""

        psql = self.psql_path
        if not psql:
            raise Exception(
                'psql not found. Please install PostgreSQL client tools.'
            )

        try:
            record = BackupRecord.objects.get(pk=backup_id)
        except BackupRecord.DoesNotExist:
            raise Exception('Backup not found.')

        if record.backup_type != BackupRecord.TYPE_SQL:
            raise Exception('Only SQL backups can be restored.')

        if not os.path.exists(record.file_path):
            raise Exception(f'Backup file missing: {record.file_path}')

        # Safety backup first
        logger.info('Creating pre-restore safety backup...')
        safety_backup = self.create_sql_backup(
            trigger=BackupRecord.TRIGGER_PRE_RESTORE
        )
        logger.info(f'Safety backup: {safety_backup.file_name}')

        try:
            env = os.environ.copy()
            env['PGPASSWORD'] = self.db_config.get('PASSWORD', '')

            cmd = [
                psql,
                '-h', self.db_config.get('HOST', 'localhost'),
                '-p', str(self.db_config.get('PORT', 5432)),
                '-U', self.db_config.get('USER', ''),
                '-d', self.db_config.get('NAME', ''),
                '-f', record.file_path,
            ]

            result = subprocess.run(
                cmd, env=env,
                capture_output=True,
                text=True,
                timeout=1800,
            )

            if result.returncode != 0:
                raise Exception(f'psql restore failed: {result.stderr}')

            logger.info(f'SQL restore successful from {record.file_name}')
            return {
                'success': True,
                'safety_backup': safety_backup,
                'restored_from': record,
            }
        except Exception as e:
            logger.exception('SQL restore failed')
            raise

    # ─── Backup Rotation ─────────────────────────────────────

    def cleanup_old_backups(self, keep_days=30):
        """Delete backup records + files older than keep_days."""
        cutoff = timezone.now() - timedelta(days=keep_days)

        old_records = BackupRecord.objects.filter(
            started_at__lt=cutoff,
        ).exclude(trigger_type=BackupRecord.TRIGGER_PRE_RESTORE)

        deleted = 0
        for record in old_records:
            try:
                if record.file_path and os.path.exists(record.file_path):
                    os.unlink(record.file_path)
                record.delete()
                deleted += 1
            except Exception as e:
                logger.warning(f'Could not delete backup {record.pk}: {e}')

        return deleted

    # ─── System Info (for UI) ────────────────────────────────

    @classmethod
    def get_system_info(cls):
        """Return diagnostic info about PostgreSQL availability."""
        return {
            'platform':     platform.system(),
            'platform_ver': platform.release(),
            'pg_dump':      find_postgres_executable('pg_dump'),
            'psql':         find_postgres_executable('psql'),
            'sql_available': find_postgres_executable('pg_dump') is not None,
        }