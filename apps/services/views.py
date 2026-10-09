"""
AquaFlow — Service Views (Updated)
Powered by Quantum Axis

Features:
- Service list with inline price management
- Service categories
- Service create/edit with smart inputs
- Quick price setting
- AJAX search for POS/Booking/Wash
"""

import logging
from decimal import Decimal, InvalidOperation

from apps.system.htmx import is_htmx, htmx_trigger
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .models import Service, ServiceCategory, ServicePrice
from apps.accounts.models import PermissionCode
from apps.system.models import AuditLog

logger = logging.getLogger('apps')


def check_permission(request, code):
    try:
        return request.user.profile.has_permission(code)
    except Exception:
        return False


def get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


# ─── SERVICE LIST ─────────────────────────────────────────────

@login_required
def service_list(request):
    if not check_permission(request, PermissionCode.SERVICES_VIEW):
        messages.error(request, 'Permission denied.')
        return redirect('dashboard')

    search   = request.GET.get('q', '').strip()
    category = request.GET.get('category', '')
    status   = request.GET.get('status', 'active')

    services = Service.objects.filter(
        is_deleted=False
    ).select_related('category').prefetch_related('prices')

    if status == 'active':
        services = services.filter(is_active=True)
    elif status == 'inactive':
        services = services.filter(is_active=False)

    if category:
        services = services.filter(category_id=category)

    if search:
        services = services.filter(
            Q(name__icontains=search) |
            Q(description__icontains=search) |
            Q(category__name__icontains=search)
        )

    services = services.order_by('category__sort_order', 'name')

    paginator = Paginator(services, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    categories = ServiceCategory.objects.filter(is_active=True).order_by('sort_order', 'name')

    # Get vehicle types for price modal
    from apps.vehicles.models import VehicleType
    vehicle_types = VehicleType.objects.filter(is_active=True).order_by('sort_order', 'name')

    from apps.branches.models import Branch
    branches = Branch.objects.filter(status='active').order_by('name')

    context = {
        'page_title':    'Services',
        'page_obj':      page_obj,
        'services':      page_obj,
        'categories':    categories,
        'vehicle_types': vehicle_types,
        'branches':      branches,
        'search':        search,
        'status':        status,
        'category':      category,
        'total_count':   paginator.count,
        'can_manage':    check_permission(request, PermissionCode.SERVICES_MANAGE),
    }
    if is_htmx(request):
        return render(request, 'services/partials/service_table.html', context)
    return render(request, 'services/service_list.html', context)

# ─── SERVICE DETAIL ───────────────────────────────────────────

@login_required
def service_detail(request, pk):
    if not check_permission(request, PermissionCode.SERVICES_VIEW):
        messages.error(request, 'Permission denied.')
        return redirect('dashboard')

    service = get_object_or_404(Service, pk=pk, is_deleted=False)
    prices = service.prices.select_related(
        'vehicle_type', 'branch'
    ).order_by('vehicle_type__sort_order', 'branch__name')

    from apps.vehicles.models import VehicleType
    from apps.branches.models import Branch

    context = {
        'page_title':    service.name,
        'service':       service,
        'prices':        prices,
        'vehicle_types': VehicleType.objects.filter(is_active=True),
        'branches':      Branch.objects.filter(status='active'),
        'can_manage':    check_permission(request, PermissionCode.SERVICES_MANAGE),
    }
    return render(request, 'services/service_detail.html', context)


# ─── SERVICE CREATE ───────────────────────────────────────────

@login_required
@require_http_methods(['GET', 'POST'])
def service_create(request):
    if not check_permission(request, PermissionCode.SERVICES_MANAGE):
        messages.error(request, 'Permission denied.')
        return redirect('service_list')

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        category_id = request.POST.get('category_id', '')
        duration = request.POST.get('duration_minutes', '30')
        default_price = request.POST.get('default_price', '').strip()

        if not name:
                if is_ajax:
                    return JsonResponse({
                        'success': True,
                        'item': {'id': service.pk, 'label': service.name, 'name': service.name},
                        'message': f'Service "{name}" created.',
                    })

                # HTMX success: close modal, show toast, refresh list
                if is_htmx(request):
                    response = HttpResponse(status=204)
                    return htmx_trigger(response, f'Service "{name}" created.', close_modal=True)

                messages.success(request, f'Service "{name}" created.')
                return redirect('service_detail', pk=service.pk)

        try:
            with transaction.atomic():
                category = None
                if category_id:
                    category = ServiceCategory.objects.filter(pk=category_id).first()

                # Check for existing (case-insensitive)
                existing = Service.objects.filter(name__iexact=name, is_deleted=False).first()
                if existing:
                    if is_ajax:
                        return JsonResponse({
                            'success': True,
                            'item': {'id': existing.pk, 'label': existing.name, 'name': existing.name},
                            'message': f'"{name}" already exists — selected.',
                        })
                    messages.info(request, f'Service "{name}" already exists.')
                    return redirect('service_detail', pk=existing.pk)

                service = Service.objects.create(
                    name=name,
                    description=description,
                    category=category,
                    duration_minutes=int(duration) if duration else 30,
                    is_active=True,
                    created_by=request.user,
                )

                # Set default price if provided
                if default_price:
                    try:
                        price_val = Decimal(default_price)
                        if price_val >= 0:
                            ServicePrice.objects.create(
                                service=service,
                                vehicle_type=None,
                                branch=None,
                                price=price_val,
                                is_active=True,
                                created_by=request.user,
                            )
                    except (InvalidOperation, ValueError):
                        pass

                AuditLog.log(
                    action='SERVICE_CREATED', module='services',
                    user=request.user, object_type='Service',
                    object_id=service.pk, object_repr=service.name,
                    new_data={'name': name, 'default_price': default_price or '0'},
                    ip_address=get_client_ip(request),
                )

                if is_ajax:
                    return JsonResponse({
                        'success': True,
                        'item': {'id': service.pk, 'label': service.name, 'name': service.name},
                        'message': f'Service "{name}" created.',
                    })

                messages.success(request, f'Service "{name}" created.')
                return redirect('service_detail', pk=service.pk)

        except Exception as e:
            logger.exception('Service create failed')
            if is_ajax:
                return JsonResponse({'success': False, 'error': str(e)}, status=500)
            messages.error(request, f'Failed: {e}')

    categories = ServiceCategory.objects.filter(is_active=True).order_by('sort_order', 'name')

    context = {
        'page_title':  'Add Service',
        'action':      'Create',
        'categories':  categories,
    }
    return render(request, 'services/service_form.html', context)


# ─── SERVICE EDIT ─────────────────────────────────────────────

@login_required
@require_http_methods(['GET', 'POST'])
def service_edit(request, pk):
    if not check_permission(request, PermissionCode.SERVICES_MANAGE):
        messages.error(request, 'Permission denied.')
        return redirect('service_list')

    service = get_object_or_404(Service, pk=pk, is_deleted=False)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        category_id = request.POST.get('category_id', '')
        duration = request.POST.get('duration_minutes', '30')
        is_active = request.POST.get('is_active') == 'true'
        tax_override = request.POST.get('tax_override_enabled') == 'true'
        tax_pct = request.POST.get('tax_override_percentage', '0')

        if not name:
            messages.error(request, 'Name required.')
            return redirect('service_edit', pk=pk)

        try:
            with transaction.atomic():
                previous = {
                    'name': service.name,
                    'is_active': service.is_active,
                    'duration': service.duration_minutes,
                }

                service.name = name
                service.description = description
                service.duration_minutes = int(duration) if duration else 30
                service.is_active = is_active
                service.tax_override_enabled = tax_override

                try:
                    service.tax_override_percentage = Decimal(tax_pct)
                except (InvalidOperation, ValueError):
                    service.tax_override_percentage = Decimal('0')

                if category_id:
                    service.category = ServiceCategory.objects.filter(pk=category_id).first()
                else:
                    service.category = None

                service.save()

                AuditLog.log(
                    action='SERVICE_UPDATED', module='services',
                    user=request.user, object_type='Service',
                    object_id=service.pk, object_repr=service.name,
                    previous_data=previous,
                    new_data={'name': name, 'is_active': is_active},
                    ip_address=get_client_ip(request),
                )

                messages.success(request, f'Service "{name}" updated.')
                return redirect('service_detail', pk=service.pk)

        except Exception as e:
            logger.exception('Service edit failed')
            messages.error(request, f'Failed: {e}')

    categories = ServiceCategory.objects.filter(is_active=True).order_by('sort_order', 'name')

    context = {
        'page_title':  f'Edit — {service.name}',
        'service':     service,
        'action':      'Update',
        'categories':  categories,
    }
    return render(request, 'services/service_form.html', context)


# ─── SERVICE DELETE ───────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def service_delete(request, pk):
    if not check_permission(request, PermissionCode.SERVICES_MANAGE):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    service = get_object_or_404(Service, pk=pk, is_deleted=False)
    name = service.name
    service.soft_delete(user=request.user)

    AuditLog.log(
        action='SERVICE_DELETED', module='services',
        user=request.user, object_type='Service',
        object_id=service.pk, object_repr=name,
        ip_address=get_client_ip(request),
    )

    if is_htmx(request):
        response = HttpResponse(status=200)
        return htmx_trigger(response, f'Service "{name}" moved to Trash.')

    return JsonResponse({'success': True, 'message': f'Service "{name}" moved to Trash.'})

# ─── PRICE ADD ────────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def service_price_add(request, pk):
    if not check_permission(request, PermissionCode.SERVICES_MANAGE):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    service = get_object_or_404(Service, pk=pk, is_deleted=False)

    try:
        price_val = Decimal(request.POST.get('price', '0'))
    except (InvalidOperation, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid price.'}, status=400)

    if price_val < 0:
        return JsonResponse({'success': False, 'error': 'Price must be positive.'}, status=400)

    vehicle_type_id = request.POST.get('vehicle_type_id') or None
    branch_id = request.POST.get('branch_id') or None

    from apps.vehicles.models import VehicleType
    from apps.branches.models import Branch

    vehicle_type = None
    if vehicle_type_id:
        vehicle_type = VehicleType.objects.filter(pk=vehicle_type_id).first()

    branch = None
    if branch_id:
        branch = Branch.objects.filter(pk=branch_id).first()

    try:
        price_obj, created = ServicePrice.objects.update_or_create(
            service=service,
            vehicle_type=vehicle_type,
            branch=branch,
            defaults={
                'price': price_val,
                'is_active': True,
                'created_by': request.user,
            }
        )

        AuditLog.log(
            action='SERVICE_PRICE_SET', module='services',
            user=request.user, object_type='ServicePrice',
            object_id=price_obj.pk, object_repr=str(price_obj),
            new_data={
                'service': service.name, 'price': str(price_val),
                'vehicle_type': vehicle_type.name if vehicle_type else 'All',
                'branch': branch.name if branch else 'All',
            },
            ip_address=get_client_ip(request),
        )

        return JsonResponse({
            'success': True,
            'message': f'Price {"created" if created else "updated"} successfully.',
            'price': str(price_val),
        })
    except Exception as e:
        logger.exception('Price add failed')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─── PRICE DELETE ─────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def service_price_delete(request, pk, price_pk):
    if not check_permission(request, PermissionCode.SERVICES_MANAGE):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    price = get_object_or_404(ServicePrice, pk=price_pk, service_id=pk)
    price.delete()

    return JsonResponse({'success': True, 'message': 'Price removed.'})


# ─── QUICK PRICE (from service list) ─────────────────────────

@login_required
@require_http_methods(['POST'])
def service_quick_price_ajax(request, pk):
    if not check_permission(request, PermissionCode.SERVICES_MANAGE):
        if is_htmx(request):
            response = HttpResponse(status=204)
            return htmx_trigger(response, f'Price {"created" if created else "updated"}.', close_modal=True)

        return JsonResponse({
            'success': True,
            'message': f'Price {"created" if created else "updated"}.',
            'price': str(price_val),
        })

    service = get_object_or_404(Service, pk=pk, is_deleted=False)

    try:
        price_val = Decimal(request.POST.get('price', '0'))
    except (InvalidOperation, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid price.'}, status=400)

    vehicle_type_id = request.POST.get('vehicle_type_id') or None
    branch_id = request.POST.get('branch_id') or None

    from apps.vehicles.models import VehicleType
    from apps.branches.models import Branch

    vehicle_type = VehicleType.objects.filter(pk=vehicle_type_id).first() if vehicle_type_id else None
    branch = Branch.objects.filter(pk=branch_id).first() if branch_id else None

    try:
        price_obj, created = ServicePrice.objects.update_or_create(
            service=service, vehicle_type=vehicle_type, branch=branch,
            defaults={'price': price_val, 'is_active': True, 'created_by': request.user},
        )

        return JsonResponse({
            'success': True,
            'message': f'Price {"created" if created else "updated"}.',
            'price': str(price_val),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ─── SERVICE INLINE EDIT ──────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def service_inline_edit(request, pk):
    if not check_permission(request, PermissionCode.SERVICES_MANAGE):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    service = get_object_or_404(Service, pk=pk, is_deleted=False)

    field = request.POST.get('field', '').strip()
    value = request.POST.get('value', '').strip()

    if field not in ['name', 'duration_minutes', 'description']:
        return JsonResponse({'success': False, 'error': 'Invalid field.'}, status=400)

    previous = {field: str(getattr(service, field))}

    if field == 'name':
        if len(value) < 2:
            return JsonResponse({'success': False, 'error': 'Name too short.'}, status=400)
        service.name = value
    elif field == 'duration_minutes':
        try:
            service.duration_minutes = int(value)
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid duration.'}, status=400)
    elif field == 'description':
        service.description = value

    service.save(update_fields=[field, 'updated_at'])

    AuditLog.log(
        action='SERVICE_UPDATED', module='services',
        user=request.user, object_type='Service',
        object_id=service.pk, object_repr=service.name,
        previous_data=previous, new_data={field: str(getattr(service, field))},
        ip_address=get_client_ip(request),
    )

    return JsonResponse({'success': True, 'message': 'Updated.', 'value': str(getattr(service, field))})


# ─── AJAX: Service Search ────────────────────────────────────

@login_required
def service_search_ajax(request):
    q = request.GET.get('q', '').strip()
    show_all = request.GET.get('all', '') == '1'
    vtype_id = request.GET.get('vehicle_type_id', '')
    branch_id = request.GET.get('branch_id', '')

    services = Service.objects.filter(is_deleted=False, is_active=True)

    if q:
        services = services.filter(
            Q(name__icontains=q) | Q(category__name__icontains=q)
        )

    limit = 50 if show_all else 20
    services = services.select_related('category').order_by('category__sort_order', 'name')[:limit]

    from apps.vehicles.models import VehicleType
    from apps.branches.models import Branch

    vehicle_type = None
    if vtype_id:
        vehicle_type = VehicleType.objects.filter(pk=vtype_id).first()

    branch = None
    if branch_id:
        branch = Branch.objects.filter(pk=branch_id).first()

    data = []
    for s in services:
        price_obj = s.get_price(vehicle_type=vehicle_type, branch=branch)
        price_amount = str(price_obj.price) if price_obj else '0'

        data.append({
            'id':       s.pk,
            'label':    s.name,
            'name':     s.name,
            'duration': s.duration_minutes,
            'price':    price_amount,
            'category': s.category.name if s.category else '',
            'meta':     f'{s.category.name if s.category else "No category"} · {s.duration_display} · Rs. {price_amount}',
        })

    return JsonResponse({'services': data})


# ─── CATEGORY VIEWS ──────────────────────────────────────────

@login_required
def category_list(request):
    if not check_permission(request, PermissionCode.SERVICES_MANAGE):
        messages.error(request, 'Permission denied.')
        return redirect('dashboard')

    categories = ServiceCategory.objects.all().order_by('sort_order', 'name')
    context = {
        'page_title':  'Service Categories',
        'categories':  categories,
    }
    return render(request, 'services/category_list.html', context)


@login_required
@require_http_methods(['POST'])
def category_create_ajax(request):
    if not check_permission(request, PermissionCode.SERVICES_MANAGE):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Name required.'}, status=400)

    existing = ServiceCategory.objects.filter(name__iexact=name).first()
    if existing:
        return JsonResponse({
            'success': True,
            'category': {'id': existing.pk, 'name': existing.name},
            'item': {'id': existing.pk, 'label': existing.name, 'name': existing.name},
            'message': f'"{name}" already exists.',
        })

    cat = ServiceCategory.objects.create(name=name, is_active=True)
    return JsonResponse({
        'success': True,
        'category': {'id': cat.pk, 'name': cat.name},
        'item': {'id': cat.pk, 'label': cat.name, 'name': cat.name},
        'message': f'Category "{cat.name}" created.',
    })