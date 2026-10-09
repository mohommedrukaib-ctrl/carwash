"""
AquaFlow — Backup Views
Powered by Quantum Axis
"""

import os
import logging
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum, Count
from django.http import JsonResponse, FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from .models import BackupRecord
from .services import BackupService
from apps.accounts.models import RoleCode
from apps.system.models import AuditLog

logger = logging.getLogger('apps')


def is_super_admin(request):
    try:
        return request.user.profile.role.code == RoleCode.SUPER_ADMIN
    except Exception:
        return False


def client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


# ─── BACKUP DASHBOARD ────────────────────────────────────────

@login_required
def backup_dashboard(request):
    if not is_super_admin(request):
        messages.error(request, 'Only Super Admin can manage backups.')
        return redirect('dashboard')

    all_backups = BackupRecord.objects.all()

    total_count   = all_backups.count()
    success_count = all_backups.filter(status=BackupRecord.STATUS_SUCCESS).count()
    failed_count  = all_backups.filter(status=BackupRecord.STATUS_FAILED).count()
    total_size    = all_backups.aggregate(Sum('file_size_bytes'))['file_size_bytes__sum'] or 0

    last_success = all_backups.filter(
        status=BackupRecord.STATUS_SUCCESS
    ).order_by('-started_at').first()

    recent = all_backups.order_by('-started_at')[:10]

    from django.conf import settings
    backup_path = getattr(settings, 'BACKUP_PATH', 'backups/')

    # Get system info (PostgreSQL detection)
    from .services import BackupService
    system_info = BackupService.get_system_info()

    context = {
        'page_title':    'Backup & Restore',
        'total_count':   total_count,
        'success_count': success_count,
        'failed_count':  failed_count,
        'total_size':    total_size,
        'total_size_mb': total_size / (1024 * 1024),
        'last_success':  last_success,
        'recent':        recent,
        'backup_path':   backup_path,
        'system_info':   system_info,
    }
    return render(request, 'backups/dashboard.html', context)

# ─── CREATE BACKUP ───────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def create_backup(request):
    if not is_super_admin(request):
        return JsonResponse(
            {'success': False, 'error': 'Only Super Admin can create backups.'},
            status=403
        )

    backup_type = request.POST.get('type', 'full')

    try:
        service = BackupService(user=request.user)

        if backup_type == 'sql':
            record = service.create_sql_backup()
            message = f'SQL backup created: {record.file_name} ({record.file_size_display})'
        elif backup_type == 'json':
            record = service.create_json_backup()
            message = f'JSON backup created: {record.file_name} ({record.file_size_display})'
        else:
            results = service.create_full_backup()
            messages_list = []
            if 'sql' in results:
                messages_list.append(f'SQL: {results["sql"].file_name}')
            if 'sql_error' in results:
                messages_list.append(f'SQL FAILED: {results["sql_error"]}')
            if 'json' in results:
                messages_list.append(f'JSON: {results["json"].file_name}')
            if 'json_error' in results:
                messages_list.append(f'JSON FAILED: {results["json_error"]}')
            message = '<br>'.join(messages_list)

        AuditLog.log(
            action='BACKUP_CREATED',
            module='backups',
            user=request.user,
            object_type='BackupRecord',
            object_repr=f'{backup_type} backup',
            new_data={'type': backup_type},
            ip_address=client_ip(request),
        )

        return JsonResponse({'success': True, 'message': message})

    except Exception as e:
        logger.exception('Backup creation failed')
        return JsonResponse(
            {'success': False, 'error': str(e)},
            status=500
        )


# ─── BACKUP LIST ─────────────────────────────────────────────

@login_required
def backup_list(request):
    if not is_super_admin(request):
        messages.error(request, 'Only Super Admin can view backups.')
        return redirect('dashboard')

    backups = BackupRecord.objects.select_related('created_by').order_by('-started_at')

    paginator = Paginator(backups, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'page_title': 'Backup History',
        'page_obj':   page_obj,
        'backups':    page_obj,
        'total':      paginator.count,
    }
    return render(request, 'backups/backup_list.html', context)


# ─── DOWNLOAD BACKUP ─────────────────────────────────────────

@login_required
def backup_download(request, pk):
    if not is_super_admin(request):
        messages.error(request, 'Permission denied.')
        return redirect('backup_dashboard')

    record = get_object_or_404(BackupRecord, pk=pk)

    if not os.path.exists(record.file_path):
        messages.error(request, 'Backup file no longer exists on disk.')
        return redirect('backup_list')

    AuditLog.log(
        action='BACKUP_DOWNLOADED',
        module='backups',
        user=request.user,
        object_type='BackupRecord',
        object_id=record.pk,
        object_repr=record.file_name,
        ip_address=client_ip(request),
    )

    return FileResponse(
        open(record.file_path, 'rb'),
        as_attachment=True,
        filename=record.file_name,
    )


# ─── DELETE BACKUP ───────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def backup_delete(request, pk):
    if not is_super_admin(request):
        return JsonResponse(
            {'success': False, 'error': 'Permission denied.'},
            status=403
        )

    record = get_object_or_404(BackupRecord, pk=pk)

    file_name = record.file_name

    try:
        if os.path.exists(record.file_path):
            os.unlink(record.file_path)

        AuditLog.log(
            action='BACKUP_DELETED',
            module='backups',
            user=request.user,
            object_type='BackupRecord',
            object_id=record.pk,
            object_repr=file_name,
            ip_address=client_ip(request),
        )

        record.delete()

        return JsonResponse({
            'success': True,
            'message': f'Backup "{file_name}" deleted.',
        })
    except Exception as e:
        logger.exception('Backup delete failed')
        return JsonResponse(
            {'success': False, 'error': str(e)},
            status=500
        )


# ─── RESTORE BACKUP ──────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def backup_restore(request, pk):
    if not is_super_admin(request):
        return JsonResponse(
            {'success': False, 'error': 'Only Super Admin can restore backups.'},
            status=403
        )

    password = request.POST.get('password', '').strip()
    confirm  = request.POST.get('confirm', '').strip()

    if not password:
        return JsonResponse({'success': False, 'error': 'Password required.'}, status=400)

    if confirm != 'RESTORE':
        return JsonResponse({
            'success': False,
            'error': 'Type "RESTORE" exactly to confirm.',
        }, status=400)

    # Re-authenticate
    user = authenticate(username=request.user.username, password=password)
    if user is None or user.pk != request.user.pk:
        return JsonResponse({'success': False, 'error': 'Invalid password.'}, status=403)

    record = get_object_or_404(BackupRecord, pk=pk)

    if record.backup_type != BackupRecord.TYPE_SQL:
        return JsonResponse({
            'success': False,
            'error': 'Only SQL backups can be restored via this system.',
        }, status=400)

    if not os.path.exists(record.file_path):
        return JsonResponse({
            'success': False,
            'error': 'Backup file no longer exists.',
        }, status=400)

    try:
        service = BackupService(user=request.user)
        result = service.restore_sql_backup(record.pk)

        AuditLog.log(
            action='BACKUP_RESTORED',
            module='backups',
            user=request.user,
            object_type='BackupRecord',
            object_id=record.pk,
            object_repr=record.file_name,
            new_data={
                'safety_backup': result['safety_backup'].file_name,
                'restored_from': record.file_name,
            },
            ip_address=client_ip(request),
        )

        return JsonResponse({
            'success': True,
            'message': f'Database restored from {record.file_name}. '
                       f'Safety backup: {result["safety_backup"].file_name}',
        })
    except Exception as e:
        logger.exception('Restore failed')
        return JsonResponse(
            {'success': False, 'error': str(e)},
            status=500
        )


# ─── CLEANUP OLD ─────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def backup_cleanup(request):
    if not is_super_admin(request):
        return JsonResponse(
            {'success': False, 'error': 'Permission denied.'},
            status=403
        )

    try:
        keep_days = int(request.POST.get('keep_days', 30))
    except ValueError:
        keep_days = 30

    try:
        service = BackupService(user=request.user)
        deleted = service.cleanup_old_backups(keep_days=keep_days)

        AuditLog.log(
            action='BACKUP_CLEANUP',
            module='backups',
            user=request.user,
            new_data={'deleted_count': deleted, 'keep_days': keep_days},
            ip_address=client_ip(request),
        )

        return JsonResponse({
            'success': True,
            'message': f'Deleted {deleted} old backup(s).',
        })
    except Exception as e:
        return JsonResponse(
            {'success': False, 'error': str(e)},
            status=500
        )


# ─── SCHEDULE CONFIG ─────────────────────────────────────────

@login_required
def backup_schedule(request):
    """View/edit backup schedule."""
    if not is_super_admin(request):
        messages.error(request, 'Only Super Admin can configure backups.')
        return redirect('dashboard')

    from .models import BackupSchedule

    schedule = BackupSchedule.get_config()

    context = {
        'page_title': 'Backup Schedule',
        'schedule':   schedule,
    }
    return render(request, 'backups/schedule.html', context)


@login_required
@require_http_methods(['POST'])
def backup_schedule_save(request):
    if not is_super_admin(request):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    from .models import BackupSchedule

    schedule = BackupSchedule.get_config()

    try:
        schedule.enabled = request.POST.get('enabled') == 'true'
        schedule.frequency = request.POST.get('frequency', 'daily')
        schedule.backup_type = request.POST.get('backup_type', 'full')

        time_str = request.POST.get('scheduled_time', '02:00')
        from datetime import datetime
        schedule.scheduled_time = datetime.strptime(time_str, '%H:%M').time()

        schedule.scheduled_day = int(request.POST.get('scheduled_day', '0'))
        schedule.retention_days = int(request.POST.get('retention_days', '30'))
        schedule.auto_cleanup = request.POST.get('auto_cleanup') == 'true'

        schedule.next_run_at = schedule.calculate_next_run()
        schedule.save()

        AuditLog.log(
            action='BACKUP_SCHEDULE_UPDATED',
            module='backups',
            user=request.user,
            new_data={
                'enabled': schedule.enabled,
                'frequency': schedule.frequency,
                'type': schedule.backup_type,
            },
            ip_address=client_ip(request),
        )

        return JsonResponse({
            'success': True,
            'message': 'Backup schedule saved.',
            'next_run': schedule.next_run_at.strftime('%d %b %Y %H:%M') if schedule.next_run_at else None,
        })
    except Exception as e:
        logger.exception('Schedule save failed')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)