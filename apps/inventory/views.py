from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden, JsonResponse
from decimal import Decimal, InvalidOperation
from django.shortcuts import get_object_or_404, redirect, render

from apps.tenants.access import tenant_permission_required
from apps.tenants.plans import tenant_has_feature

from apps.core.models import log_event

from .forms import ConsumableForm, InventoryCategoryForm, StockMovementForm
from .models import Consumable, InventoryCategory, InventoryMovement, ensure_default_categories
from .services import apply_stock_movement, notify_low_stock


def _stock_state(consumable):
    if consumable.planned_stock <= 0:
        return "empty", "Закончился"
    if consumable.minimum_stock > 0 and consumable.planned_stock <= consumable.minimum_stock:
        return "low", "На исходе"
    return "normal", "В норме"


@tenant_permission_required("inventory_view")
def consumable_list(request):
    ensure_default_categories(request.tenant)
    consumables = list(Consumable.objects.filter(
        tenant=request.tenant, location=request.current_location, active=True,
    ).select_related("category"))
    status_counts = {"normal": 0, "low": 0, "empty": 0}
    for item in consumables:
        item.stock_status, item.stock_status_label = _stock_state(item)
        status_counts[item.stock_status] += 1
    return render(request, "inventory/consumable_list.html", {
        "consumables": consumables,
        "low_stock": [item for item in consumables if item.is_low_stock],
        "status_counts": status_counts,
    })


@tenant_permission_required("inventory_manage")
def consumable_create(request):
    form = ConsumableForm(
        request.POST or None, tenant=request.tenant, location=request.current_location,
        advanced=tenant_has_feature(request.tenant, "inventory_automation"),
    )
    if request.method == "POST" and form.is_valid():
        consumable = form.save(commit=False)
        consumable.tenant = request.tenant
        consumable.location = request.current_location
        consumable.planned_stock = consumable.actual_stock
        consumable.save()
        notify_low_stock(consumable)
        if consumable.actual_stock:
            InventoryMovement.objects.create(
                tenant=request.tenant, location=request.current_location, consumable=consumable,
                kind=InventoryMovement.Kind.RECEIPT,
                planned_delta=consumable.actual_stock, actual_delta=consumable.actual_stock,
                planned_after=consumable.actual_stock, actual_after=consumable.actual_stock,
                unit_cost_snapshot=consumable.cost_per_unit,
                reason="Начальный фактический остаток", created_by=request.user,
            )
        log_event(tenant=request.tenant, actor=request.user, action="create", entity=consumable,
                  description=f"Добавлен расходник «{consumable.name}»")
        messages.success(request, "Расходник добавлен.")
        return redirect("consumable-list")
    return render(request, "inventory/consumable_form.html", _consumable_context(form, "Новый расходник"))


@tenant_permission_required("inventory_count")
def consumable_edit(request, consumable_id):
    consumable = get_object_or_404(
        Consumable, tenant=request.tenant, location=request.current_location,
        id=consumable_id, active=True,
    )
    original_plan, original_fact = consumable.planned_stock, consumable.actual_stock
    can_manage = request.tenant_permissions.get("inventory_manage", False)
    form = ConsumableForm(
        request.POST or None, instance=consumable, tenant=request.tenant,
        location=request.current_location,
        advanced=tenant_has_feature(request.tenant, "inventory_automation"),
    )
    if not can_manage:
        for name in ("name", "category", "cost_per_unit", "active"):
            form.fields[name].disabled = True
    if request.method == "POST" and form.is_valid():
        consumable = form.save(commit=False)
        fact_changed = consumable.actual_stock != original_fact
        if fact_changed:
            consumable.planned_stock = consumable.actual_stock
        consumable.save()
        notify_low_stock(consumable)
        if fact_changed:
            InventoryMovement.objects.create(
                tenant=request.tenant, location=request.current_location, consumable=consumable,
                kind=InventoryMovement.Kind.INVENTORY,
                planned_delta=consumable.planned_stock - original_plan,
                actual_delta=consumable.actual_stock - original_fact,
                planned_after=consumable.planned_stock, actual_after=consumable.actual_stock,
                unit_cost_snapshot=consumable.cost_per_unit,
                reason="Обновление фактического остатка в карточке", created_by=request.user,
            )
        log_event(tenant=request.tenant, actor=request.user, action="inventory", entity=consumable,
                  description=f"Обновлены остатки «{consumable.name}»")
        messages.success(request, "Фактический и плановый остатки обновлены.")
        return redirect("consumable-list")
    return render(request, "inventory/consumable_form.html", _consumable_context(form, "Расходник", consumable))


@tenant_permission_required("inventory_view")
def consumable_fact_update(request, consumable_id):
    if not (
        request.tenant_permissions.get("inventory_count")
        or request.tenant_permissions.get("inventory_manage")
    ):
        return HttpResponseForbidden("Недостаточно прав")
    if request.method != "POST":
        return JsonResponse({"error": "Метод не поддерживается"}, status=405)
    consumable = get_object_or_404(
        Consumable, tenant=request.tenant, location=request.current_location,
        id=consumable_id, active=True
    )
    try:
        value = Decimal(request.POST.get("actual_stock", "").replace(",", "."))
    except (InvalidOperation, AttributeError):
        return JsonResponse({"error": "Введите корректное значение"}, status=422)
    if value < 0:
        return JsonResponse({"error": "Остаток не может быть отрицательным"}, status=422)
    original_plan, original_fact = consumable.planned_stock, consumable.actual_stock
    consumable.actual_stock = consumable.planned_stock = value
    consumable.save(update_fields=("actual_stock", "planned_stock", "updated_at"))
    notify_low_stock(consumable)
    InventoryMovement.objects.create(
        tenant=request.tenant, location=request.current_location,
        consumable=consumable, kind=InventoryMovement.Kind.INVENTORY,
        planned_delta=value - original_plan, actual_delta=value - original_fact,
        planned_after=value, actual_after=value, unit_cost_snapshot=consumable.cost_per_unit,
        reason="Обновление факта из списка", created_by=request.user,
    )
    log_event(tenant=request.tenant, actor=request.user, action="inventory", entity=consumable,
              description=f"Фактический остаток «{consumable.name}» изменён на {value}")
    status_key, status_label = _stock_state(consumable)
    return JsonResponse({
        "ok": True, "value": str(value), "planned": str(value),
        "status": status_key, "status_label": status_label,
    })


@tenant_permission_required("inventory_manage")
def consumable_delete(request, consumable_id):
    if request.method != "POST":
        return JsonResponse({"error": "Метод не поддерживается"}, status=405)
    consumable = get_object_or_404(
        Consumable, tenant=request.tenant, location=request.current_location,
        id=consumable_id, active=True
    )
    consumable.active = False
    consumable.save(update_fields=("active", "updated_at"))
    log_event(tenant=request.tenant, actor=request.user, action="archive", entity=consumable,
              description=f"Расходник «{consumable.name}» удалён")
    messages.success(request, "Расходник удалён.")
    return redirect("consumable-list")


def _consumable_context(form, title, consumable=None):
    return {
        "form": form,
        "title": title,
        "consumable": consumable,
        "category_units": {
            str(category.id): category.unit for category in form.fields["category"].queryset
        },
    }


@tenant_permission_required("inventory_manage")
def movement_create(request, consumable_id):
    consumable = get_object_or_404(
        Consumable, tenant=request.tenant, location=request.current_location, id=consumable_id,
    )
    form = StockMovementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            movement = apply_stock_movement(
                consumable=consumable, actor=request.user, **form.cleaned_data
            )
        except ValidationError as exc:
            form.add_error("quantity", exc.message)
        else:
            log_event(
                tenant=request.tenant, actor=request.user, action=movement.kind,
                entity=consumable,
                description=f"{movement.get_kind_display()}: {movement.actual_delta:+} {consumable.unit_display}",
                changes={
                    "plan": {"before": str(movement.planned_after - movement.planned_delta), "after": str(movement.planned_after)},
                    "fact": {"before": str(movement.actual_after - movement.actual_delta), "after": str(movement.actual_after)},
                },
            )
            messages.success(request, "Складская операция сохранена.")
            return redirect("consumable-list")
    return render(request, "inventory/movement_form.html", {"form": form, "consumable": consumable})


@tenant_permission_required("inventory_view")
def movement_list(request):
    movements = InventoryMovement.objects.filter(
        tenant=request.tenant, location=request.current_location,
    ).select_related(
        "consumable", "appointment", "created_by"
    )[:500]
    return render(request, "inventory/movement_list.html", {"movements": movements})


@tenant_permission_required("inventory_manage")
def category_list(request):
    ensure_default_categories(request.tenant)
    categories = InventoryCategory.objects.filter(tenant=request.tenant).prefetch_related("consumables")
    return render(request, "inventory/category_list.html", {"categories": categories})


@tenant_permission_required("inventory_manage")
def category_create(request):
    form = InventoryCategoryForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        category = form.save(commit=False)
        category.tenant = request.tenant
        category.save()
        log_event(tenant=request.tenant, actor=request.user, action="create", entity=category,
                  description=f"Добавлена категория расходников «{category.name}»")
        if request.GET.get("modal") == "1":
            return JsonResponse({"id": str(category.id), "name": category.name, "unit": category.unit})
        messages.success(request, "Категория добавлена.")
        return redirect("inventory-category-list")
    if request.GET.get("modal") == "1":
        return render(
            request, "inventory/_category_modal_form.html",
            {"form": form, "title": "Новая категория"},
            status=422 if request.method == "POST" else 200,
        )
    return render(request, "inventory/category_form.html", {"form": form, "title": "Новая категория"})


@tenant_permission_required("inventory_manage")
def category_edit(request, category_id):
    category = get_object_or_404(InventoryCategory, tenant=request.tenant, id=category_id)
    form = InventoryCategoryForm(request.POST or None, instance=category, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_event(tenant=request.tenant, actor=request.user, action="update", entity=category,
                  description=f"Изменена категория расходников «{category.name}»")
        messages.success(request, "Категория сохранена.")
        return redirect("inventory-category-list")
    return render(request, "inventory/category_form.html", {"form": form, "title": "Редактирование категории"})
