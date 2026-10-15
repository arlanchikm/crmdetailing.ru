from apps.tenants.access import tenant_owner_required
from django.urls import reverse
from django import forms
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.db import models, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from decimal import Decimal, InvalidOperation

from apps.tenants.access import employee_for_request, permissions_for_request, tenant_permission_required
from apps.operations.calendar import build_calendar_context
from apps.operations.views import _selected_date
from apps.core.models import log_event
from apps.tenants.plans import tenant_has_feature

from .forms import ServiceCategoryForm, ServiceConsumableNormFormSet, ServiceForm, WorkPostForm
from .models import (
    Service, ServiceCategory, ServiceVehicleClassPrice, WorkPost,
    ensure_default_service_categories, ensure_system_services,
)


def _norm_groups(request, form, enabled):
    groups = []
    for vehicle_class in [None] + [row["vehicle_class"] for row in form.vehicle_class_rows]:
        prefix = f"norms_{vehicle_class.id}" if vehicle_class else "norms"
        submitted = request.method == "POST" and enabled and f"{prefix}-TOTAL_FORMS" in request.POST
        formset = ServiceConsumableNormFormSet(
            request.POST if submitted else None, instance=form.instance, prefix=prefix,
            queryset=form.instance.consumable_norms.filter(vehicle_class=vehicle_class),
            form_kwargs={"tenant": request.tenant, "location": request.current_location},
        )
        groups.append({"vehicle_class": vehicle_class, "formset": formset, "submitted": submitted,
                       "title": vehicle_class.name if vehicle_class else "Общий норматив"})
    return groups


def _norm_groups_valid(groups):
    return all([group["formset"].is_valid() for group in groups if group["submitted"]])


def _save_norm_groups(groups, service):
    for group in groups:
        if not group["submitted"]:
            continue
        formset = group["formset"]
        formset.instance = service
        norms = formset.save(commit=False)
        for deleted in formset.deleted_objects:
            deleted.delete()
        for norm in norms:
            norm.tenant = service.tenant
            norm.vehicle_class = group["vehicle_class"]
            norm.save()


@tenant_permission_required("services_view")
def service_list(request):
    ensure_system_services(request.tenant, request.current_location)
    ensure_default_service_categories(request.tenant, request.current_location)
    services = Service.objects.filter(
        tenant=request.tenant, location=request.current_location,
    ).select_related("category").prefetch_related(
        "consumable_norms__consumable__category", "vehicle_class_prices__vehicle_class"
    )
    categories = ServiceCategory.objects.filter(
        tenant=request.tenant, location=request.current_location, active=True,
    )
    active_counts = {
        row["category_id"]: row["total"]
        for row in services.filter(active=True).values("category_id").annotate(total=models.Count("id"))
    }
    for category in categories:
        category.active_service_count = active_counts.get(category.id, 0)
    return render(request, "catalog/service_list.html", {
        "services": services, "service_categories": categories,
        "active_service_count": services.filter(active=True).count(),
    })


@tenant_permission_required("services_manage")
def service_create(request):
    norms_enabled = tenant_has_feature(request.tenant, "inventory_automation")
    form = ServiceForm(request.POST or None, tenant=request.tenant, location=request.current_location)
    groups = _norm_groups(request, form, norms_enabled)
    formset = groups[0]["formset"]
    if request.method == "POST" and form.is_valid() and (not norms_enabled or _norm_groups_valid(groups)):
        with transaction.atomic():
            service = form.save(commit=False)
            service.tenant = request.tenant
            service.location = request.current_location
            service.save()
            form.save_vehicle_class_prices(service)
            if norms_enabled:
                _save_norm_groups(groups, service)
        messages.success(request, "Услуга добавлена.")
        log_event(tenant=request.tenant, actor=request.user, action="service_create", entity=service,
                  description=f"Добавлена услуга «{service.name}»")
        return redirect("service-list")
    return render(request, "catalog/service_form.html", _service_form_context(
        form, formset, "Новая услуга", request.tenant, norms_enabled, groups,
    ))


@tenant_permission_required("services_manage")
def service_edit(request, service_id):
    service = get_object_or_404(
        Service.objects.prefetch_related("consumable_norms"), id=service_id,
        tenant=request.tenant, location=request.current_location,
    )
    if request.method == "POST" and request.POST.get("action") == "toggle_active":
        if service.system_key == "other":
            return JsonResponse({"error": "Системную услугу нельзя отключить."}, status=400)
        service.active = not service.active
        service.save(update_fields=("active", "updated_at"))
        log_event(
            tenant=request.tenant, actor=request.user, action="service_update", entity=service,
            description=f"Услуга «{service.name}» {'включена' if service.active else 'отключена'}",
        )
        return JsonResponse({"ok": True, "active": service.active})
    norms_enabled = tenant_has_feature(request.tenant, "inventory_automation")
    form = ServiceForm(
        request.POST or None, instance=service, tenant=request.tenant,
        location=request.current_location,
    )
    groups = _norm_groups(request, form, norms_enabled)
    formset = groups[0]["formset"]
    if request.method == "POST" and form.is_valid() and (not norms_enabled or _norm_groups_valid(groups)):
        with transaction.atomic():
            service = form.save(commit=False)
            if service.system_key == "other":
                service.name = "Другое"
                service.active = True
            service.save()
            form.save_vehicle_class_prices(service)
            if norms_enabled:
                _save_norm_groups(groups, service)
        messages.success(request, "Услуга сохранена.")
        log_event(tenant=request.tenant, actor=request.user, action="service_update", entity=service,
                  description=f"Изменена услуга «{service.name}»")
        return redirect("service-list")
    return render(request, "catalog/service_form.html", _service_form_context(
        form, formset, "Редактирование услуги", request.tenant, norms_enabled, groups,
    ))


@tenant_permission_required("services_manage")
def service_category_list(request):
    ensure_default_service_categories(request.tenant, request.current_location)
    categories = ServiceCategory.objects.filter(
        tenant=request.tenant, location=request.current_location,
    ).annotate(service_count=models.Count("services"))
    return render(request, "catalog/service_category_list.html", {"categories": categories})


@tenant_permission_required("services_manage")
def service_category_create(request):
    form = ServiceCategoryForm(
        request.POST or None, tenant=request.tenant, location=request.current_location,
    )
    if request.method == "POST" and form.is_valid():
        category = form.save(commit=False)
        category.tenant = request.tenant
        category.location = request.current_location
        category.save()
        log_event(
            tenant=request.tenant, actor=request.user, action="create", entity=category,
            description=f"Добавлена категория услуг «{category.name}»",
        )
        messages.success(request, "Категория услуг добавлена.")
        return redirect("service-category-list")
    return render(request, "catalog/service_category_form.html", {
        "form": form, "title": "Новая категория услуг",
    })


@tenant_permission_required("services_manage")
def service_category_edit(request, category_id):
    category = get_object_or_404(
        ServiceCategory, id=category_id, tenant=request.tenant,
        location=request.current_location,
    )
    form = ServiceCategoryForm(
        request.POST or None, instance=category, tenant=request.tenant,
        location=request.current_location,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        log_event(
            tenant=request.tenant, actor=request.user, action="update", entity=category,
            description=f"Изменена категория услуг «{category.name}»",
        )
        messages.success(request, "Категория услуг сохранена.")
        return redirect("service-category-list")
    return render(request, "catalog/service_category_form.html", {
        "form": form, "title": "Редактирование категории услуг", "category": category,
    })


@tenant_permission_required("services_manage")
def service_category_delete(request, category_id):
    if request.method != "POST":
        return JsonResponse({"error": "Метод не поддерживается"}, status=405)
    category = get_object_or_404(
        ServiceCategory, id=category_id, tenant=request.tenant,
        location=request.current_location,
    )
    name = category.name
    affected = category.services.count()
    category.delete()
    log_event(
        tenant=request.tenant, actor=request.user, action="delete", entity=request.tenant,
        description=f"Удалена категория услуг «{name}», без категории осталось услуг: {affected}",
    )
    messages.success(request, "Категория удалена. Связанные услуги оставлены без категории.")
    return redirect("service-category-list")


@tenant_permission_required("services_manage")
def service_class_price_update(request, service_id, price_id):
    if request.method != "POST":
        return JsonResponse({"error": "Метод не поддерживается"}, status=405)
    service = get_object_or_404(
        Service, id=service_id, tenant=request.tenant, location=request.current_location,
    )
    bulk_values = {
        key.removeprefix("price_"): value
        for key, value in request.POST.items()
        if key.startswith("price_")
    }
    if bulk_values:
        rows = {
            str(row.id): row
            for row in ServiceVehicleClassPrice.objects.filter(
                id__in=bulk_values, service=service, tenant=request.tenant,
            ).select_related("vehicle_class")
        }
        errors = {}
        parsed = {}
        price_field = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0)
        for row_id, raw_value in bulk_values.items():
            try:
                value = price_field.clean(raw_value.replace(",", "."))
            except (ValidationError, AttributeError):
                errors[row_id] = "Неверное значение"
                continue
            if row_id not in rows:
                errors[row_id] = "Неверное значение"
                continue
            parsed[row_id] = value
        if errors or len(rows) != len(bulk_values):
            return JsonResponse({"errors": errors or {row_id: "Неверное значение" for row_id in bulk_values}}, status=422)
        with transaction.atomic():
            for row_id, value in parsed.items():
                rows[row_id].price = value
                rows[row_id].save(update_fields=("price",))
            service.price = min(
                service.vehicle_class_prices.filter(price__isnull=False).values_list("price", flat=True)
            )
            service.save(update_fields=("price", "updated_at"))
        log_event(
            tenant=request.tenant, actor=request.user, action="service_price_update", entity=service,
            description=f"Обновлены цены «{service.name}» для {len(parsed)} классов автомобилей",
        )
        return JsonResponse({
            "ok": True, "minimum": str(service.min_price), "maximum": str(service.max_price),
        })

    row = get_object_or_404(
        ServiceVehicleClassPrice, id=price_id, service=service, tenant=request.tenant
    )
    try:
        price = Decimal(request.POST.get("price", "").replace(",", "."))
    except (InvalidOperation, AttributeError):
        return JsonResponse({"error": "Введите корректную цену"}, status=422)
    if price < 0:
        return JsonResponse({"error": "Цена не может быть отрицательной"}, status=422)
    row.price = price
    row.save(update_fields=("price",))
    service.price = min(service.vehicle_class_prices.filter(price__isnull=False).values_list("price", flat=True))
    service.save(update_fields=("price", "updated_at"))
    log_event(tenant=request.tenant, actor=request.user, action="service_price_update", entity=service,
              description=f"Цена «{service.name}» для класса «{row.vehicle_class.name}»: {price} ₽")
    return JsonResponse({
        "ok": True, "price": str(price), "minimum": str(service.min_price),
        "maximum": str(service.max_price),
    })


def _service_form_context(form, formset, title, tenant, norms_enabled=True, norm_groups=None):
    consumables = formset.forms[0].fields["consumable"].queryset if formset.forms else formset.empty_form.fields["consumable"].queryset
    return {
        "form": form,
        "formset": formset,
        "norm_groups": norm_groups or [],
        "title": title,
        "norms_enabled": norms_enabled,
        "service_cost": form.instance.cost if not form.instance._state.adding else 0,
        "consumable_data": {
            str(item.id): {"unit": item.unit, "cost": str(item.cost_per_unit)}
            for item in consumables
        },
    }


@tenant_permission_required("appointments_view")
def post_list(request):
    request.user.calendar_resource_kind="post"
    request.user.save(update_fields=("calendar_resource_kind",))
    return redirect("appointment-calendar")


@tenant_owner_required
def post_create(request):
    form = WorkPostForm(request.POST or None, tenant=request.tenant, location=request.current_location)
    if request.method == "POST" and form.is_valid():
        post = form.save(commit=False)
        post.tenant = request.tenant
        post.location = request.current_location
        post.save()
        log_event(tenant=request.tenant, actor=request.user, action="post_create", entity=post,
                  description=f"Добавлен рабочий пост «{post.name}»")
        messages.success(request, "Рабочий пост добавлен.")
        return redirect(reverse("company-settings")+"#work-posts")
    return render(request, "catalog/form.html", {"form": form, "title": "Новый рабочий пост"})


@tenant_owner_required
def post_edit(request, post_id):
    post = get_object_or_404(
        WorkPost, id=post_id, tenant=request.tenant, location=request.current_location,
    )
    form = WorkPostForm(
        request.POST or None, instance=post, tenant=request.tenant,
        location=request.current_location,
    )
    if request.method == "POST" and form.is_valid():
        post = form.save()
        log_event(tenant=request.tenant, actor=request.user, action="post_update", entity=post,
                  description=f"Изменён рабочий пост «{post.name}»")
        messages.success(request, "Рабочий пост сохранен.")
        return redirect(reverse("company-settings")+"#work-posts")
    return render(request, "catalog/form.html", {"form": form, "title": "Редактирование поста"})
