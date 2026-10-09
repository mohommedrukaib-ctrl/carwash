"""
AquaFlow — Vehicle Views
Powered by Quantum Axis
"""

import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Q

from django.urls import reverse
from django.utils.text import Truncator

from .models import Vehicle, Brand, VehicleModel, VehicleType, Color, FuelType
from .forms import VehicleForm, BrandForm, VehicleModelForm
from apps.system.models import AuditLog
from apps.accounts.models import PermissionCode

logger = logging.getLogger('apps')


def check_permission(request, permission_code):
    try:
        return request.user.profile.has_permission(permission_code)
    except Exception:
        return False


def get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


# ─── Vehicle List ─────────────────────────────────────────────

@login_required
def vehicle_list(request):
    if not check_permission(request, PermissionCode.VEHICLES_VIEW):
        messages.error(request, 'Permission denied.')
        return redirect('dashboard')

    search = request.GET.get('q', '').strip()
    status = request.GET.get('status', 'active')

    vehicles = Vehicle.objects.filter(
        is_deleted=False
    ).select_related(
        'customer', 'brand', 'model',
        'vehicle_type', 'color', 'fuel_type'
    )

    if status and status != 'all':
        vehicles = vehicles.filter(status=status)

    if search:
        vehicles = vehicles.filter(
            Q(registration_number__icontains=search) |
            Q(brand__name__icontains=search)         |
            Q(model__name__icontains=search)         |
            Q(customer__name__icontains=search)      |
            Q(customer__phone__icontains=search)
        )

    vehicles   = vehicles.order_by('-created_at')
    paginator  = Paginator(vehicles, 25)
    page       = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    status_classes = {
        'active': 'badge bg-success',
        'inactive': 'badge bg-secondary',
    }

    vehicle_rows = []

    for v in page_obj.object_list:
        vehicle_rows.append({
            'id': v.pk,
            'registrationNumber': v.registration_number,
            'brandName': v.brand.name if v.brand else '',
            'modelName': v.model.name if v.model else '',
            'year': v.year or '',
            'vehicleType': v.vehicle_type.name if v.vehicle_type else '—',
            'colorName': v.color.name if v.color else '—',
            'colorHex': (
                v.color.hex_code
                if v.color and v.color.hex_code else ''
            ),
            'fuelType': v.fuel_type.name if v.fuel_type else '—',
            'customerName': Truncator(v.customer.name).chars(20),
            'customerFullName': v.customer.name,
            'customerPhone': v.customer.phone or '',
            'customerUrl': reverse(
                'customer_detail', kwargs={'pk': v.customer_id}
            ),
            'statusDisplay': v.get_status_display(),
            'statusClass': status_classes.get(
                str(v.status or '').lower(),
                'badge bg-light text-dark',
            ),
            'detailUrl': reverse('vehicle_detail', kwargs={'pk': v.pk}),
            'editUrl': reverse('vehicle_edit', kwargs={'pk': v.pk}),
            'deleteUrl': reverse('vehicle_delete', kwargs={'pk': v.pk}),
        })

    can_create = check_permission(request, PermissionCode.VEHICLES_CREATE)
    can_edit = check_permission(request, PermissionCode.VEHICLES_EDIT)
    can_delete = check_permission(request, PermissionCode.VEHICLES_DELETE)

    vehicle_page_data = {
        'vehicles': vehicle_rows,
        'search': search,
        'status': status,
        'totalCount': paginator.count,
        'listUrl': reverse('vehicle_list'),
        'createUrl': reverse('vehicle_create'),
        'brandListUrl': reverse('brand_list'),
        'canCreate': can_create,
        'canEdit': can_edit,
        'canDelete': can_delete,
        'pagination': {
            'number': page_obj.number,
            'numPages': paginator.num_pages,
            'hasOtherPages': page_obj.has_other_pages(),
            'hasPrevious': page_obj.has_previous(),
            'previousPage': (
                page_obj.previous_page_number()
                if page_obj.has_previous() else None
            ),
            'hasNext': page_obj.has_next(),
            'nextPage': (
                page_obj.next_page_number()
                if page_obj.has_next() else None
            ),
        },
    }

    context = {
        'page_title': 'Vehicles',
        'page_obj': page_obj,
        'vehicles': page_obj,
        'search': search,
        'status': status,
        'total_count': paginator.count,
        'can_create': can_create,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'vehicle_page_data': vehicle_page_data,
    }
    return render(request, 'vehicles/vehicle_list.html', context)


# ─── Vehicle Detail ───────────────────────────────────────────

@login_required
def vehicle_detail(request, pk):
    if not check_permission(request, PermissionCode.VEHICLES_VIEW):
        messages.error(request, 'Permission denied.')
        return redirect('dashboard')

    vehicle = get_object_or_404(Vehicle, pk=pk, is_deleted=False)

    context = {
        'page_title': vehicle.registration_number,
        'vehicle':    vehicle,
        'can_edit':   check_permission(request, PermissionCode.VEHICLES_EDIT),
        'can_delete': check_permission(request, PermissionCode.VEHICLES_DELETE),
        'breadcrumbs': [
            {'label': 'Vehicles',                    'url': '/vehicles/'},
            {'label': vehicle.registration_number,   'url': None},
        ],
    }
    return render(request, 'vehicles/vehicle_detail.html', context)


# ─── Helper: Resolve Pending Items ────────────────────────────

def _resolve_pending_items(request):
    """
    Convert pending brand/model/type/color/fuel names into IDs,
    creating them if they don't exist.
    """
    post = request.POST.copy()

    # Brand
    if not post.get('brand') and post.get('brand_new'):
        name = post['brand_new'].strip().upper()
        if name:
            brand, _ = Brand.get_or_create_safe(name)
            post['brand'] = str(brand.pk)

    # Model (needs brand)
    if not post.get('model') and post.get('model_new'):
        name = post['model_new'].strip().upper()
        brand_id = post.get('brand')
        if name and brand_id:
            try:
                brand = Brand.objects.get(pk=brand_id)
                model, _ = VehicleModel.get_or_create_safe(brand, name)
                post['model'] = str(model.pk)
            except Brand.DoesNotExist:
                pass

    # Vehicle Type
    if not post.get('vehicle_type') and post.get('vehicle_type_new'):
        name = post['vehicle_type_new'].strip().upper()
        if name:
            vt = VehicleType.objects.filter(name__iexact=name).first()
            if not vt:
                vt = VehicleType.objects.create(name=name, is_active=True)
            post['vehicle_type'] = str(vt.pk)

    # Color
    if not post.get('color') and post.get('color_new'):
        name = post['color_new'].strip().upper()
        if name:
            c = Color.objects.filter(name__iexact=name).first()
            if not c:
                c = Color.objects.create(name=name, is_active=True)
            post['color'] = str(c.pk)

    # Fuel Type
    if not post.get('fuel_type') and post.get('fuel_type_new'):
        name = post['fuel_type_new'].strip().upper()
        if name:
            f = FuelType.objects.filter(name__iexact=name).first()
            if not f:
                f = FuelType.objects.create(name=name, is_active=True)
            post['fuel_type'] = str(f.pk)

    return post


# ─── Vehicle Create ───────────────────────────────────────────

@login_required
@require_http_methods(['GET', 'POST'])
def vehicle_create(request):
    if not check_permission(request, PermissionCode.VEHICLES_CREATE):
        messages.error(request, 'Permission denied.')
        return redirect('dashboard')

    is_ajax     = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    customer_id = request.GET.get('customer') or request.POST.get('customer')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                post_data = _resolve_pending_items(request)
                form = VehicleForm(post_data, customer_id=customer_id)

                if form.is_valid():
                    vehicle = form.save(commit=False)
                    vehicle.registration_number = (
                        vehicle.registration_number.strip().upper()
                    )
                    vehicle.created_by = request.user
                    vehicle.save()

                    AuditLog.log(
                        action      = 'VEHICLE_CREATED',
                        module      = 'vehicles',
                        user        = request.user,
                        object_type = 'Vehicle',
                        object_id   = vehicle.pk,
                        object_repr = str(vehicle),
                        new_data    = {
                            'registration_number': vehicle.registration_number,
                            'brand':               vehicle.brand.name,
                            'model':               vehicle.model.name,
                            'customer':            vehicle.customer.name,
                        },
                        ip_address  = get_client_ip(request),
                    )

                    messages.success(
                        request,
                        f'✓ Vehicle {vehicle.registration_number} added.'
                    )

                    if customer_id:
                        return redirect('customer_detail', pk=customer_id)
                    return redirect('vehicle_detail', pk=vehicle.pk)
                else:
                    messages.error(
                        request,
                        'Please fix the errors below.'
                    )
        except Exception as e:
            logger.error(f'Vehicle create error: {e}', exc_info=True)
            messages.error(request, f'Failed to create vehicle: {e}')
            form = VehicleForm(request.POST, customer_id=customer_id)
    else:
        form = VehicleForm(customer_id=customer_id)

    customer = None
    if customer_id:
        from apps.customers.models import Customer
        customer = Customer.objects.filter(
            pk=customer_id, is_deleted=False
        ).first()

    context = {
        'page_title': 'Add Vehicle',
        'form':       form,
        'action':     'Add',
        'customer':   customer,
        'brands':     Brand.objects.filter(is_active=True).order_by('name'),
        'breadcrumbs': [
            {'label': 'Vehicles',    'url': '/vehicles/'},
            {'label': 'Add Vehicle', 'url': None},
        ],
    }
    return render(request, 'vehicles/vehicle_form.html', context)


# ─── Vehicle Edit ─────────────────────────────────────────────

@login_required
@require_http_methods(['GET', 'POST'])
def vehicle_edit(request, pk):
    if not check_permission(request, PermissionCode.VEHICLES_EDIT):
        messages.error(request, 'Permission denied.')
        return redirect('dashboard')

    vehicle = get_object_or_404(Vehicle, pk=pk, is_deleted=False)

    previous_data = {
        'registration_number': vehicle.registration_number,
        'brand':  vehicle.brand.name,
        'model':  vehicle.model.name,
        'status': vehicle.status,
    }

    if request.method == 'POST':
        try:
            with transaction.atomic():
                post_data = _resolve_pending_items(request)
                form = VehicleForm(post_data, instance=vehicle)

                if form.is_valid():
                    vehicle = form.save(commit=False)
                    vehicle.registration_number = (
                        vehicle.registration_number.strip().upper()
                    )
                    vehicle.save()

                    AuditLog.log(
                        action        = 'VEHICLE_UPDATED',
                        module        = 'vehicles',
                        user          = request.user,
                        object_type   = 'Vehicle',
                        object_id     = vehicle.pk,
                        object_repr   = str(vehicle),
                        previous_data = previous_data,
                        new_data      = {
                            'registration_number': vehicle.registration_number,
                            'brand':  vehicle.brand.name,
                            'model':  vehicle.model.name,
                            'status': vehicle.status,
                        },
                        ip_address = get_client_ip(request),
                    )

                    messages.success(
                        request,
                        f'✓ Vehicle {vehicle.registration_number} updated.'
                    )
                    return redirect('vehicle_detail', pk=vehicle.pk)
                else:
                    messages.error(request, 'Please fix the errors below.')
        except Exception as e:
            logger.error(f'Vehicle edit error: {e}', exc_info=True)
            messages.error(request, f'Failed to update: {e}')
            form = VehicleForm(request.POST, instance=vehicle)
    else:
        form = VehicleForm(instance=vehicle)

    context = {
        'page_title': f'Edit — {vehicle.registration_number}',
        'form':       form,
        'vehicle':    vehicle,
        'action':     'Update',
        'brands':     Brand.objects.filter(is_active=True).order_by('name'),
        'breadcrumbs': [
            {'label': 'Vehicles',                   'url': '/vehicles/'},
            {'label': vehicle.registration_number,  'url': f'/vehicles/{pk}/'},
            {'label': 'Edit',                       'url': None},
        ],
    }
    return render(request, 'vehicles/vehicle_form.html', context)


# ─── Vehicle Delete ───────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def vehicle_delete(request, pk):
    if not check_permission(request, PermissionCode.VEHICLES_DELETE):
        return JsonResponse(
            {'success': False, 'error': 'Permission denied.'},
            status=403
        )

    vehicle = get_object_or_404(Vehicle, pk=pk, is_deleted=False)

    try:
        with transaction.atomic():
            reg = vehicle.registration_number
            vehicle.soft_delete(user=request.user)

            AuditLog.log(
                action      = 'VEHICLE_DELETED',
                module      = 'vehicles',
                user        = request.user,
                object_type = 'Vehicle',
                object_id   = vehicle.pk,
                object_repr = reg,
                ip_address  = get_client_ip(request),
            )

        return JsonResponse({
            'success': True,
            'message': f'Vehicle {reg} moved to Trash.',
        })
    except Exception as e:
        logger.error(f'Vehicle delete error: {e}')
        return JsonResponse(
            {'success': False, 'error': 'Failed to delete vehicle.'},
            status=500
        )


# ─── AJAX: Models by Brand ────────────────────────────────────

@login_required
def models_by_brand(request):
    """
    Returns models for a given brand.
    Used by the vehicle form for dynamic model dropdown.
    """
    brand_id = request.GET.get('brand_id')
    if not brand_id:
        return JsonResponse({'models': []})

    try:
        models = VehicleModel.objects.filter(
            brand_id=brand_id,
            is_active=True,
        ).order_by('name').values('id', 'name')

        return JsonResponse({'models': list(models)})
    except Exception:
        return JsonResponse({'models': []})


# ─── AJAX: Vehicle Search (for POS) ──────────────────────────

@login_required
def vehicle_search_ajax(request):
    q           = request.GET.get('q', '').strip()
    customer_id = request.GET.get('customer_id', '')
    show_all    = request.GET.get('all', '') == '1'

    vehicles = Vehicle.objects.filter(
        is_deleted=False,
        status=Vehicle.STATUS_ACTIVE,
    ).select_related(
        'customer', 'brand', 'model', 'vehicle_type', 'color'
    )

    if customer_id:
        vehicles = vehicles.filter(customer_id=customer_id)

    if q:
        vehicles = vehicles.filter(
            Q(registration_number__icontains=q) |
            Q(brand__name__icontains=q) |
            Q(model__name__icontains=q) |
            Q(customer__name__icontains=q)
        )

    limit = 30 if show_all else 15
    vehicles = vehicles.order_by('registration_number')[:limit]

    data = [
        {
            'id':                  v.pk,
            'label':               v.registration_number,
            'registration_number': v.registration_number,
            'brand':               v.brand.name,
            'model':               v.model.name,
            'color':               v.color.name if v.color else '',
            'vehicle_type':        v.vehicle_type.name if v.vehicle_type else '',
            'customer_id':         v.customer_id,
            'customer_name':       v.customer.name,
            'meta':                f'{v.brand.name} {v.model.name} · {v.customer.name}',
        }
        for v in vehicles
    ]
    return JsonResponse({'vehicles': data})


# ─── AJAX: Search Endpoints ───────────────────────────────────

@login_required
def brand_search_ajax(request):
    q = request.GET.get('q', '').strip()
    show_all = request.GET.get('all', '') == '1'
    brands = Brand.objects.filter(is_active=True)
    if q:
        brands = brands.filter(name__icontains=q)
    limit = 50 if show_all else 20
    brands = brands.order_by('name')[:limit]
    data = [{'id': b.pk, 'label': b.name, 'name': b.name} for b in brands]
    return JsonResponse({'brands': data})


@login_required
def model_search_ajax(request):
    q        = request.GET.get('q', '').strip()
    brand_id = request.GET.get('brand_id', '')
    show_all = request.GET.get('all', '') == '1'

    models = VehicleModel.objects.filter(is_active=True)
    if brand_id:
        models = models.filter(brand_id=brand_id)
    if q:
        models = models.filter(name__icontains=q)

    limit = 50 if show_all else 20
    models = models.select_related('brand').order_by('name')[:limit]

    data = [
        {
            'id':       m.pk,
            'label':    m.name,
            'name':     m.name,
            'brand_id': m.brand_id,
            'meta':     m.brand.name,
        }
        for m in models
    ]
    return JsonResponse({'models': data})


@login_required
def vehicle_type_search_ajax(request):
    q = request.GET.get('q', '').strip()
    show_all = request.GET.get('all', '') == '1'
    types = VehicleType.objects.filter(is_active=True)
    if q:
        types = types.filter(name__icontains=q)
    limit = 50 if show_all else 20
    types = types.order_by('sort_order', 'name')[:limit]
    data = [{'id': t.pk, 'label': t.name, 'name': t.name} for t in types]
    return JsonResponse({'types': data})


@login_required
def color_search_ajax(request):
    q = request.GET.get('q', '').strip()
    show_all = request.GET.get('all', '') == '1'
    colors = Color.objects.filter(is_active=True)
    if q:
        colors = colors.filter(name__icontains=q)
    limit = 50 if show_all else 20
    colors = colors.order_by('name')[:limit]
    data = [{'id': c.pk, 'label': c.name, 'name': c.name} for c in colors]
    return JsonResponse({'colors': data})


@login_required
def fuel_type_search_ajax(request):
    q = request.GET.get('q', '').strip()
    show_all = request.GET.get('all', '') == '1'
    fuels = FuelType.objects.filter(is_active=True)
    if q:
        fuels = fuels.filter(name__icontains=q)
    limit = 50 if show_all else 20
    fuels = fuels.order_by('name')[:limit]
    data = [{'id': f.pk, 'label': f.name, 'name': f.name} for f in fuels]
    return JsonResponse({'fuels': data})


# ─── Brand List ───────────────────────────────────────────────

@login_required
def brand_list(request):
    perm = (
        PermissionCode.VEHICLES_MANAGE
        if hasattr(PermissionCode, 'VEHICLES_MANAGE')
        else PermissionCode.SERVICES_MANAGE
    )
    if not check_permission(request, perm):
        messages.error(request, 'Permission denied.')
        return redirect('dashboard')

    brands = Brand.objects.all().order_by('name')

    context = {
        'page_title': 'Vehicle Brands',
        'brands':     brands,
        'breadcrumbs': [
            {'label': 'Vehicles', 'url': '/vehicles/'},
            {'label': 'Brands',   'url': None},
        ],
    }
    return render(request, 'vehicles/brand_list.html', context)


# ─── Brand Create ─────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def brand_create_ajax(request):
    if not check_permission(request, PermissionCode.VEHICLES_CREATE):
        return JsonResponse(
            {'success': False, 'error': 'Permission denied.'}, status=403
        )

    name = request.POST.get('name', '').strip().upper()
    if not name:
        return JsonResponse(
            {'success': False, 'error': 'Name required.'}, status=400
        )

    try:
        with transaction.atomic():
            brand, created = Brand.get_or_create_safe(name)
            if created:
                AuditLog.log(
                    action      = 'BRAND_CREATED',
                    module      = 'vehicles',
                    user        = request.user,
                    object_type = 'Brand',
                    object_id   = brand.pk,
                    object_repr = brand.name,
                    ip_address  = get_client_ip(request),
                )
        return JsonResponse({
            'success': True,
            'item':    {
                'id':    brand.pk,
                'label': brand.name,
                'name':  brand.name,
            },
            'created': created,
            'message': (
                f'Brand "{brand.name}" added.' if created
                else f'Brand "{brand.name}" already exists — selected.'
            ),
        })
    except Exception as e:
        logger.error(f'Brand create error: {e}')
        return JsonResponse(
            {'success': False, 'error': 'Failed.'}, status=500
        )


# ─── Model Create ─────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def model_create_ajax(request):
    if not check_permission(request, PermissionCode.VEHICLES_CREATE):
        return JsonResponse(
            {'success': False, 'error': 'Permission denied.'}, status=403
        )

    brand_id = request.POST.get('brand_id') or request.POST.get('brand')
    name     = request.POST.get('name', '').strip().upper()

    if not brand_id:
        return JsonResponse(
            {'success': False, 'error': 'Brand required.'}, status=400
        )
    if not name:
        return JsonResponse(
            {'success': False, 'error': 'Model name required.'}, status=400
        )

    try:
        with transaction.atomic():
            brand = Brand.objects.get(pk=brand_id)
            model, created = VehicleModel.get_or_create_safe(brand, name)
            if created:
                AuditLog.log(
                    action      = 'VEHICLE_MODEL_CREATED',
                    module      = 'vehicles',
                    user        = request.user,
                    object_type = 'VehicleModel',
                    object_id   = model.pk,
                    object_repr = str(model),
                    ip_address  = get_client_ip(request),
                )
        return JsonResponse({
            'success': True,
            'item':    {
                'id':       model.pk,
                'label':    model.name,
                'name':     model.name,
                'brand_id': model.brand_id,
            },
            'created': created,
            'message': (
                f'Model "{model.name}" added.' if created
                else f'Model "{model.name}" already exists — selected.'
            ),
        })
    except Brand.DoesNotExist:
        return JsonResponse(
            {'success': False, 'error': 'Brand not found.'}, status=400
        )
    except Exception as e:
        logger.error(f'Model create error: {e}')
        return JsonResponse(
            {'success': False, 'error': 'Failed.'}, status=500
        )


# ─── Vehicle Type Create ──────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def vehicle_type_create_ajax(request):
    if not check_permission(request, PermissionCode.VEHICLES_CREATE):
        return JsonResponse(
            {'success': False, 'error': 'Permission denied.'}, status=403
        )
    name = request.POST.get('name', '').strip().upper()
    if not name:
        return JsonResponse(
            {'success': False, 'error': 'Name required.'}, status=400
        )

    try:
        with transaction.atomic():
            vt = VehicleType.objects.filter(name__iexact=name).first()
            created = False
            if not vt:
                vt = VehicleType.objects.create(name=name, is_active=True)
                created = True
                AuditLog.log(
                    action      = 'VEHICLE_TYPE_CREATED',
                    module      = 'vehicles',
                    user        = request.user,
                    object_type = 'VehicleType',
                    object_id   = vt.pk,
                    object_repr = vt.name,
                    ip_address  = get_client_ip(request),
                )
        return JsonResponse({
            'success': True,
            'item':    {'id': vt.pk, 'label': vt.name, 'name': vt.name},
            'created': created,
            'message': (
                f'Vehicle type "{vt.name}" added.' if created
                else f'"{vt.name}" already exists — selected.'
            ),
        })
    except Exception as e:
        logger.error(f'VehicleType create error: {e}')
        return JsonResponse(
            {'success': False, 'error': 'Failed.'}, status=500
        )


# ─── Color Create ─────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def color_create_ajax(request):
    if not check_permission(request, PermissionCode.VEHICLES_CREATE):
        return JsonResponse(
            {'success': False, 'error': 'Permission denied.'}, status=403
        )
    name = request.POST.get('name', '').strip().upper()
    if not name:
        return JsonResponse(
            {'success': False, 'error': 'Name required.'}, status=400
        )

    try:
        with transaction.atomic():
            c = Color.objects.filter(name__iexact=name).first()
            created = False
            if not c:
                c = Color.objects.create(name=name, is_active=True)
                created = True
                AuditLog.log(
                    action      = 'COLOR_CREATED',
                    module      = 'vehicles',
                    user        = request.user,
                    object_type = 'Color',
                    object_id   = c.pk,
                    object_repr = c.name,
                    ip_address  = get_client_ip(request),
                )
        return JsonResponse({
            'success': True,
            'item':    {'id': c.pk, 'label': c.name, 'name': c.name},
            'created': created,
            'message': (
                f'Color "{c.name}" added.' if created
                else f'"{c.name}" already exists — selected.'
            ),
        })
    except Exception as e:
        logger.error(f'Color create error: {e}')
        return JsonResponse(
            {'success': False, 'error': 'Failed.'}, status=500
        )


# ─── Fuel Type Create ─────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def fuel_type_create_ajax(request):
    if not check_permission(request, PermissionCode.VEHICLES_CREATE):
        return JsonResponse(
            {'success': False, 'error': 'Permission denied.'}, status=403
        )
    name = request.POST.get('name', '').strip().upper()
    if not name:
        return JsonResponse(
            {'success': False, 'error': 'Name required.'}, status=400
        )

    try:
        with transaction.atomic():
            f = FuelType.objects.filter(name__iexact=name).first()
            created = False
            if not f:
                f = FuelType.objects.create(name=name, is_active=True)
                created = True
                AuditLog.log(
                    action      = 'FUEL_TYPE_CREATED',
                    module      = 'vehicles',
                    user        = request.user,
                    object_type = 'FuelType',
                    object_id   = f.pk,
                    object_repr = f.name,
                    ip_address  = get_client_ip(request),
                )
        return JsonResponse({
            'success': True,
            'item':    {'id': f.pk, 'label': f.name, 'name': f.name},
            'created': created,
            'message': (
                f'Fuel type "{f.name}" added.' if created
                else f'"{f.name}" already exists — selected.'
            ),
        })
    except Exception as e:
        logger.error(f'FuelType create error: {e}')
        return JsonResponse(
            {'success': False, 'error': 'Failed.'}, status=500
        )


# ─── Customer Quick Create ────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def customer_quick_create_ajax(request):
    """Quick customer create for smart input popup."""
    if not check_permission(request, PermissionCode.VEHICLES_CREATE):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    from apps.customers.models import Customer
    from apps.businesses.models import Business
    from apps.branches.models import Branch

    name  = request.POST.get('name', '').strip()
    phone = request.POST.get('phone', '').strip()

    if not name:
        return JsonResponse({'success': False, 'error': 'Name required.'}, status=400)
    if not phone:
        return JsonResponse({'success': False, 'error': 'Phone required.'}, status=400)

    try:
        with transaction.atomic():
            business = Business.objects.get(pk=1)
            branch   = Branch.objects.filter(is_default=True).first()

            customer = Customer.objects.create(
                name       = name,
                phone      = phone,
                business   = business,
                branch     = branch,
                created_by = request.user,
            )

            AuditLog.log(
                action      = 'CUSTOMER_CREATED',
                module      = 'customers',
                user        = request.user,
                object_type = 'Customer',
                object_id   = customer.pk,
                object_repr = str(customer),
                new_data    = {'name': name, 'phone': phone},
                ip_address  = get_client_ip(request),
            )

        return JsonResponse({
            'success': True,
            'item': {
                'id':    customer.pk,
                'label': customer.name,
                'name':  customer.name,
                'phone': customer.phone,
                'meta':  f'{customer.customer_code} · {customer.phone}',
            },
            'message': f'Customer "{customer.name}" added.',
        })
    except Exception as e:
        logger.error(f'Customer quick create error: {e}')
        return JsonResponse({'success': False, 'error': 'Failed.'}, status=500)


# ─── AJAX: Registration Live Check ────────────────────────────

@login_required
def vehicle_registration_check(request):
    """
    Live check if a vehicle registration exists.
    Returns full vehicle details if found.
    """
    reg = request.GET.get('reg', '').strip().upper()

    if len(reg) < 2:
        return JsonResponse({'exists': False})

    vehicle = Vehicle.objects.filter(
        registration_number__iexact=reg,
        is_deleted=False,
    ).select_related(
        'customer', 'brand', 'model',
        'vehicle_type', 'color', 'fuel_type'
    ).first()

    if not vehicle:
        return JsonResponse({'exists': False})

    return JsonResponse({
        'exists': True,
        'vehicle': {
            'id':                  vehicle.pk,
            'registration_number': vehicle.registration_number,
            'brand':               vehicle.brand.name,
            'model':               vehicle.model.name,
            'vehicle_type':        vehicle.vehicle_type.name
                                   if vehicle.vehicle_type else '',
            'color':               vehicle.color.name if vehicle.color else '',
            'fuel_type':           vehicle.fuel_type.name
                                   if vehicle.fuel_type else '',
            'year':                vehicle.year or '',
            'mileage':             vehicle.mileage or '',
            'notes':               vehicle.notes or '',
            'status':              vehicle.get_status_display(),
            'created_at':          vehicle.created_at.strftime('%d %b %Y'),
            'customer_id':         vehicle.customer.pk,
            'customer_name':       vehicle.customer.name,
            'customer_code':       vehicle.customer.customer_code,
            'customer_phone':      vehicle.customer.phone or '',
            'edit_url':            f'/vehicles/{vehicle.pk}/edit/',
            'detail_url':          f'/vehicles/{vehicle.pk}/',
        }
    })